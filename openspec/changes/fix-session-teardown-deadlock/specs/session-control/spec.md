# Spec Delta

## ADDED Requirements

### Requirement: 会话拆除不得阻塞事件循环

拆除一个会话（`SessionManager.remove_session`、`WebRTCOutput.stop`、`HumanPlayer._stop`）时，任何线程 `join` MUST 带超时，MUST NOT 无界等待工作线程退出。`HumanPlayer._stop` MUST 在有限时间内返回，即使渲染线程仍在运行。会话的渲染线程、推理线程 MUST 标记为守护线程，使得拆除超时后不会阻塞进程退出。

#### Scenario: 渲染线程仍在运行时拆除

- **WHEN** 会话的渲染线程因为队列拥塞没有在超时内退出
- **THEN** `HumanPlayer._stop` MUST 在 join 超时后返回并记录告警
- **AND** 调用方（事件循环）MUST NOT 被该线程继续阻塞

#### Scenario: 拆除后服务仍可响应

- **WHEN** 某个页面断开连接并触发会话拆除
- **THEN** 服务 MUST 继续响应其它 HTTP 请求（含静态文件与 `/api/status`）
- **AND** 用户 MUST 能再次发起 `/offer` 建立新会话

### Requirement: 工作线程的队列写入必须有界

渲染、推理、特征提取三条线程向有界队列写入时 MUST NOT 使用无超时的阻塞写入。队列持续满时 MUST 在有限时间内放弃本次写入（可先丢弃最旧一项腾位），MUST NOT 让生产线程永久挂起。丢弃 MUST 被限频记录，MUST NOT 每帧刷屏。

消费端等待 MUST 可被 `quit_event` 打断：不得在无超时的 `get` 上无限等待。

#### Scenario: 消费端已停止（连接断开）

- **WHEN** 浏览器断开后音频队列不再被消费而写满
- **THEN** 音频、推理结果、特征批次的写入 MUST 在有限时间内返回
- **AND** 渲染与推理线程 MUST NOT 因为队列满而永久阻塞

#### Scenario: 推理线程等待音频

- **WHEN** 推理线程在 `asr.output_queue` 上没有可取数据
- **THEN** MUST 在限时等待后按静音补齐继续推进
- **AND** `quit_event` 置位后 MUST NOT 继续无限等待

### Requirement: 单会话断连不得使服务失去响应

任一会话断开、拆除或异常，MUST NOT 影响服务对外的 HTTP 可用性：`GET /api/status`、静态资源与新建连接（`POST /offer`）MUST 仍能在正常时间内得到响应。

#### Scenario: 断开后立即重连

- **WHEN** 用户点击「断开连接」后再点击「开始连接」
- **THEN** 控制台 MUST 能收到 `/offer` 的响应并重新出画面
- **AND** 不得出现服务端完全不响应、前端无限等待的静默状态

### Requirement: 控制台连接与断开必须可观测且可重试

控制台建立连接的全过程 MUST 在有限时间内落到终态。ICE 候选收集 MUST 设等待上限，到点后用已收集候选继续；`POST /offer` MUST 设请求超时并给出可读失败原因（状态码或响应片段），失败信息 MUST 显示在页面状态区而不是仅弹窗。

断开连接 MUST 重置连接状态：中止在途协商请求、关闭旧 `PeerConnection`、关闭上一次由控制台打开的展示页窗口，使下一次连接从干净状态开始；`PeerConnection` 的回调 MUST 使用本次连接的局部引用，MUST NOT 读写被后续连接替换的全局实例。

#### Scenario: 服务端不响应

- **WHEN** `POST /offer` 在超时内没有返回
- **THEN** 页面 MUST 中止请求并在状态区显示可读的失败原因
- **AND** MUST NOT 停留在"连接中"而没有进一步动作

#### Scenario: 断开后再次连接

- **WHEN** 用户先断开再重新连接
- **THEN** 上一次的展示页窗口 MUST 被关闭、旧连接 MUST 已销毁
- **AND** 新连接 MUST NOT 把旧协商的 SDP 写入新的 `PeerConnection`
