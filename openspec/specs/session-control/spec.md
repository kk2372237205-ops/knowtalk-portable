# session-control Specification

## Purpose
定义活跃数字人会话的列举与踢出契约：管理员能查看当前所有活跃会话，并能终止指定会话以回收其占用的资源，同时保持既有只读接口的返回结构不变。

## Requirements

### Requirement: 活跃会话列举

服务 MUST 继续提供 `GET /api/admin/sessions`，返回活跃会话列表，且 MUST NOT 改变其现有返回结构：成功信封为 `{"code": 0, "msg": "ok", "data": {"sessions": [...]}}`，每个元素 MUST 含 `sessionid`、`speaking`、`recording`；会话携带配置时 MUST 另含 `model`、`avatar_id`、`REF_FILE`、`transport`、`batch_size`、`customopt`。尚未完成构建的会话 MUST NOT 出现在列表中。

#### Scenario: 列举活跃会话

- **WHEN** 调用方请求 `GET /api/admin/sessions` 且当前存在已构建完成的会话
- **THEN** 响应 MUST 为成功信封，且 `data.sessions` 中该会话 MUST 含 `sessionid`、`speaking`、`recording`
- **AND** 该会话携带配置时 MUST 同时含 `model`、`avatar_id`、`REF_FILE`、`transport`、`batch_size`、`customopt`

#### Scenario: 未完成的会话不出现在列表

- **WHEN** 某会话仍处于构建中、尚未就绪
- **THEN** 该会话 MUST NOT 出现在 `data.sessions` 中

### Requirement: 会话踢出接口

服务 MUST 提供 `DELETE /api/admin/sessions/{sessionid}`，用于终止指定会话并回收其资源。当 `sessionid` 对应存在的会话时 MUST 返回成功信封 `{"code": 0, "msg": "ok"}`；不存在时 MUST 返回业务错误信封（`code` 非 0、`msg` 说明会话不存在），MUST NOT 返回 HTTP 5xx 或导致主服务异常退出。

#### Scenario: 踢出存在的会话

- **WHEN** 调用方对存在的 `sessionid` 发起 `DELETE /api/admin/sessions/{sessionid}`
- **THEN** 响应 MUST 为成功信封 `{"code": 0, "msg": "ok"}`

#### Scenario: 踢出不存在的会话

- **WHEN** 调用方对不存在的 `sessionid` 发起踢出
- **THEN** 响应 MUST 为业务错误信封且 `code` 非 0、`msg` 可理解地说明会话不存在
- **AND** 响应 MUST NOT 是 HTTP 5xx，主服务 MUST 继续正常服务

### Requirement: 踢出释放资源且不影响其它会话

踢出 MUST 释放该会话占用的资源：停止其渲染线程、停止输出通道、结束进行中的录音、清空待播放队列，并把该 `sessionid` 从会话表与活动时间记录中移除。踢出操作 MUST NOT 改变其它会话的状态或可用性。

#### Scenario: 资源被回收

- **WHEN** 一个活跃会话被成功踢出
- **THEN** 该会话 MUST 不再出现在 `GET /api/admin/sessions` 的列表中
- **AND** 该会话的渲染线程、输出通道、录音与待播放队列 MUST 已停止或清空

#### Scenario: 其它会话不受影响

- **WHEN** 在存在多个活跃会话时踢出其中一个
- **THEN** 其余会话 MUST 仍在列表中，且 MUST 仍能正常应答其状态查询

### Requirement: 进行中的会话可被踢出

对正在说话或正在录制的会话，踢出 MUST 被允许执行。服务 MUST 先停止该会话的播放与录制，再回收其资源，MUST NOT 因会话正处于活动状态而拒绝该请求或长时间阻塞。

#### Scenario: 踢出正在说话的会话

- **WHEN** 对 `speaking` 为真的会话发起踢出
- **THEN** 服务 MUST 先停止该会话的播放再回收资源
- **AND** 响应 MUST 为成功信封

#### Scenario: 踢出正在录制的会话

- **WHEN** 对 `recording` 为真的会话发起踢出
- **THEN** 服务 MUST 先结束该会话的录制再回收资源
- **AND** 响应 MUST 为成功信封
