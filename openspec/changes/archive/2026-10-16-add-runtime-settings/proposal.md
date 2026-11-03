# Proposal

## Why

KnowTalk 目前**没有任何运行期配置的用户入口**。检索结果：

- 全站 61 个路由中，**写入配置的接口 0 个**；`/api/admin/config` 只能 GET，返回的是 `vars(opt)`（启动参数，且不含任何密钥）。
- `web/admin.html` 有 **0 个可编辑控件**（无 `input`/`select`/`textarea`），纯粹只读展示启动参数与会话列表。
- `config.py` 里的 **22 个命令行参数**页面上一个都改不了。
- LLM 的 key / base_url / model 全部来自 `.env` 的 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`（`llm.py:58/64/86`）；系统提示词硬编码在 `llm.py:82`。

后果是：**换一个模型、换一把 key、改一句人设，都必须手工编辑文件并重启进程**。对"老师自己部署一台机器"的使用场景来说，这是最影响可用性的缺口。

同时，底层其实已经具备多供应商能力：`llm.py` 用的是 `openai` SDK + 可配置 `base_url`，任何 OpenAI 兼容端点（DeepSeek、OpenAI、Moonshot、通义、OpenRouter、one-api/new-api 中转等）改个地址就能用——缺的只是把它暴露到界面上。

另外，一旦允许从界面写入 API Key，就必须同时解决鉴权：当前管理类接口**零鉴权**（审计已记为 P0），局域网内任何人都能读取或覆盖 key、并消耗额度。**写配置的能力不能脱离鉴权单独上线**。

## What Changes

- 新增能力 `access-control`：启动时确定一个访问口令，所有管理类与配置类接口（含新增的设置接口）必须携带正确口令才响应；未携带或错误时拒绝，且 MUST NOT 泄露任何配置内容。
- 新增能力 `runtime-configuration`：建立统一的运行期设置存储（服务端文件持久化）、读写契约、密钥掩码规则，并明确每一类设置的**生效时机**——「立即生效」「对新建会话生效」「重启后生效」三档，界面上必须如实标注。
- 新增能力 `llm-configuration`：LLM 供应商预设（DeepSeek / OpenAI / Anthropic 兼容网关 / Moonshot / 通义 等）+ 通用 OpenAI 兼容配置（`base_url` / `api_key` / `model`）、系统提示词（人设）配置，以及**连通性测试**（用当前配置发一次最小请求并回报结果）。
- 新增能力 `session-control`：会话列表带踢出操作（复用已有的 `session_manager.remove_session`），补上目前只能看不能管的缺口。
- 新增能力 `rag-sidecar-control`：RAG 参数配置的读写、sidecar 运行状态查询与**由主服务代为重启**（参数在 RAG 进程导入期绑定，必须重启才生效）。
- 新增 `web/settings.html` 设置页面，并把入口挂到主控台与 `admin.html`；`admin.html` 从"只读展示"升级为"可编辑 + 展示生效时机"。
- 设置文件（含密钥）MUST 落在被 `.gitignore` 覆盖的位置，MUST NOT 入库；接口返回密钥时 MUST 掩码。
- **BREAKING**：无。所有既有接口的路径、方法与响应结构不变；只是当访问口令启用后，管理类接口需要额外携带口令。

## Capabilities

### New Capabilities

- `access-control`: 访问口令的确定方式、受保护接口的范围、校验与拒绝行为、口令传递方式，以及口令缺失时的降级策略。
- `runtime-configuration`: 运行期设置的持久化位置与格式、读取与写入接口、密钥掩码、取值校验、生效时机分级，以及设置页必须覆盖的配置项清单。
- `llm-configuration`: LLM 供应商预设与通用 OpenAI 兼容端点配置、系统提示词、连通性测试的行为契约。
- `session-control`: 活跃会话的列举与踢出行为。
- `rag-sidecar-control`: RAG 参数的读写、sidecar 状态查询与代主服务重启的行为契约。

### Modified Capabilities

- `digital-human-api`: 只改一处——「统一 JSON 响应格式与业务错误」要求里，把访问口令校验失败加入 4xx 例外清单，并补一条场景说明它 MUST NOT 套用 `{"code": -1, ...}` 业务信封。

  其余看似需要改动的地方，实现时按"新能力自己拥有契约"处理，因此不构成对既有规格的修改：`/api/admin/config` 保持只读不变（设置目录由 `runtime-configuration` 拥有）；会话踢出接口是 `session-control` 的 ADDED 要求，不是对既有查询接口的修改；`/api/status` 的返回值**未**改动（原计划要加设置状态，实现时判断没有必要——它只服务前端状态条，加了反而扩大公开信息面）。

## Impact

- **新增文件**：`utils/settings_store.py`（设置存储与掩码）、`server/settings_routes.py`（设置与控制接口）、`server/auth.py`（口令校验）、`web/settings.html`（设置页面）、`data/settings/settings.json`（运行时生成，已忽略）。
- **修改文件**：`app.py`（初始化口令与设置存储、注册新路由、把设置注入应用上下文）、`llm.py`（从设置读取供应商配置与系统提示词，保留环境变量兜底）、`server/routes.py`（管理类接口接受口令、`/api/status` 增加设置状态）、`server/session_manager.py`（暴露安全的踢出方法）、`web/admin.html`（改为可编辑并标注生效时机）、`web/index.html`（挂设置入口）、`.gitignore`（忽略 `data/settings/`）。
- **不受影响**：WebRTC 推流链路、TTS 合成与口型推理的核心流程、RAG 的检索与生成算法、Avatar 生成流程。启动类参数（`--model`、`--transport`、`--modelres`、`--batch_size`、`--listenport`、`--fps`、`--max_session`）仍然只能启动时确定，本次只提供"查看 + 标注需重启"，不做运行期切换。
- **用户可见变化**：首次访问管理类页面需要输入访问口令；新增设置页可以配置 AI 接口、人设、TTS、默认形象、RAG 参数，并清楚看到每项何时生效。
- **依赖新增**：无（继续使用已安装的 `openai` SDK；Claude 通过其 OpenAI 兼容入口接入，不引入 `anthropic`）。
