# Proposal

## Why

现场反馈：控制台点「开始连接」→「断开连接」→ 再点「开始连接」，就**再也连不上**。复现后抓到两层证据：

1. 服务端 `knowtalk.log` 里，第二次连接**一条 `Creating sessionid` 都没有** —— `/offer` 请求根本没被处理。
2. 对 `127.0.0.1:8010` 请求 `/api/status`、甚至连静态文件 `/quiz-panel.js` 都**超时无响应**，而同机 RAG（:8090）正常，端口 TCP 能建立连接。说明 aiohttp 事件循环已经被卡死，不是网络也不是前端。

`py-spy dump` 打出完整死锁环（`app.py` 主线程卡在 `threading.join`）：

```
MainThread:  _wait_for_tstate_lock → join → HumanPlayer._stop (server/webrtc.py:332)
             ← WebRTCOutput.stop (server/webrtc.py:208) ← aiortc RTCRtpSender._run_rtp
media-player: queue.put  ← MelASR.run_step (avatars/audio_features/mel.py:104 feat_queue.put)
inference:    queue.put  ← BaseAvatar.inference (avatars/base_avatar.py:600 res_frame_queue.put)
process_frames: queue.put ← push_audio (server/webrtc.py:276 __audio._queue.put(block=True))
```

链路是闭环的：

1. 浏览器断开 → aiortc 不再调用 `PlayerStreamTrack.recv()` → 音频队列（`Queue(maxsize=100)`）**只进不出**；
2. `process_frames` 在 `push_audio` 的 `put(block=True)` 上**永久阻塞**（注释写的是"队列满时短暂阻塞"，实际没有上限）；
3. `res_frame_queue`（`Queue(batch_size*3)`）跟着塞满 → `inference` 的 `put` 永久阻塞；
4. `feat_queue`（`Queue(maxsize=5)`）跟着塞满 → `render()` 主循环里的 `mel.run_step()` 的 `put` 永久阻塞，**`render` 再也回不到 `while not quit_event.is_set()` 的判断**；
5. `HumanPlayer._stop()` 在事件循环线程里 `self.__thread.join()` **无超时** → 整个 aiohttp 事件循环被一个已死的会话永久拖住。

即：**任何一个页面断开连接，都可能让整个服务失去响应**，与提问模式、与具体浏览器无关。之前只加固了展示页前端（`harden-display-connection`），但前端再怎么重试，服务端已经不回包了。

## What Changes

- **有界队列的写入一律不得无限阻塞**：`push_audio`（音频）、`inference`（推理结果）、`mel.run_step`（特征批次）三处 `put` 改为"限时等待 + 丢最旧一项腾位 + 最坏情况丢弃本帧"，并限频告警。实时流宁可丢一帧，也不能让生产线程永久挂死。
- **消费端的等待必须可中断**：`inference` 里 `asr.output_queue.get()` 由无超时阻塞改为限时等待并检查 `quit_event`，缺帧时按静音补齐（与既有缺帧分支同一策略）。
- **线程 join 一律有界**：`HumanPlayer._stop()` 的 `join()`、`BaseAvatar.render()` 里对 inference / process 线程的 `join()` 全部加超时；超时只告警不阻塞调用方。三个工作线程标记为 daemon，进程退出不再被它们拖住。
- **拆除顺序修正**：`SessionManager.remove_session()` 先置 `quit_event` 并清空队列（唤醒阻塞中的生产者），再 `output.stop()`（此时 join 才有可能在超时内返回）。
- **控制台连接不得静默等待**：`web/index.html` 的 `negotiate()` 目前 ICE 收集等待和 `/offer` 请求都没有超时（与展示页修复前同款缺陷），一旦服务端变慢/不响应就永久停在"连接中"。补上 ICE 等待上限、请求超时与可读失败原因，并让「断开连接」关闭上一次打开的展示窗口、彻底重置 `PeerConnection`，避免旧会话残留。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `session-control`：新增"会话拆除不得阻塞事件循环"、"工作线程队列写入必须有界"、"单会话断连不得使服务失去响应"三条要求，并补充控制台连接与断开的重置契约。

## Impact

- 代码：`server/webrtc.py`（音频队列写入、`HumanPlayer._stop/_start`）、`avatars/base_avatar.py`（推理结果写入、输出队列读取、`render` 的 join）、`avatars/audio_features/mel.py`（特征队列写入）、`server/session_manager.py`（拆除顺序）、新增 `utils/queue_guard.py`、`web/index.html`（连接超时与断开重置）。
- 协议：`/offer`、`/api/*` 与前端接口签名**不变**；`app.py` 需重启才生效。
- 用户可见行为：断开连接后能立刻重连；服务端不会再因为某个页面消失而整体失去响应。极端拥塞下的代价是**丢帧**（音频短暂断音 / 视频跳帧），而不是整机假死。
- 风险：改动涉及渲染线程的阻塞语义，需要在真机上确认长时间讲课不出现明显丢帧；`join` 超时后线程可能仍在运行，因此在 `remove_session` 之后要继续避免复用同一 `sessionid`。
