# Spec Delta

## Purpose

定义 KnowTalk 运行期设置的持久化位置与格式、读取与写入契约、密钥掩码规则、取值校验，以及每项设置的生效时机分级，使部署者不必编辑 `.env` 或记忆命令行参数就能调整配置，并如实知道改动何时生效。

## ADDED Requirements

### Requirement: 运行期设置持久化为服务端 JSON 文件

运行期设置 MUST 持久化在服务端可写文件 `data/settings/settings.json` 中，内容为 UTF-8 编码的 JSON 对象。该路径与格式 MUST 保持稳定，MUST NOT 因版本升级而改变；服务 MUST 在写入时保持该文件为合法 JSON。

#### Scenario: 界面保存后文件落盘

- **WHEN** 通过写入接口成功修改任一设置项
- **THEN** `data/settings/settings.json` 被创建或更新
- **AND** 文件内容为可被标准 JSON 解析器解析的对象，且包含刚写入的值

#### Scenario: 重启后读取

- **WHEN** 服务重启后重新读取设置
- **THEN** 上一次保存的值 MUST 仍然生效

### Requirement: 设置文件必须位于版本库忽略范围

`data/settings/` MUST 被 `.gitignore` 覆盖，设置文件 MUST NOT 进入版本控制。用于承载密钥的目录与文件 MUST NOT 出现于仓库跟踪文件列表中。

#### Scenario: 检查版本库跟踪内容

- **WHEN** 生成设置文件后检查版本控制状态
- **THEN** 该文件与所在目录 MUST NOT 出现在待提交或被跟踪的文件列表中

### Requirement: 取值优先级为设置文件、环境变量、内置默认

单个设置项的取值 MUST 按以下优先级解析：设置文件中由界面写入的值 > 环境变量中的对应值 > 内置默认值。用户从未在界面上修改过的项 MUST 继续沿用环境变量或内置默认值。

#### Scenario: 只在 .env 中配置过的既有部署

- **WHEN** 某部署仅在 `.env` 中配置过 AI 接口参数，从未在界面修改过任何设置
- **THEN** 升级后该项的取值与生效行为 MUST 与升级前完全一致
- **AND** 生成设置文件这一动作 MUST NOT 改变该值

#### Scenario: 界面写入覆盖环境变量

- **WHEN** 环境变量中存在某配置项的值，用户在界面上为同一项写入了新值
- **THEN** 生效的是界面写入的值
- **AND** 该值在后续重启后仍然生效

### Requirement: 每项设置必须标注生效时机档位

读取接口返回的每一项设置 MUST 携带生效时机字段 `effect`，其取值 MUST 只能是 `immediate`（下一次调用即生效）、`new_session`（对此后新建的会话生效）与 `restart`（需重启对应进程后生效）三者之一。MUST NOT 出现其它取值或缺失该字段。

#### Scenario: 读取单项设置

- **WHEN** 客户端携带正确口令请求 `GET /api/settings`
- **THEN** 返回的每一项都包含 `effect` 字段
- **AND** 该字段取值属于上述三档

### Requirement: 各配置项的生效档位归属固定

档位归属 MUST 固定如下，且 MUST NOT 随调用时机改变：`immediate` 覆盖 LLM 的 `api_key`、`base_url`、`model` 与系统提示词；`new_session` 覆盖 TTS 引擎、TTS 语速、参考音色、默认形象与口型微调参数；`restart` 覆盖主服务启动类参数与 RAG 检索生成参数。

#### Scenario: 修改 LLM 模型名

- **WHEN** 用户修改 `model` 字段并保存
- **THEN** 该项的 `effect` 为 `immediate`
- **AND** 下一次 LLM 调用即使用新模型

#### Scenario: 修改 TTS 语速

- **WHEN** 用户修改 TTS 语速并保存
- **THEN** 该项的 `effect` 为 `new_session`
- **AND** 新建会话使用新语速，已存在的会话参数 MUST 保持不变

#### Scenario: 修改 RAG 参数

- **WHEN** 用户修改 RAG 检索生成参数并保存
- **THEN** 该项的 `effect` 为 `restart`
- **AND** 在重启对应进程之前，运行中的行为 MUST NOT 改变

### Requirement: 读取接口返回当前值、生效档位与说明

读取接口 MUST 返回每一项的当前生效值、生效档位，以及一句面向用户的说明；对 `restart` 档的项，说明 MUST 指明需要重启的进程。

#### Scenario: 读取全部设置

- **WHEN** 客户端携带正确口令请求 `GET /api/settings`
- **THEN** 返回结构包含每一项的当前值与 `effect`
- **AND** 每个 `restart` 档的项都带有指示需重启的说明文字

### Requirement: 密钥类字段必须掩码返回

`api_key` 等密钥类字段在接口响应中 MUST 掩码，形如 `sk-1****8056`，即保留可辨识的前缀与末尾若干位、其余以 `*` 代替。任何读取响应 MUST NOT 返回完整密钥。

#### Scenario: 读取已保存的密钥

- **WHEN** 客户端携带正确口令请求设置读取接口
- **THEN** 密钥字段的值为掩码字符串
- **AND** 响应体任何位置 MUST NOT 出现完整密钥

### Requirement: 写入时掩码值与空值表示不修改

写入请求中，某字段的值为空字符串、`null`，或与该字段当前的掩码值相同时，服务 MUST 视为"不修改该项"并保留原值。要清空某项时，请求 MUST 显式携带明确的清空标志。

#### Scenario: 用户看到掩码后直接保存

- **WHEN** 用户打开设置页后未改动密钥输入框，直接提交表单，提交内容中的密钥字段等于读取时返回的掩码
- **THEN** 已保存的密钥 MUST 保持原值
- **AND** 密钥 MUST NOT 被写成星号字符串

#### Scenario: 显式清空密钥

- **WHEN** 写入请求对密钥字段同时给出空值与该字段的清空标志
- **THEN** 该密钥被清空
- **AND** 该项回落到环境变量或内置默认值的解析结果

### Requirement: 取值校验失败必须整次拒绝

写入接口 MUST 校验每一项的类型、取值范围与枚举取值。任一字段不符时，服务 MUST 拒绝整次写入并返回错误，MUST NOT 出现部分字段已生效的状态；错误信息 MUST 指明不合法的字段名。

#### Scenario: 类型不符

- **WHEN** 写入请求把某个数值型字段写成非数值
- **THEN** 请求被拒绝
- **AND** 响应体指明该字段名
- **AND** 设置文件中所有字段 MUST 保持写入前的值

#### Scenario: 数值越界与枚举非法

- **WHEN** 写入请求把语速写成超出允许范围的值，或把某个枚举字段写成未定义的取值
- **THEN** 请求被拒绝且返回错误信息指明对应字段
- **AND** MUST NOT 有任何字段被部分写入

### Requirement: 启动类参数只读展示

`--model`、`--transport`、`--modelres`、`--batch_size`、`--listenport`、`--fps`、`--max_session` MUST 以只读形式展示当前值并标注 `restart`，MUST NOT 提供写入这些项的接口能力。

#### Scenario: 尝试写入启动类参数

- **WHEN** 写入请求中包含上述任一启动类参数
- **THEN** 请求被拒绝且错误信息指明该字段不可写
- **AND** 设置文件中 MUST NOT 新增这些字段

#### Scenario: 查看启动类参数

- **WHEN** 客户端携带正确口令请求设置读取接口
- **THEN** 上述参数以只读项返回，标注为 `restart`
- **AND** 返回值与当前进程实际使用的启动参数一致

### Requirement: 设置文件不存在时以默认值启动并自动创建

设置文件不存在时，服务 MUST 以环境变量与内置默认值启动，MUST NOT 因缺少该文件而启动失败，并 MUST 在需要写入时自动创建该文件及其所在目录。

#### Scenario: 全新部署首次启动

- **WHEN** `data/settings/settings.json` 不存在且 `data/settings/` 目录也不存在的情况下启动服务
- **THEN** 服务正常启动并可响应请求
- **AND** 首次写入设置时该目录与文件被自动创建

### Requirement: 设置文件损坏时以默认值启动并保留原文件

设置文件存在但无法解析或结构不合法时，服务 MUST 以默认值启动并记录明确错误，MUST 保留原文件内容以便人工检查，MUST NOT 直接删除或覆盖用户数据。

#### Scenario: 手工编辑导致 JSON 语法错误

- **WHEN** 设置文件被手工改坏导致无法解析后启动服务
- **THEN** 服务以默认值正常启动并在日志中记录解析失败
- **AND** 原文件内容 MUST 仍然存在于磁盘上

#### Scenario: 损坏后的恢复

- **WHEN** 用户修复该文件后重启服务
- **THEN** 修复后的值被正常读取并生效

### Requirement: 设置解析必须宽容

解析设置文件时，未知字段 MUST 被忽略且 MUST NOT 导致错误；缺失字段 MUST 取默认值。跨版本的设置文件 MUST 可以被新旧版本互相读取而不失败。

#### Scenario: 文件含旧版本遗留字段

- **WHEN** 设置文件中包含当前版本不认识的字段
- **THEN** 服务正常启动与响应
- **AND** 未识别的字段被忽略，已识别的字段正常生效

#### Scenario: 文件缺少部分字段

- **WHEN** 设置文件只包含若干字段，其余字段缺失
- **THEN** 缺失字段取默认值（或在存在环境变量时取环境变量值）
- **AND** 读取接口对每个缺失项仍返回当前生效值与 `effect`
