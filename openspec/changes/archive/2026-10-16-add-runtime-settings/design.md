# Design

## Context

动机与检索结论见 `proposal.md` —— Why。这里只记录方案设计所需的事实与取舍。

### 关键事实：每类配置现在在哪里被读取

这决定了它能做到什么程度的"生效"：

| 配置 | 读取点 | 能否热生效 |
|---|---|---|
| LLM key / base_url / model | `llm.py:58`、`:64`、`:86`，每次调用都读 | 立即生效 |
| 系统提示词 | `llm.py:82` 内联字符串 | 立即生效（改为读取设置后） |
| TTS 引擎 `--tts` | `avatars/base_avatar.py:107` `registry.create("tts", opt.tts, ...)`，**每个会话构造一次** | 对新建会话生效 |
| TTS 语速 / 参考音色 | 同上，经 `opt` 传入 TTS 插件 | 对新建会话生效 |
| 默认形象 `--avatar_id` | `app.py:93` `copy.deepcopy(opt)`、`app.py:96` `resolve_avatar_id`，**每个会话构造时** | 对新建会话生效 |
| 口型微调 `--mouth_alpha` 等 | `avatars/wav2lip_avatar.py:128-130` `getattr(opt, ...)`，会话构造时读取 | 对新建会话生效 |
| RAG 参数 | `rag_service/config.py:20-40` 在**模块导入时**绑定为模块级常量 | 需重启 RAG 子进程 |
| `--model` | `app.py:302-322` 启动时加载权重、`warm_up()` | 必须重启主服务 |
| `--modelres` / `--batch_size` | `wav2lip_avatar.py:105-108` 的 `warm_up`，仅启动时 | 必须重启主服务 |
| `--transport` | `app.py:331-340`、`:376-380`，决定服务形态与首页 | 必须重启主服务 |
| `--listenport` / `--fps` / `--max_session` / `--push_url`(rtcpush) | `app.py:387`、`:396-400`，仅启动时 | 必须重启主服务 |
| `--pad_scale` | `avatars/wav2lip/genavatar.py:134`，形象**生成任务**的参数 | 属于任务参数，不是渲染设置 |

### 约束

- 主服务是单进程 aiohttp 应用，已经用 `request.app.get("opt")`（`server/routes.py:340`）把启动参数挂到应用上下文，说明"应用级共享可变对象"是现有模式，可以直接复用。
- 会话是通过 `copy.deepcopy(opt)`（`app.py:93`）从全局 `opt` 派生的，所以**改全局 `opt` 对已存在的会话无效、对新建会话有效**——这个语义已经存在，本次只是把它变成用户可见的契约。
- `server/routes.py:475-481` 的 `voice_apply` 已经示范了"运行期改会话 opt"的用法，说明运行期调整是既有能力。
- 当前管理类接口零鉴权（审计 P0）。新增写配置能力必须先解决这一点。

## Goals / Non-Goals

**Goals:**

- 提供一个设置页面，让部署者不用编辑文件、不用记命令行参数就能配置 AI 接口、人设、TTS、默认形象与 RAG 参数。
- 对每一类设置**如实标注生效时机**，不让用户以为所有改动都立刻生效。
- 在同一变更内为管理与配置类接口加一道最小但真实的访问口令校验。
- 密钥在接口响应中始终掩码，落盘文件不入库。

**Non-Goals:**

- 不做运行期切换数字人模型（`--model`）、输出方式（`--transport`）、推理分辨率与批大小——这些需要重新加载权重或改变服务形态，属于独立的、风险更高的改造。本次只在设置页展示当前值并标注"需重启"。
- 不做用户体系、多账号、角色权限。口令是单一口令，面向"一个人部署一台教学机"的场景。
- 不引入 `anthropic` SDK。Claude 通过其 OpenAI 兼容入口按"通用 OpenAI 兼容端点"接入；若某个 Anthropic 端点不兼容，由用户改用兼容网关。
- 不改动 RAG 的检索与生成算法，只暴露参数与重启能力。
- 不把设置持久化到数据库，也不做版本迁移（只做"未知字段忽略、缺失字段取默认值"的宽容解析）。

## Decisions

### 决策 1：设置存 `data/settings/settings.json`，优先级为 设置文件 > 环境变量 > 内置默认

- 选文件而不是改写 `.env`：`.env` 是用户手工维护的、面向部署的凭据文件，让程序去改写它容易破坏用户自己的注释与组织方式，而且 `.env` 里的值被 `load_dotenv` 以"已存在则不覆盖"的方式写入 `os.environ`，改写后不重启也不生效，语义混乱。
- 选 `data/` 下：`data/` 已被 `.gitignore` 覆盖，密钥天然不入库。为此在 `.gitignore` 中补一条 `/data/settings/`（`/data/` 已忽略整个目录，这条是防御性显式化）。
- 优先级取"设置文件 > 环境变量 > 默认"：界面写过的值最优先；没写过的继续用 `.env` 或默认值，保证已有部署的行为不变。

### 决策 2：生效时机分三档，并在接口与界面上都是显式字段

每项设置都有一个 `effect` 字段，取值：

- `immediate`：下一次调用即生效（LLM 供应商配置、系统提示词）
- `new_session`：对此后新建的会话生效，已存在会话不变（TTS、默认形象、口型微调）
- `restart`：需要重启对应进程后生效（主服务启动类参数、RAG 参数）

`GET` 接口返回每项的当前值、生效档位与（对 `restart` 项）一句说明。前端把三档用不同样式标出。

理由：这是本变更里最容易被误解的地方。把它做成一等公民的字段，而不是写在文档里靠用户读，才不会出现"我改了怎么没用"。

### 决策 3：口令来源三级兜底，默认自动生成并打印

取值优先级：

1. 环境变量 `ADMIN_TOKEN`（已有 `.env` 部署可显式指定）
2. 设置文件里的 `access_token`
3. 都没有时，**启动时生成 32 字节随机口令**，写入设置文件，并在控制台与日志中打印一次，提示"这是首次生成的访问口令"。

选"自动生成 + 打印"而不是"默认无鉴权"：默认无鉴权等于新功能上线即带一个洞；默认生成会带来一次"要去日志里找口令"的成本，但那是安全的一侧。

口令比较使用常量时间比较，避免时序侧信道。

### 决策 4：口令用请求头传递，前端存 `sessionStorage`

- 传递方式：`X-Admin-Token: <token>` 请求头。选它而不是 Cookie，是因为不需要 CSRF 防护、不涉及跨站携带、语义清晰。
- 前端存 `sessionStorage` 而不是 `localStorage`：关掉标签页即失效，避免共用机器上口令长期驻留。
- 校验范围按「配置与凭据面 + 管理面」划分：
  - **受保护**：`/api/settings/*`、`/api/control/*`、`/api/admin/*`、`/rag/admin/*`、`/api/avatar/task*`、`/api/voice/upload`、`/api/voice/delete`。
  - **豁免**：静态页面与资源、`/offer`、`/human`、`/humanaudio`、`/interrupt_talk`、`/is_speaking`、`/llm_status`、`/record`、`/api/status`、`/api/voices`、`/api/voice/apply`、`/api/slides*`、`/api/current_slide`、`/api/materials/*`。
- 为什么按这个边界而不是简单的 `/api/voice/*` 前缀：`web/index.html:605` 与 `:1227` 在**页面加载时**就 `fetch('/api/voices')`，语音管理（上传/删除/应用）也走 `voice-manager.js`，而主控台是课堂现场要随手打开的页面。因此只把**会写或删文件**的 `/api/voice/upload`、`/api/voice/delete` 纳入保护，把只读的列表与"把音色应用到当前会话"留在豁免侧，避免为了鉴权牺牲课堂可用性。
- 同样出于课堂可用性，`/api/slides*` 与 `/api/materials/*` 留在豁免侧——但这两个面里有破坏性写入（上传图片会先清空 `data/materials/images/`），`/api/voice/delete` 在加口令前也接受任意路径删除。**这些缺陷不因加了口令就算解决**，它们已在审计记录中登记为独立变更；本变更只保证"凭据与配置不会被未授权的人读取或覆盖"这一目标。

### 决策 5：密钥掩码采用"前缀 + 后四位"，且提交掩码值等于不修改

- `GET` 返回形如 `sk-1****8056` 的掩码，永不返回完整密钥。
- `PUT` 时若某字段的值为空串、`null`、或与当前掩码相同，视为"不修改该项"；要清空则显式传 `""` 与 `clear: true`。
- 理由：用户看到掩码后直接点保存是很自然的动作，如果不做这个约定就会把密钥覆盖成一串星号。

### 决策 6：供应商预设只在前端生效，后端只认 OpenAI 兼容三件套

预设（`deepseek` / `openai` / `anthropic` / `moonshot` / `dashscope` / `custom`）在前端用于填充 `base_url` 与候选 `model` 列表，后端**不校验供应商名**，只使用 `base_url` + `api_key` + `model` 发起 OpenAI 兼容请求。这样新增供应商不需要改后端，也不会因为某个供应商改名而需要发版。

### 决策 7：连通性测试是一个独立的只读端点

`POST /api/settings/llm/test` 用请求体里的（或已保存的）配置发一次 `max_tokens` 很小的非流式请求，返回 `成功/失败`、耗时、模型回显与错误摘要。它不写入设置，因此用户可以"先测再存"。超时上限设为一个较短的值（如 20 秒），避免页面卡死。

### 决策 8：RAG 参数由主服务在重启 sidecar 时以环境变量注入

RAG 的配置在 `rag_service/config.py` 导入期绑定，改文件对已运行进程无效。主服务已经掌握 sidecar 生命周期（`app.py:191-251` 的 `start_rag_service` / `stop_rag_service`），因此：

- 设置写进 `settings.json`；
- `POST /api/control/rag/restart` 由主服务停掉 sidecar，再用 `subprocess.Popen(..., env={**os.environ, **rag_settings})` 重新拉起。

这样不需要改 RAG 服务的读取方式，也不需要程序去改写 `.env.rag`。

### 决策 9：启动类参数只读展示

`--model`、`--transport`、`--modelres`、`--batch_size`、`--listenport`、`--fps`、`--max_session`、`--l/-m/-r` 在设置页以只读形式展示，标注 `restart`，并给出对应的命令行写法。不提供编辑，因为改了也无法在本次生命周期内生效，提供可编辑反而制造"改了没用"的困惑。

### 决策 10：RAG 重启的阻塞部分必须放到线程池，并发守卫用布尔标志

**这是实现阶段实测发现的缺陷**：`stop_rag_service()` / `start_rag_service()` 是阻塞实现（`subprocess.wait`、`time.sleep` 轮询），若直接在 aiohttp 处理函数里 `await` 它们，整个事件循环会被占住 10–20 秒——重启期间全站失去响应。

更隐蔽的是并发守卫：最初用 `asyncio.Lock` + `lock.locked()` 判断，实测**两个并发重启请求都执行了**。原因是临界区里若没有真正让出事件循环的 await（`start_rag_service` 是纯阻塞的），第二个请求在 aiohttp 单线程模型下根本来不及被处理，于是 `locked()` 永远看不到竞争。

因此改为：

1. 阻塞部分整体放进 `loop.run_in_executor`，事件循环在重启期间保持可用（实测 `/api/status` 中位延迟 32 ms，未被阻塞）。
2. 并发守卫改成普通布尔标志：检查与置位都在第一个 `await` 之前同步完成，在单线程事件循环下判定是确定的，不依赖锁的时序。

验证方式：两个并发重启请求，一个在 0.01 秒内被拒绝（"已有一次 RAG 重启正在进行"），另一个正常完成；连续两次重启后 RAG 进程数保持恒定（不累积）。

## Risks / Trade-offs

- **[口令成为新的单点]** → 自动生成长随机口令；常量时间比较；口令可在设置页修改（需先通过校验）；读取密钥与本机文件的操作全部在口令之后。
- **[设置文件含明文密钥]** → 文件位于被忽略的 `data/` 下；接口响应一律掩码；文档中明确提示该文件的敏感性与备份注意事项。
- **[`/api/status` 保持公开]** → 它只返回三个布尔量与会话数，不含任何凭据；前端状态条在鉴权前就要用。若日后要收紧，可把 session 数移出。
- **[给既有接口加口令会造成破坏性变更]** → 口令启用后，既有的 curl 脚本、`dashboard.html` 等直接调用管理接口的路径需要带口令。按 `proposal.md` 判定为非 BREAKING（路径与方法不变），但需要在 README 与 `docs/` 中给出带上口令的调用示例，并在设置页显示"如何带上口令"。
- **[改动面大]** → 拆成五个能力规格分别评审；实现上先落地 `access-control` 与 `runtime-configuration` 两个基础能力，再逐项接入 LLM、会话、RAG。
- **[运行期改 `opt` 影响并发会话]** → 严格采用"只影响新建会话"的语义并在界面标注；不做已存在会话的批量热更新，避免打断正在授课的会话。

## Migration Plan

1. 落地口令与设置存储（`access-control`、`runtime-configuration`），此时既有部署行为不变（口令自动生成并打印，管理接口开始要求口令）。
2. 落地 LLM 配置与连通性测试，`llm.py` 改为优先读设置、回落环境变量。
3. 落地会话踢出与 RAG 参数/重启。
4. 落地 `web/settings.html` 与 `admin.html` 改造，把生效时机标注出来。
5. 更新 `README.md` 与 `docs/`：访问口令、带口令的调用示例、设置项与生效时机表。

回滚：删除 `data/settings/settings.json` 并移除新增路由即可回到纯 `.env` 配置；口令未设时按"自动生成"处理，回滚后旧脚本需去掉口令头。

## Open Questions

- 访问口令是否需要"仅允许本机访问"的开关（把管理接口限制在 `127.0.0.1`）？这属于部署策略，可以后续作为独立的小变更加入，不影响本次的接口设计。
