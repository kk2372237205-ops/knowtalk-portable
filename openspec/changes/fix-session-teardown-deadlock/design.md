# Design

## 现场证据

`py-spy dump --pid <app.py>`（连接断开后服务端已无 HTTP 响应）：

```
Thread (MainThread): _wait_for_tstate_lock → join → HumanPlayer._stop (webrtc.py:332)
                     ← WebRTCOutput.stop (webrtc.py:208) ← aiortc RTCRtpSender._run_rtp
Thread "media-player":  queue.put ← MelASR.run_step (mel.py:104)
Thread "inference":     queue.put ← BaseAvatar.inference (base_avatar.py:600)
Thread "process_frames":queue.put ← WebRTCOutput.push_audio (webrtc.py:276)
```

`/api/status`、`/quiz-panel.js` 均超时，同机 8090 正常 → 事件循环被 `join` 占死。

## 死锁环

```
浏览器断开
  → aiortc 停止调用 PlayerStreamTrack.recv()
  → __audio._queue(有界) 只进不出
  → process_frames: push_audio.put(block=True) 永久阻塞
  → res_frame_queue(有界) 塞满 → inference.put 永久阻塞
  → feat_queue(maxsize=5) 塞满 → render 主循环里 mel.run_step().put 永久阻塞
  → render 无法回到 quit_event 判断，media-player 线程永不退出
  → HumanPlayer._stop().join()（无超时，且跑在事件循环线程）→ aiohttp 永久停止服务
```

关键点：**`quit_event.set()` 对阻塞在 `put` 上的线程无效**——它阻塞的是队列，不是事件。所以"置位退出标志"必须配合"队列写入有界 + 拆除时清队列"才有意义。

## 决策

### 1. 队列写入用"限时 + 丢最旧"，不做无界丢弃

新增 `utils/queue_guard.py::put_capped(q, item, timeout, should_abort, on_drop)`：

- 先 `put(item, timeout)`；满了就 `get_nowait()` 丢最旧一项腾位再试一次；仍失败则丢弃本次写入。
- 最坏阻塞 `2 × timeout`（默认 0.5s → 最多 1s），恒定有界。
- 保留"丢最旧"而非"丢新"，因为实时音视频要的是最新画面/声音。
- 选它而不是 `put_nowait`：正常运行时队列几乎不会满，限时等待能让偶发拥塞自愈，不产生可感知的丢帧。

### 2. 消费端等待必须能被 quit 打断

`inference` 里 `asr.output_queue.get()`（无超时）改成 `get(timeout=...)` + 检查 `quit_event`，缺帧时按静音补齐——与既有 `feat_queue` 超时分支同一策略，不新增语义。

### 3. join 一律有界，且线程为 daemon**

- `HumanPlayer._stop()`：`join(timeout=TEARDOWN_JOIN_TIMEOUT=2.0)`，超时告警并把引用置空，不再阻塞事件循环。
- `BaseAvatar.render()`：对 inference / process 线程同样加超时。
- 三个工作线程 `daemon=True`：即使某次拆除超时留下残线程，也不会阻塞进程退出。

### 4. 拆除顺序：先"叫醒"再"停止"

`remove_session()` 原顺序是 `quit_event.set() → output.stop() → flush_talk()`。`output.stop()` 会 join，而此时生产者还卡在 `put` 上——顺序反了。改为 `quit_event.set() → flush_talk()`（清 `res_frame_queue` / 音频缓冲 / asr 队列，把阻塞的生产者放出来）`→ output.stop()`。

### 5. 控制台连接的静默等待一并消除

`web/index.html::negotiate()` 与展示页修复前的缺陷同款：ICE 收集等待只有成功路径、`/offer` 无超时。补 `waitIceGathering(pc, ms)` 软超时与 `postOffer()` 的 `AbortController` 超时、非 2xx 可读错误；`start()` 先重置旧连接并用局部 `peer` 引用（避免旧协商链把 SDP 写进新 `PeerConnection`）；`stop()` 关闭上一次打开的展示窗口。

## 兼容性

- `/offer`、`/api/*` 协议不变，无需改前端调用方。
- 丢帧分支只在队列持续满时触发；正常讲课路径不受影响。
- 单测覆盖：`tests/test_queue_guard.py`（helper 语义）、`tests/test_teardown_deadlock.py`（有界 join / push_audio 有界 / 拆除顺序 / 源码守卫）。
