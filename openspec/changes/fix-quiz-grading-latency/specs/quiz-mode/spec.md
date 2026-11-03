# Spec Delta

## ADDED Requirements

### Requirement: 作答判分与语音转写 MUST NOT 阻塞事件循环

提问模式下，学员作答所触发的阻塞调用（大模型判分、语音转写）MUST 在事件循环之外的线程中执行；请求协程 MUST 在此期间让出事件循环。判分或转写进行时，媒体收发与状态轮询 MUST 保持可响应。

#### Scenario: 判分期间音视频与轮询不中断

- **WHEN** 学员提交文字作答，判分调用需要 3s
- **THEN** 该期间 `/api/status` 与 `/api/quiz/status` MUST 仍能正常返回
- **AND** WebRTC 音视频 MUST NOT 因判分而停流

#### Scenario: 语音作答同样不阻塞

- **WHEN** 学员提交语音作答
- **THEN** 转写与随后的判分 MUST 都在事件循环之外执行
- **AND** 转写期间其它 HTTP 请求 MUST 保持可响应

### Requirement: 判分调用 MUST 有上限并复用连接

单次判分 MUST 设置不超过 20 秒的请求超时与有限次重试，总等待时间 MUST 有确定上界；判分客户端 MUST 复用已建立的连接；每次判分 MUST 记录耗时。

#### Scenario: 模型不可用时快速降级

- **WHEN** 判分接口超时或持续报错
- **THEN** 系统 MUST 在超时上限内放弃本次判分并降级（`degraded=true`）
- **AND** MUST NOT 因重试而把课堂挂起数十秒

#### Scenario: 连续判分复用连接

- **WHEN** 同一进程内连续判分多次且配置未变化
- **THEN** 系统 MUST 复用同一个判分客户端实例

### Requirement: 判分进行中编排器 MUST NOT 推进

学员提交作答后到判分结论产生之前，编排 MUST 处于 `grading` 阶段且 MUST NOT 投喂下一段教学内容、MUST NOT 触发作答超时；重复提交 MUST 被拒绝。

#### Scenario: 判分期间不投喂

- **WHEN** 编排器处于 `grading` 阶段且朗读通道已空闲
- **THEN** 编排器 MUST NOT 推进到下一段教学或下一道题

#### Scenario: 连点提交

- **WHEN** 学员在判分尚未完成时再次提交作答
- **THEN** 第二次提交 MUST 被拒绝
- **AND** 该题 MUST 只产生一条作答记录

### Requirement: 题目呈现时 MUST 暂停音频与幻灯片跟随

题目一呈现给学员（`stage` 变为 `asking`），MUST 立即切断数字人音频、MUST 停止跟随服务端幻灯片变化（停在当前页），播放按钮 MUST 显示为可恢复的暂停态；恢复 MUST 仅由学员操作或作答流程触发。等待页码阶段（`waiting_slide`）MUST NOT 暂停。

#### Scenario: 题目一出现课堂就安静下来

- **WHEN** 展示页轮询到本会话 `stage` 变为 `asking`
- **THEN** 数字人音频 MUST 立即静音
- **AND** 幻灯片 MUST 停留在当前页，不再跟随服务端切换
- **AND** 播放按钮 MUST 显示「继续播放」

#### Scenario: 等页码期间不暂停

- **WHEN** 展示页轮询到本会话 `stage` 为 `waiting_slide`
- **THEN** 音频 MUST 保持播放
- **AND** 幻灯片跟随 MUST NOT 被冻结
- **WHY** 页码是在段落音频首帧播放时才切过去的，此刻暂停会让页码永远等不到

#### Scenario: 作答后自动恢复

- **WHEN** 学员在暂停态提交作答
- **THEN** 音频 MUST 立即恢复，使判分反馈能被听到
- **AND** 幻灯片跟随 MUST 恢复

#### Scenario: 念题暂停态点击继续播放

- **WHEN** 学员在 `asking` 暂停态点击「继续播放」
- **THEN** 音频与幻灯片跟随 MUST 恢复，使题目能被听完
- **AND** 该题 MUST NOT 被跳过（题目还没念完，不算未作答）

#### Scenario: 等作答暂停态点击继续播放

- **WHEN** 学员在 `awaiting_answer` 暂停态点击「继续播放」
- **THEN** 音频与幻灯片跟随 MUST 恢复
- **AND** 系统 MUST 先跳过当前题目（记为未作答）再继续讲课

#### Scenario: 未进入提问不受影响

- **WHEN** 本会话未启用提问模式
- **THEN** 音频、幻灯片跟随与播放按钮行为 MUST 与本次变更前一致

### Requirement: 题目呈现后 MUST 可以立即作答

题目一旦呈现，学员 MUST 能够提交作答，MUST NOT 被迫等待数字人把题目念完。念题未结束时提交，系统 MUST 清除尚未播完的念题，使判分反馈紧随其后播放。

#### Scenario: 念题期间提交作答

- **WHEN** 编排器处于 `asking` 阶段，学员提交作答
- **THEN** 该作答 MUST 被接受并产生一条作答记录
- **AND** 尚未播完的念题 MUST 被清空
- **AND** 判分反馈 MUST 紧随其后朗读

#### Scenario: 判分期间的重复提交

- **WHEN** 学员在判分尚未完成时再次提交作答
- **THEN** 第二次提交 MUST 被拒绝
- **AND** 该题 MUST 只产生一条作答记录
