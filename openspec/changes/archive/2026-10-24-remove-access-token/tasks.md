# Tasks

## 1. 先写防回潮锁（红灯）

- [x] 1.1 新建 `tests/test_access_token_removed.py`：断言 `server.auth` 不可导入、`SettingsStore` 无口令方法、设置页无口令门与相关区块、管理后台无口令提示、`app.py` 不挂校验中间件、`/api/settings/access-token` 路由未注册
- [x] 1.2 跑该测试确认红灯

## 2. 后端移除

- [x] 2.1 删除 `server/auth.py`
- [x] 2.2 `app.py`：去掉 `create_auth_middleware` 导入、`ensure_access_token` 调用与口令来源日志、`middlewares.append` 一行
- [x] 2.3 `utils/settings_store.py`：删除 `ensure_access_token` / `access_token` / `token_source` / `set_access_token` 与实例字段；`access_token` 键不再纳入读写
- [x] 2.4 `server/settings_routes.py`：删除 `put_access_token` 与 `/api/settings/access-token` 路由

## 3. 前端移除

- [x] 3.1 `web/settings.html`：删除 `#tokenGate` 登录块、「修改访问口令」卡片、「清除口令」按钮，以及 `TOKEN_KEY` / `getToken` / `setToken` / `clearToken` / `showGate` / 401 处理；`api()` 改为不带口令的请求
- [x] 3.2 `web/admin.html`：删除 `#tokenHint` 提示与跳设置页逻辑

## 4. 测试与文档

- [x] 4.1 删除 `tests/test_auth.py`
- [x] 4.2 `tests/test_settings_store.py`：移除访问口令用例
- [x] 4.3 `tests/test_cors_policy.py`：预检用例改用中性自定义请求头（不再暗示存在管理口令）
- [x] 4.4 README 补一句「对外暴露请在反向代理层加认证」

## 5. 验证

- [x] 5.1 `pytest tests/` 全量通过
- [x] 5.2 `openspec validate remove-access-token --strict` 通过
- [x] 5.3 设置页与管理后台 HTML 标签平衡；设置页可无口令直接读到 `/api/settings`
