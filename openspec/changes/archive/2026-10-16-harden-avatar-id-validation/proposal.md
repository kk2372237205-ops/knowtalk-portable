# Proposal

## Why

实机探测发现一个**单请求清空整个形象库**的缺陷，已在沙箱里确凿复现：

```
DELETE /api/avatars/...        →  {"code": 0, "msg": "ok"}   随后 data/avatars 整个消失
DELETE /api/avatars/%2e%2e%2e  →  同上
```

根因是规格本身太松，四步串起来：

1. `openspec/specs/avatar-management/` 的 `avatar_id 合法性约束` 只要求标识匹配 `[A-Za-z0-9_.-]+`，而**点号在白名单里**，所以 `'...'` 是"合法标识"；
2. aiohttp/yarl 只归一化**裸**点段，因此 `%2e%2e%2e`（以及字面 `/api/avatars/...`）能把 `'...'` 原样送进处理函数（`%2e%2e` 反而被归一化掉，所以三个点以上才有效）；
3. Windows 会剥掉路径末段的结尾点，`Path(DATA_DIR, '...').resolve()` **等于 `DATA_DIR` 本身**；
4. `is_dir()` 为真 → `shutil.rmtree(DATA_DIR)`。

需要管理员口令才能触发，但它是"一次请求销毁全部形象素材（150 帧 × 2 套 + `coords.pkl` + 手势）"的原语。

同一处判定还被 `DELETE /api/avatars/{avatar_id}`、`PUT /api/avatars/{avatar_id}`、`GET /api/avatars/{avatar_id}/thumbnail`、`GET /api/avatars/{avatar_id}/gestures` 四个接口共用，因此修在判定层而不是在每个处理函数里补丁。重命名路径实测被 `WinError 87` 挡住、不可利用，但同样应受约束。

**为什么是本仓库自己的规格缺陷**：现有要求"不匹配时 MUST NOT 读取目标路径、改名或删除任何目录"表达的是意图，但把 `'...'` 判为合法就使该意图落空。要修的是判定条件本身。

## What Changes

- **标识不再允许纯点段**：只由点组成的标识（`.`、`..`、`...` 及更长）MUST 被拒绝。
- **增加兜底约束**：无论标识是否通过字符校验，其解析后的绝对路径 MUST **严格位于**形象素材根目录之内（比较在解析符号链接之后进行，且不允许等于根目录本身）。这条是"正则写错也不至于毁库"的第二道防线。
- **四个接口共用同一判定**：删除、重命名（源与目标）、缩略图、手势列举 MUST 都走该校验，MUST NOT 各自实现。
- 不改变任何合法标识的行为；`Mr.Chen` 这类正常标识的响应结构与状态码保持原样。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `avatar-management`：`avatar_id 合法性约束` 增加"拒绝纯点段"与"解析后必须严格位于根目录内"两条约束，并补充对应场景。

## Impact

- **修改代码**：`server/avatar_routes.py`（`AVATAR_ID_PATTERN` 与四个处理函数共用的校验；删除/重命名解析后加包含性兜底）。判定复用已有模块 `utils/path_guard.py`（本仓库在 `harden-file-paths-and-cors` 中引入）。
- **新增测试**：`tests/test_avatar_routes_guard.py`——对四个接口分别断言纯点段被拒、越界被拒、合法标识不受影响；并用沙箱 `DATA_DIR` 断言 `DELETE /api/avatars/...` 不再删除任何目录。
- **不影响**：合法形象的一切行为、前端、RAG、TTS、讲稿链路、依赖清单。
- **BREAKING**：无。合法标识集合不变，只收紧了从未有过合法用途的纯点段。
- **风险**：极低。唯一可能的误伤是有人真的用一个纯点段命名形象目录；Windows 上这种目录名本身不可用（会被系统剥点），因此不存在。
