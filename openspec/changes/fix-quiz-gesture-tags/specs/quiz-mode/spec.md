# Spec Delta

## ADDED Requirements

### Requirement: 提问式讲课的教学段 MUST 保留并触发手势标签

提问式讲课解析讲稿时 MUST 保留 `[GESTURE:<id>]` 标签所声明的手势，MUST NOT 在分段阶段丢弃。
编排器投喂每个教学段时 MUST 把该段的手势随该段的朗读数据（`datainfo`）一起下发，
使渲染层在该段音频首帧播放时切换动作。

手势 MUST 遵守既有语义：只属于声明它的那一段，MUST NOT 继承到下一段；
MUST NOT 出现在题目、判分反馈或结束评分的朗读里。
朗读文本 MUST NOT 包含任何 `[GESTURE:...]` 标签本身。

#### Scenario: 讲稿中的手势随教学段生效

- **WHEN** 以 `[PPT:1] 开场。[GESTURE:wave] 大家好。[PPT:2] 下一节。` 启动提问式讲课
- **THEN** 第二段投喂的朗读数据 MUST 包含手势 `wave`
- **AND** 该段朗读文本 MUST 为「大家好。」，不含标签

#### Scenario: 手势不继承到下一段

- **WHEN** 某一段带手势 `wave`，紧接着的下一段没有任何手势标签
- **THEN** 下一段投喂的朗读数据 MUST NOT 包含手势

#### Scenario: 提问与反馈不触发教学手势

- **WHEN** 编排器抛出题目、朗读判分反馈或结束评分
- **THEN** 这些朗读数据 MUST NOT 包含手势
- **AND** MUST NOT 因上一教学段的手势而切换动作

### Requirement: 提问式讲课 MUST 在启动时校验手势可用性并降级但不静默

启动提问式讲课 MUST 按当前形象校验讲稿引用的手势。
引用了当前形象不可用的手势时，MUST 只摘除对应段的手势指令，
MUST NOT 拒绝整篇讲稿、MUST NOT 阻止朗读；同时 MUST 记录 warning 日志，
并在 `/api/quiz/lecture` 响应中返回可读的警告（含不可用手势名与当前形象可用手势）。

可用的手势 MUST 在开课前预热；预热失败 MUST NOT 阻止课堂开始。

#### Scenario: 不可用手势只摘动作、不影响朗读

- **WHEN** 讲稿引用 `[GESTURE:nosuch]`，而当前形象没有该手势
- **THEN** 该段文字 MUST 照常被投喂与朗读
- **AND** 该段的朗读数据 MUST NOT 包含手势
- **AND** 响应 MUST 包含说明 `nosuch` 不可用的 `warnings`

#### Scenario: 可用手势被保留并预热

- **WHEN** 讲稿引用当前形象已有的手势 `wave`
- **THEN** 该手势 MUST 保留到对应教学段的朗读数据
- **AND** 开课前 MUST 对该手势发起预热

### Requirement: 讲稿回退与显式 segments MUST 保留手势

启动请求未携带讲稿、回退使用本会话留底讲稿时，MUST 同样保留并生效其中的手势标签。

显式传入 `segments` 时 MUST 同时接受两元素与三元素形式：
两元素 `[文本, 幻灯片编号]` 视为该段无手势，三元素 `[文本, 幻灯片编号, 手势标识]` 的第三项 MUST 生效。

#### Scenario: 从留底讲稿回退

- **WHEN** 展示页先通过 `/human` 投喂了含 `[GESTURE:wave]` 的整篇讲稿，随后启动提问式讲课且未携带 `text`
- **THEN** 编排器 MUST 使用留底讲稿分段
- **AND** 对应教学段 MUST 携带手势 `wave`

#### Scenario: 两元素 segments 仍然可用

- **WHEN** 请求以 `segments: [["第一段", 1], ["第二段", 2]]` 启动
- **THEN** 两段 MUST 被正常投喂
- **AND** 两段的朗读数据 MUST NOT 包含手势

#### Scenario: 三元素 segments 的手势生效

- **WHEN** 请求以 `segments: [["第一段", 1, "wave"]]` 启动
- **THEN** 该段朗读数据 MUST 包含手势 `wave`
