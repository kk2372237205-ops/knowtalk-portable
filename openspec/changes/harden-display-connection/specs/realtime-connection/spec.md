# Spec Delta

## ADDED Requirements

### Requirement: 连接全过程必须有超时且不得静默等待

数字人展示页建立 WebRTC 连接的全过程 MUST 在有限时间内走到终态（`streaming` 或 `failed`），MUST NOT 停在中间状态无限等待。ICE 候选收集 MUST 设等待上限，到点后 MUST 使用 `localDescription` 中已收集到的候选继续发送，MUST NOT 因为收集未完成而永久挂起；`POST /offer` MUST 设请求超时。

#### Scenario: ICE 收集不推进

- **WHEN** `iceGatheringState` 在等待上限内没有变为 `complete`
- **THEN** 页面 MUST 结束等待并使用已收集候选发送 `POST /offer`
- **AND** MUST NOT 停留在等待状态而不再发出信令请求

#### Scenario: 信令请求无响应

- **WHEN** `POST /offer` 在请求超时内没有返回
- **THEN** 页面 MUST 中止该请求并进入失败流程
- **AND** 占位层 MUST 显示可读的失败原因

#### Scenario: 服务端返回错误

- **WHEN** `POST /offer` 返回非 2xx，或响应体缺少 `sdp`/`type`
- **THEN** 页面 MUST 抛出包含状态码或响应片段的可读错误
- **AND** MUST NOT 把缺失 `sdp` 的响应直接交给 `setRemoteDescription`

### Requirement: 连接完成以视频首帧为判据

页面 MUST 以 `<video>` 真正出帧作为"连接完成"的判据，而不是以信令完成或媒体通道建立作为判据。`track` 事件收到轨道时，即使 `evt.streams` 为空也 MUST 用该轨道构造 `MediaStream` 赋给对应元素。首帧到达前占位层 MUST 保持可见并显示进行中的状态文案；媒体通道已建立但在首帧上限内没有画面时 MUST 按失败处理。

#### Scenario: track 事件不带 streams

- **WHEN** `track` 事件的 `evt.streams` 为空
- **THEN** 页面 MUST 用 `new MediaStream([evt.track])` 构造流并赋给 `<video>` 或 `<audio>`
- **AND** MUST NOT 因为没有 `evt.streams[0]` 而跳过赋值

#### Scenario: 已连接但无画面

- **WHEN** PeerConnection 已进入 `connected`，但在首帧上限内没有收到任何视频帧
- **THEN** 页面 MUST 判定为失败（`已连接但无画面`）
- **AND** MUST 进入重试流程而不是继续显示"正在连接"

#### Scenario: 首帧到达

- **WHEN** `<video>` 首次产出可播放数据
- **THEN** 页面 MUST 隐藏占位层、状态置为已连接
- **AND** MUST 清除首帧看门狗定时器

### Requirement: 失败必须可恢复

连接失败后页面 MUST 提供恢复手段：按退避自动重试，最多 3 次；自动重试耗尽后 MUST 在占位层提供手动「重试」入口。每次重试 MUST 先销毁旧 `PeerConnection` 与其定时器再重新协商，MUST NOT 在旧连接上重复添加 transceiver；协商进行中重复触发重试 MUST 被忽略。

#### Scenario: 自动重试

- **WHEN** 一次连接尝试失败且尝试次数未达上限
- **THEN** 页面 MUST 在退避后自动发起下一次连接
- **AND** 每次重试前 MUST 关闭上一次的 `PeerConnection`

#### Scenario: 自动重试耗尽

- **WHEN** 连续失败次数达到上限
- **THEN** 占位层 MUST 显示「重试」按钮与失败原因
- **AND** 用户点击后 MUST 重置尝试计数并重新连接

#### Scenario: 并发重试

- **WHEN** 协商正在进行时再次触发重连
- **THEN** 页面 MUST 忽略该次触发，MUST NOT 产生第二路协商

### Requirement: 脚本异常不得伪装成"正在连接"

页面 MUST 捕获未处理异常与未处理的 Promise 拒绝，并把异常信息显示在占位层与页面状态条。脚本发生异常后，页面 MUST NOT 继续以「正在连接数字人…」呈现。

#### Scenario: 运行期异常

- **WHEN** 页面脚本抛出未捕获异常或被拒绝的 Promise
- **THEN** 占位层与状态条 MUST 显示异常信息
- **AND** 页面 MUST NOT 停留在"正在连接"的静态文案上

### Requirement: 重连不得累积副作用

页面内的周期任务（幻灯片定时刷新）MUST 保持单实例：重连或刷新后 MUST NOT 叠加多份定时器。连接状态变化时，`disconnected` MUST 经过宽限窗口再判定为断开，`failed` MUST 立即按失败处理；页面关闭或刷新（`pagehide` 且未进入 bfcache）时 MUST 主动关闭 `PeerConnection`。

#### Scenario: 多次重连

- **WHEN** 页面经历多次重试或刷新
- **THEN** 幻灯片定时刷新 MUST 只有一份在运行

#### Scenario: 短暂断连

- **WHEN** `connectionState` 短暂变为 `disconnected` 后恢复
- **THEN** 页面 MUST NOT 立即判定断线并触发重连
