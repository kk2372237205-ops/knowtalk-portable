# rag-sidecar-control Specification

## Purpose
定义 RAG 旁路服务的参数读写、运行状态查询与由主服务代为重启的行为契约，并如实标注这些参数只有在 RAG 服务重启后才会生效。

## Requirements

### Requirement: RAG 可配置参数清单

下列参数 MUST 均可通过 `GET /api/settings/rag` 与 `PUT /api/settings/rag` 读取与写入：`RAG_LLM_MODEL`、`RAG_EMBED_MODEL`、`RAG_TOKENIZER_NAME`、`RAG_RERANKER_NAME`、`RAG_CHUNK_SIZE`、`RAG_CHUNK_OVERLAP`、`RAG_VECTOR_TOP_K`、`RAG_BM25_TOP_K`、`RAG_RERANK_TOP_K`、`RAG_VECTOR_THRESHOLD`、`RAG_REQUEST_TIMEOUT`、`OLLAMA_BASE_URL`、`RAG_BASE_URL`、`RAG_ENABLED`。`GET` MUST 返回每一项的当前值、生效档位与说明。

#### Scenario: 读取 RAG 参数

- **WHEN** 调用方请求 `GET /api/settings/rag`
- **THEN** 响应 MUST 为成功信封，且 `data` MUST 覆盖上述全部参数
- **AND** 每一项 MUST 带当前值与生效档位

### Requirement: RAG 参数读写接口

服务 MUST 提供 `GET /api/settings/rag` 与 `PUT /api/settings/rag`。`PUT` MUST 只接受可配置参数清单中的字段，未提交的项 MUST 保持原值，未知字段 MUST 被忽略且 MUST NOT 使请求失败；写入成功后再次读取 MUST 返回新值。

#### Scenario: 写入 RAG 参数

- **WHEN** 调用方以 `PUT /api/settings/rag` 提交若干可配置参数的新值
- **THEN** 后续 `GET /api/settings/rag` MUST 回显这些新值

#### Scenario: 未提交的项保持原值

- **WHEN** `PUT /api/settings/rag` 只提交了部分可配置参数
- **THEN** 未提交项 MUST 保持写入前的值

#### Scenario: 未知字段被忽略

- **WHEN** `PUT /api/settings/rag` 的请求体包含不在可配置参数清单内的字段
- **THEN** 该字段 MUST 被忽略，且 MUST NOT 导致请求失败

### Requirement: RAG 参数标注为重启生效

上述 RAG 参数在 RAG 服务启动、模块导入阶段被读取并绑定，因此每项 MUST 标注生效档位为 `restart`。写入设置后，服务 MUST NOT 在响应或界面中声称参数已生效，MUST 明确提示需要重启 RAG 服务。

#### Scenario: 生效档位为 restart

- **WHEN** 调用方读取任一 RAG 参数的生效档位
- **THEN** 该档位 MUST 为 `restart`

#### Scenario: 写入后不声称已生效

- **WHEN** 调用方通过 `PUT /api/settings/rag` 修改了某个参数
- **THEN** 响应 MUST NOT 声称该参数已生效，且 MUST 提示需要重启 RAG 服务

### Requirement: RAG 运行状态查询

服务 MUST 提供 `GET /api/control/rag/status`。当 `RAG_ENABLED` 为真时，响应 MUST 包含：RAG 服务是否可达、健康检查是否通过、以及当前生效参数的来源标识（至少区分来自运行期设置文件与来自 `.env.rag`）。可达性 MUST 通过请求 RAG 服务的健康检查端点判定。

#### Scenario: RAG 可达时返回状态

- **WHEN** RAG 服务健康检查端点返回成功且 `RAG_ENABLED` 为真
- **THEN** 响应 MUST 表明 RAG 可达且健康检查通过
- **AND** 响应 MUST 给出当前生效参数的来源标识

#### Scenario: RAG 不可达时不报错

- **WHEN** RAG 服务无法连通
- **THEN** 响应 MUST 表明 RAG 不可达且健康检查未通过
- **AND** 响应 MUST NOT 是服务端错误导致的状态查询失败

### Requirement: RAG 重启接口

服务 MUST 提供 `POST /api/control/rag/restart`，由主服务停止自身启动的 RAG 进程，再以设置中的参数重新启动。响应 MUST 包含重启是否成功与就绪状态。无论重启成功或失败，主服务 MUST NOT 退出或中断其它功能；失败时 MUST 返回可理解的错误信息。

#### Scenario: 重启成功

- **WHEN** 调用方对由主服务启动的 RAG 服务发起重启且新进程在等待窗口内就绪
- **THEN** 响应 MUST 表明重启成功且 RAG 已就绪

#### Scenario: 重启失败不影响主服务

- **WHEN** 重启后 RAG 进程启动失败或未在等待窗口内就绪
- **THEN** 响应 MUST 返回可理解的失败信息
- **AND** 主服务 MUST 继续运行并正常响应其它接口

#### Scenario: 并发重启请求

- **WHEN** 两个重启请求几乎同时到达
- **THEN** 同一时刻 MUST 只执行一次停止与启动
- **AND** 后到的请求 MUST 得到明确的"正在进行中"或等价结果，MUST NOT 再启动第二个 sidecar 进程

### Requirement: 仅允许重启主服务启动的本机 sidecar

仅当 `RAG_BASE_URL` 指向本机地址（主机名为 `127.0.0.1` 或 `localhost`）时，服务才允许代为重启 sidecar；对非本机地址（远程 RAG），重启请求 MUST 被拒绝并返回可理解的错误信息，MUST NOT 尝试停止或影响该远程服务。对本机地址但并非由主服务启动的实例，重启请求同样 MUST 被拒绝并提示调用方自行重启。

#### Scenario: 拒绝重启远程 RAG

- **WHEN** `RAG_BASE_URL` 指向非本机地址且调用方发起重启
- **THEN** 请求 MUST 被拒绝并返回可理解的错误信息
- **AND** 该远程 RAG 服务 MUST NOT 被停止或影响

#### Scenario: 拒绝重启非主服务启动的实例

- **WHEN** `RAG_BASE_URL` 为本机地址，但当前 RAG 实例并非由主服务启动
- **THEN** 请求 MUST 被拒绝并提示调用方需自行重启该实例

### Requirement: RAG 禁用时的行为

当 `RAG_ENABLED` 为假（`0`、`false`、`no`、`off`，大小写不敏感）时，主服务 MUST NOT 启动 RAG sidecar；此时 `GET /api/control/rag/status` MUST 如实返回「已禁用」状态，MUST NOT 返回错误信封，也 MUST NOT 将其报告为运行异常。

#### Scenario: 禁用时不启动 sidecar

- **WHEN** `RAG_ENABLED` 为假且主服务启动
- **THEN** 主服务 MUST NOT 拉起 RAG sidecar

#### Scenario: 禁用时状态查询如实反映

- **WHEN** `RAG_ENABLED` 为假且调用方请求 `GET /api/control/rag/status`
- **THEN** 响应 MUST 表明 RAG 已被禁用
- **AND** 响应 MUST NOT 是错误信封

### Requirement: 冷启动超时的既有行为

主服务启动时拉起 RAG sidecar 后 MUST 在有限等待窗口内轮询其健康检查（当前窗口约 15 秒）。窗口内未就绪时，主服务 MUST 只记录可理解的警告并继续启动，MUST NOT 使主服务启动失败。此后 `GET /api/control/rag/status` MUST 能在 RAG 真正就绪后反映其真实状态。

#### Scenario: 等待窗口内未就绪

- **WHEN** RAG sidecar 在启动等待窗口内未通过健康检查
- **THEN** 主服务 MUST 记录可理解的警告并继续以正常状态运行

#### Scenario: 稍后反映真实就绪

- **WHEN** RAG sidecar 在等待窗口之后才就绪，调用方随后请求 `GET /api/control/rag/status`
- **THEN** 响应 MUST 反映 RAG 当前真实的可达与健康状态
