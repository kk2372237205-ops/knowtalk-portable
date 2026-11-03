# 本地账号登录

## Why

产品已经有四个管理面（工作台、数字人、知识库、接口与参数），但完全没有身份概念：
谁打开这个地址谁就能改配置、删形象、动知识库。顶栏右侧那个「未登录」目前只是个占位符，
点不动，也不反映任何真实状态。

用户要求补齐登录：能登录、能重置密码、微信扫码先占位；并且**未登录与已登录必须在界面上看得出差别**，
明确点到工作台左下角那个位置。

## What Changes

- 新增本地账号存储 `data/auth/users.json`：用户名、显示名、PBKDF2-HMAC-SHA256 密码哈希。
- 新增签名 Cookie 会话（HttpOnly、无服务端会话表），签名密钥落在 `data/auth/secret.key`。
- 新增接口：会话查询、首位管理员创建（setup）、登录、登出、改自己的密码。
- 新增登录页 `/login.html`：账号密码表单、首次运行的「创建管理员」表单、忘记密码指引；
  微信扫码入口以禁用态占位（标「即将开放」，不发任何请求）。
- 新增登录保护中间件：**无账号时全放行**（否则没法建第一个账号）；建号后按开关决定是否拦截。
  未登录访问页面 302 到 `/login.html?next=<原路径>`，访问接口返回 401 JSON。
- 新增 `scripts/reset_password.py`：本机命令行重置密码，并轮换签名密钥。
- 顶栏用户位与工作台左下角改为真实登录态：未登录显示「未登录 · 去登录」，已登录显示显示名，
  并提供「修改密码 / 退出登录」。
- 登录后所有页面顶栏一致呈现账号；登录态在页面间跳转后不丢。

## Impact

- 新增：`utils/auth_store.py`、`server/auth_routes.py`、`server/auth_mw.py`（中间件）、
  `web/login.html`、`web/auth.js`（前端登录态与菜单）、`scripts/reset_password.py`、`tests/test_local_auth.py`。
- 修改：`app.py`（装配路由与中间件）、`web/shell.css`、`web/site.js` 或各页顶栏用户位、
  `web/workbench.html` + `web/workbench.css`（左栏底部登录态）、`web/settings.html`（会话区/退出）。
- 不引入第三方依赖：密码哈希用标准库 `hashlib.pbkdf2_hmac`，签名用 `hmac` + `secrets`。
- 不做：微信扫码的真实对接、多角色权限、操作审计（各自另立变更）。
- 不做：口令式访问控制（`access-control` 已于 2026-10-24 退役，不复活）。
