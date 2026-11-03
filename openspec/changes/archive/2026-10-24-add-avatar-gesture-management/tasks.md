# Tasks

## 1. 先写测试（红灯）

- [x] 1.1 `tests/test_gesture_management.py`：列举含 `frames`/`width`/`height`；预览返回 `image/webp` 且落缓存；重新生成条件；改名写入 `gesture.json` 且保留其它键；空白/超长名被拒；删除移除目录并清缓存；非法 `avatar_id`/`gesture_id` 一律 400 且不动文件
- [x] 1.2 跑该测试确认红灯

## 2. 素材层（utils/gestures.py）

- [x] 2.1 `gesture_frames()` / `gesture_info()`：帧列表与首帧尺寸
- [x] 2.2 `set_gesture_label()`：只改 `label`，保留其它键
- [x] 2.3 `delete_gesture()`：删目录
- [x] 2.4 `gesture_preview_file()`：按（帧数 + 最新 mtime）指纹生成/复用 WebP 动画，清理同手势的旧缓存

## 3. 接口层（server/avatar_routes.py）

- [x] 3.1 列举返回 `frames`/`width`/`height`
- [x] 3.2 `GET .../preview`
- [x] 3.3 `PUT .../{gesture_id}`
- [x] 3.4 `DELETE .../{gesture_id}`
- [x] 3.5 注册三个路由

## 4. 页面（web/avatar.html）

- [x] 4.1 形象卡新增「动作」按钮（带该形象手势数量）
- [x] 4.2 手势面板：预览动画、显示名、帧数、改名、删除（二次确认）、复制 `[GESTURE:id]`
- [x] 4.3 无手势时提示「暂无手势」

## 5. 验证

- [x] 5.1 `pytest tests/` 全量通过
- [x] 5.2 `openspec validate add-avatar-gesture-management --strict` 通过
- [x] 5.3 `web/avatar.html` 标签平衡；真实素材（Mr.Chen 的 4 套手势）能出预览
