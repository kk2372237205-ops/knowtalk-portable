# Design

## Context

动机见 `proposal.md` —— Why。这里只记录方案设计所需的事实与约束。

### 现状事实（勘察结果）

- `openspec/` 已初始化（`schema: spec-driven`），`openspec/specs/` 只有 `.gitkeep`，无任何能力规格。
- `docs/` 下 5 篇文档，规模与实测状态：

| 文档 | 行数 | 实测状态 |
|---|---|---|
| `docs/api.md` | 187 | 过时：只覆盖 `/offer`、`/human`、`/humanaudio`、`/interrupt_talk`、`/is_speaking`、`/record`、`/set_audiotype`，缺 `/llm_status`、`/api/status`、`/api/voices`、`/api/voice/*` |
| `docs/admin_api.md` | 122 | 与代码一致（`/api/admin/config`、`/api/admin/sessions`） |
| `docs/avatar_api.md` | 133 | 不完整：缺 `/api/avatars`、缩略图、重命名、删除 |
| `docs/knowledge_base_management_plan.md` | 735 | 规划稿口吻，功能已落地（`web/knowledge-base.html`），无状态/日期标注 |
| `docs/rag_knowledge_base_technical方案.md` | 864 | 设计稿口吻，与实现有偏差，无状态/日期标注 |

- 实际路由：主服务 38 个（`app.py:348-349`、`server/routes.py:519-541` 含 `/rag/admin/{tail}` 的 4 种方法、`server/material_routes.py:149-157`、`server/avatar_routes.py:216-223`），RAG 服务 23 个（`rag_service/api.py` 4 个、`rag_service/admin_api.py` 19 个），合计 61 个。三篇接口文档合计覆盖不到三分之一。
- 已确认的文档与代码矛盾：`rag_service/api.py:36` 只接受 `.pdf`，而 `README.md:28`、`rag_service/README.md:38` 声称支持 pptx/docx/srt/vtt/md/txt。设计稿推荐 BGE 嵌入 + LangChain 分片，实现使用 Ollama `qwen3-embedding:4b` 与自研分片。
- `README.md` 与 `README-EN.md` 都不链接 `docs/`；`README-EN.md` 正文仍是上游 LiveTalking 内容（Features 列 ernerf/musetalk/wav2lip、Docker Run、Commercial Version），无过时提示。`assets/main.png`、`assets/faq.md` 无任何文档引用。
- 文件编码经逐字节校验：`docs/*.md`、`README*.md`、`app.py` 等均为合法 UTF-8 无 BOM；`start.bat` 不是 UTF-8。文件名中英混排仅 `docs/rag_knowledge_base_technical方案.md`。
- 仓库卫生：`knowtalk.log` 8.5 MB、`stuck_screenshot.png` 1.06 MB、`.trash_cleanup_20260917/` 28.2 MB、`.numba_cache/` 2.2 MB、`.venv-rag/` 1549.3 MB、`data/tmp` 上传残留 183.7 MB。`.gitignore` 存在 `data`（缺斜杠）、`*.mp4` 重复、`rag_service/data/` 重复、`_*.py` / `_*.png` 可能误伤合法文件、未覆盖截图。`.env.rag` 与 `.env.rag.example` 内容相同且未被跟踪（无泄露）；`opencode.json` 未被跟踪但含真实密钥。
- 约束：本变更不修改任何运行时代码；`requirements*.txt`、`Dockerfile`、`start.bat` 本次只记录问题。

### 代码审计结论（本变更不修复，仅用于划定后续变更）

审计覆盖 53 个自有 Python 文件 / 6627 行、16 个 HTML 6630 行与其他前端资源。

| 维度 | 实测结论 |
|---|---|
| 分层与耦合 | 无 import 环；`server/routes.py:109` 底部导入与 `material_routes → routes` 构成隐式双向耦合；`rag_service/index_store.py:294`、`api.py:16` 有 import 副作用（建 Chroma 客户端 + 全量清洗） |
| 超长函数 | `base_avatar.py:412 process_frames` 176 行、`_render_shape` 134 行、`app.py:270 main` 134 行、`llm.py:50 llm_response` 82 行 |
| 异常处理 | 裸 `except` 16 处（`utils/material_manager.py` 占 15 处：169/184/231/256/283/288/292/401/403/417/420/450/463/501/542；`tts/tencent.py:108`） |
| 可变默认参数 | 6 处（`tts/base_tts.py:40`、`llm.py:50`、`avatars/base_avatar.py:129/133/137/155`） |
| 死代码 | `app.py` 未用导入 13 个；`app.py:86 randN`、`app.py:168 _llm_configured`、`config.py:10 str_or_int` 无引用；`utils/audio.py`、`utils/audioutil.py`、`utils/async_utils.py` 全项目零引用 |
| 安全 | `server/routes.py:475-481` 任意文件删除；`:388-397` 上传文件名可穿越写盘；`server/avatar_routes.py:126` 未净化文件名、`:154` 任意本地 `video_path`、`:178` + `server/task_manager.py:136` SSRF；管理类接口零鉴权；`app.py:365-371` CORS 通配且 `allow_credentials=True`；`avatars/base_avatar.py:297-298` `os.system` 字符串拼接 |
| 并发与资源 | `SessionManager` 单例字典无锁且 `routes.py:353` 边遍历边改；`utils/material_manager.py` 全局幻灯片状态与 JSON 读改写无锁；`rag_service/reranker.py:7-12`、`chunking.py:13-17` 无锁惰性单例；`tts/edge.py:49`、`tts/doubao.py:113` 每段合成 `new_event_loop()` 从不关闭；无界队列 `tts/base_tts.py:27`、`streamout/rtmp.py:38` |
| 功能缺陷 | `tts/indextts2.py:11` 的 `IndexTTS2` 缺 `@register`，`--tts indextts2` 必然抛 `ValueError` |
| 工程规范 | 380 个函数中类型注解完整 38 个（10.0%）；函数 docstring 124/380（32.6%）；`app.py` 26 处 `print`；无 `pyproject.toml`/lint/type/test 配置；**全仓库 0 个测试文件** |
| 前端 | 16 个 HTML 中英重复（admin 86%、avatar 70%、webrtcapi/webrtcapi-asr 99%），`index-en.html` 仍是上游旧版（与 `index.html` 仅 14% 相同）；WebRTC 样板 5 份，`web/client.js` 与 `web/whep.js` 有 68 行逐字相同；`web/index.html:1068/1094/1246/1374` 未统一转义、`web/knowtalk-chat.html:1396` 把数据拼进 `onerror=`；`http://cdn.sockjs.org/sockjs-0.3.4.js` 走明文 HTTP |
| 依赖与构建 | 三份 requirements 版本策略互相矛盾（无锁 / `==` 钉版 / `>=` 下界）；`opencv-python` 与 `opencv-python-headless` 并存；`numpy`、`transformers` 版本冲突；`torch` 未在 `requirements.txt` 声明；`Dockerfile` 因 `conda activate` 在 `RUN` 中无效、`Copy ../nerfstream` 越出构建上下文而等同不可用 |

正面结论：未发现硬编码明文密钥入库，`.env`/`.env.rag` 已正确忽略，模型权重已走 Git LFS（`git lfs ls-files` 命中 `avatars/wav2lip/.../s3fd.pth`），提交信息符合 Conventional Commits。

## Goals / Non-Goals

**Goals:**

- 让 `openspec/specs/` 成为项目规范性内容的单一来源，并为已实现能力建立可校验的规格基线。
- 把 `docs/` 从"事实的第二份拷贝"改造成只承担操作与开发指南，并让文档从 `README` 可直达。
- 用可校验的 Requirement/Scenario 固化文档与仓库卫生规则，而不是写成散文式规范。
- 清除可再生的仓库垃圾并修正忽略规则缺陷，使 `git status` 的干净是真实的。

**Non-Goals:**

- 不修改任何运行时代码、接口签名、依赖版本或部署方式。
- 不重写 `README-EN.md` 或 `index-en.html`（本变更只标注状态，重写需要独立变更）。
- 不重构前端（抽公共 WebRTC 客户端、引入 i18n/构建工具属于后续变更）。
- 不修复任何安全、并发、性能或死代码问题（记录为后续变更，见文末）。
- 不清理 `.venv-rag/`、`models/`、`cosyvoice_libs/`、`data/avatars/` 等运行依赖与素材目录。

## Decisions

### 决策 1：按能力切分规格，而不是按文件或做成一个巨型规格

采用 7 个能力目录，每个 `specs/<capability>/spec.md`。备选方案与取舍：

- 单一大 `spec.md`：文件会超过千行，无法按能力评审，也无法被后续变更精准修改。拒绝。
- 按原 `docs/` 文件一一对应：`api.md` 与 `admin_api.md` 同属主服务接口，拆开会导致同一能力被两个规格描述。拒绝。
- 按代码模块切（`routes`、`avatar_routes`、`material_routes`）：模块是实现的划分，接口会随重构搬家，规格不应随实现走。拒绝。

能力边界取"稳定的系统行为"：`documentation-governance`、`repository-hygiene`、`digital-human-api`、`avatar-management`、`slide-material-management`、`rag-knowledge-base`、`knowledge-base-management`。

### 决策 2：基线规格以代码为准，方案稿只贡献约束

`docs/rag_knowledge_base_technical方案.md` 与 `docs/knowledge_base_management_plan.md` 是设计稿，其中选型建议、伪代码、备选方案对比属于论证性内容，不进规格；只有可校验的行为约束进规格，且必须以当前代码行为为准（例如"只接受 PDF"而不是方案稿的"支持多格式"）。设计稿本身在 `docs/` 保留为设计资料，但必须标注状态与核对日期。

### 决策 3：`docs/` 只保留指南，接口文档整体迁出

迁移映射：

| 现有文档 | 去向 |
|---|---|
| `docs/api.md` | 内容进入 `digital-human-api` 规格；文件删除，接口清单在规格中补全 |
| `docs/admin_api.md` | 同上合并进 `digital-human-api`（管理与会话查询同属主服务接口） |
| `docs/avatar_api.md` | 内容进入 `avatar-management` 规格并补全形象库接口；文件删除 |
| `docs/rag_knowledge_base_technical方案.md` | 重命名为 ASCII 文件名，标注状态，保留在 `docs/` 作为 RAG 设计资料；可校验约束进入 `rag-knowledge-base` 规格 |
| `docs/knowledge_base_management_plan.md` | 同上，标注为已实现，保留为设计资料；接口契约进入 `knowledge-base-management` 规格 |
| 新增 | `docs/README.md` 文档索引；`docs/` 下的安装与运维指南按需要保留/补充 |

选择"接口文档删除"而不是"保留但标记为过时"：因为保留第二份字段表就是保留一个必然漂移的副本，而这正是本次要消除的问题。

### 决策 4：治理规则写成 Requirement/Scenario 而非散文

`documentation-governance` 与 `repository-hygiene` 描述的是仓库状态约束，但它们同样可被客观校验（文件编码可测、链接可测、`git check-ignore` 可测、`git ls-files` 可测、镜像可构建）。因此把它们写成带 Scenario 的 Requirement，使规则可以被检查和评审，而不是只能靠自觉。

### 决策 5：仓库清理采用"删除 + 修规则"双管齐下

只补 `.gitignore` 不删文件，会让本地目录继续混乱且 `git status` 的干净是假象；只删文件不修规则，会很快重新堆积。因此两者都做。删除范围严格限定为可再生产物；对每个待删路径先解析绝对路径并确认在项目根内再删除。

### 决策 6：`.gitignore` 采用目录锚定与去重

把 `data` 改为 `/data/`，合并重复的 `*.mp4` 与 `rag_service/data/`，收窄 `_*.py` / `_*.png` 这类会误伤合法文件名的通配（改为只匹配根目录下的调试产物），并补充调试截图等本次发现的产物。每条修改后用 `git check-ignore`/`git status` 验证：目标被忽略、合法文件不被忽略。

### 决策 7：英文版收敛为准确的入口页，不做完整翻译

重写 `README-EN.md` 为完整英文版需要产品文案决策，不适合夹在文档治理变更里。但原文件正文是上游 LiveTalking 的英文 README（Features 列 ernerf、Demo 链接、gitee 镜像、Docker 镜像、"Commercial Version" 一节），会把上游能力当成本系统能力呈现，仅加状态标注不足以消除误导。

因此本变更采取的做法是：把 `README-EN.md` 收敛为一页**准确**的英文入口页——声明同步状态、给出与实现一致的能力清单、指向中文版与 OpenSpec 能力规格作为权威来源，并保留上游署名。完整英文翻译与 `index-en.html` 的同步仍登记为后续变更。

### 决策 8：基线规格以"当前实测行为"为准，已知缺陷不写成要求

基线规格是"系统今天实际表现成什么样"的契约，不是"我们希望它表现成什么样"的愿望清单。因此：

- 对已知缺陷，分两种情况处理：
  - **接口正常路径**：按当前实际行为描述（例如上传落盘到 `./data/tmp/`、`video_path` 为绝对路径时直接使用、`notifyurl` 会向任意给定地址发通知）。修复缺陷的后续变更 MUST 用 MODIFIED 更新对应 Requirement，使规格与代码一起演进。
  - **纯粹的安全姿态**（管理接口鉴权、CORS 收紧到可信来源、回调地址允许列表、删除路径限定在声音目录内）：基线规格**不写这条要求**，由修复变更连同新的 Requirement 一起引入。理由：这类约束要么与现状相反（写了就是假规格），要么只能写成"必须保持当前的不安全配置"（写了就是糟糕的规格）。
- 反例（不采用）：现在就把"必须拒绝越界路径""必须按允许列表校验回调地址"写成 Requirement。这样归档后主规格会声明一个系统并不具备的行为，而本变更按范围又不修代码，等于人为制造规格与实现不一致。

### 决策 9：审计发现的问题不在本变更内修复，但必须被登记

安全、并发、死代码、依赖与构建问题都需要改代码，与"仅文档与规范治理"的范围冲突。它们在本设计文档中作为后续变更清单登记，在 `tasks.md` 中作为"输出审计报告文档"的输入。

## Risks / Trade-offs

- **[反向工程的规格可能偏离真实行为]** → 每个基线规格都由直接阅读源码得出并逐条给出代码位置；在规格对应的任务中要求人工复核，归档前用 `openspec validate --strict` 校验。
- **[规格数量多，评审成本高]** → 5 个基线规格只写可观察契约，不写实现细节；评审可按能力逐个进行，治理与卫生两个规格优先。
- **[删除文件误删有用内容]** → 只删除日志、截图、回收目录、缓存、`__pycache__`、`data/tmp` 上传残留这些可再生产物；删除前逐个解析绝对路径并确认位于项目根内；`.venv-rag/`、`models/`、`cosyvoice_libs/`、`data/avatars/` 明确不动。
- **[接口文档删除后短期内没有替代入口]** → 同一变更内先建立规格、再更新 `README.md` 文档导航与 `docs/README.md`，最后才删除原文件。
- **[设计稿被误认为权威规格]** → 两篇设计稿在迁移时明确标注状态与核对日期，并写明规范性内容以对应能力规格为准。
- **[已发现的安全问题被误读为已修复]** → 设计文档与任务中明确标注"未修复"，并要求在审计报告文档中单独列出。

## Migration Plan

1. 建立 7 个能力规格（本变更的 delta 规格），`openspec validate --strict` 通过。
2. 迁移 `docs/`：先新增 `docs/README.md` 索引与状态标注，再删除迁出的接口文档。
3. 更新 `README.md`（文档导航、API 章节入口指向能力规格）与 `README-EN.md`（同步状态标注、修正指向上游的徽章/链接说明）。
4. 修正 `rag_service/README.md` 使之与实现一致（仅 PDF）。
5. 仓库清理：删除可再生产物，修正 `.gitignore` 与 `.gitattributes`，逐条用 `git check-ignore`/`git status` 验证。
6. 归档变更，使 7 个能力规格进入 `openspec/specs/`。

回滚：全部改动都是文件级且不含代码，可按提交粒度 `git revert`；被删除的运行产物会由服务重新生成。

## 后续变更清单（本变更不实施）

| 优先级 | 后续变更 | 依据 |
|---|---|---|
| P0 | 管理接口鉴权与 CORS 收敛；修复任意文件删除、上传文件名穿越、任意本地路径与 SSRF | `server/routes.py:475-481`、`:388-397`、`app.py:365-371`、`server/avatar_routes.py:126/154/178`、`server/task_manager.py:136` |
| P0 | 轮换 `opencode.json` 中的明文密钥 | 工作区实测 |
| P1 | 修复 `tts/indextts2.py:11` 缺失 `@register` 导致 `--tts indextts2` 不可用 | 审计结论 |
| P1 | 让 `RAG_RERANK_TOP_K` 真正生效（`rag_service/api.py:73` 当前硬编码 `keep=3`），或删除该配置项避免误导 | 审计结论 |
| P2 | 打通或明确禁止知识库在线编辑后的重新索引：编辑内容落盘为 `.md`，而索引只接受 `.pdf`，导致 `reindex=true` 时索引状态恒为 `failed` | 审计结论 |
| P1 | 并发与资源治理：会话字典加锁与遍历保护、共享幻灯片状态加锁、无锁惰性单例、TTS event loop 泄漏、无界队列 | `server/session_manager.py`、`utils/material_manager.py`、`rag_service/reranker.py:7-12`、`tts/edge.py:49`、`tts/base_tts.py:27` |
| P1 | 工程基线：引入 `pyproject.toml`、lint/format/type 配置与最小测试集（当前 0 测试） | 审计结论 |
| P2 | 依赖清单统一版本策略、消除冲突、补齐缺失声明、重写 `Dockerfile` | 审计结论 |
| P2 | 前端治理：抽公共 WebRTC 客户端、消除中英重复、统一转义、升级 jQuery、替换明文 HTTP CDN、重写 `index-en.html` 与 `README-EN.md` | 前端审计结论 |
| P3 | 清理死代码与裸 `except`、拆分超长函数、统一日志体系、补齐类型注解 | 审计结论 |

## Open Questions

- 两篇设计稿（RAG 技术方案、知识库管理方案）最终是长期保留在 `docs/` 作为设计资料，还是在对应能力规格稳定后归档删除？本变更按"保留并标注状态"处理，不阻塞后续决策。
