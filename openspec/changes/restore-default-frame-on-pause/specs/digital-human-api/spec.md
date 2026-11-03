# Spec Delta

## ADDED Requirements

### Requirement: 暂停播放时画面回落默认帧

系统 SHALL 提供会话级播放暂停控制：`POST /api/playback` 接受 JSON 体
`{"sessionid": <会话标识>, "paused": <布尔>}`，把该会话切换为暂停或恢复。
`sessionid` 不存在时 MUST 返回 `code` 404；`paused` 缺失或不是布尔值时 MUST 返回 `code` 400，
且 MUST NOT 改变该会话状态。

进入暂停后，该会话输出的画帧 MUST 是**该形象基础素材的帧**（闭嘴、静止），
MUST NOT 继续输出推理产生的说话帧。暂停 MUST NOT 清空朗读队列、MUST NOT 改变
朗读进度与幻灯片跟随状态。

恢复播放后，MUST 在下一帧起恢复输出推理画面，MUST NOT 重播已朗读过的段落，
MUST NOT 重新开始整篇讲稿。

同一会话重复设置同一暂停态 MUST 是幂等的。

暂停期间画面 MUST 取**形象自身的基础素材帧**，而不取「当前帧源」的帧：动作（手势）
播放期间暂停时，MUST NOT 停在"手臂抬起"的动作帧上。

#### Scenario: 暂停后画面为默认帧

- **WHEN** 会话正在朗读，客户端调用 `POST /api/playback` 且 `paused` 为 true
- **THEN** 此后输出的画帧 MUST 等于该形象基础素材的帧
- **AND** 不再出现张嘴的推理帧

#### Scenario: 动作播放期间暂停

- **WHEN** 会话正在播放动作（帧源是手势素材），客户端切换为暂停
- **THEN** 画帧 MUST 回到形象基础素材的帧（默认姿态、闭嘴）
- **AND** MUST NOT 停留在动作素材的某一帧上
- **AND** 恢复后 MUST 从暂停处的动作帧继续，MUST NOT 重播该动作

### Requirement: 所有暂停入口都同步服务端暂停态

客户端的每一个「暂停播放」入口 MUST 调用 `POST /api/playback` 同步暂停态，
MUST NOT 只静音本地音频或禁用本地音轨——服务端不知情就会继续推理并推送说话帧，
画面上的嘴会一直动。

至少 MUST 覆盖这些入口：手动暂停/继续、讲稿（预设演讲）暂停与恢复、
提问模式下「抛出题目等待作答」的自动暂停与恢复、答题后恢复朗读。

恢复路径 MUST 同步 `paused: false`；MUST NOT 出现「暂停通知了、恢复没通知」导致
画面永久定格在默认帧。同步失败 MUST 只记录告警，MUST NOT 让暂停按钮失灵。

#### Scenario: 提问等待作答时自动暂停

- **WHEN** 提问模式抛出题目进入等待作答，客户端切断音频
- **THEN** 客户端 MUST 同时把 `paused: true` 提交给服务端
- **AND** 服务端此后的画帧 MUST 是形象基础素材的帧，嘴 MUST NOT 继续动

#### Scenario: 从提问暂停恢复

- **WHEN** 学员点「继续播放」或编排器继续投喂，客户端恢复音频
- **THEN** 客户端 MUST 提交 `paused: false`
- **AND** 画帧 MUST 恢复为推理画面，MUST NOT 一直定格在默认帧

#### Scenario: 讲稿暂停也同步

- **WHEN** 客户端暂停或恢复预设演讲（整篇讲稿）播放
- **THEN** 同样 MUST 提交对应的 `paused` 值给服务端

#### Scenario: 恢复后继续而不重播

- **WHEN** 会话处于暂停态，客户端调用同一接口且 `paused` 为 false
- **THEN** 下一帧起恢复输出推理画面
- **AND** 朗读从暂停处继续，MUST NOT 重新从讲稿开头朗读

#### Scenario: 会话不存在

- **WHEN** 请求携带未建立或已回收的 `sessionid`
- **THEN** 响应 `code` 为 404，且不改变任何会话状态

#### Scenario: 参数非法

- **WHEN** 请求体缺少 `paused`，或 `paused` 不是布尔值（例如 `"yes"`）
- **THEN** 响应 `code` 为 400
- **AND** 该会话的暂停态 MUST 保持不变

#### Scenario: 幂等

- **WHEN** 对已暂停的会话再次提交 `paused` 为 true
- **THEN** 响应 `code` 为 0，会话保持暂停态，MUST NOT 产生额外副作用
