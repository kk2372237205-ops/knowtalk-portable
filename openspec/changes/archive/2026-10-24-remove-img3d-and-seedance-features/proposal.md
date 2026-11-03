# 下线「图片转 3D 数字人」与「Seedance 手势视频」两个功能

## Why

用户 2026-10-24 明确要求下线两个功能，原话：

> 图片转 3D / 上传图片生成三维数字人 / Seedance 手势视频 / 原图片生成带手势演讲视频 /
> 这两个功能不要了。

两个功能均已上线并暴露在首页，但用户不再需要：

- **图片转 3D**（`web/img-to-3d.html`）：上传图片 → 三视图/生成/绑骨/动画 → 浏览器
  three.js 预览。规格 `openspec/specs/img-to-3d-avatar/` 已进入基线（6 个 Requirement），
  下线它 = 改变对外可观察行为，必须通过本变更的 REMOVED delta 完成。
- **Seedance 手势视频**（`web/seedance.html`）：上传原图 + 选手势 → tu-zi.com 聚合的
  doubao-seedance 图生视频 → 平台内播放。它的 OpenSpec 变更
  `add-seedance-gesture-video/` **创建后从未归档**，即其规格从未进入基线，
  下线它只需删除该未归档变更目录 + 删除功能代码，无需 REMOVED delta。

删除而非"入口隐藏"的理由：用户要的是功能下线，保留死代码会让后续每次排查都要
先排除这些干扰项（与 2026-10-21 `remove-dead-code` 变更同一逻辑）。

## What Changes

1. **删除图片转 3D 功能全部代码**：
   - `utils/img_to_3d.py`、`server/img_to_3d_routes.py`（5 个 `/api/img3d/*` 路由）；
   - `app.py` 中 `setup_img_to_3d_routes(appasync)` 注册段（含注释）；
   - `web/img-to-3d.html` 页面与 `web/index.html` 首页「图片转 3D」卡片；
   - `web/vendor/three.min.js`、`web/vendor/GLTFLoader.js` 两个仅被该页面引用的
     vendor 文件，并同步 `web/vendor/README.md` 的资产清单；
   - `tests/test_img_to_3d.py`、`tests/test_img_to_3d_hy3d.py`、
     `tests/test_img_to_3d_routes.py`。
2. **删除 Seedance 手势视频功能全部代码**：
   - `utils/seedance_video.py`、`server/seedance_routes.py`（5 个 `/api/seedance/*` 路由）；
   - `app.py` 中 `setup_seedance_routes(appasync)` 注册段（含注释）；
   - `web/seedance.html` 页面与 `web/index.html` 首页「Seedance 手势视频」卡片；
   - `scripts/seedance_gesture_test.py`（该功能的独立验证脚本）；
   - `tests/test_seedance_video.py`；
   - 未归档变更目录 `openspec/changes/add-seedance-gesture-video/` 整体删除
     （从未进基线，删除后 `openspec validate` 应无残留引用）。
3. **基线规格下线**：对 `openspec/specs/img-to-3d-avatar/spec.md` 应用
   `## REMOVED Requirements` delta，移除全部 6 个 Requirement；归档时
   `specs/img-to-3d-avatar/` 整个目录被删除。
4. **文档引用清理**：
   - `README.md`：删除功能表格行与「图片转 3D 真实后端」说明段；
   - `docs/gesture-prompt-pack.md`：第 3-4 行（通路说明与 `utils/seedance_video.py`
     对比）、第 402 行（Seedance 档位）、第 430 行（`/api/seedance/gestures` 数据源）
     改写为不依赖已删代码的表述——该文档本身是手势素材制作指南，仍然存活。
5. **保留不动**：
   - `openspec/changes/archive/2026-10-20-add-image-to-3d-avatar/` 与
     `2026-10-20-connect-hy3d-real-backend/`：归档变更是不可变历史；
   - `docs/single-image-motion-research.md`：手势模块设计文档，不含这两个功能的依赖；
   - 运行时数据 `data/img3d/`、`data/seedance/`：留在磁盘，已无代码引用，
     由用户自行决定是否删除（在最终交付说明中告知）。

## Impact

- **对外可观察行为**：`/api/img3d/*`（5 个路由）、`/api/seedance/*`（5 个路由）从
  **404 变为不存在**（路由注册被摘除）；`/img-to-3d.html`、`/seedance.html` 页面
  与首页两张卡片消失。其余接口（`/human`、`/api/quiz/*`、手势编排等）不变。
- **前端 vendor 体积**：`web/vendor/` 减少约 700 KB（three.min.js 589 KB +
  GLTFLoader.js 94 KB）；Bootstrap/jQuery 等其余资产不受影响。
- **不受影响**：实时朗读（wav2lip）、提问模式、PPT 切页、手势模块
  （`add-gesture-module`）、`data/avatars/Mr.Chen/gestures/` 现有四套手势素材
  均不依赖这两个功能的任何代码。
- **测试**：删除 4 个测试文件；新增 `tests/test_removed_features_absent.py`
  作为"下线锁定"测试，断言模块/页面/卡片/路由注册均不存在，防止回潮。
