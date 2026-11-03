# Spec Delta

## ADDED Requirements

### Requirement: 提问点 MUST 在指定页码抛出

当提问点带有目标页码时，编排器 MUST 等到当前幻灯片到达该页之后才抛出题目；
未到达时 MUST 停在等待状态，MUST NOT 提前投喂题目音频，也 MUST NOT 推进后续教学段。

页码比较 MUST 按幻灯片在课件中的先后顺序判定，而不是按 `slide_id` 的数值大小。

#### Scenario: 页码未到不提问

- **WHEN** 第 3 页绑定了题目，而当前幻灯片仍在第 1 页
- **THEN** 编排器 MUST 处于 `waiting_slide` 阶段
- **AND** 该题 MUST NOT 被投喂到朗读通道

#### Scenario: 到达页码后提问

- **WHEN** 幻灯片翻到第 3 页
- **THEN** 编排器 MUST 在该页之后抛出绑定到第 3 页的题目
- **AND** 阶段 MUST 变为 `asking`

#### Scenario: 乱序跳页也能命中

- **WHEN** 当前页从第 2 页直接跳到第 5 页，而第 4 页绑定了题目
- **THEN** 该题 MUST 仍然被抛出（判定为"已越过该页"）

### Requirement: 等待页码 MUST 不设时限

编排器 MUST NOT 为等待页码设置任何时限（没有超时、没有最大等待时长）：
只要目标页未到达，MUST 一直停在 `waiting_slide` 阶段，MUST NOT 自行抛出题目。

等待期间 MUST 周期性记录一条「仍在等待第 N 页」的日志，用于区分「正在等翻页」
与「已经卡死」；该日志 MUST NOT 改变编排行为。

#### Scenario: 页码长时间未到达

- **WHEN** 编排器已等待页码很久（远超过任何历史阈值），当前页仍未到达
- **THEN** 编排器 MUST 仍处于 `waiting_slide` 阶段
- **AND** 该题 MUST NOT 被投喂到朗读通道
- **AND** MUST 持续记录包含目标页码的等待日志

#### Scenario: 通过跳过接口离开等待

- **WHEN** 编排器处于 `waiting_slide` 阶段，且收到该会话的 `/api/quiz/skip` 请求
- **THEN** 编排器 MUST 立即抛出该题目，不再等待页码
- **AND** 阶段 MUST 变为 `asking`
- **AND** 该次跳出 MUST NOT 记为未作答（题目尚未作答）

### Requirement: 等待页码期间 MUST NOT 暂停播放

`waiting_slide` 阶段展示页 MUST 保持正常播放：MUST NOT 切断音频、MUST NOT 把播放按钮
切成「继续播放」、MUST NOT 冻结幻灯片跟随。

理由：幻灯片翻页发生在**段落音频首帧播放时**，等页码靠的正是这份音频继续往下播。
一旦暂停，页码永远不会到达，学员看到的现象反而是「还没到指定页就停了」。

暂停 MUST 只由 `awaiting_answer`（题目已抛出、等学员作答）触发。

#### Scenario: 还没翻到指定页

- **WHEN** 服务端状态为 `waiting_slide`，当前在第 1 页，题目绑在第 3 页
- **THEN** 展示页 MUST 继续播放音频与幻灯片
- **AND** 播放按钮 MUST 保持「暂停播放」
- **AND** 只有页码到达、题目抛出之后才 MUST 暂停

#### Scenario: 等页码时点播放按钮

- **WHEN** `waiting_slide` 阶段学员点播放按钮
- **THEN** 该操作 MUST 按普通暂停/恢复处理
- **AND** MUST NOT 被解释为「跳过等待并立即出题」

### Requirement: 纯提问模式 MUST 按页码顺序提问

没有教学段（讲稿留空）时，问答对 MUST 按其页码升序提问；
未指定页码的问答对 MUST 排在指定页码的问答对之后，并保持填写顺序。

#### Scenario: 无讲稿但填了页码

- **WHEN** 提交三对问答，页码分别为 3、1、2，且未提供讲稿
- **THEN** 提问顺序 MUST 为 1、2、3 对应的问答对
- **AND** 每道题 MUST 等对应页码到达后才抛出

#### Scenario: 未指定页码的问答对

- **WHEN** 部分问答对未填页码
- **THEN** 它们 MUST 在全部指定页码的问答对之后提问
- **AND** 它们之间的顺序 MUST 与填写顺序一致

### Requirement: 讲稿缺失时 MUST 复用本会话最近的讲稿

服务端 MUST 记录每个会话最近一次整篇投喂的、带 `[PPT:N]` 标签的讲稿；
启动提问式讲课未提供讲稿时 MUST 回退使用该记录。

#### Scenario: 展示页先播整篇预设再开课

- **WHEN** 展示页通过 `/human` 投喂了带 `[PPT:N]` 的整篇讲稿，随后控制台启动提问式讲课且未携带 `text`
- **THEN** 编排器 MUST 用这份讲稿分段
- **AND** MUST NOT 以 0 段启动课堂

#### Scenario: 确实没有讲稿

- **WHEN** 既未提供讲稿，也没有可复用的记录
- **THEN** 编排器 MUST 按纯提问模式启动
- **AND** 状态响应中的 `has_lecture` MUST 为 `false`

### Requirement: 判分结束后 MUST 继续讲授

作答判分完成且反馈朗读结束后，只要还有剩余教学内容，编排器 MUST 继续投喂下一段教学并继续推进后续提问点。

#### Scenario: 答完继续讲后面的页

- **WHEN** 讲稿共 5 段、第 2 段后绑定了一次提问，学员完成作答
- **THEN** 反馈朗读结束后 MUST 投喂第 3 段
- **AND** 课堂 MUST 直至全部段落讲完才结束

#### Scenario: 编排器不负责讲授时不得打断展示页

- **WHEN** 课堂以纯提问模式运行（`has_lecture` 为 `false`）
- **THEN** 展示页 MUST NOT 销毁自身预设演讲的播放状态
- **AND** 判分结束后剩余课程 MUST 能继续播放

### Requirement: 状态响应 MUST 暴露是否由编排器讲授

`/api/quiz/status` 的响应 MUST 包含布尔字段 `has_lecture`，取值等价于「本次课堂是否带教学段」。

#### Scenario: 有讲稿的课堂

- **WHEN** 请求本次课堂的状态
- **THEN** `has_lecture` MUST 为 `true`
- **AND** 展示页 MUST 停用自身预设演讲，避免双段朗读
