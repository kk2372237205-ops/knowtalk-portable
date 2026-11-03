# 本地账号登录

## 1. 变更前置

- [x] 1.1 建 `add-local-auth`，`validate --strict` 通过
- [x] 1.2 确认 `data/` 已被版本控制忽略，`data/auth/` 不会入库（`.gitignore` 第 26 行 `/data/`）

## 2. 账号与密码（纯逻辑，先写测试）

- [x] 2.1 `utils/auth_store.py`：`data/auth/users.json` 读写（原子替换 + RLock），密码 PBKDF2 哈希与校验
- [x] 2.2 用户名/显示名/密码的校验规则（用户名 1–32 位、允许字母数字下划线横线点；密码 ≥8 位）
- [x] 2.3 `require_login` 开关持久化；`has_users()`；密码修改后轮换 `secret.key`

## 3. 会话

- [x] 3.1 `issue_token()` / `read_token()`：HMAC 签名 payload（用户名 + 过期时间戳）
- [x] 3.2 Cookie 下发与清除：`kt_session`、`HttpOnly`、`SameSite=Lax`、7 天
- [x] 3.3 签名密钥生成（`secrets.token_bytes(32)`）与轮换

## 4. 接口

- [x] 4.1 `GET /api/auth/session` → 是否已登录、显示名、`require_login`、`has_users`
- [x] 4.2 `POST /api/auth/setup`（仅无账号时）→ 创建首位管理员，可选择是否开启登录保护，并直接登录
- [x] 4.3 `POST /api/auth/login` / `POST /api/auth/logout`
- [x] 4.4 `POST /api/auth/password`（需已登录，凭旧密码）
- [x] 4.5 中间件 `server/auth_mw.py`：页面 302 到 `/login.html?next=`、接口 401 JSON、
      放行 `/login.html`、`/api/auth/*`、静态资源、OPTIONS 预检、以及无账号时的全部请求

## 5. 前端

- [x] 5.1 `web/login.html`：登录表单 / 首次运行的创建管理员表单 / 忘记密码指引；微信扫码禁用占位
- [x] 5.2 `web/auth.js`：拉会话、填充顶栏用户位、驱动工作台左栏底部登录条目与菜单
- [x] 5.3 工作台左栏底部加登录态条目；顶栏 `.site-user` 接真实状态
- [x] 5.4 设置页加「账号」段：当前账号、修改密码、退出登录

## 6. 运维兜底

- [x] 6.1 `scripts/reset_password.py`：`reset_password.py <用户名> [--password 新密码]`，无密码时自动生成并打印
- [x] 6.2 登录页内嵌「忘记密码」指引（命令行路径），失败提示里也带上现有账号列表

## 7. 验证

- [x] 7.1 后端测试：哈希不落明文、弱密码拒绝、登录/登出、篡改 Cookie、改密踢旧会话、
      跳转带 next、开放模式不拦、无账号不拦、命令行重置、页面占位（48 项）
- [x] 7.2 前端截图：未登录与已登录两态在工作台左下角、顶栏都可见
- [x] 7.3 全量测试：后端 **787 passed**（基线 739 + 48）；前端 187/188（唯一失败仍是既有 `hook-gate-shell`）

## 8. 实测记录（2026-10-25）

### 8.1 接口行为（curl 实测）

- 未登录 `GET /workbench.html` → `302 Location: /login.html?next=/workbench.html`
- 未登录 `POST /api/quiz/lecture` → `401 {"code":401,"msg":"请先登录"}`
- 登录后 `GET /workbench.html` → 200；`GET /api/auth/session` → `authenticated:true, display_name:"王老师"`
- 改密后旧 Cookie 失效、当前浏览器自动续票（`/api/auth/password` 响应里重发 Cookie）

### 8.2 踩到的坑

- **`hidden` 被 `display: block` 盖掉**：登录页的「显示名 / 确认密码 / 需要登录」三个字段
  写着 `hidden` 却照常露出来（`.login-field { display: block }` 赢了 UA 的 `[hidden]`）。
  在 `shell.css` 加全局 `[hidden] { display: none !important; }` 兜底。
- **防回潮锁误伤**：`tests/test_access_token_removed.py` 断言 `app.py` 不含 `"server.auth"`，
  新模块 `server.auth_mw` 正好命中子串。改成正则 `server\.auth(?![\w])` 只锁已退役的那一个模块。
- **预览服务自己也被拦**：临时自动登录端点放在 `/__login`，被中间件 302 掉；
  挪进 `/api/auth/` 前缀（放行区）才通。
- **`settings-collect.test.js` 的约束再次生效**：账号段的脚本全部写进 `auth.js`，
  没有往 `settings.html` 里加内联脚本（它抓第一个内联 script 当"设置项收集器"跑）。

### 8.3 设计门禁

- `shell.css` + `workbench.css`：**0 FAIL / 10 WARN**。
  左栏登录条目原本用 `border-top: 1px` 分隔，被判 `[hairline]` FAIL，改为纯留白分隔。
- 截图：`docs/design/auth-login.png`、`auth-login-500.png`、`auth-wb-out.png`、
  `auth-wb-in.png`、`auth-account.png`。

### 8.4 没做的

- 微信扫码真实对接（界面禁用占位，`即将开放`）
- 多角色权限、操作审计、账号删除（规格里明确不提供删除接口，避免删掉唯一账号锁死）
- 登录失败次数限流（内网单机场景，暂不做）
