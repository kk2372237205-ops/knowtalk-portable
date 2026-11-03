# 任务

## 1. OpenSpec 变更

- [x] 1.1 建 `support-image-generation-and-rag-config`（2 能力 4 场景），`validate --strict` 通过

## 2. 工作台登录态 UI（前端增强）

- [x] 2.1 新增 `web/auth-guard.js`：加载时拉 `/api/auth/session`，未登录跳 `/login.html?next=<当前路径>`；导出 `mountUserBadge(容器)` 渲染"当前账号 + 登出"
- [x] 2.2 `workbench.html`、`workflow.html` 引入 `auth-guard.js`，头部加"当前账号 + 登出"入口

## 3. 生成任务支持图片（后端 + 前端）

- [x] 3.1 新增 `utils/media_type.py`：`is_image_file` / `is_video_file` / `is_media_file`（纯函数，可单测）
- [x] 3.2 `avatars/wav2lip/genavatar.py`：图片分支——`video_path` 为图片时复制为 `full_imgs/00000000.png` 单帧，与 musetalk 对齐
- [x] 3.3 `server/avatar_routes.py`：`create_avatar_task` 接受图片（沿用 `video_file` 字段，按扩展名判定），缺两者时报错文案更新为"视频或图片"
- [x] 3.4 `web/avatar.html`：素材上传 `accept` 改为 `video/*,image/*`，文案"视频或图片文件"

## 4. 知识库技术配置暴露（后端 + 前端）

- [x] 4.1 `rag_service/api.py` 新增 `GET /rag/config`，从 `config.py` 读取向量库/嵌入/大模型/切分/重排/召回/资料来源并装配
- [x] 4.2 `server/routes.py` 新增 `GET /rag/config` 代理到 sidecar
- [x] 4.3 `web/knowledge-base.html` 新增"技术配置"卡片，拉 `/rag/config` 展示

## 5. 验证

- [x] 5.1 `tests/test_rag_config.py`：配置结构含各分组、关键值与 `config.py` 常量一致
- [x] 5.2 `tests/test_media_type.py`：`is_image_file` / `is_video_file` 判定正确
- [x] 5.3 后端全量测试通过；`openspec validate --all --strict` 全绿
- [x] 5.4 手动核对：工作台未登录跳登录页；上传图片可建任务；知识库页出现技术配置卡片
