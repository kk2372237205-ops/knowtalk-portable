# Tasks

## 1. 访问口令与设置存储（基础能力）

- [x] 1.1 新建 `utils/settings_store.py`：实现设置的加载、默认值合并、宽容解析（忽略未知字段、缺失字段取默认）、原子写入与密钥掩码。验证：新增 `tests/test_settings_store.py`，仅用标准库 `unittest`（不引入新依赖），覆盖「文件不存在时用默认值并创建」「文件损坏时用默认值启动且保留原文件」「未知字段被忽略」「掩码对短密钥与长密钥都不泄露完整值」；用 `python -m unittest` 运行全绿
- [x] 1.2 在 `utils/settings_store.py` 中实现访问口令的确定逻辑：`ADMIN_TOKEN` 环境变量 > 设置文件 `access_token` > 随机生成并写入设置文件。验证：`tests/test_settings_store.py` 增加用例覆盖三种来源的优先级与"自动生成不少于 32 字节且两次不同"；`python -m unittest` 全绿
- [x] 1.3 新建 `server/auth.py`：实现 `X-Admin-Token` 请求头的常量时间校验与 401 响应；错误响应 MUST NOT 携带任何配置内容。验证：新增 `tests/test_auth.py` 覆盖「正确口令放行」「缺失口令 401」「错误口令 401」「三者响应体一致且不含密钥」，并断言比较使用 `hmac.compare_digest`；`python -m unittest` 全绿
- [x] 1.4 在 `app.py` 中初始化口令与设置存储并挂到应用上下文，同时打印一次首次生成的口令。验证：启动日志出现口令提示且仅出现一次；重复启动不再重复生成
- [x] 1.5 为受保护路径接入校验：`/api/admin/*`、`/api/voice/*`、`/api/avatar/task*`、`/rag/admin/*`、`/api/settings/*`、`/api/control/*`；豁免路径保持原样。验证：逐一请求受保护路径且不带口令，全部 401；逐一请求豁免路径且不带口令，全部正常
- [x] 1.6 扩展 `.gitignore`：显式忽略 `/data/settings/`。验证：`git check-ignore -v data/settings/settings.json` 命中，且 `git status` 中不出现该文件

## 2. 设置读写接口

- [x] 2.1 新建 `server/settings_routes.py`，实现 `GET /api/settings`：返回全部可配置项的当前值、生效档位与说明；密钥字段为掩码。验证：实际请求该接口，确认响应中不含任何完整密钥，且每一项都带生效档位字段
- [x] 2.2 实现 `PUT /api/settings`：按字段写入、按掩码约定跳过未修改项、逐字段校验（类型/范围/枚举），任一项非法则整次拒绝。验证：`tests/test_settings_store.py` 增加用例覆盖「传掩码不覆盖密钥」「非法值整次拒绝且文件未变」「合法值写入后可读回」；`python -m unittest` 全绿
- [x] 2.3 实现启动类参数（`--model`、`--transport`、`--modelres`、`--batch_size`、`--listenport`、`--fps`、`--max_session`）的只读展示，标注 `restart`，写入接口对它们 MUST 返回拒绝。验证：尝试写入这些字段，返回拒绝且指明字段名；GET 中它们标记为 `restart`
- [x] 2.4 实现 `PUT /api/settings/access-token`：要求先通过当前口令校验，成功后立即对新请求生效。验证：用旧口令改新口令，旧口令随即 401、新口令可用

## 3. LLM 配置接入

- [x] 3.1 改造 `llm.py`：`api_key` / `base_url` / `model` 与系统提示词改为优先读取设置存储，回落环境变量 `DEEPSEEK_*`，最后内置默认值。验证：自检覆盖三种来源的优先级；未配置任何来源时行为与改造前一致
- [x] 3.2 实现 `GET/PUT /api/settings/llm` 与预设数据（DeepSeek / OpenAI / Anthropic 兼容 / Moonshot / 通义 / 自定义）。验证：GET 返回预设与当前值；`api_key` 为掩码
- [x] 3.3 实现 `POST /api/settings/llm/test`：用请求体配置（缺省取已保存值）发一次非流式最小请求，返回成功与否、耗时、模型回显与错误摘要；不写设置；超时上限 20 秒。验证：用当前真实 DeepSeek 配置测试返回成功与耗时；用一个故意错误的 base_url 测试返回失败且错误摘要中不含完整密钥
- [x] 3.4 确认生效时机：改完配置后**不重启进程**发起一次 chat 调用，确认使用了新配置。验证：用一个可区分的 model 值（例如临时改成 `deepseek-flash` 再改回）跑两次调用，观察请求确实使用了新值（可用日志中的模型名或连通性测试回显核对）

## 4. 会话控制与 RAG 控制

- [x] 4.1 在 `server/session_manager.py` 暴露安全的踢出方法，并在 `server/routes.py` 实现 `DELETE /api/admin/sessions/{sessionid}`。注意 `remove_session`（`session_manager.py:86-107`）已经会设置 `quit_event`、停止 output、停止录音并清空队列，**但它不会关闭对应的 WebRTC PeerConnection** —— `rtc_manager.pcs` 只在 `connectionstatechange` 时清理，且没有 sessionid → pc 的映射。因此需要为 `RTCManager` 增加该映射并在踢出时关闭对应连接，否则会残留连接。验证：会话不存在时返回业务错误而非 500；成功时返回成功信封、会话从列表消失，且踢出后 `rtc_manager` 中不再持有该会话的连接
- [x] 4.2 实现 `GET /api/control/rag/status`：返回是否可达、健康检查结果、参数来源（设置文件或 `.env.rag`）。验证：RAG 运行时返回可达；停掉 RAG 后返回不可达且不抛异常
- [x] 4.3 实现 `POST /api/control/rag/restart`：以设置中的参数重启由主服务启动的 sidecar；远程 `RAG_BASE_URL` 时拒绝并给出可理解错误；同一时刻只处理一个重启请求（用互斥保护 `app.py:78-84` 的 `rag_process` 全局），并发请求返回"正在进行中"而不是启动第二个进程。验证：实际重启一次，`/health` 恢复；把 `RAG_BASE_URL` 指到非本机地址后调用返回拒绝；并发发起两个请求，确认只产生一个 sidecar 进程
- [x] 4.4 实现 `GET/PUT /api/settings/rag`，参数为 14 项：`RAG_LLM_MODEL`、`RAG_EMBED_MODEL`、`RAG_TOKENIZER_NAME`、`RAG_RERANKER_NAME`、`RAG_CHUNK_SIZE`、`RAG_CHUNK_OVERLAP`、`RAG_VECTOR_TOP_K`、`RAG_BM25_TOP_K`、`RAG_RERANK_TOP_K`、`RAG_VECTOR_THRESHOLD`、`RAG_REQUEST_TIMEOUT`、`OLLAMA_BASE_URL`、`RAG_BASE_URL`、`RAG_ENABLED`，全部标注 `restart`（其中分词器与重排模型路径是为了让用户不必再手改 `.env.rag` 就能指向自己的模型目录）。验证：写入后 GET 能读到，生效档位为 `restart`；逐项核对与 `rag_service/config.py:20-40` 的对应关系
- [x] 4.5 确认新增路由的注册顺序：`/api/settings/*`、`/api/control/*`、`DELETE /api/admin/sessions/{sessionid}` MUST 注册在 `server/routes.py:546` 的 `app.router.add_static('/', path='web')` 之前。验证：实际请求 `/api/settings` 返回 JSON 而不是被静态资源规则吞掉

## 5. 设置页面与既有页面改造

- [x] 5.1 新建 `web/settings.html`：口令输入与保存（存 `sessionStorage`）、分组展示全部设置项、按三档生效时机标注、密钥输入框显示掩码占位、LLM 连通性测试按钮、RAG 参数与重启按钮。验证：无头浏览器渲染该页面，确认布局与图标正常（本地 vendor 资源生效）且无控制台错误
- [x] 5.2 在 `web/index.html` 与 `web/admin.html` 增加设置页入口；`admin.html` 的全局配置区标注每项的生效时机。验证：两个页面均能跳转到设置页；`admin.html` 上启动类参数标注为需重启
- [x] 5.3 为受保护接口的前端调用统一附带 `X-Admin-Token`，并在 401 时引导用户去设置页输入口令。验证：清空 `sessionStorage` 后打开 `admin.html`，确认弹出/跳转到口令输入引导，输入正确口令后数据正常加载

## 6. 文档更新

- [x] 6.1 更新 `README.md`：新增"访问口令"与"运行期设置"两节，给出带 `X-Admin-Token` 的 curl 示例，以及一张"配置项 → 生效时机"表。验证：文档中的每条 curl 示例实际可执行成功（口令替换为真实值）
- [x] 6.2 更新 `rag_service/README.md`：说明 RAG 参数现在可以从设置页配置、修改后需通过重启接口生效；并明确一条限制——**只有由主服务自己启动的 sidecar 才能被代为重启**，若本机 8090 已被外部手工启动的实例占用（`app.py:203-205` 会跳过启动且不持有进程句柄），界面上的重启会被拒绝，需要用户自行重启该实例。验证：文档描述与 `/api/settings/rag`、`/api/control/rag/restart` 的实际行为一致
- [x] 6.3 在 `docs/README.md` 的索引中补上必要的说明（如需新增指南）；确认没有产生失效链接。验证：链接检查脚本对仓库内 Markdown 相对链接逐个判定存在性，无失效项

## 7. 集成验证

- [x] 7.1 运行 `openspec validate --all --strict`，确认变更与全部规格通过严格校验。验证：命令退出码为 0
- [x] 7.2 端到端跑一遍：启动主服务 → 不带口令访问 `/api/admin/config` 得 401 → 带口令得配置 → 在设置页改一次 LLM 模型 → 连通性测试成功 → 发起一次 chat 调用确认用新配置 → 踢掉该会话 → 重启 RAG 并确认 `/health` 恢复。验证：记录每一步的实际响应
- [x] 7.3 做一次"升级不破坏既有部署"检查：删除 `data/settings/settings.json` 后启动，确认自动生成口令、其余配置仍完全来自 `.env` 与默认值，且数字化人可正常连接与播报。验证：记录启动日志与 `/api/status`、`/api/admin/config` 的响应
- [x] 7.4 复核仓库状态：`git status --short` 中只剩本变更预期的新增与修改，`data/settings/` 不出现在其中。验证：逐项说明每一项归属

## 8. 回归修复（实施后续变更时实测发现）

- [x] 8.1 修复「运行期设置覆盖显式命令行参数」的缺陷。`apply_to_opt` 原本用**内置默认值**也无条件覆盖 `opt`，而 `tts.engine` 的默认值是 `edgetts`，于是 `--tts sapi`、`--TTS_RATE`、`--REF_FILE`、`--avatar_id` 四个命令行参数被静默改回去。发现场景：实施 `add-offline-tts-engine` 做端到端验证时传了 `--tts sapi`，服务却启动为 `edgetts`。修复为两条规则：**显式命令行参数优先**（新增 `cli_locked` 参数，由 `app.py` 扫描 `sys.argv` 得出并打日志），且**内置默认值不覆盖任何东西**（只应用来源为设置文件或环境变量的字段）。验证：新增 5 个单元测试覆盖"默认值不覆盖 CLI""显式设置生效""cli_locked 优先""写法容错""环境变量算显式"；端到端复核服务启动为 `TTS: sapi` 且日志打印被锁定的参数
- [x] 8.2 修复 `REF_TEXT` 被无条件清空的连带问题：未显式配置时原本把 `opt.REF_TEXT` 置为 `None`，现在未配置即不触碰。验证：单元测试断言未配置时 `REF_TEXT` 保持原值

## Workflow follow-up

- 用户确认设置页与口令行为可用后归档本变更：`openspec archive add-runtime-settings`。
- 归档后确认 `openspec/specs/` 下新增 5 个能力规格，且 `digital-human-api` 的「统一 JSON 响应格式与业务错误」已包含访问口令失败的例外场景。
