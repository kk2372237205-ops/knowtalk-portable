# KnowTalk 项目审计记录

> **审计日期**：2026-10-12
> **审计对象**：`D:\00-Project\KnowTalking-main`（KnowTalk 知课 · 实时交互数字人微课系统）
> **状态**：本次审计发现的问题**在本变更（`establish-docs-governance`）中一律未修复**。本变更只做文档与规范治理，代码级问题登记在文末的"后续变更清单"中。
>
> **后续更新（本审计之后的进展）**：鉴权已由 `add-runtime-settings` 变更落地（`server/auth.py` 的 `X-Admin-Token` 中间件）；
> 第 3.5 节的路径与跨域类问题（任意文件删除、上传文件名穿越、任意 `video_path`、`notifyurl` SSRF、CORS 通配来源）
> 已由 `harden-file-paths-and-cors` 变更修复，逐项状态见第 3.5 节的表。**仍未修复**的是该表最后一行的 `os.system`
> （现行号 `avatars/base_avatar.py:323`）。

---

## 1. 审计范围与方法

| 项目 | 范围 |
|---|---|
| 后端 | 根目录 4 个文件 + `server/` `streamout/` `tts/` `utils/` `rag_service/`，共 53 个自有 Python 文件 / 6627 行（排除 `cosyvoice_libs/` 与 `avatars/` 内的第三方代码） |
| 前端 | `web/` 下 16 个 HTML（6630 行）、自有 JS 与 `web/asr/` 子目录 |
| 文档 | `README.md`、`README-EN.md`、`docs/` 5 篇、`rag_service/README.md` |
| 仓库 | `.gitignore`、`.gitattributes`、`requirements*.txt`、`Dockerfile`、`start.bat`、`.env*`、Git 跟踪状态与提交历史 |

方法：逐文件通读源码并按行号取证；用 `git ls-files` / `git check-ignore` / `git lfs ls-files` 实测版本控制状态；用逐字节解码校验文件编码；对中英文页面做逐行比对统计重复度；对统计口径（类型注解、docstring、裸 `except` 等）做脚本化计数。

审计由三个并行的专项审计分别完成后汇总，本记录中的行号为审计时的代码位置。

---

## 2. 结论摘要

系统功能完整、架构分层大体成立（`routes → llm/tts → avatars → streamout` 的链路清晰，插件注册机制可用，RAG 以独立进程旁路集成、不侵入主链路），但**工程规范严重欠账**，并且存在**需要立即处理的安全问题**：

- **安全**：管理类接口全部无鉴权，CORS 通配来源且允许凭据；存在任意文件删除、上传文件名穿越、任意本地路径读取、SSRF 回调四处真实注入面。
- **工程规范**：全仓库 **0 个测试文件**，无 `pyproject.toml`、无 lint/type/format 配置、无 CI；类型注解完整率 10.0%；`print` 与 `logging` 双轨混用。
- **稳定性**：多处共享可变状态无锁；TTS 每次合成都泄漏一个未关闭的事件循环；存在无界队列；路由注册顺序导致批量接口可能被遮蔽。
- **文档**：`openspec/specs/` 此前为空；`docs/` 与 README 互不链接，文档不可达；接口文档只覆盖 61 个路由中的一小部分，且与实现存在明确矛盾。
- **仓库卫生**：根目录混入 8.5 MB 日志、1 MB 截图、28 MB 回收目录、183.7 MB 上传残留；`.gitignore` 有会误伤同名路径的裸目录规则。

正面结论见第 7 节。

---

## 3. 后端实测结论

### 3.1 分层与耦合

- **无 import 环**，但存在隐式双向耦合：`server/routes.py:109` 在文件中部才导入 `session_manager` 与 `avatar_routes`，而 `server/material_routes.py` 又反向依赖 `server/routes.py`。
- **import 副作用**：`rag_service/index_store.py:294` 与 `rag_service/api.py:16` 在导入阶段创建 Chroma 客户端并执行全量清洗，导入即产生 I/O 与网络行为。
- **全局可变状态**：`app.py:75-84` 有 6 处模块级可变全局（`opt`、`model`、`global_avatars`、`rag_process`、`rag_log_file`、`rtc_manager`），`main()` 通过 `global` 赋值。
- **注册表类别不一致**：`registry.py:8-14` 声明了 `output` 类别，实际实现使用 `streamout`；`stt`、`llm` 两个类别声明后为空。
- **RAG 隔离良好**：`rag_service` 是独立进程（`app.py:217` 用 `.venv-rag` 的解释器拉起），与主服务只通过 HTTP 通信，设计意图得到落实。

### 3.2 超长函数与复杂度

| 位置 | 长度 |
|---|---|
| `avatars/base_avatar.py:412` `process_frames` | 176 行 |
| `avatars/base_avatar.py` `_render_shape` | 134 行 |
| `app.py:270` `main` | 134 行 |
| `llm.py:50` `llm_response` | 82 行 |

`app.py` 的 `main()` 同时承担参数解析、RAG 子进程生命周期、模型加载、路由注册、CORS 配置、事件循环启动六件事。

### 3.3 异常处理

- **裸 `except` 共 16 处**，其中 `utils/material_manager.py` 占 15 处（第 169、184、231、256、283、288、292、401、403、417、420、450、463、501、542 行），`tts/tencent.py:108` 1 处。
- `utils/material_manager.py:184` 的 `except: pass` 直接吞掉删除失败，会让"先清空旧图片"的语义静默失效。

### 3.4 死代码与未使用项

- `app.py` 有 **13 个未使用的导入**（如 `flask`、`flask_sockets`、`numpy`、`BytesIO`、`re` 的部分用途等）；`app.py:73` 定义了 Flask `app` 对象但服务实际由 aiohttp 提供，属遗留死代码。
- 定义了但全项目无引用：`app.py:86 randN`、`app.py:168 _llm_configured`、`config.py:10 str_or_int`。
- 整个文件零引用：`utils/audio.py`、`utils/audioutil.py`、`utils/async_utils.py`。
- `rag_service/parsers.py:113` 与 `:138` 因 `rag_service/ingest.py:15` 的白名单（只允许 `.pdf`）而实际不可达。
- 未发现 `TODO` / `FIXME` 遗留标记。

### 3.5 安全问题（需要优先处理）

> 下表的"状态"列为**审计之后的复核结果**（2026-10-12 之后）。"已修复"的每一项都能在
> `harden-file-paths-and-cors`（路径与跨域）或 `add-runtime-settings`（鉴权）中找到对应实现与测试；
> 行号是审计当时的代码位置，修复后普遍已经位移。

| 严重度 | 位置（审计时） | 问题 | 状态 |
|---|---|---|---|
| 高 | `server/routes.py:475-481` | 声音删除接口直接把请求体的 `path` 拼成绝对路径并删除文件，无任何包含性校验 → 任意文件删除 | **已修复**（`harden-file-paths-and-cors`：`realpath`+`commonpath` 限定在 `data/voices/` 内） |
| 高 | `server/routes.py:388-397` | 上传声音时用请求提供的文件名直接落盘，未净化 → 可写出到目标目录之外 | **已修复**（同前：`validate_filename` + 扩展名白名单 + 落盘前包含性兜底） |
| 高 | `app.py:365-374` | 对**全部**路由配置 CORS：通配来源 + `allow_credentials=True`；结合下面的无鉴权问题，任意站点可读取管理数据 | **已修复**（同前：`server/cors_policy.py` 只信任 `CORS_ALLOWED_ORIGINS`，未配置则不安装跨域中间件） |
| 高 | 全部管理接口 | `/api/admin/*`、`/api/voice/delete`、`/api/avatar/task`、`/rag/admin/*` 均无任何鉴权 | **已修复**（`add-runtime-settings`：`server/auth.py` 的 `X-Admin-Token` 中间件） |
| 高 | `server/avatar_routes.py:178` + `server/task_manager.py:136` | `notifyurl` 由请求方任意指定，服务会向其发起 POST → SSRF | **已修复**（`harden-file-paths-and-cors`：协议白名单 + `AVATAR_NOTIFY_ALLOWED_HOSTS` 主机允许列表，缺省拒绝一切） |
| 中 | `server/avatar_routes.py:126` | 上传文件名直接拼进 `f"{uuid4()}_{filename}"`，`..` 片段可逐级上跳 | **已修复**（同前：落盘前取 `sanitize_filename` 后的 basename） |
| 中 | `server/avatar_routes.py:154` | `video_path` 为绝对路径时原样接受，可指向任意本地文件 | **已修复**（同前：`AVATAR_VIDEO_ROOTS` 允许根，服务自己的 `data/` 永远在内） |
| 中 | `avatars/base_avatar.py:297-298`（现行号 `:323`） | 使用 `os.system` 拼接字符串执行命令 | **未修复** —— 参数由服务自身与 `data/` 下的路径拼成，入口处已无请求可控字符串，但仍是应当改成 `subprocess` 列表调用的写法 |

**正面结论**：未发现硬编码明文密钥；`.env` / `.env.rag` 已正确忽略；仓库只跟踪 `.env.example` / `.env.rag.example` 占位示例。

**工作区风险（不涉及入库）**：`opencode.json` 含真实密钥 `sk-4d90ae43...c14935`。该文件已被 `.gitignore:39` 忽略、未入库，但明文留在工作区，**建议按已泄露处理并轮换**。（截至本次复核，该文件仍在工作区，此项**仍未处理**。）

### 3.6 并发与资源

- `server/session_manager.py` 的单例字典无锁；`server/routes.py:353` 在遍历 `session_manager.sessions` 的同时可能发生增删 → 可抛 `RuntimeError`。
- `utils/material_manager.py` 的 `_session_slides` / `_current_ppt_name` 全局状态与 JSON 读改写无锁。
- `rag_service/reranker.py:7-12`、`rag_service/chunking.py:13-17` 为无锁惰性单例。
- `tts/edge.py:49` 与 `tts/doubao.py:113` 每合成一段就 `new_event_loop()` 且从不 `close()` → 文件描述符泄漏。
- 无界队列：`tts/base_tts.py:27`、`streamout/rtmp.py:38`。
- `IndexStore.bm25_data` 常驻全量数据，随语料增长而线性占用内存。

### 3.7 功能性缺陷

- `tts/indextts2.py:11` 的 `IndexTTS2` 类**缺少 `@register` 装饰器**（其余 9 个 TTS 插件都有），`registry.create("tts", "indextts2")` 必然抛 `ValueError`，即 `--tts indextts2` 一定启动失败。
- `server/material_routes.py:151-152`：`PUT /api/slides/{id}` 早于 `PUT /api/slides/batch` 注册。**经实测不构成缺陷** —— aiohttp 3.14.3 的 `UrlDispatcher` 用完整的 canonical 路径作为 PlainResource 的索引键，`resolve()` 优先匹配更具体的静态路径，因此 `/api/slides/batch` 命中静态资源，与注册顺序无关（验证：同一组路由在两种注册顺序下解析结果一致）。最初的"路由遮蔽"判断已被推翻，不作为后续变更。
- `rag_service/api.py:73` 硬编码 `keep=3`，环境变量 `RAG_RERANK_TOP_K` 对 `/rag/ask` 完全无效。
- 知识库在线编辑后重新索引必然失败：编辑内容按 `admin_store.py:307` 落盘为 `.md` 版本文件，而 `ingest.py:28` 只允许 `.pdf` 进索引，`reindex=true` 时索引状态恒为 `failed`。
- **端口被占用时启动体验很差**（实测复现）：`app.py:403` 把 `run_server()` 放在模型加载与预热**之后**，而真正绑定端口发生在 `app.py:388` 的 `site.start()`。因此第二次启动一个已占用 8010 的实例时，会先花时间加载 204.7 MB 权重并完成预热，最后才抛 `OSError: [Errno 10048]` 原始 traceback 退出，既不友好也浪费一次完整加载。
- **绑定失败会遗留 RAG sidecar**：`app.py:280` 的 `start_rag_service()` 在绑定端口之前就已执行，而回收 RAG 子进程的 `stop_rag_service()` 只挂在 `appasync.on_shutdown`（`app.py:347`）上。服务从未启动成功时该回调不会触发，于是本次已经拉起的 RAG 进程会被留在后台。本次实测中 RAG 因 `.venv-rag` 损坏自行退出（`exit_code=1`）才没暴露，环境修好后这条路径会真实泄漏。
- **多轮对话记忆只存了用户提问，助手的回答从未写入**（实测复现）—— **已在本变更修复**：起因是验证 DeepSeek 配置时顺手跑了两轮真实调用，发现连续两轮 chat 请求后会话历史为 `["user", "user"]`，始终不存在 `assistant` 条目。根因在 `llm.py`：第 115 行的 `take_complete_sentences(result, force=True)` 会把 `result` 排空成空串，因此第 123 行的守卫 `if result.strip()` 恒为假，第 124 行写记忆的代码永远不会执行，第 125 行的历史截断同样是死代码。当时后果是数字人无法引用自己上一轮说过的话，"最近 10 轮"实际只有用户侧的 10 条提问，与 README 宣称的 P1 特性不符。
  修复方式：改用在整个流式过程中逐片累加的 `avatar_session.llm_text` 作为回答全文，并保留「被打断不入史」的守卫。修复后实测两轮历史角色序列为 `user, assistant, user, assistant`，`CHAT_HISTORY_ROUNDS=2` 时三轮问答后历史长度正确截断为 4 条，被中断的请求不产生 `assistant` 条目。同一变更还收紧了 `digital-human-api` 规格中对应要求，补上「回答写入会话记忆」「多轮之后历史交替出现」「被打断的回答不入史」三个场景，避免再次出现"实现不违反字面要求但特性其实是坏的"。
- **RAG 冷启动可能超过主服务的等待窗口**（实测复现）：`app.py:227-235` 最多等待 30 × 0.5 秒 = 15 秒确认 RAG 就绪，超时只记一条 `RAG 服务启动超时` 警告便继续启动主服务。本次实测该警告确实出现，而十几秒后 `/health` 与 `/api/status` 的 `rag_available` 均恢复正常——系统能自愈，但启动日志给出一条误导性的失败提示，使用者容易据此误判 RAG 没起来。

### 3.8 工程规范

| 指标 | 实测 |
|---|---|
| 函数总数 / 有完整类型注解 | 380 / 38 = **10.0%** |
| 函数 docstring | 124 / 380 = **32.6%** |
| 类 docstring | 8 / 34 = **23.5%** |
| 裸 `except` | 16 处 |
| 可变默认参数 | 6 处（`tts/base_tts.py:40`、`llm.py:50`、`avatars/base_avatar.py:129/133/137/155`） |
| `print` 调用 | 仅 `app.py` 就有 26 处；`tts/xtts.py:50`、`:63` 用 `print` 报错 |
| 日志体系 | 双轨：`utils/logger.py` 只注册文件 handler，与标准库 `logging` 混用 |
| 测试 | **0 个**测试文件 |
| 工具链配置 | 无 `pyproject.toml` / `setup.py` / ruff / flake8 / mypy / pytest 配置；`.github/` 只有一个 `FUNDING.yml`，无 CI |

### 3.9 依赖与构建

- 三份清单版本策略互相矛盾：`requirements.txt` 基本不锁版本；`requirements_cosyvoice.txt` 严格 `==` 钉版；`requirements-rag.txt` 用 `>=` 下界。
- 冲突与重复：`opencv-python` 与 `opencv-python-headless` 并存；`requirements_cosyvoice.txt` 的 `numpy==1.26.4` / `transformers==4.51.3` 与主清单的 `transformers==4.46.2` 冲突；`transformers`、`soundfile`、`fastapi`/`uvicorn` 在多个清单中重复声明。
- **缺失声明**：`rtmp_streaming`、`pyvirtualcam`/`pyaudio`、`azure-cognitiveservices-speech`、`gradio_client`、`dashscope`、`pywin32`、`requests`、`python-pptx`、`Pillow`，且 **`torch` 未在 `requirements.txt` 中声明**。
- `Dockerfile` 等同不可用：`Dockerfile:31` 的 `RUN source ~/.bashrc` 与 `:32-33` 的 `conda activate` 在 `RUN` 中均为空操作；conda 建好环境后未激活就 `pip install`（装进 base 环境）；`Copy ../nerfstream /nerfstream` 越出构建上下文；`CMD` 未复制任何应用源码，也没有 web/、RAG 或 DeepSeek 环境变量。

---

## 4. 前端实测结论

### 4.1 规模与内联比例

- 16 个 HTML 共 6630 行 / 303.7 KB；5 个自有 JS 共 991 行。最大的是 `web/knowtalk-chat.html`（1470 行 / 58.9 KB）与 `web/index.html`（1452 行 / 77.6 KB）。
- 内联比例很高：`index.html` 约 96%（style 392 行 + script 996 行）、`knowtalk-chat.html` 约 94%、`admin.html` 约 82%、`dashboard.html` 约 80%。
- 无 `package.json`、无 lockfile、无构建工具，第三方库全部走 CDN。

### 4.2 中英页面重复度（按非空行完全相同比对）

| 页面对 | 相同比例 |
|---|---|
| `webrtcapi.html` / `webrtcapi-asr.html` | 99% |
| `rtcpushapi.html` / `rtcpushchat.html` | 98% |
| `rtmpapi.html` / `rtmpchat.html` | 98% |
| `admin.html` / `admin-en.html` | 86% |
| `avatar.html` / `avatar-en.html` | 70% |
| `index.html` / `index-en.html` | **14%** |

前四组基本是复制粘贴，无任何 i18n 或模板机制，估算可去重约 1200 行。最后一组相反：`index.html` 已重写为 KnowTalk 版本（41 个 DOM id），`index-en.html` 仍是上游旧版（16 个 id，仍含 `audioFile` / `audiotypeVal` 之类的旧元素），属**严重过时未同步**。

### 4.3 硬编码

- 多数页面用 `window.location.hostname`，这一点是规范的。
- 但 SRS 端口写死：`web/whep.js:26`、`web/dashboard.html:761`、`web/rtcpushapi.html:113`、`web/rtcpushchat.html:113` 硬编码 `:1985`；`web/rtmpapi.html:52`、`web/rtmpchat.html:52` 硬编码 `:8080`；`web/index.html:22` 写死 `https://127.0.0.1:10095/`。
- `.env.rag.example` 内含 `D:/models/Qwen3-Embedding-4B`、`D:/models/bge-reranker-base` 这类本机绝对路径。
- 未发现被提交的前端密钥。

### 4.4 XSS 与依赖风险

- `web/index.html` 有 27 处 `innerHTML`，其中 `:596` 使用了 `escapeHtml`，但 `:1068`、`:1094`、`:1246`、`:1374` 等模板拼接未统一转义。
- `web/knowtalk-chat.html:1396` 把 `slide.name` 直接拼进 `onerror=` 属性，是**真实注入面**。
- `web/knowledge-base.html`（14 处）与 `web/avatar.html` 自带 `escapeHtml` 且用法一致，是做得好的部分。
- CDN 混用：`jquery-2.1.1`（2014 年版，6 个页面在用）、`jquery-3.6.0`、`bootstrap 5.3.0`；`http://cdn.sockjs.org/sockjs-0.3.4.js` 仍走**明文 HTTP**（混合内容风险）。

### 4.5 其他

- WebRTC 样板重复 5 份：`web/index.html:556`、`web/index-en.html:264`、`web/knowtalk-chat.html:860`、`web/client.js:54`、`web/whep.js:52`。其中 `web/client.js`（95 行）与 `web/whep.js`（75 行）有 **68 行逐字相同**，差异只在协商端点（`/offer` 与 `:1985/rtc/v1/whep`）与 sessionid 处理上。
- 错误处理不均：`web/rtmpapi.html`、`web/rtmpchat.html` 没有任何 `.catch`；`index.html` 25 处 fetch / 13 处 catch；`knowtalk-chat.html` 13 / 17。
- 全局污染：`web/asr/main.js` 有 83 条顶层 `var` / `function`（无 IIFE 或模块包裹），`web/voice-manager.js` 19 条。
- 前端同样**没有任何测试**。

---

## 5. 文档与仓库卫生实测结论

### 5.1 文档

| 文档 | 行数 | 实测状态 |
|---|---|---|
| `docs/api.md` | 177 | 过时：缺 `/llm_status`、`/api/status`、`/api/voices`、`/api/voice/*`、`/api/slides`、`/api/materials/*`、`/api/current_slide`、RAG 代理与跨域说明 |
| `docs/admin_api.md` | 122 | 与代码一致 |
| `docs/avatar_api.md` | 133 | 不完整：缺 `/api/avatars` 列举、缩略图、重命名、删除 |
| `docs/knowledge_base_management_plan.md` | 735 | 规划稿口吻，功能已落地（`web/knowledge-base.html`），无状态与日期标注 |
| `docs/rag_knowledge_base_technical方案.md` | 717 | 设计稿口吻，与实现存在 17 处偏差，无状态与日期标注 |

- 两篇文档的**文件名中英混排**（`rag_knowledge_base_technical方案.md`）。
- 实际注册路由共 **61 个**（主服务 38、RAG 服务 23），三篇接口文档合计覆盖不到三分之一。
- `README.md` 与 `README-EN.md` **都没有链接 `docs/` 下的任何文档**——文档事实上不可达。
- **文档与代码的明确矛盾**：`README.md` 与 `rag_service/README.md` 声称支持 `.pptx` / `.docx` / `.srt` / `.vtt` / `.md` / `.txt` 上传，而 `rag_service/api.py:36` 只放行 `.pdf`。
- `README-EN.md` 正文仍是上游 KnowTalking 的英文 README（Features 列 ernerf、Demo 链接、gitee 镜像、Docker 镜像、`Commercial Version` 一节），仅头部换成 KnowTalk 标识，无任何过时提示。
- 孤立资源：`assets/main.png`（181.6 KB）与 `assets/faq.md`（上游 ernerf 时代的 FAQ）此前无任何文档引用。

### 5.2 仓库卫生

| 项 | 大小 |
|---|---|
| `.venv-rag/` | 1549.3 MB（运行环境，**保留**） |
| `data/` | 270.1 MB，其中 `data/tmp/` 上传残留 183.7 MB（19 个 mp4） |
| `models/` | 204.7 MB（已忽略，**保留**） |
| `cosyvoice_libs/` | 107.3 MB（**保留**） |
| `.trash_cleanup_20260917/` | 28.2 MB（含 25.1 MB 日志与调试脚本） |
| `knowtalk.log` | 8.5 MB |
| `.numba_cache/` | 2.2 MB |
| `stuck_screenshot.png` | 1.06 MB |

- `.gitignore` 缺陷：第 10 行的 `data`（无斜杠，会匹配任意同名路径）、`*.mp4` 重复（第 5、15 行）、`rag_service/data/` 重复（第 23、31 行）、`_*.py` / `_*.png` 等通配会误伤合法文件、未覆盖调试截图。
- `.gitattributes` 只有 4 行 LFS 规则（`pth` / `pt` / `onnx` / `safetensors`），未覆盖 `*.mp4`、`*.pkl`、`*.fst` 等大二进制。
- 版本控制层面**是干净的**：`git ls-files` 未发现被跟踪的日志、虚拟环境或密钥文件；模型权重已正确走 Git LFS（`git lfs ls-files` 命中 `avatars/wav2lip/.../s3fd.pth`）。
- 提交信息符合 Conventional Commits + 中文描述，但**粒度粗**：`3e3f946` 一次提交 170 个文件，`84d5f3d` 一次提交覆盖 P0/P1/P2 全部优化，无法按项回滚；无 tag、无 CI、只有 main 分支。
- `start.bat` 结构清晰（`chcp 65001`、分步校验、RAG 缺失只提示不阻断），但使用系统 `python` 而非项目环境，且只校验 `aiohttp`、`aiortc`、`numpy` 三个包就认为依赖齐备。

---

## 6. 后续变更清单

以下问题**在本变更中未修复**，需要各自独立成变更。优先级依据影响面与修复成本排序。

### 本变更内已修复的两项

这两项属于文档与仓库规范治理本身的范围，因此随本变更一并处理：

| 问题 | 处理方式 |
|---|---|
| `start.bat` 以 GBK 保存，而脚本第 2 行就执行 `chcp 65001`，导致运行时所有中文提示显示为乱码（违反"文本文件统一 UTF-8"） | 转为 UTF-8（无 BOM）并保留 CRLF；`.gitattributes` 增加 `*.bat text eol=crlf`，避免批处理的 CRLF 被后续规范化破坏 |
| `.gitignore` 的 `data` 与 `models/` 是裸目录名，会匹配任意层级的同名路径，导致 **24 个合法源码文件从未进入版本库**：`server/__init__.py`、`tts/__init__.py`、`streamout/__init__.py`、`rag_service/__init__.py`、`avatars/wav2lip/models/{__init__,conv,syncnet,wav2lip,wav2lip_v2}.py`、`avatars/musetalk/models/{syncnet,unet,vae}.py` 以及若干 `__init__.py`／`__main__.py`。这会让全新克隆直接缺少被 import 的模块 | 忽略规则改为锚定形式（`/data/`、`/models/`、`/_*.py` 等），并把上述 24 个文件纳入版本控制 |

### P0 — 安全（建议立即处理）

| 建议变更 | 范围 | 依据 | 状态 |
|---|---|---|---|
| `harden-admin-endpoints` | 为 `/api/admin/*`、`/api/voice/*`、`/api/avatar/task`、`/rag/admin/*` 增加鉴权；CORS 从通配来源收敛到显式配置的可信来源 | `app.py:365-374`；`server/routes.py:531-541`、`:523-524` | **已完成**，拆成两个变更落地：鉴权 → `add-runtime-settings`；跨域收敛 → `harden-file-paths-and-cors`（`server/cors_policy.py`） |
| `fix-file-path-and-ssrf` | 声音删除限定在已登记声音目录内；上传文件名净化；`video_path` 限定在允许目录；`notifyurl` 改为管理员允许列表 | `server/routes.py:475-481`、`:388-397`；`server/avatar_routes.py:126`、`:154`、`:178`；`server/task_manager.py:136` | **已完成**（变更 `harden-file-paths-and-cors`）：共用判定原语 `utils/path_guard.py` + `utils/net_guard.py`；测试 `tests/test_path_guard.py`、`test_net_guard.py`、`test_voice_guard.py`、`test_avatar_guard.py`、`test_cors_policy.py`；变异检查 `tests/_mutation_check_path_guards.py` 19/19 抓住 |
| `rotate-leaked-key` | 轮换 `opencode.json` 中的明文密钥，并确认新密钥只存在于被忽略的本地文件 | 工作区实测 | **未完成** —— 该文件仍在工作区且仍含明文密钥 |
| （审计未登记，本次新增） | `avatars/base_avatar.py` 的 `os.system` 改列表式 `subprocess` 调用 | 本记录 3.5 | **未完成**，仍属 P0 遗留项 |

### P1 — 功能与稳定性

| 建议变更 | 范围 | 依据 |
|---|---|---|
| `fix-tts-registration` | 补上 `IndexTTS2` 的 `@register`，让 `--tts indextts2` 可用 | `tts/indextts2.py:11` |
| `fix-rerank-top-k-config` | 让 `RAG_RERANK_TOP_K` 生效，或删除该配置项 | `rag_service/api.py:73` |
| `fix-kb-inline-edit-reindex` | 打通在线编辑后的重新索引，或明确禁止编辑并告知用户 | `rag_service/admin_store.py:307`、`rag_service/ingest.py:28` |
| `fix-concurrency-and-resources` | 会话字典加锁与遍历保护；共享幻灯片状态加锁；无锁惰性单例；TTS 事件循环泄漏；无界队列加上限 | `server/session_manager.py`、`server/routes.py:353`、`utils/material_manager.py`、`rag_service/reranker.py:7-12`、`rag_service/chunking.py:13-17`、`tts/edge.py:49`、`tts/doubao.py:113`、`tts/base_tts.py:27`、`streamout/rtmp.py:38` |
| `improve-startup-failure-handling` | 把端口绑定提前到模型加载之前；绑定失败时给出中文可读提示而不是原始 traceback；启动流程任一步失败都要回收已拉起的 RAG 子进程；把 RAG 就绪等待从固定 15 秒改为带进度提示的更长等待或异步重试 | `app.py:280`、`:347`、`:388`、`:403`、`:227-235` |
| ~~`fix-chat-memory`~~ | ~~修复多轮记忆：让助手回答真正写入会话历史~~ —— **已完成**（变更 `fix-chat-memory`）：改用 `llm_text` 作为回答全文写入历史，并收紧 `digital-human-api` 规格。实测两轮历史为 `user, assistant, user, assistant` | `llm.py:105`、`:115`、`:123-125` |

### P1 — 工程基线

| 建议变更 | 范围 | 依据 |
|---|---|---|
| `establish-engineering-baseline` | 引入 `pyproject.toml`、ruff/format 配置、mypy（可先只覆盖新代码）、pytest 与最小测试集、CI；统一日志体系并清理 `print` | 本记录 3.8 |

### P2 — 依赖与构建

| 建议变更 | 范围 | 依据 |
|---|---|---|
| `unify-dependencies` | 三份清单统一版本策略、消除 `numpy`/`transformers`/`opencv` 冲突、补齐缺失声明（含 `torch`） | 本记录 3.9 |
| `fix-container-build` | 重写 `Dockerfile`：修正 `RUN` 中的环境激活、复制源码、安装依赖、暴露端口、给出可运行启动命令 | `Dockerfile:31-33`、`:53-55` |

### P2 — 前端治理

| 建议变更 | 范围 | 依据 |
|---|---|---|
| `refactor-frontend-shared-client` | 抽出公共 `webrtc-client.js` 消除 5 份样板；把内联脚本拆成模块；消除中英页面重复 | 本记录 4.1、4.2、4.5 |
| `fix-frontend-xss-and-cdn` | 统一转义策略、修掉 `onerror=` 拼接；升级 jQuery 2.1.1；替换明文 HTTP CDN | `web/index.html:1068/1094/1246/1374`、`web/knowtalk-chat.html:1396` |
| `sync-english-ui-and-readme` | 重写 `web/index-en.html` 与 `README-EN.md` 的完整英文版，与中文版保持一致 | 本记录 4.2、5.1 |

### P3 — 代码整理

| 建议变更 | 范围 | 依据 |
|---|---|---|
| `clean-dead-code-and-exceptions` | 删除未使用导入与无引用模块/函数；消除 16 处裸 `except`；修正 6 处可变默认参数 | 本记录 3.3、3.4 |
| `split-long-functions` | 拆分 `process_frames`、`_render_shape`、`main`、`llm_response`；把 `app.py` 的启动流程拆成独立模块 | 本记录 3.2、3.1 |
| `add-type-hints-and-docstrings` | 逐步补齐类型注解与 docstring，并把 mypy 覆盖范围收紧 | 本记录 3.8 |

---

## 7. 正面结论

- **密钥未入库**：`.env` / `.env.rag` 已正确忽略，仓库只跟踪 `.example` 占位文件；`.env.rag` 与 `.env.rag.example` 内容一致且不含真实凭据。
- **模型权重走 Git LFS**：`git lfs ls-files` 命中 `avatars/wav2lip/.../s3fd.pth`，大文件未以普通对象提交。
- **提交信息规范**：8 条提交全部使用 Conventional Commits 前缀 + 中文描述。
- **架构意图得到落实**：RAG 以独立进程旁路集成（`app.py:217`），`/human`、`/offer`、TTS 与 Avatar 主链路未被侵入；插件注册机制可用，新增 TTS/Avatar 不需要改主流程。
- **无 TODO/FIXME 遗留**：虽然死代码不少，但没有长期挂起的待办标记。
- **部分前端做得规范**：`web/knowledge-base.html` 与 `web/avatar.html` 自带统一的 `escapeHtml`；多数页面用 `window.location.hostname` 而非写死地址。
- **错误处理有基础**：主服务路由普遍用 `logger.exception` 记录并返回结构化错误，不是静默失败。

---

## 8. 本次审计未覆盖

- `avatars/` 与 `cosyvoice_libs/` 内的第三方源码（只确认其版本控制与 LFS 状态）。
- 实际运行时的性能与显存占用（只做静态审核）。
- 推理质量、TTS 音质、RAG 检索效果等需要实测数据集的评估。
- 前端在真实浏览器中的运行时行为（未做端到端测试）。

## 9. 路由 ↔ 能力规格覆盖矩阵

实际注册路由共 61 个（主服务 38 = `app.py` 2 + `server/routes.py` 19 + `server/material_routes.py` 9 + `server/avatar_routes.py` 8；RAG 23 = `rag_service/api.py` 4 + `rag_service/admin_api.py` 19）。已排除 2 个 `add_static`（`/` → `web/`、`/materials/images`）。**未覆盖数 0。**

| # | 方法 路由 | 能力规格 / Requirement |
|---|---|---|
| 1 | `POST /offer` | digital-human-api / WebRTC 会话建立接口、`/offer` 的可选会话参数 |
| 2 | `GET /record/{sessionid}` | digital-human-api / 录制控制与录像下载 |
| 3 | `POST /human` | digital-human-api / 文本驱动的 echo 模式、文本驱动的 chat 模式 |
| 4 | `POST /rag/ask` | digital-human-api / RAG 旁路服务代理 |
| 5–8 | `GET/POST/PUT/DELETE /rag/admin/{tail}` | digital-human-api / RAG 管理接口代理与超时码；knowledge-base-management / 管理接口的代理路径与超时契约 |
| 9 | `POST /humanaudio` | digital-human-api / 音频上传驱动 |
| 10 | `POST /set_audiotype` | digital-human-api / 自定义动作状态设置接口 |
| 11 | `POST /record` | digital-human-api / 录制控制与录像下载 |
| 12 | `POST /interrupt_talk` | digital-human-api / 打断参数与打断接口 |
| 13 | `POST /is_speaking` | digital-human-api / 说话状态与 LLM 文本查询 |
| 14 | `POST /llm_status` | digital-human-api / 说话状态与 LLM 文本查询 |
| 15 | `GET /api/admin/config` | digital-human-api / 管理配置与活跃会话查询 |
| 16 | `GET /api/admin/sessions` | digital-human-api / 管理配置与活跃会话查询 |
| 17 | `POST /api/voice/upload` | digital-human-api / 声音克隆样音管理 |
| 18 | `GET /api/voices` | digital-human-api / 声音克隆样音管理 |
| 19 | `POST /api/voice/apply` | digital-human-api / 声音克隆样音管理 |
| 20 | `POST /api/voice/delete` | digital-human-api / 声音样本删除 |
| 21 | `GET /api/status` | digital-human-api / 服务状态查询接口 |
| 22 | `GET /api/slides` | slide-material-management / 幻灯片列表与条目结构 |
| 23 | `POST /api/slides` | slide-material-management / 新增幻灯片 |
| 24 | `PUT /api/slides/{id}` | slide-material-management / 幻灯片信息更新 |
| 25 | `PUT /api/slides/batch` | slide-material-management / 幻灯片信息更新 |
| 26 | `DELETE /api/slides/{id}` | slide-material-management / 幻灯片删除 |
| 27 | `DELETE /api/slides` | slide-material-management / 幻灯片删除 |
| 28 | `POST /api/materials/upload-image` | slide-material-management / 素材上传请求约束、图片素材上传语义 |
| 29 | `POST /api/materials/upload-ppt` | slide-material-management / 素材上传请求约束、PPT 上传与分页输出 |
| 30 | `GET /api/current_slide` | slide-material-management / 幻灯片按会话隔离与当前幻灯片读写 |
| 31 | `GET /api/avatars` | avatar-management / 形象库列举与缩略图 |
| 32 | `GET /api/avatars/{avatar_id}/thumbnail` | avatar-management / 形象库列举与缩略图、`avatar_id` 合法性约束 |
| 33 | `PUT /api/avatars/{avatar_id}` | avatar-management / 形象重命名 |
| 34 | `DELETE /api/avatars/{avatar_id}` | avatar-management / 形象删除 |
| 35 | `POST /api/avatar/task` | avatar-management / 生成任务的必填参数与视频来源、可选参数与默认值、视频来源的落盘与路径解析 |
| 36 | `GET /api/avatar/task/{task_id}` | avatar-management / 生成任务状态流转与进度查询 |
| 37 | `DELETE /api/avatar/task/{task_id}` | avatar-management / 任务删除 |
| 38 | `GET /api/avatar/tasks` | avatar-management / 任务列表 |
| 39 | `GET /health` | rag-knowledge-base / RAG 服务健康检查 |
| 40 | `POST /rag/upload` | rag-knowledge-base / 资料上传只接受 PDF |
| 41 | `POST /rag/index` | rag-knowledge-base / 课程重新索引语义 |
| 42 | `POST /rag/ask` | rag-knowledge-base / 课程问答请求的字段约束、课程问答的响应结构 |
| 43 | `GET /rag/admin/courses` | knowledge-base-management / 课程列表与关键字搜索 |
| 44 | `POST /rag/admin/courses` | knowledge-base-management / 创建课程与 `course_id` 规则 |
| 45 | `GET /rag/admin/courses/{course_id}` | knowledge-base-management / 课程详情查询与更新 |
| 46 | `PUT /rag/admin/courses/{course_id}` | knowledge-base-management / 课程详情查询与更新 |
| 47 | `DELETE /rag/admin/courses/{course_id}` | knowledge-base-management / 课程删除与级联清理 |
| 48 | `GET /rag/admin/courses/{course_id}/documents` | knowledge-base-management / 课程文档列表与筛选 |
| 49 | `POST /rag/admin/courses/{course_id}/documents/upload` | knowledge-base-management / 文档上传与后台索引状态流转 |
| 50 | `POST /rag/admin/courses/{course_id}/reindex` | knowledge-base-management / 索引任务查询与课程级重新索引 |
| 51 | `GET /rag/admin/documents/{document_id}` | knowledge-base-management / 文档详情与索引失败原因可见 |
| 52 | `GET /rag/admin/documents/{document_id}/content` | knowledge-base-management / 文档内容读取与在线编辑 |
| 53 | `PUT /rag/admin/documents/{document_id}/content` | knowledge-base-management / 文档内容读取与在线编辑 |
| 54 | `DELETE /rag/admin/documents/{document_id}` | knowledge-base-management / 文档删除 |
| 55 | `GET /rag/admin/documents/{document_id}/download` | knowledge-base-management / 文档原文下载与 PDF 内嵌预览 |
| 56 | `GET /rag/admin/documents/{document_id}/preview` | knowledge-base-management / 文档原文下载与 PDF 内嵌预览 |
| 57 | `POST /rag/admin/documents/{document_id}/reindex` | knowledge-base-management / 索引任务查询与课程级重新索引 |
| 58 | `GET /rag/admin/documents/{document_id}/versions` | knowledge-base-management / 文档版本列表与版本恢复 |
| 59 | `POST /rag/admin/documents/{document_id}/versions/{version}/restore` | knowledge-base-management / 文档版本列表与版本恢复 |
| 60 | `GET /rag/admin/documents/{document_id}/chunks` | knowledge-base-management / 文档分片查看 |
| 61 | `GET /rag/admin/tasks/{task_id}` | knowledge-base-management / 索引任务查询与课程级重新索引 |

规格位置：`openspec/specs/<capability>/`（归档前位于 `openspec/changes/establish-docs-governance/specs/<capability>/`）。
