# Design

## Context

动机见 `proposal.md` —— Why。这里只记录方案设计所需的事实。

### 实测事实（本机）

| 目标 | 结果 |
|---|---|
| `https://cdn.jsdelivr.net/...` | **不可达**（TLS 连接被重置；8.4 秒超时） |
| `http://cdn.sockjs.org/sockjs-0.3.4.js` | **不可达**（4.1 秒超时） |
| `https://code.jquery.com/jquery-3.6.0.min.js` | 可达，5.9 秒 |
| `https://fonts.googleapis.com/css2?...Inter...` | 可达 |
| `https://unpkg.com/bootstrap@5.3.0/...` | 可达，1–11 秒 |
| `https://registry.npmmirror.com/...` | 可达，0.6–5 秒 |

### 页面依赖清单

- Bootstrap 5.3.0 样式：`index.html`、`index-en.html`、`admin.html`、`admin-en.html`、`avatar.html`、`avatar-en.html`、`dashboard.html`、`knowledge-base.html`、`ppt-library.html`（9 页）
- bootstrap-icons 1.10.0 样式：上述 9 页 + `knowtalk-chat.html`（10 页）
- Bootstrap 5.3.0 bundle JS：`admin.html`、`admin-en.html`、`avatar.html`、`avatar-en.html`、`dashboard.html`、`knowledge-base.html`（6 页）
- jQuery 3.6.0：`admin.html`（6 处 `$(`）、`admin-en.html`（6 处）、`dashboard.html`（39 处）
- jQuery 2.1.1：`rtmpapi.html`（4 处）、`rtmpchat.html`、`rtcpushapi.html`、`rtcpushchat.html`（13 处）、`webrtcapi.html`（13 处）、`webrtcapi-asr.html`
- sockjs：仅 `rtcpushchat.html:50` 一处引用，全仓库**没有任何 `SockJS(` 调用**
- Google Fonts（Inter）：仅 `admin.html:8`、`admin-en.html:8`；两页字体栈已是 `'Inter', system-ui, -apple-system, sans-serif`（`admin.html:26`）

### 约束

- 主服务已用 `app.router.add_static('/', path='web')`（`server/routes.py:546`）把整个 `web/` 目录挂在根路径下，因此放进 `web/vendor/` 的文件天然可通过 `/vendor/...` 访问，**不需要改任何后端代码**。
- 页面必须继续在 `--transport rtmp / rtcpush / virtualcam` 下工作，这些模式直接打开 `rtmpapi.html` / `rtcpushapi.html` 等页面。
- 本变更不修改任何 `onclick`、DOM id 或页面交互逻辑。

## Goals / Non-Goals

**Goals:**

- 让全部页面在不访问外网的情况下完整渲染（布局、组件、图标一致）。
- 让引入的第三方前端资源可追溯：版本、来源、许可证集中登记。
- 消除无用的与明文 HTTP 的外部脚本引用。

**Non-Goals:**

- 不重构前端（不抽公共 WebRTC 客户端、不引入构建工具或包管理、不做 i18n）——这些是审计记录里已登记的 `refactor-frontend-shared-client` / `fix-frontend-xss-and-cdn` 后续变更。
- 不修复页面内的 XSS 拼接问题（如 `knowtalk-chat.html` 的 `onerror=` 拼接）。
- 不升级 Bootstrap / jQuery 的版本，只把现有版本本地化。
- 不改动 `web/asr/` 子目录（第三方录音库页面，不在本次报障范围）。

## Decisions

### 决策 1：本地化而非换 CDN

备选方案对比：

- **换到可达的镜像**（unpkg / npmmirror）：改动最小，但把"页面能否渲染"继续押在外网可用性上；换机器、换网络、镜像被墙都会再次崩。拒绝。
- **本地化到 `web/vendor/`**：一次性把资源放进仓库，走同源请求，离线可用、加载更快、版本可控。代价是仓库增加约 600 KB。采用。
- **引入包管理 + 构建**（npm + vite）：能自动化，但会把这个纯静态前端变成需要构建的工程，与"教学机开箱即用"冲突，且属于更大的改造。留给后续变更。

### 决策 2：放在 `web/vendor/` 而不是改后端加路由

`server/routes.py:546` 已经把 `web/` 整个目录挂在根路径，`web/vendor/` 会自动以 `/vendor/` 暴露。这样**后端零改动**，也避免了新增路由带来的契约变化。

### 决策 3：删除 sockjs 引用而不是本地化它

`sockjs-0.3.4.js` 只被 `rtcpushchat.html` 引用一次，而全仓库不存在 `SockJS(` 调用。为一个从未使用的库去镜像一个已停止维护的包，是把问题复杂化。直接删除引用，同时消除该页的明文 HTTP 混合内容请求。

### 决策 4：删除 Google Fonts 外链

字体服务不可用时的回退是优雅的（浏览器直接用下一个字体），所以它不构成渲染故障；但保留它意味着页面在离线机器上仍会发起一次注定失败的外网请求，也仍然是一个"页面依赖外网"的口子。两页的字体栈已经声明了 `system-ui, -apple-system, sans-serif` 回退，删除后排版不受影响，只是字形从 Inter 变为系统字体。取舍是：**用字形一致性换掉最后一个外网依赖**。

### 决策 5：登记 `web/vendor/README.md`

本地资产最大的风险是"来源不明、无法复现、不知道许可证"。清单记录文件名、版本、来源 URL、许可证与下载日期，使任何一次版本升级都可追溯。

### 决策 6：引用路径统一写成 `/vendor/...`

页面里已存在 `/admin.html`、`/avatar.html` 这类根路径形式的链接（`index.html:421`），所以统一使用根路径 `/vendor/...` 与现有风格一致，也避免相对路径随路由变化而失效。

## Risks / Trade-offs

- **[本地资源与上游版本漂移]** → `web/vendor/README.md` 记录版本号与来源 URL；升级时按清单重新获取并同步页面引用。
- **[图标字体文件缺失导致图标仍不显示]** → bootstrap-icons 的 CSS 通过相对路径 `./fonts/bootstrap-icons.woff2` 引字体，必须一并落盘并在同一目录层级还原；验证时直接请求该字体文件确认 200。
- **[删除 Google Fonts 后管理页字形变化]** → 已在决策 4 中接受；字体栈有系统回退，版式不受影响。
- **[改动 16 个页面可能误伤交互逻辑]** → 只替换 `src` / `href` 属性值，或用行内替换删除指定行；改完对每个页面做「引用的本地文件都存在」与「页面仍无外网引用」两项检查。
- **[首次下载资源失败]** → 首选 unpkg（实测可达），失败时回退 `registry.npmmirror.com`；两个源都失败则停止并报告，不做半成品替换。

## Migration Plan

1. 下载 6 个资源文件到 `web/vendor/`，校验文件大小与内容非空、非错误页。
2. 写 `web/vendor/README.md` 登记版本与来源。
3. 逐个页面替换引用路径（Bootstrap → jQuery → 图标字体），保持引用顺序不变。
4. 删除 `rtcpushchat.html` 的 sockjs 行、`admin.html` / `admin-en.html` 的 Google Fonts 行。
5. 启动主服务，逐个请求本地资源确认 200 与正确 `Content-Type`，并确认页面源码中不再存在外部资源域名。

回滚：全部改动是静态文件与 HTML 属性，可按文件 `git checkout` 回退；删除 `web/vendor/` 即可完全还原。

## Open Questions

无。
