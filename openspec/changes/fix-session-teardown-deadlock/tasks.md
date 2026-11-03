# Tasks

## 1. 队列写入有界化

- [x] 1.1 新增 `utils/queue_guard.py::put_capped(q, item, timeout=0.5, should_abort=None, on_drop=None)`：限时写入 → 满则丢最旧一项再试 → 仍失败则丢弃本次写入；返回是否写入成功，`on_drop` 便于调用方限频告警。验证：`tests/test_queue_guard.py`（满队列不阻塞、丢最旧、`should_abort` 立即返回、正常队列不丢）
- [x] 1.2 `server/webrtc.py::HumanPlayer.push_audio` 改用它（保留"音频保序、不主动丢新帧"的语义，只在队列持续满时丢最旧）；轨道 `readyState === 'ended'` 时直接丢弃不等待；丢帧告警限频。验证：`tests/test_teardown_deadlock.py::PushAudioBoundedTestCase`
- [x] 1.3 `avatars/base_avatar.py::inference` 三处 `res_frame_queue.put`（静音分支 ×1、说话分支 ×2）改用 `put_res`（内部 `put_capped`，`should_abort=quit_event.is_set`）。验证：`SourceGuardTestCase`
- [x] 1.4 `avatars/audio_features/mel.py::run_step` 的 `output_queue.put` 与 `feat_queue.put` 改用 `put_capped`。验证：`SourceGuardTestCase`

## 2. 消费端等待可中断

- [x] 2.1 `avatars/base_avatar.py::inference` 的 `self.asr.output_queue.get()`（无超时）改为限时等待并检查 `quit_event`，缺帧按静音补齐（与 `feat_queue` 超时分支一致）；拆除中丢弃本轮。验证：`SourceGuardTestCase::test_inference_audio_wait_is_interruptible`

## 3. 线程 join 有界

- [x] 3.1 `server/webrtc.py::HumanPlayer._stop` 的 `join()` 加 `timeout=TEARDOWN_JOIN_TIMEOUT`（2s），超时告警并置空引用；`_start` 的线程标记 `daemon=True`。验证：`StopJoinBoundedTestCase`（线程不退出时 `_stop` 在 3.5s 内返回）
- [x] 3.2 `avatars/base_avatar.py::render` 对 inference / process 线程的 `join()` 加超时（`THREAD_JOIN_TIMEOUT`）并告警；两线程 `daemon=True`。验证：`SourceGuardTestCase::test_render_joins_are_bounded`

## 4. 拆除顺序与资源释放

- [x] 4.1 `server/session_manager.py::remove_session`：`quit_event.set()` 后先 `flush_talk()`（清 `res_frame_queue`/音频缓冲/asr 队列，唤醒阻塞的生产者）再 `output.stop()`；删掉尾部重复的 `flush_talk`。验证：`RemoveSessionOrderTestCase`（断言顺序为 `flush:quit → stop`）

## 5. 控制台连接不得静默等待

- [x] 5.1 `web/index.html::negotiate` 的 ICE 收集等待加软超时（`waitIceGathering(peer, 3000)`，到点用已收集候选继续），`/offer` 走 `postOffer()`（`AbortController` + 15s 超时），非 2xx / 缺 `sdp` 抛可读错误。验证：`tests/js/console-connect-guard.test.js`
- [x] 5.2 `start()` 先 `resetConnection()`、用局部 `peer` 引用（`peer !== pc` 守卫丢弃旧链），失败信息写进 `#mainStatus` 并恢复「开始连接」按钮，不再 `alert`。验证：同上
- [x] 5.3 `stop()` 走 `resetConnection()`（中止在途请求 + 关闭旧 `PeerConnection`）、关闭上一次打开的展示窗口并清空引用

## 6. 验证

- [x] 6.1 `tests/test_queue_guard.py`（10 项）、`tests/test_teardown_deadlock.py`（12 项）全绿；全量 pytest **696 passed**
- [x] 6.2 `node --test`（显式列文件）**104 passed**，含新增 `tests/js/console-connect-guard.test.js`（7 项）；两页内联脚本 `node --check` 通过
- [x] 6.3 `openspec validate --all --strict` 通过（24 passed）
- [x] 6.4 合成 WebRTC 客户端实测（`.tmp_verify/reconnect_probe.py`，aiortc 客户端）：连接 → 出帧（`audio start` / `video start`）→ 关闭连接 → 六次 `/api/status` 探测全部在线 → 再连接并再次出帧 → 再关闭后仍在线；服务端日志两轮均出现 `Connection state is closed → Removing session → resources released`，无 join 超时告警，另有一次 `[背压] res_frame ...（abort）`（推理线程按 quit 及时停手）。RESULT: PASS
- [ ] 6.5 浏览器人工走查：控制台「开始连接」→「断开连接」→「开始连接」可成功重连并出画面；断开后展示页窗口被关闭、状态区显示「已断开连接」；服务端 `/api/status` 始终有响应（留待用户浏览器验证）
