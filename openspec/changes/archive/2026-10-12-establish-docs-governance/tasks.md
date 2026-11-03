# Tasks

## 1. 规格基线复核

- [x] 1.1 逐个能力复核本变更的 5 个基线规格（`digital-human-api`、`avatar-management`、`slide-material-management`、`rag-knowledge-base`、`knowledge-base-management`）：把每个 Requirement 对照源码实际行为核一遍，修正任何与代码不符的措辞；对无法在代码中定位的 Requirement 直接删除。验证：每个 Requirement 都能指向具体文件位置，且 `openspec validate establish-docs-governance --type change --strict` 通过
- [x] 1.2 核对规格与实际路由集合是否一致：以 `server/routes.py:519-541`、`server/material_routes.py:149-157`、`server/avatar_routes.py:216-223`、`app.py:348-349`（主服务 38 个路由对象）、`rag_service/api.py`、`rag_service/admin_api.py`（RAG 23 个路由）为基准，确认 61 个路由全部被某个能力规格覆盖，且规格中没有不存在的端点。验证：输出一份"路由 ↔ Requirement"对照清单，未覆盖数为 0
- [x] 1.3 复核 `docs/rag_knowledge_base_technical方案.md` 与 `docs/knowledge_base_management_plan.md` 中"设计稿主张但实现未采纳"的条目（如 BGE 嵌入、LangChain 分片、多格式上传、资料未涉及时的固定回答），确认它们没有被写成已实现的行为。验证：清单中每一条都能在规格或文档状态标注中找到归宿

## 2. 文档索引与迁移

- [x] 2.1 新增 `docs/README.md`：列出 `docs/` 下全部文档、每篇一句话用途、并写明"规范性内容以 `openspec/specs/` 对应能力规格为准"。验证：索引中每一行链接指向真实存在的文件
- [x] 2.2 把 `docs/rag_knowledge_base_technical方案.md` 重命名为全小写 ASCII 文件名，并在文档开头加状态栏（状态：已实现/部分实现、最后核对日期、规范性内容以 `rag-knowledge-base` 规格为准），同时更新全部引用。验证：仓库中对旧路径的引用为 0，新路径可被 `docs/README.md` 打开
- [x] 2.3 把 `docs/knowledge_base_management_plan.md` 重命名为全小写 ASCII 的 `docs/knowledge-base-management-plan.md` 并加同样的状态栏（状态：已实现，对应页面 `web/knowledge-base.html`，规范性内容以 `knowledge-base-management` 规格为准）。验证：文档开头存在状态与日期，且指向正确的能力规格
- [x] 2.4 删除已迁入规格的 `docs/api.md`、`docs/admin_api.md`、`docs/avatar_api.md`，并确认它们的内容已在 `digital-human-api`、`avatar-management` 规格中完整覆盖（含原文档有而规格遗漏的参数）。验证：三个文件不存在，且按原文档逐项抽查 10 个字段都能在规格中找到
- [x] 2.5 修正 `rag_service/README.md`：把"支持 `.pptx`、`.pdf`、`.docx`、`.srt`、`.vtt`、`.md`、`.txt`"改为与 `rag_service/api.py:36` 一致的"当前只接受 `.pdf`"，并说明其它格式走 PPT 演示库；连带修正同文档里用 `.pptx` 演示上传的 curl 示例。验证：`rag_service/README.md` 中不再出现与实现矛盾的支持格式列表

## 3. README 与文档可达性

- [x] 3.1 更新 `README.md`：新增"文档导航"章节指向 `docs/README.md`，并在现有的功能/架构内容处把规范性细节改为指向对应能力规格；同时标注 `README-EN.md` 的同步状态。验证：从 `README.md` 出发能在两次点击内到达任一 `docs/` 文档与能力规格
- [x] 3.2 处理 `README-EN.md`：在开头明确标注与中文版的同步状态及落后范围，并修正徽章、Demo 链接中仍指向上游 `lipku/LiveTalking` 的描述，使其不再把上游功能当作本系统功能呈现。验证：文档开头存在同步状态说明，且不再出现 ernerf / Commercial Version 等与本仓库无关的功能承诺
- [x] 3.3 处理孤立资源：`assets/main.png` 与 `assets/faq.md` 要么被某篇文档引用，要么删除；同时确认新加的状态栏与索引没有产生失效链接。验证：写出一个检查脚本或命令，对仓库内 Markdown 相对链接逐个判断存在性，结果为空

## 4. 仓库卫生

- [x] 4.1 删除可再生的运行产物：`knowtalk.log`、`stuck_screenshot.png`、`.trash_cleanup_20260917/`、`__pycache__/`（含各子目录）、`.numba_cache/`。删除前逐个 `Resolve-Path` 确认位于项目根内；`.venv-rag/`、`models/`、`cosyvoice_libs/`、`data/avatars/`、`avatars/` 明确不动。验证：`git status --short` 中不再出现被删除项，且服务仍能按 `README.md` 的步骤启动
- [x] 4.2 清理上传残留：`data/tmp/` 下的 19 个 mp4（约 183.7 MB）确认属于已上传素材的临时副本后删除，保留目录本身。验证：`data/tmp/` 体积回到可忽略量级，PPT/素材上传功能不受影响
- [x] 4.3 修正 `.gitignore`：把 `data` 改为 `/data/`，合并重复的 `*.mp4` 与 `rag_service/data/`，把 `_*.py` / `_*.png` / `_*.npy` / `_*.pkl` 收窄为只匹配根目录调试产物，补充 `stuck_screenshot.png` 之类的调试截图模式。验证：对一条目标路径与一条合法的 `_xxx.py` 候选文件分别执行 `git check-ignore -v`，前者命中、后者不命中
- [x] 4.4 补全 `.gitattributes` 的 LFS 覆盖范围（在现有 `*.pth`、`*.pt`、`*.onnx`、`*.safetensors` 基础上评估 `*.pkl`、`*.bin`、`*.fst`、`*.mp4`）。验证：`git lfs ls-files` 与 `git check-attr filter -- <样例文件>` 的结果符合预期
- [x] 4.5 复核"仓库根目录只保留项目资产"这条要求：逐项列出根目录内容并归入源码/配置/依赖清单/文档/许可五类，把无法归类的项删除或移入忽略范围，并在 `README.md` 的目录结构章节同步反映最终结构。验证：根目录清单每一项都有归类，且 `README.md` 目录结构与实际一致

## 5. 审计记录输出

- [x] 5.1 输出 `docs/audit/` 下的项目审计记录，内容包含：本次审计的范围与方法、后端（分层耦合、超长函数、异常处理、死代码、安全、并发与资源、工程规范、依赖与构建）与前端（重复度、内联规模、XSS 面、CDN、全局污染）的实测结论、正面结论，以及逐条给出证据位置的问题清单。验证：文档中每条结论都带文件位置或命令输出依据，且不出现无证据的判断
- [x] 5.2 在审计记录中单列"后续变更清单"，按优先级给出建议的独立变更名称与范围，并明确标注这些问题在本变更中**未修复**。验证：清单覆盖 `design.md` 中登记的全部条目，且每条的优先级与依据位置齐全

## 6. 集成验证

- [x] 6.1 运行 `openspec validate --all --strict`，确认变更与全部规格通过严格校验。验证：命令退出码为 0，无 findings
- [x] 6.2 运行 `openspec list --specs`，确认 7 个能力在归档前处于变更内、归档后出现在 `openspec/specs/`。验证：能力目录数量与名称符合预期
- [x] 6.3 做一次跨文档一致性抽查：随机选取 5 个接口，确认其参数与返回结构在能力规格中的描述与 `server/routes.py` 的实际实现一致，且 `docs/` 中不存在第二份字段表。验证：抽查结果全部一致，无重复定义
- [x] 6.4 做一次"干净工作区"检查：`git status --short` 中只剩本次变更预期新增/修改的文件，没有残留的运行产物。验证：逐项说明 `git status` 每一项的归属

## Workflow follow-up

- 在项目评审要求满足后归档本变更：`openspec archive establish-docs-governance`，使 7 个能力规格由变更 delta 晋升为 `openspec/specs/` 的主规格。
- 归档后确认 `openspec/specs/` 下 7 个 `spec.md` 均存在，且 Purpose 不是 `TBD` 占位。
