# 合并课程知识库与 PPT 库，收敛上传入口

## Why

用户反馈「知识库和 PPT 库这两个库帮我合并一下，合并成一个包含课程文件和 PPT 的知识库，但是要分类好」，并要求「把素材准备这一栏去除掉，让用户直接到知识库里面来进行上传/设置」。

现状是资料散落在三处：首页控制台有「课程知识库 / 素材上传 / PPT 分页标签命名」三张上传类卡片，`/ppt-library.html` 又是独立的 PPT 库页面，`/knowledge-base.html` 只管 RAG 课程文档。同一件事（准备课程资料）要跑三个地方，且首页被一次性准备动作占满。

## What Changes

- **`/knowledge-base.html` 升级为统一的「课程知识库」页**：页面内分三个分区——课程文档（现有 RAG 功能原样保留）、PPT 课件（从 `/ppt-library.html` 迁入）、图片素材（从首页素材上传卡迁入）。PPT 上传、网格预览、改名、删除整册、分页标签命名全部在此完成。
- **`/ppt-library.html` 退役删除**：站内所有入口（首页工作流段、页脚、README）改指 `/knowledge-base.html`；`/api/slides`、`/api/materials/*` 等后端 API 契约不变。
- **首页控制台移除「素材准备」区**：课程知识库、素材上传、PPT 分页标签命名三张卡及相关 JS 从首页删除；讲稿区的「选择 PPT 插入标签」（`tagPptSelect`）保留——那是讲授时操作，不是准备动作。
- **首页 Hero 下方能力条移除**（3 种口型驱动 / 6 种 TTS / 3 路推流 / 提问式课堂四项数据条）：属营销展示层，随本次入口收敛一并精简。

## 非目标

- 不改任何后端 API 契约（`slide-material-management`、`knowledge-base-management` 两个规格的接口行为零变化）。
- 不把 PPT 挂到 RAG 课程实体下（slides 按 `ppt_id` 全局分组的模型不变）；「分类好」由页面内三分区实现。
- 不动首页开播区与讲授区（连接、预设文本、提问模式）。

## Impact

- Affected specs: 新建能力 `course-library-page`（ADDED）。
- Affected code: `web/knowledge-base.html`（扩展）、`web/index.html`（删区/删函数/改链接）、`web/ppt-library.html`（删除）、`web/knowtalk-chat.html`（链接已指向知识库页，仅确认）、`README.md`（页面说明表合并）。
