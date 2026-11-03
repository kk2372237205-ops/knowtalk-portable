# Proposal

## Why

数字人展示页（`web/knowtalk-chat.html`）刷新（尤其是强刷 Ctrl+F5）后会**一直停在「正在连接数字人…」**，既不报错也不恢复。现场证据是服务端 `knowtalk.log` 在刷新后**完全没有新的 `/offer` 记录**（`Creating sessionid` 一条都没有），说明请求根本没发出去，卡在浏览器端。

排查下来，现有连接链路存在三处会"静默永久等待"的缺陷：

1. **ICE 收集等待没有超时**。`negotiate()` 用 `pc.addEventListener('icegatheringstatechange', ...)` 等 `iceGatheringState === 'complete'`，这个 `Promise` 只有成功路径、**没有超时也没有失败路径**。一旦状态因网络/浏览器侧原因不再推进，`negotiate()` 永远不 resolve，后面的 `fetch('/offer')` 永远不会执行 —— 服务端于是看不到任何请求，页面永远转圈。
2. **信令请求没有超时，错误也不可读**。`fetch('/offer')` 直接 `.then(r => r.json())`：服务端返回 5xx 错误体时 `answer.sdp` 不存在，`setRemoteDescription(undefined)` 抛出的是一条和真实原因无关的异常；请求本身卡住时也没有任何兜底。
3. **没有"视频首帧"判定，只按信令成功就认为连上了**。当前 `negotiate()` resolve 后立刻 `setConnected(true)` 并把占位层隐藏条件绑在 `track` 事件的 `evt.streams[0]` 上：若 `evt.streams` 为空则永远不设 `srcObject`；若会话的渲染线程没出帧（GPU/资源竞争、上一次刷新的会话还没释放），`<video>` 永远没有画面。这两种情况页面都表现为"看起来在连、其实永远好不了"，且用户没有任何重试入口。

同时还有两处会随刷新累积的副作用：`setInterval(loadSlideList, 10000)` 每次重连都新建且从不清理（刷新/重连多次后轮询叠加）；`connectionstatechange` 把瞬时的 `disconnected` 直接当成断开（网络抖动即假掉线）。

## What Changes

- **连接链路全流程加超时与阶段化状态**：ICE 收集等待设上限（到点用已收集到的候选继续，不无限等）；`/offer` 请求加超时与 `AbortController`；每个阶段（协商中 / 建立媒体通道 / 等待画面 / 已就绪）都写进页面状态与占位层文案，任何一个阶段超时都能落到**明确可读的失败原因**。
- **以"视频首帧"作为连接完成的判据**：`track` 事件在 `evt.streams` 为空时用 `new MediaStream([evt.track])` 兜底；只有 `video` 真正出帧后才隐藏占位层。媒体通道已连但迟迟无帧时按超时处理，MUST NOT 继续无条件转圈。
- **自动重试 + 手动重试入口**：失败后按退避自动重试（最多 3 次），每次重试重建干净的 `PeerConnection`；自动重试耗尽后在占位层给出「重试」按钮，用户可随时手动重连且不被旧的失败尝试阻塞。
- **运行期异常可见化**：页面注册 `error` / `unhandledrejection` 兜底，脚本级异常直接显示在占位层与状态条，MUST NOT 让页面在异常后继续停留在"正在连接"的假象里。
- **修复随刷新累积的副作用**：页面内的幻灯片定时刷新改为单实例（重连先清旧定时器）；`disconnected` 状态给宽限窗口再判定断开，`failed` 才立刻按失败处理；关闭连接时同时处理 `pagehide`，刷新时尽早释放服务端会话。

## Capabilities

### New Capabilities

- `realtime-connection`：数字人展示页 WebRTC 连接的可观测性、超时、首帧判据与重连契约。

### Modified Capabilities

无。

## Impact

- 代码：`web/knowtalk-chat.html`（连接与重连逻辑、占位层与状态呈现）。不涉及服务端接口变更，`app.py` 无需重启，仅需浏览器强刷页面。
- 用户可见行为：连接失败/卡住时从"无声转圈"变成"明确原因 + 重试入口"；连接成功判定从"信令完成"收紧为"画面真正出帧"。
- 兼容性：`/offer` 协议与既有服务端行为不变；首次连接成功后的使用流程不变。
- 风险：ICE 收集超时后按"已收集候选继续"发送，理论上在极端网络下可能连不上（此时会落到失败并给出重试，而不是无限等待）；`disconnected` 宽限窗口会延后约 3 秒才显示断开。
