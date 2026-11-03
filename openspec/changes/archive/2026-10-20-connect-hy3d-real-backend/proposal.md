# Proposal: connect-hy3d-real-backend

## Why

`add-image-to-3d-avatar` 落地时，`generate` 阶段的真实后端（`hy3d_api`）只是一个**未验证的占位骨架**：endpoint 是猜的 `api.hunyuan.cloud.tencent.com/v1/images/generations`，且假设一次同步请求就能拿到模型文件。联网核实后，混元3D 官方 API（腾讯云 `api.ai3d.cloud.tencent.com`）是**异步**的——`POST /v1/ai3d/submit` 拿 JobID，再 `GET /v1/ai3d/query` 轮询，完成才返回模型文件。占位骨架既连不上真实服务，协议也完全错。用户已确认走云端官方 API（本机 8GB 显存跑不了本地 Hunyuan3D-2.1），需要把骨架替换为可工作的真实异步调用。

## What Changes

- 重写 `utils/img_to_3d.py::_generate_via_hy3d_api`：按腾讯云 OpenAI 兼容异步接口实现 `submit → poll → download`，读取 `HY3D_API_KEY`（鉴权容错 `sk-` 直填或 `Bearer`）、`HY3D_API_BASE`（默认 `https://api.ai3d.cloud.tencent.com`）、`HY3D_MODEL`（默认 3.1），对返回字段做多路径容错提取。
- `server/img_to_3d_routes.py::img3d_generate`：用 `loop.run_in_executor` 运行同步 `run_pipeline`，避免 3D 生成（分钟级）阻塞 aiohttp 事件循环。
- 单元测试：用 `unittest.mock` 替身桩 `urllib` 的 submit/poll/download 三段响应，验证真实后端写出的是下载内容（而非 mock 立方体），且字段容错生效。

## Impact

- 对外行为变化：`IMG3D_GENERATE_BACKEND=hy3d_api` + `HY3D_API_KEY` 配置后，生成阶段真实调用混元3D，产出由图片重建的 3D 网格（非占位立方体）；未配 Key 仍显式报错，不静默降级。
- 不影响：`mock` 默认路径、三视图/绑骨/动画阶段、上传与下载路由、前端协议（仍同步等 generate 返回，由 executor 在后台轮询）。
- 依赖新增：无新 pip 依赖（复用标准库 `urllib` / `asyncio`）。
- 配置新增（环境变量，不入库）：`HY3D_API_KEY`、`HY3D_API_BASE`、`HY3D_MODEL`。
