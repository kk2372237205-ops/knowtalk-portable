# Design

## 1. 连接状态机

展示页的连接过程收敛为一个显式状态机，任何状态都必须能走到终态（`streaming` 或 `failed`），不允许停在中间态无限等待。

```
idle
  └─► negotiating      创建 offer + 等待 ICE（有上限）
        └─► signaling   POST /offer（有上限）
              └─► media 已拿到 answer，等 PeerConnection 连上（有上限）
                    └─► waiting_frame  已连上，等第一帧画面（有上限）
                          └─► streaming  首帧到达 → 隐藏占位层
任意阶段超时/异常 ─► retrying（退避自动重试，最多 3 次）─► failed（显示重试按钮）
```

失败原因统一写成中文短句展示在占位层：`信令超时`、`服务端返回 500`、`媒体通道未建立`、`已连接但无画面`、`脚本异常`。

## 2. 时间预算

| 阶段 | 上限 | 超时行为 |
| --- | --- | --- |
| ICE 收集 | 3s（软超时） | 用 `pc.localDescription.sdp` 里**已收集到的**候选继续 POST；不失败，仅控制台警告 |
| `POST /offer` | 60s | 中止请求（`AbortController`），按失败处理 |
| PeerConnection 连上 | 20s | 按失败处理（`媒体通道未建立`） |
| 第一帧画面 | 20s（从已连上起算） | 按失败处理（`已连接但无画面`） |

ICE 收集用"软超时"而不是失败：`localDescription` 会随收集过程累积候选，到点发送已收集部分在局域网/同机场景足够；而无限等才是刷新后直接卡死的根因。

## 3. 首帧判据

- `track` 事件：`var stream = (evt.streams && evt.streams[0]) ? evt.streams[0] : new MediaStream([evt.track]);` 再赋给 `<video>` / `<audio>`。当前代码在 `evt.streams` 为空时什么都不做，是"连上了但永远黑屏/转圈"的隐患。
- `video` 元素监听 `loadeddata`（并兼容 `requestVideoFrameCallback`）→ `markVideoReady()`：清首帧看门狗、隐藏占位层、状态置为「已连接」。
- 在此之前 `setConnected(true)` 只表示"媒体通道已建立"，占位层文案显示「已连接，正在等待画面…」，而不是直接消失。

## 4. 重试策略

- 自动重试：失败后 `min(1.5s × attempt, 4s)` 退避，最多 3 次；每次重试前 `resetPeer()`（关闭旧 `pc`、清空 `srcObject`、清所有定时器、`_connectInFlight=false`），再 `startConnection()`。旧连接不清理会导致 `pc.getTransceivers()[1]` 索引错位与重复协商。
- 手动重试：`retryConnection()` 重置尝试计数并立即重连，供自动重试用尽后使用；重试期间按钮禁用，避免并发协商。
- `_connectInFlight` 守卫：`negotiate()` 期间重复触发（自动重试与用户点击并发）直接忽略，不产生两路 `PeerConnection`。

## 5. 状态呈现与异常可见化

- 占位层拆成"图标 + 文案 + （可选）重试按钮"。文案随状态机更新，失败时显示原因与重试按钮；不再有"文案永远是正在连接"的静态表现。
- 注册 `window.addEventListener('error')` / `'unhandledrejection'`：把异常消息写到占位层与 `#status-indicator`，避免脚本异常后页面停留在"正在连接"的假象（这正是本次现场无法自诊断的原因）。
- `connectionstatechange`：`connected` → 进入 `waiting_frame`；`disconnected` 给 3s 宽限再判断（网络抖动不立即报断开）；`failed` 立即按失败处理并进入重试。

## 6. 刷新时的资源释放

- 既有 `window.onunload` 关闭 `pc`；补 `pagehide`（`evt.persisted === false` 时同样关闭），提高刷新/关标签时服务端能观察到连接关闭的概率，减少泄漏会话与新会话争抢资源。
- 幻灯片定时刷新改为单实例：重连前 `clearInterval(_slideRefreshTimer)`，避免多次刷新后一分钟内叠加多次 `/api/slides` 请求。

## 7. 不做什么

- 不改 `/offer` 协议、不改服务端会话隔离策略（每条 WebRTC 连接一个会话是刻意的，共享会互相覆盖 `output._player`）。
- 不引入 trickle ICE 信令通道（需要服务端新接口），ICE 上限到点用已收集候选继续即可覆盖目标场景。
- 不改提问模式（`improve-quiz-mode-ux`）的既有语义，仅在其之上保证连接层不再静默卡死。
