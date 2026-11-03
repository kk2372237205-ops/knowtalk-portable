# Change: add-image-to-3d-avatar

## Why

用户希望"上传一张图片 → 生成三维数字人 → 绑骨 → 用音频驱动嘴唇说话与手势动作"，
直接嵌进 KnowTalking 的讲课数字人流。这是一个会改变对外可观察行为的新功能
（新增上传入口、HTTP 接口、前端预览页），按工程工作流必须先有本变更。

关键现实约束：
- 本机没有可跑混元3D 的 GPU（本地需 ~29GB 显存），也没有现成的 Hunyuan3D API Key；
- 绑骨（AccuRig/RigNet）、音频驱动面部（Audio2Face / blendshape 求解）都需要额外二进制或商用授权。

因此本变更交付的是**完整可运行、可测试的管线骨架 + 接入点**，默认用 `mock` 后端
使整条流程（上传→生成→预览）无需任何外部模型即可跑通；真正的模型通过环境变量
接入，缺省时给出清晰错误而不是静默失败。

## What Changes

- 新增 `utils/img_to_3d.py`：管线编排器，按阶段分发到可插拔后端（三视图/生成/绑骨/动画）。
- 新增 `server/img_to_3d_routes.py`：aiohttp 路由（上传、生成、拉取 GLB 与清单、后端可用性）。
- 新增 `web/img-to-3d.html`：上传 + three.js 预览 + 动画清单时间轴；`index.html` 增加入口。
- 本地化 `web/vendor/three.min.js` 与 `web/vendor/GLTFLoader.js`（仓库禁用 CDN）。
- 全部落盘路径经 `utils/path_guard` 校验，复用 `json_ok/json_error` 信封。

## Impact

- **新增文件**：`utils/img_to_3d.py`、`server/img_to_3d_routes.py`、`web/img-to-3d.html`、
  `web/vendor/three.min.js`、`web/vendor/GLTFLoader.js`、`openspec/changes/...` 全套。
- **修改文件**：`app.py`（注册路由）、`web/index.html`（入口链接）、`web/vendor/README.md`（资源清单）。
- **不受影响**：既有的 2D 数字人渲染（wav2lip）、提问模式、RAG、TTS 逻辑。
- **实时驱动**：本变更只产出 GLB + 动画清单；把 3D 模型接入实时讲课时推流是独立后续变更
  （需 three.js/Babylon 渲染分支），不在本次范围。
- **依赖新增**：无 Python 依赖（aiohttp 已在 `requirements.txt`）。three.js 为前端静态资源。
