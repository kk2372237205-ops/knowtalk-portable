# 数字人管理页的动作管理与预览

## Why

用户要求：「数字人管理那个部分可以用来管理动作，并且可以预览动作」。

现状是手势素材（`data/avatars/{id}/gestures/{gid}/` 的三件套）只能靠翻磁盘和跑脚本维护：想确认某套手势装了没有、画面是什么样、显示名叫什么，都得回到命令行。一旦装上就再也改不动显示名，想去掉一套只能手动删目录——而目录删错位置的代价是形象废掉。数字人管理页已经有形象库与声音克隆，缺的正是动作这一块。

## What Changes

- **手势列举带上预览所需信息**：`GET /api/avatars/{avatar_id}/gestures` 每项新增 `frames`、`width`、`height`，页面不必再逐帧探测。
- **新增预览动画接口**：`GET /api/avatars/{avatar_id}/gestures/{gesture_id}/preview` 返回循环播放的 WebP 动画，首次请求生成、按源帧数与最新帧时间失效重建，缓存落在 `data/tmp/gesture_preview/`（可再生数据，可随时清空）。
- **新增改名接口**：`PUT /api/avatars/{avatar_id}/gestures/{gesture_id}` 只改 `gesture.json` 的 `label`，保留其它键；空白或超长显示名被拒。
- **新增删除接口**：`DELETE /api/avatars/{avatar_id}/gestures/{gesture_id}` 删除整套手势素材并清理它的预览缓存，路径校验沿用形象库的 `resolve_avatar_dir` 与 `is_valid_gesture_id` 双闸门。
- **页面**：`/avatar.html` 每张形象卡新增「动作」按钮，打开面板后可看预览动画与帧数、改显示名、删除、复制 `[GESTURE:<id>]` 标签。

## Impact

- 受影响规格：`avatar-management`（新增 5 条 Requirement）。
- 受影响代码：`utils/gestures.py`（帧列举、改名、删除、预览生成）、`server/avatar_routes.py`（3 个新路由 + 列举字段扩展）、`web/avatar.html`（动画面板）。
- **不改动的**：既有手势运行时（`load_gesture`、帧源替换、`[GESTURE:id]` 解析）完全不动；三件套格式与目录约定不变；不新增上传通路（素材仍由外部生成管线产出后落到 `gestures/` 下）。
- 安全边界：预览生成只读 `full_imgs/` 下的图片，删除只作用于校验通过的手势目录；两者都不接受绝对路径或含 `..` 的标识。
