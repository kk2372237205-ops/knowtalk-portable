# Tasks

## 1. 连接链路加超时与阶段化状态

- [x] 1.1 `web/knowtalk-chat.html` 新增连接常量与状态机：`CONNECT_LIMITS`（iceMs=3000、offerMs=60000、mediaMs=20000、frameMs=20000、maxAttempts=3）、`_connectPhase`、`_connectAttempt`、`_connectInFlight`；新增 `setConnectPhase(phase, reason)` 统一更新 `#status-indicator` 与占位层文案
- [x] 1.2 `negotiate()` 的 ICE 等待改为 `waitIceGathering(pc, ms)`：`complete` 立即 resolve，到点软超时 resolve（控制台警告），不再无限等待；发送前仍从 `pc.localDescription` 取 SDP
- [x] 1.3 `POST /offer` 改为 `postOffer(body)`：带 `AbortController` 超时、非 2xx 时读取响应体并抛出可读错误、校验 `sdp`/`type` 存在后再 `setRemoteDescription`

## 2. 首帧判据与画面就绪

- [x] 2.1 `track` 事件对 `evt.streams` 为空的情况用 `new MediaStream([evt.track])` 兜底（视频、音频轨都做）
- [x] 2.2 `video` 的 `loadeddata` 触发 `markVideoReady()`：清首帧看门狗、隐藏占位层、状态置「已连接」；在此之前占位层显示「已连接，正在等待画面…」
- [x] 2.3 媒体通道 `connected` 后启动首帧看门狗（20s），到点无帧按失败处理（`已连接但无画面`）

## 3. 重试与清理

- [x] 3.1 新增 `resetPeer()`：关闭旧 `pc`、清空 `video/audio.srcObject`、清所有定时器（含 `_slideRefreshTimer`、`_firstFrameTimer`、`_mediaTimer`、`_disconnectedTimer`）、复位 `connected`/`_connectInFlight`、移除首帧监听
- [x] 3.2 失败后自动重试：`min(1.5s × attempt, 4s)` 退避，最多 3 次；耗尽后进入 `failed` 并显示「重试」按钮
- [x] 3.3 新增 `retryConnection()` 供按钮调用：重置尝试计数后 `resetPeer()` 再重连；在途协商的失败由 `peer !== pc` 守卫丢弃
- [x] 3.4 `connectionstatechange`：`failed` 立即失败并重试，`disconnected` 给 3s 宽限窗口后再判定，`connected` 进入 `waiting_frame`
- [x] 3.5 幻灯片定时刷新改为单实例（新增 `stopSlidePolling()`，重连前清旧定时器）；新增 `pagehide` 关闭 `pc`

## 4. 异常可见化

- [x] 4.1 脚本顶部注册 `window` 的 `error` 与 `unhandledrejection`：异常消息写入占位层与 `#status-indicator`
- [x] 4.2 主脚本前插入内联启动看门狗（`__connectStartWatchdog`，8s）：主脚本被资源阻塞/加载失败而没跑起来时，改写占位层为「页面脚本未能启动…」，不再静默转圈

## 5. 验证

- [x] 5.1 `node --check` 校验改动后的两段内联脚本语法（均 OK）
- [x] 5.2 全量 pytest 627 passed；JS 测试 50 passed（含新增 `tests/js/display-connect-guard.test.js` 7 项源码级回归守卫）
- [x] 5.3 `openspec validate --all --strict` 通过（23 passed）
- [ ] 5.4 人工走查：强刷展示页能正常连上；停服务后强刷能在有限时间内显示失败原因与「重试」按钮，恢复后点「重试」能连上（留待用户浏览器验证）
