# 移除访问口令机制

## Why

用户在设置页要求「设置需要访问口令这个内容帮我去除掉」，并确认按「完全移除口令机制」实施。

现状的摩擦成本高于它带来的收益：每次打开设置页都要去服务日志里翻一次口令粘进来，而这是一个单机自用部署，管理面本来就只在可信网络内。附带证据是同一套机制的落地并不完整——`web/voice-manager.js` 从未发送过 `X-Admin-Token`，所以数字人管理页的「声音克隆」上传与删除一直在被中间件 401 拦掉；访问口令既没保护住什么，又让功能出现了一个不易察觉的断点。

## What Changes

- **删除口令校验中间件**：`server/auth.py` 整体删除，`app.py` 不再挂 `create_auth_middleware`。原先受保护的 `/api/settings*`、`/api/control*`、`/api/admin/*`、`/rag/admin/*`、`/api/avatar/task*`、`/api/voice/upload`、`/api/voice/delete`、`/api/quiz/bank*`、`/api/quiz/records*` 恢复为免口令，与课堂面接口一致。
- **删除口令的确定与持久化**：`utils/settings_store.py` 的 `ensure_access_token` / `access_token` / `token_source` / `set_access_token` 移除；启动时不再生成口令、不再写入设置文件、不再往控制台打印。设置文件中历史遗留的 `access_token` 键被忽略且不再回写。
- **删除口令修改接口**：`PUT /api/settings/access-token` 从 `server/settings_routes.py` 移除。
- **删除设置页的口令门**：`web/settings.html` 去掉「需要访问口令」登录块、「修改访问口令」区块、「清除口令」按钮，以及 `sessionStorage` 令牌读写与 401 后跳登录门的逻辑——打开即用。
- **清理管理后台的提示**：`web/admin.html` 去掉「该接口需要访问口令，已跳转到设置页输入」提示与相关跳转。
- **规格连带修订**：`access-control` 能力整体退役（9 条 Requirement 全部移除）；`rag-sidecar-control` 与 `session-control` 各移除 1 条"受访问口令约束"的 Requirement；`digital-human-api` 与 `runtime-configuration` 中把口令当作前置条件的表述改写掉。

## Impact

- 受影响规格：`access-control`（退役）、`rag-sidecar-control`、`session-control`、`digital-human-api`、`runtime-configuration`。
- 受影响代码：`server/auth.py`（删）、`app.py`、`utils/settings_store.py`、`server/settings_routes.py`、`web/settings.html`、`web/admin.html`、`tests/test_auth.py`（删）、`tests/test_settings_store.py`。
- 行为影响：设置页、管理后台、形象/声音/题库管理接口不再要求口令，局域网内任何能访问本服务的人都可以改 API Key、删除形象、上传或删除音色。**这是安全性的净下降**，属用户明确接受的选择。
- 对外部署提醒：README 中补充说明——若要对外暴露，应在反向代理层加认证。
- 不受影响：`/api/settings/access-token` 之外的设置读写契约、RAG 控制、踢出、声音克隆本身的参数与错误结构均不变。
