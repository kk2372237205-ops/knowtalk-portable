# Spec Delta

## MODIFIED Requirements

### Requirement: avatar_id 合法性约束

所有以 `{avatar_id}` 寻址的接口 SHALL 只接受同时满足以下两条的标识：匹配 `[A-Za-z0-9_.-]+`，且**不是纯点段**（`.`、`..`、`...` 以及任何只由点组成的取值）。除字符校验外，标识解析得到的绝对路径 MUST **严格位于**形象素材根目录之内——比较 MUST 在解析符号链接之后进行，且 MUST NOT 接受等于根目录本身的结果。任一条不满足时 MUST 拒绝请求（响应体 `code` 为 400，`msg` 指明标识非法），且 MUST NOT 读取目标路径、改名或删除任何目录。

#### Scenario: 含路径分隔符的标识被拒绝

- **WHEN** 请求携带含 `/` 或 `\` 的 `avatar_id`（例如 `a/b`）
- **THEN** 响应 `code` 为 400，且 MUST NOT 访问或列举该路径对应的目录

#### Scenario: 重命名目标标识非法

- **WHEN** `PUT /api/avatars/{avatar_id}` 的 `new_id` 含非法字符
- **THEN** 响应 `code` 为 400，且原形象目录名称保持不变

#### Scenario: 删除请求的标识非法

- **WHEN** `DELETE /api/avatars/{avatar_id}` 的 `avatar_id` 含非法字符
- **THEN** 响应 `code` 为 400，且 MUST NOT 删除任何目录

#### Scenario: 纯点段标识被拒绝

- **WHEN** 请求以纯点段寻址，例如 `DELETE /api/avatars/...` 或 `DELETE /api/avatars/%2e%2e%2e`
- **THEN** 响应 `code` 为 400
- **AND** 形象素材根目录及其下任何形象目录 MUST 仍然存在且内容不变

#### Scenario: 解析后落在根目录之外或等于根目录的标识被拒绝

- **WHEN** 标识解析得到的绝对路径不是形象素材根目录的严格子目录（落在根目录之外，或等于根目录本身）
- **THEN** 响应 `code` 为 400，且 MUST NOT 删除、改名或列举该路径

#### Scenario: 合法标识不受影响

- **WHEN** 以真实存在的合法标识（例如 `Mr.Chen`）调用列举、缩略图、重命名或删除
- **THEN** 请求 MUST 按原有语义正常处理，状态码与响应结构与改动前一致
