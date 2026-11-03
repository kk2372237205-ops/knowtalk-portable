# Spec Delta

## MODIFIED Requirements

### Requirement: 每项设置必须标注生效时机档位

读取接口返回的每一项设置 MUST 携带生效时机字段 `effect`，其取值 MUST 只能是 `immediate`（下一次调用即生效）、`new_session`（对此后新建的会话生效）与 `restart`（需重启对应进程后生效）三者之一。MUST NOT 出现其它取值或缺失该字段。

#### Scenario: 读取单项设置

- **WHEN** 客户端请求 `GET /api/settings`
- **THEN** 返回的每一项都包含 `effect` 字段
- **AND** 该字段取值属于上述三档

### Requirement: 读取接口返回当前值、生效档位与说明

读取接口 MUST 返回每一项的当前生效值、生效档位，以及一句面向用户的说明；对 `restart` 档的项，说明 MUST 指明需要重启的进程。

#### Scenario: 读取全部设置

- **WHEN** 客户端请求 `GET /api/settings`
- **THEN** 返回结构包含每一项的当前值与 `effect`
- **AND** 每个 `restart` 档的项都带有指示需重启的说明文字

### Requirement: 密钥类字段必须掩码返回

`api_key` 等密钥类字段在接口响应中 MUST 掩码，形如 `sk-1****8056`，即保留可辨识的前缀与末尾若干位、其余以 `*` 代替。任何读取响应 MUST NOT 返回完整密钥。

#### Scenario: 读取已保存的密钥

- **WHEN** 客户端请求设置读取接口
- **THEN** 密钥字段的值为掩码字符串
- **AND** 响应体任何位置 MUST NOT 出现完整密钥

### Requirement: 启动类参数只读展示

`--model`、`--transport`、`--modelres`、`--batch_size`、`--listenport`、`--fps`、`--max_session` MUST 以只读形式展示当前值并标注 `restart`，MUST NOT 提供写入这些项的接口能力。

#### Scenario: 尝试写入启动类参数

- **WHEN** 写入请求中包含上述任一启动类参数
- **THEN** 请求被拒绝且错误信息指明该字段不可写
- **AND** 设置文件中 MUST NOT 新增这些字段

#### Scenario: 查看启动类参数

- **WHEN** 客户端请求设置读取接口
- **THEN** 上述参数以只读项返回，标注为 `restart`
- **AND** 返回值与当前进程实际使用的启动参数一致
