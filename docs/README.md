# KnowTalk 文档索引

本目录存放**面向人**的材料：设计资料、运行与排障记录、评审与审计记录。

> **规范性内容不在这里。** 系统的接口契约、参数、返回结构、状态流转与各类约束，以 `openspec/specs/`
> 下的 OpenSpec 能力规格为单一来源。本目录的文档与规格不一致时，以规格为准。
>
> - 主规格路径：`openspec/specs/<capability>/spec.md`
> - 变更中的规格（尚未归档时）：`openspec/changes/<change-name>/specs/<capability>/spec.md`

## 本目录文档

| 文档 | 用途 | 状态 |
|---|---|---|
| [avatar-motion-selection.md](./avatar-motion-selection.md) | 人物建模与肢体动作的**技术选型报告**：三条路线的可行性、8 GB 显存与磁盘的真实约束、分步推荐方案与最小 PoC 清单。**含实施期的机制更正**（原建议的 `customopt` 路线已否决） | 部分实现 |
| [single-image-motion-research.md](./single-image-motion-research.md) | 单图驱动肢体摆动的**专项调研**：三种输入形态对比、8 GB 可跑方案的硬件与速度数据、"最自然"的五个决定因素、离线生成 + 运行时回放的落地架构与最小 PoC | 部分实现 |
| [knowledge-base-management-plan.md](./knowledge-base-management-plan.md) | 知识库管理功能的规划与设计资料（页面 `web/knowledge-base.html`、接口 `rag_service/admin_api.py`） | 已实现 |
| [rag-knowledge-base-design.md](./rag-knowledge-base-design.md) | RAG 知识库的技术方案与设计取舍；**与当前实现存在已知差异，见文档开头** | 部分实现 |
| [audit/2026-10-12-project-audit.md](./audit/2026-10-12-project-audit.md) | 项目审计记录：后端、前端、文档与仓库卫生的实测结论，以及后续变更清单 | 已完成 |
| [quiz-mode-manual-test.md](./quiz-mode-manual-test.md) | 提问模式的**人工验收清单**：必须在真实浏览器 + WebRTC 会话里走查的 6 组用例（普通模式回归、端到端、语音作答成功/失败、判分降级、精确绑定） | 待执行 |
| [gesture-mode-manual-test.md](./gesture-mode-manual-test.md) | 手势模式的**人工验收清单**：必须在真实浏览器 + WebRTC 会话里走查的 6 组用例（无手势回归、只播一遍、边说边做、循环接缝、错误拦截、多手势与缓存） | 待执行 |

## 本目录之外的文档

| 文档 | 用途 |
|---|---|
| [../README.md](../README.md) | 项目主入口：特性、快速开始、配置、架构、FAQ；含**运行期设置**说明（设置页 `/settings.html`，打开即用、无需口令），以及**提问模式**一节（用法、`STT_*` 配置、CPU 延迟实测量级、评判要点写法）与**手势模式**一节（用法、`[GESTURE:<id>]` 语义、素材目录约定、查询接口、已知限制） |
| [../openspec/changes/add-gesture-module/](../openspec/changes/add-gesture-module/proposal.md) | **手势模块**的设计与规格（变更中，尚未归档）：素材目录约定、`[GESTURE:<id>]` 标签语义、运行时帧源切换的设计决策（含三处并发安全风险）与任务清单。规范性内容以其中的 specs 为准 |
| [../README-EN.md](../README-EN.md) | 英文入口页（尚未与中文版完整同步） |
| [../rag_service/README.md](../rag_service/README.md) | RAG 旁路服务的独立安装、启动与调试；含模型离线下载与参数配置 |
| [../.dsh/README.md](../.dsh/README.md) | 开发工作流：两套代理技能集的职责分界、superpowers 的来源与版本、升级步骤、**硬性拦截（hooks）的三层机制、安装/卸载与失效排查**、Claude Code / OpenCode 接入方式 |

## 维护约定

- 新增文档后，请在本索引中补一行，并确认每一行的链接指向真实存在的文件。
- 设计类文档必须在开头标注**状态**与**最后核对日期**。
- 与实现不符且不再维护的文档应标注为已废弃并说明替代方案，或直接删除，不要静默保留。
- 文件名使用小写 ASCII（字母、数字、`-`、`_`），不要中英混排。
- 具体规则见 `openspec/specs/documentation-governance/` 能力规格。
