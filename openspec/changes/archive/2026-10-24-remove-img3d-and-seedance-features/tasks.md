# Tasks

## 1. 下线锁定测试（先红）

- [x] 1.1 新建 `tests/test_removed_features_absent.py`：断言 `utils.img_to_3d`、
      `utils.seedance_video`、`server.img_to_3d_routes`、`server.seedance_routes`
      模块不可导入；`web/img-to-3d.html`、`web/seedance.html` 不存在；
      `web/vendor/three.min.js`、`web/vendor/GLTFLoader.js` 不存在；
      `web/index.html` 不含 `img-to-3d` / `seedance` 卡片；
      `app.py` 源码不含 `setup_img_to_3d_routes` / `setup_seedance_routes` 注册；
      `openspec/changes/add-seedance-gesture-video/` 目录不存在。
- [x] 1.2 运行确认新测试**失败**（代码尚在），记录红灯。（实测 2 failed / 1 passed，
      失败点：`/api/seedance/`、`/api/img3d/` 仍在 seedance.html / img-to-3d.html 中）

## 2. 删除功能代码（转绿）

- [x] 2.1 删除 `utils/img_to_3d.py`、`utils/seedance_video.py`
- [x] 2.2 删除 `server/img_to_3d_routes.py`、`server/seedance_routes.py`
- [x] 2.3 `app.py`：删除图片转 3D 注册段（含注释，原 504-506 行）与
      Seedance 注册段（含注释，原 508-510 行）
- [x] 2.4 删除 `web/img-to-3d.html`、`web/seedance.html`
- [x] 2.5 `web/index.html`：删除「图片转 3D」与「Seedance 手势视频」两张卡片
      （原 497/498 行）
- [x] 2.6 删除 `web/vendor/three.min.js`、`web/vendor/GLTFLoader.js`
- [x] 2.7 删除 `scripts/seedance_gesture_test.py`
- [x] 2.8 删除测试：`tests/test_img_to_3d.py`、`tests/test_img_to_3d_hy3d.py`、
      `tests/test_img_to_3d_routes.py`、`tests/test_seedance_video.py`
- [x] 2.9 删除未归档变更 `openspec/changes/add-seedance-gesture-video/`（整目录）
- [x] 2.10 运行 `tests/test_removed_features_absent.py` 确认转绿
      （6 passed, 29 subtests passed）

## 3. 文档引用清理

- [x] 3.1 `README.md`：删除「图片转 3D 数字人」表格行（原 32 行）与
      「图片转 3D 真实后端」说明段（原 34 行）
- [x] 3.2 `docs/gesture-prompt-pack.md`：改写第 3-4 行（通路说明、与
      `utils/seedance_video.py::GESTURE_PROMPTS` 的对比）、第 430 行
      （`/api/seedance/gestures` 数据源说明）为不依赖已删代码的表述；
      第 402 行保留（指外部图生视频服务档位，与已删平台功能无关）
- [x] 3.3 `web/vendor/README.md`：删除 three.min.js / GLTFLoader.js 两行资产
      说明与来源/哈希两行（原 23-24 / 37-38 行）

## 4. 全量验证

- [x] 4.1 后端全量 pytest：**716 passed, 29 subtests passed, 0 failed**
      （对账：基线 747 − 37〔4 个删除的测试文件〕+ 6〔新锁定测试〕= 716）
- [x] 4.2 `openspec validate --all --strict`：26 passed, 0 failed（归档前）；
      归档后 24 passed, 0 failed
- [x] 4.3 前端 `node --test tests/js/*.test.js`：187/188 通过；
      唯一失败为 `hook-gate-shell.test.js:199`「在其它项目中不注入、不拦截」，
      系记忆在案的预存在失败，与本变更无关
- [x] 4.4 冒烟：`import app` 成功且源码中无两个 setup 函数残留
      （app.py 的 main() 在 `__main__` 保护下，模块级导入即充分）

## 5. 归档

- [x] 5.1 `openspec archive remove-img3d-and-seedance-features -y`：
      首次因「移除某规格最后一个 Requirement」被拒，按提示在
      `.openspec.yaml` 增加 `retire_capabilities: true` 后成功；
      6 个 Requirement 全部移除，`openspec/specs/img-to-3d-avatar/` 退役删除，
      变更移入 `archive/2026-10-24-remove-img3d-and-seedance-features/`
- [x] 5.2 更新 `.workbuddy/memory/2026-10-24.md` 记录下线结果；
      最终回复已告知用户 `data/img3d/`、`data/seedance/` 已无代码引用、可自行删除
