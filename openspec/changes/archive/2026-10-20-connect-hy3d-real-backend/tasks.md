# Tasks

- [x] 1. 重写 `utils/img_to_3d.py::_generate_via_hy3d_api`：真实异步 submit→poll→download，读 `HY3D_API_KEY`/`HY3D_API_BASE`/`HY3D_MODEL`，鉴权与字段容错
- [x] 2. `server/img_to_3d_routes.py::img3d_generate` 改用 `loop.run_in_executor` 运行同步 `run_pipeline`，避免阻塞事件循环
- [x] 3. 写 `tests/test_img_to_3d_hy3d.py`：用 `unittest.mock` 桩 submit/poll/download 三段，验证真实后端写出下载内容（非立方体）且字段容错生效
- [x] 4. `openspec validate connect-hy3d-real-backend --strict` 与 `pytest tests/test_img_to_3d.py tests/test_img_to_3d_routes.py tests/test_img_to_3d_hy3d.py` 全绿
- [x] 5. 复核 `README.md` / `docs/` 口径（真实后端配置项），归档本变更
