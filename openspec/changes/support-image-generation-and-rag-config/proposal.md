# 生成任务支持图片输入 + 知识库技术配置可查询

## 为什么

1. **工作台登录**：本地账号登录（`add-local-auth`）已实现，中间件对未登录且已建账号的
   所有 `.html` 页面统一 302 拦截，工作台 `/workbench.html` 已受保护。但工作台头部没有
   "当前账号 / 登出" 这类登录态 UI，且 SPA 内的接口调用一旦 401 不会自动跳登录。需要在前端
   补一层登录态自检与登出入口，让"进工作台必须先登录"这件事在界面上可见、可控。

2. **生成任务只收视频**：`avatar.html` 的"创建新生成任务"表单 `accept="video/*"`，只能上传
   视频来训练数字人。讲师手头常只有一张正面照，希望也能**上传图片**生成模型。
   musetalk 的 `genavatar` 已经支持单张图片（复制进 `full_imgs`），wav2lip 的 `video2imgs`
   仍只认视频——需要给 wav2lip 补一个图片分支。单张图片生成的会是**静态背景、嘴部由驱动视频
   驱动**的肖像形象（符合 wav2lip/musetalk 的渲染原理），这是图片输入的固有边界，需在界面上说明。

3. **知识库配置不可见**：RAG 用的向量库（Chroma + BM25 混合）、切分（jieba + 滑动窗口
   420/55）、重排（bge-reranker-base）、召回（vector10 + bm2510 → rerank3、阈值 0.55）全部
   硬编码在 `rag_service/config.py` 里，没有任何接口暴露。用户希望"从接口与参数里能看出用了
   什么技术"，需要新增一个配置查询接口并在知识库管理页展示。

## 做什么

- **登录态 UI（工作台）**：新增共享 `auth-guard.js`，在 `workbench.html` / `workflow.html`
  加载时拉 `/api/auth/session`，未登录直接跳 `/login.html?next=<当前路径>`；头部加"当前账号 +
  登出"。中间件服务端拦截保持不变。
- **图片生成**：`avatar.html` 的素材上传 `accept` 改为 `video/*,image/*`，文案改为
  "视频或图片"；后端 `create_avatar_task` 接受图片（沿用 `video_file` 字段，按扩展名判定）；
  `avatars/wav2lip/genavatar.py` 增加图片分支（单帧复制进 `full_imgs`），与 musetalk 对齐；
  任务列表、轮询逻辑不变。
- **RAG 配置接口**：sidecar 新增 `GET /rag/config`，返回向量库 / 嵌入模型 / 大模型 / 切分 /
  重排 / 召回 / 资料来源等配置；主服务 `server/routes.py` 加 `/rag/config` 代理；知识库管理页
  新增"技术配置"卡片展示。

## 不做什么

- 不做"图片生成可驱动说话"的魔法：单张图片只能产出静态背景形象，嘴部运动仍来自运行时的驱动视频。
- 不改 `genavatar` 的算法与 `load_gesture` 加载逻辑，只在入口增加图片分支。
- 不改 RAG 的检索/重排实现，只把既有配置**暴露**出来，不引入新的可调参数页面（参数仍走
  `.env.rag` / 设置页）。

## 影响

- 新增纯函数模块 `utils/media_type.py`（判定图片/视频），无新依赖。
- RAG 配置接口只读，不影响问答与索引链路。
- 图片生成对 wav2lip 是多一个分支，视频路径行为不变。
