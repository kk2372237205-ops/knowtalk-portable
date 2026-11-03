# llm-configuration Specification

## Purpose
定义 KnowTalk 对话 LLM 的运行期配置能力契约：供应商接口参数（`base_url` / `api_key` / `model`）与系统提示词（人设）的读写、供应商预设的定位、取值优先级、生效时机、密钥掩码与连通性测试。本能力只描述新增的配置能力，不重新定义 chat 模式既有的请求与朗读行为。

## Requirements

### Requirement: 读取 LLM 配置接口

`GET /api/settings/llm` MUST 在 `data` 中返回当前生效的 LLM 配置，字段为 `base_url`、`api_key`、`model`、`system_prompt`、`effect`。`effect` MUST 为 `immediate`。`api_key` MUST 为掩码形式，MUST NOT 为完整值。某字段从未被写入时，MUST 返回其环境变量值或内置默认值。响应 MUST 遵循统一 JSON 信封。

#### Scenario: 读取当前配置

- **WHEN** 客户端调用 `GET /api/settings/llm`
- **THEN** `data` 含 `base_url`、`api_key`、`model`、`system_prompt`、`effect`
- **AND** `effect` 为 `immediate`
- **AND** `api_key` 为掩码形式

#### Scenario: 字段回落到环境变量

- **WHEN** 设置文件中没有 `model` 字段，而环境变量 `DEEPSEEK_MODEL` 为 `deepseek-chat`
- **THEN** `data.model` 为 `deepseek-chat`

### Requirement: 写入 LLM 配置接口

`PUT /api/settings/llm` MUST 接受 `base_url`、`api_key`、`model`、`system_prompt` 字段并持久化，MUST 返回写入后的生效配置。请求体中未出现的字段 MUST 保持原值不被改变。`system_prompt` 写入空串 MUST 表示使用内置默认提示词。

#### Scenario: 保存供应商配置

- **WHEN** 客户端提交 `{"base_url": "https://api.moonshot.cn/v1", "api_key": "sk-xxxx", "model": "moonshot-v1-8k"}`
- **THEN** 响应为成功信封并回显写入后的配置
- **AND** 随后的 `GET /api/settings/llm` 返回同样的 `base_url` 与 `model`，`api_key` 为掩码

#### Scenario: 只改一个字段

- **WHEN** 客户端只提交 `{"model": "deepseek-chat"}`
- **THEN** `model` 被更新，`base_url` 与已保存的 `api_key` 保持不变

### Requirement: 供应商预设只用于界面填充

系统 MUST 提供内置供应商预设，至少包含 DeepSeek、OpenAI、Anthropic 兼容入口、Moonshot、通义。预设（含名称、`base_url` 与候选 `model` 列表）MUST 可由界面获取。预设 MUST 只用于在界面上填充 `base_url` 与候选 `model` 列表；后端 MUST NOT 因供应商名不同而改变请求方式，一律以 `base_url` + `api_key` + `model` 按 OpenAI 兼容协议发起请求。

#### Scenario: 选择预设填充表单

- **WHEN** 用户在设置界面选择 Moonshot 预设
- **THEN** 界面填入该预设的 `base_url` 与候选 `model` 列表
- **AND** 后端仍只依据 `base_url` + `api_key` + `model` 发起请求

#### Scenario: 未知供应商名不影响请求

- **WHEN** 配置中的供应商名不在内置预设列表中，但 `base_url` + `api_key` + `model` 合法
- **THEN** chat 请求按 OpenAI 兼容协议正常发出，MUST NOT 因供应商名未识别而报错

### Requirement: 未内置的 OpenAI 兼容端点可直接使用

`base_url` MUST 接受任意 OpenAI 兼容端点的地址。只要该端点提供 `POST {base_url}/chat/completions`，仅填写 `base_url` + `api_key` + `model` MUST 即可在 chat 模式使用，MUST NOT 要求先匹配某个内置预设。

#### Scenario: 仅填 base_url 接入自建网关

- **WHEN** 用户只填写自建中转网关的 `base_url`、其 `api_key` 与 `model`，该供应商不在预设列表中
- **THEN** 下一次 chat 请求使用该端点并正常朗读回答

### Requirement: 配置取值优先级

生效值 MUST 按「设置文件 > 环境变量 > 内置默认」确定。环境变量为 `DEEPSEEK_API_KEY`（默认空）、`DEEPSEEK_BASE_URL`（默认 `https://api.deepseek.com/v1`）、`DEEPSEEK_MODEL`（默认 `deepseek-v4-pro`）。仅在 `.env` 中配置过 LLM 的既有部署 MUST 保持原有行为不变。

#### Scenario: 设置文件覆盖环境变量

- **WHEN** 设置文件与 `.env` 都提供了 `model`
- **THEN** 生效 `model` 为设置文件中的值

#### Scenario: 只在 .env 配置的部署

- **WHEN** 设置文件中没有任何 LLM 字段，`.env` 提供 `DEEPSEEK_API_KEY` 与 `DEEPSEEK_MODEL`
- **THEN** chat 模式按 `.env` 的值发起请求，行为与未引入本能力前一致

#### Scenario: 均未配置时取默认值

- **WHEN** 设置文件与 `.env` 都没有 `base_url` 与 `model`
- **THEN** 生效 `base_url` 为 `https://api.deepseek.com/v1`，生效 `model` 为 `deepseek-v4-pro`

### Requirement: 未配置 api_key 时的 chat 行为

当设置文件与 `DEEPSEEK_API_KEY` 均为空时，chat 模式的请求 MUST 仍按既有契约返回成功信封，MUST NOT 产生任何朗读内容，并且 MUST 记录一条可理解的日志，说明 LLM 未配置、chat 不可用。

#### Scenario: 无密钥时仍返回成功

- **WHEN** 未配置 `api_key`，客户端提交 `type=chat` 的 `POST /human`
- **THEN** 响应为 `{"code": 0, "msg": "ok"}`
- **AND** 该会话不产生新的朗读内容
- **AND** 日志中出现一条 `DEEPSEEK_API_KEY` 未设置、Chat LLM 模式不可用的错误记录

### Requirement: 配置变更立即生效

LLM 配置与系统提示词的 `effect` MUST 为 `immediate`：写入成功后，**下一次** chat 请求 MUST 使用新配置，MUST NOT 需要重启进程，也 MUST NOT 需要重建会话。

#### Scenario: 改完立刻用新配置

- **WHEN** 某会话已建立，客户端先 `PUT` 新的 `model`，紧接着提交 `type=chat` 的请求
- **THEN** 该次请求即使用新 `model`，无需重启服务或重新建立会话

### Requirement: 系统提示词配置

`system_prompt` 为空串或未配置时，服务 MUST 使用内置默认提示词；非空时 MUST 使用配置值。提示词 MUST 以 `system` 角色消息参与请求，MUST NOT 与用户提问合并为同一条用户消息。提示词内容 MUST NOT 出现在回答文本中被朗读。

#### Scenario: 留空使用内置默认提示词

- **WHEN** `system_prompt` 为空串
- **THEN** 请求中使用内置默认提示词，用于约束简短、口语化、无 Markdown 的纯文本输出

#### Scenario: 自定义人设生效且不复述

- **WHEN** 客户端把 `system_prompt` 设为「你是一位小学数学老师」
- **THEN** 请求的 `system` 消息为该人设内容
- **AND** 回答正文与朗读内容中不复述该提示词

### Requirement: LLM 连通性测试接口

`POST /api/settings/llm/test` MUST 用请求体给出的配置（未给出的字段取当前生效值）发一次非流式的最小请求。超时上限 MUST 为 20 秒。响应 MUST 返回是否成功、耗时与上游回显的模型名；失败时 MUST 返回可理解的错误摘要，且 MUST NOT 包含完整 `api_key`。测试 MUST NOT 写入设置。

#### Scenario: 测试成功

- **WHEN** 客户端提交有效的 `base_url`、`api_key`、`model`
- **THEN** 响应表明测试成功，并含耗时与上游回显的模型名
- **AND** 已保存的设置未被改动

#### Scenario: 测试超时

- **WHEN** 上游在 20 秒内没有返回
- **THEN** 测试以失败结束并返回超时摘要
- **AND** 已保存的设置未被改动

#### Scenario: 测试失败不泄露密钥

- **WHEN** 提交的 `api_key` 无效导致上游拒绝
- **THEN** 响应返回可理解的错误摘要
- **AND** 响应内容中不含完整的 `api_key`

### Requirement: 密钥掩码与不修改语义

`GET` 返回的 `api_key` MUST 为「前缀 + 后四位」形式的掩码（如 `sk-1****8056`），MUST NOT 返回完整值。`PUT` 时该字段为空串、`null` 或与当前掩码相同时，MUST 视为不修改该项，MUST NOT 用掩码覆盖已保存的密钥；清空密钥 MUST 由显式字段表达。

#### Scenario: 读取时返回掩码

- **WHEN** 已保存密钥 `sk-1234567890abcdef8056`，客户端调用 `GET /api/settings/llm`
- **THEN** `data.api_key` 为保留前缀与后四位的掩码形式
- **AND** 响应中不出现完整密钥

#### Scenario: 回传掩码不覆盖密钥

- **WHEN** 客户端把 `GET` 得到的掩码值原样 `PUT` 回去
- **THEN** 已保存的 `api_key` 保持不变

### Requirement: 多轮记忆不受本配置影响

本配置能力 MUST NOT 改变会话历史的条数、截断语义与写入时机。历史轮数上限 MUST 继续由 `CHAT_HISTORY_ROUNDS` 控制（默认 10），MUST 继续按 `sessionid` 隔离，且历史 MUST NOT 被复述进回答。

#### Scenario: 换模型后历史语义不变

- **WHEN** 写入新的 `model` 后，某会话连续追问多轮
- **THEN** 每轮请求携带的历史不超过 `CHAT_HISTORY_ROUNDS` 设定的最近 N 轮
- **AND** 其它会话的历史不出现在该请求中

### Requirement: 非法配置拒绝写入

`PUT /api/settings/llm` 遇到非法取值时 MUST 拒绝写入并返回业务错误，错误信息 MUST 指明有问题的字段：`base_url` 不是合法 URL、`model` 为空串。拒绝时 MUST NOT 改变任何已保存的值。

#### Scenario: base_url 非法

- **WHEN** 客户端提交 `{"base_url": "not-a-url"}`
- **THEN** 返回 `code` 为 `-1` 的业务错误，且 `msg` 指明 `base_url` 字段非法
- **AND** 已保存的 `base_url` 不变

#### Scenario: model 为空

- **WHEN** 客户端提交 `{"model": ""}`
- **THEN** 返回 `code` 为 `-1` 的业务错误，且 `msg` 指明 `model` 字段非法
- **AND** 已保存的 `model` 不变
