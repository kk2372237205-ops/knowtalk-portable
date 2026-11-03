# Proposal

## Why

项目已经积累了约 6600 行自有 Python、6600 行前端与 30+ 个 HTTP 接口，但文档处在"有文档、无体系"的状态：

- `openspec/` 已初始化，但 `openspec/specs/` 是空的——OpenSpec 目前没有承载任何项目知识，文档全部散落在 `docs/`。
- 5 篇 `docs/` 文档彼此不成体系：3 篇接口文档（`api.md`、`admin_api.md`、`avatar_api.md`）只覆盖了实际 61 个路由（主服务 38 个、RAG 服务 23 个）中的一小部分；2 篇是已经落地却仍以"规划方案"口吻书写、且没有状态/日期标注的长文档。
- `README.md` 与 `README-EN.md` 都没有链接 `docs/` 下的任何文档，文档事实上不可达；`README-EN.md` 正文仍是上游 LiveTalking 内容，没有过时提示。
- 文档与代码已经出现可验证的偏差：`rag_service` 实际上只接受 `.pdf`（`rag_service/api.py:36`），而 `README.md:28` 与 `rag_service/README.md:38` 都声称支持 pptx/docx/srt/vtt/md/txt。
- 仓库根目录混入了可再生的运行产物：`knowtalk.log`（8.5 MB）、`stuck_screenshot.png`（1.06 MB）、`.trash_cleanup_20260917/`（28.2 MB）、`__pycache__/`、`.numba_cache/`、`.idea/`，以及 `data/tmp` 下 183.7 MB 上传残留；`.gitignore` 存在 `data`（缺斜杠）、重复条目、`_*.py` 误伤合法文件、未忽略截图等缺陷。

本次变更把项目文档收敛到 OpenSpec 规格作为单一来源，为现有能力建立可校验的行为规格基线，并建立文档与仓库卫生规则，使后续任何改动都有明确的落点。

## What Changes

- 新增能力规格 `documentation-governance`：定义文档的唯一存放位置与单一来源、编码与命名、状态标注、链接可达性、文档与代码一致性、README 结构、规格与操作指南的职责边界。
- 新增能力规格 `repository-hygiene`：定义禁止入库的产物、`.gitignore`/`.gitattributes` 要求、依赖清单规范、启动脚本与容器镜像可复现要求、敏感信息不得入库。
- 为 5 个已实现的能力建立反向工程的行为规格基线（原 `docs/` 内容迁入并补齐）：
  - `digital-human-api`：主服务 HTTP 接口契约（`/offer`、`/human`、`/humanaudio`、`/interrupt_talk`、`/is_speaking`、`/llm_status`、`/record`、`/set_audiotype`、`/record/{sessionid}`、`/api/admin/*`、`/api/status`、`/api/voices`、`/api/voice/*`）。
  - `avatar-management`：Avatar 生成任务与形象库管理（`/api/avatar/*`、`/api/avatars*`）。
  - `slide-material-management`：幻灯片与素材库管理（`/api/slides*`、`/api/materials/*`、`/api/current_slide`）。
  - `rag-knowledge-base`：RAG 旁路服务的检索、重排、生成与引用契约（`/health`、`/rag/ask`、`/rag/upload`、`/rag/index`）。
  - `knowledge-base-management`：知识库课程/文档管理 API 与页面（`/rag/admin/*`）。
- 补齐文档缺口：此前无文档或被部分记录的接口（主服务 38 个路由对象、avatar 8 个、slide/material 9 个、RAG 基础 4 个与管理 19 个）纳入对应能力规格。
- 文档可达性：新增 `docs/README.md` 文档索引；`README.md` 增加文档导航入口；`README-EN.md` 明确标注为待同步/归档状态；`rag_service/README.md` 校正为与服务实现一致（仅 PDF）。
- 仓库清理：删除可再生的运行产物（日志、截图、回收目录、缓存、`__pycache__`、`data/tmp` 上传残留），并修正 `.gitignore` 的现存缺陷。
- **BREAKING**：无。本次不改动任何运行时行为、接口签名或依赖版本；仅改变 `docs/` 布局与 `openspec/specs/` 的内容。

## Capabilities

### New Capabilities

- `documentation-governance`: 项目文档必须存放在何处、如何命名与编码、如何标注状态、如何保持与代码一致，以及 README、API 规格、操作指南三者的职责边界与链接要求。
- `repository-hygiene`: 哪些文件禁止进入版本库、`.gitignore`/`.gitattributes` 必须覆盖哪些模式、依赖清单如何声明、启动脚本与容器镜像必须可复现、敏感信息如何隔离。
- `digital-human-api`: 主服务对外 HTTP 接口的统一响应格式、会话寻址、文本/音频输入、打断、录制、状态查询与声音克隆接口的行为契约。
- `avatar-management`: Avatar 生成任务的创建/查询/删除、形象库的列举/缩略图/重命名/删除，以及形象目录结构校验与回退规则。
- `slide-material-management`: 幻灯片条目的增删改查、素材上传（图片/PPT）与转换、以及 `[PPT:N]` 标签驱动的当前幻灯片联动规则。
- `rag-knowledge-base`: RAG 旁路服务的健康检查、文档上传与分片、混合检索与重排、带引用的答案生成，以及"资料未涉及"时的回答约束。
- `knowledge-base-management`: 知识库课程与文档的管理接口（课程 CRUD、文档上传/预览/编辑/删除/重编索引/版本恢复、任务状态查询）及其页面行为。

### Modified Capabilities

无。`openspec/specs/` 当前为空，本次是首次建立规格基线，不存在被修改的既有能力。

## Impact

- **受影响的文件**
  - 新增：`docs/README.md`、`openspec/specs/<7 个能力>/spec.md`（归档后）。
  - 重写/迁移：`docs/api.md`、`docs/admin_api.md`、`docs/avatar_api.md`、`docs/rag_knowledge_base_technical方案.md`、`docs/knowledge_base_management_plan.md`。
  - 修改：`README.md`、`README-EN.md`、`rag_service/README.md`、`.gitignore`、`.gitattributes`。
  - 删除：`knowtalk.log`、`stuck_screenshot.png`、`.trash_cleanup_20260917/`、`__pycache__/`、`.numba_cache/`、`data/tmp/*` 残留。
- **不受影响**：`app.py`、`server/`、`rag_service/*.py`、`tts/`、`streamout/`、`utils/`、`web/` 的任何代码与运行时行为；`requirements*.txt` 与 `Dockerfile` 本次只记录问题，不在本变更内修改。
- **用户可见变化**：`docs/` 目录结构变化，原 3 篇接口文档的路径与形式改变；`README` 增加文档导航。无接口变化。
- **流程影响**：后续功能变更必须遵循 `documentation-governance` 的落点规则；当前未文档化的代码级问题（见 design.md 的"审计发现与后续变更"）需要在独立变更中处理。
