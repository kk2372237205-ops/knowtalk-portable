# Tasks

## 1. 共用判定

- [x] 1.1 `server/avatar_routes.py` 增加共用的标识校验函数（如 `resolve_avatar_dir(avatar_id)`）：字符校验沿用 `AVATAR_ID_PATTERN`，额外拒绝纯点段，并用 `utils/path_guard.is_within` 要求解析结果严格位于 `DATA_DIR` 之内（不允许等于 `DATA_DIR`）。返回解析后的绝对路径，不合法时抛错。验证：单元测试覆盖 `Mr.Chen`（通过）、`a.b`（通过）、`a/b`、`..`、`.`、`...`、`....`、`../x`、空串（均拒绝）
- [x] 1.2 纯点段判定必须按"整段是否只由点组成"实现，不得写成"含点即拒"——否则会误伤 `Mr.Chen` 这类正常标识。验证：单元测试断言 `Mr.Chen`、`a.b`、`v1.2` 仍通过校验

## 2. 四个接口接入

- [x] 2.1 `delete_avatar`：改为走共用判定；不合法时返回 `code` 400，MUST NOT 调用 `shutil.rmtree`。验证：沙箱把 `DATA_DIR` 指向临时目录后 `DELETE /api/avatars/...` 与 `DELETE /api/avatars/%2e%2e%2e` 均返回 400，且临时目录下的 avatars 目录与其中的形象目录仍然存在
- [x] 2.2 `rename_avatar`：`old_id` 与 `new_id` 都走共用判定；不合法时返回 400 且 MUST NOT 调用 `Path.rename`。验证：沙箱断言两个取值非法时目录名不变
- [x] 2.3 `avatar_thumbnail` 与 `list_avatar_gestures`：改为走共用判定。验证：断言纯点段返回 400，且 MUST NOT 触碰文件系统（用替身断言未调用 `is_dir`/`glob`）
- [x] 2.4 保留既有错误语义：`avatar_id` 非法时 `code` 为 400、`msg` 指明标识非法；不存在的合法标识仍返回 `Avatar 不存在`（404 语义）。验证：既有 `tests/test_gesture_routes.py` 的相关用例保持通过

## 3. 回归

- [x] 3.1 合法路径不受影响。验证：`GET /api/avatars`、`GET /api/avatars/Mr.Chen/thumbnail`、`GET /api/avatars/Mr.Chen/gestures` 返回与改动前一致
- [x] 3.2 全量回归无新增失败。验证：`python -m pytest tests/ -q` 通过；`openspec validate harden-avatar-id-validation --type change --strict` 通过
