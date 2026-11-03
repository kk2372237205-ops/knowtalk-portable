# Spec Delta

## Purpose

让主服务受理讲稿中的手势指示：在文本受理阶段就校验所引用的手势在该会话所用形象上确实可用，把校验通过的手势标识随分段一起下发到渲染层；并提供查询某形象可用手势的接口，供前端渲染插入按钮。手势的播放语义由 `gesture-orchestration` 定义，本 delta 只覆盖接口契约。

## MODIFIED Requirements

### Requirement: 文本驱动的 echo 模式

`POST /human` 在 `type=echo` 时 MUST 将请求体的 `text` 直接交由 TTS 朗读，MUST NOT 调用 LLM。请求体中的 `tts` 对象（如 `voice`、`emotion`）存在时 MUST 透传给 TTS。接口 MUST 在受理后立即返回成功响应，MUST NOT 等待朗读完成。`type` 或 `text` 缺失时 MUST 返回业务错误。

`text` 中的分段标签 MUST 按 `[PPT:N]` / `[slide:N]` 与 `[GESTURE:<id>]` 两类一起解析，两类标签 MUST 都作为段落边界且 MUST 都不进入朗读文本。`[GESTURE:<id>]` 所引用的手势 MUST 在该会话当前所用形象上校验可用性：存在任一不可用的手势时，接口 MUST 返回业务错误并在错误信息中给出不可用的手势名与可用手势列表，MUST NOT 把该次请求的任何一段送入朗读队列。校验通过时，每段 MUST 携带其手势标识随音频下发到渲染层。

#### Scenario: 朗读指定文本

- **WHEN** 客户端提交 `{"sessionid": "<sid>", "type": "echo", "text": "欢迎来到课堂"}`
- **THEN** 该会话朗读"欢迎来到课堂"，且不产生 LLM 请求
- **AND** 响应为 `{"code": 0, "msg": "ok"}`

#### Scenario: 缺少 type 字段

- **WHEN** 客户端提交 `{"sessionid": "<sid>", "text": "你好"}`
- **THEN** 服务返回 `code` 为 `-1` 的业务错误响应

#### Scenario: 手势可用时的分段下发

- **WHEN** 客户端提交 `type=echo`，文本含 `[GESTURE:explain]` 且该会话形象确实有 `explain` 手势
- **THEN** 响应为 `{"code": 0, "msg": "ok"}`
- **AND** 该段 MUST 携带手势标识 `explain` 交付渲染层

#### Scenario: 引用了不存在的手势

- **WHEN** 客户端提交 `type=echo`，文本含 `[GESTURE:nosuch]` 而该形象没有该手势
- **THEN** 服务 MUST 返回业务错误，错误信息 MUST 指明 `nosuch` 与该形象的可用手势列表
- **AND** MUST NOT 有任何一段文本被送入该会话的朗读队列

#### Scenario: 形象没有任何手势

- **WHEN** 客户端提交 `type=echo`，文本含手势标签，而该会话形象下没有任何可用手势
- **THEN** 服务 MUST 返回业务错误，并说明该形象没有可用手势
- **AND** MUST NOT 有任何一段文本被送入朗读队列

#### Scenario: 不含手势标签时行为不变

- **WHEN** 客户端提交 `type=echo`，文本只含 `[PPT:N]` 标签或不含任何标签
- **THEN** 分段、切页时机与响应 MUST 与引入手势之前完全一致

## ADDED Requirements

### Requirement: 按形象列举可用手势

服务 MUST 提供按形象查询可用手势的接口，返回该形象下三件套齐备的手势集合，每项 MUST 含手势标识与显示名。

`avatar_id` 含声明字符集之外的字符时 MUST 返回业务错误并 MUST NOT 触碰文件系统。形象目录不存在时 MUST 返回业务错误。形象存在但没有手势时 MUST 返回成功响应与空集合。

返回的手势顺序 MUST 稳定（同一目录状态下的多次查询结果一致）。

#### Scenario: 列出可用手势

- **WHEN** 客户端查询某形象的可用手势，该形象下存在三件套齐备的手势
- **THEN** 返回成功响应，`data` 中含手势数组
- **AND** 每项含手势标识与显示名

#### Scenario: 非法形象标识

- **WHEN** 客户端以包含路径分隔符或空格的 `avatar_id` 查询
- **THEN** 服务 MUST 返回业务错误
- **AND** MUST NOT 读取该路径

#### Scenario: 形象不存在

- **WHEN** 客户端查询一个不存在的形象
- **THEN** 服务 MUST 返回业务错误

#### Scenario: 形象没有手势

- **WHEN** 客户端查询一个存在但没有手势目录的形象
- **THEN** 服务 MUST 返回成功响应，手势数组为空
