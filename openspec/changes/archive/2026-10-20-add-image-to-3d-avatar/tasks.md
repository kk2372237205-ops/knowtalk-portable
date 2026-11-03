# Tasks

- [x] 1. 写 `utils/img_to_3d.py`：阶段化编排器 + 配置解析 + mock GLB 生成 + 动画清单
- [x] 2. 写 `server/img_to_3d_routes.py`：上传 / 生成 / 结果 / 清单 / 状态路由，全部 path_guard
- [x] 3. 下载 `web/vendor/three.min.js` 与 `web/vendor/GLTFLoader.js`，登记 SHA256 到 README
- [x] 4. 写 `web/img-to-3d.html`：上传 + three.js 预览 + 动画时间轴；`index.html` 加入口
- [x] 5. 在 `app.py` 于 `setup_routes` 前注册 `setup_img_to_3d_routes(app)`
- [x] 6. 写 `tests/test_img_to_3d.py` 与 `tests/test_img_to_3d_routes.py`
- [x] 7. `openspec validate --all --strict` 与 `pytest` 全绿后归档本变更
- [x] 8. 更新 `README.md` / `docs/` 口径，记一条 memory
