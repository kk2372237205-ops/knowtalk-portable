# Tasks

## 1. 本地资源落盘与登记

- [x] 1.1 下载 Bootstrap 5.3.0 的 `bootstrap.min.css` 与 `bootstrap.bundle.min.js` 到 `web/vendor/`，校验文件非空且内容以预期文本开头（CSS 以注释开头、JS 以注释或 `!function`/`(()=>` 开头）。验证：两个文件存在且大小与上游发布版一致（CSS 约 230 KB、bundle 约 80 KB）
- [x] 1.2 下载 bootstrap-icons 1.10.0 的 `bootstrap-icons.css` 到 `web/vendor/`，并从 CSS 中读取它引用的字体相对路径（`./fonts/bootstrap-icons.woff2` 等），把对应字体文件下载到 `web/vendor/fonts/`。验证：CSS 里出现的每个 `url(...)` 目标文件都在磁盘上存在
- [x] 1.3 下载 jQuery 3.6.0 与 2.1.1 两个版本的 min 文件到 `web/vendor/`。验证：文件存在且首行为 jQuery 版权注释
- [x] 1.4 新建 `web/vendor/README.md`，逐项登记文件名、版本号、来源 URL、许可证与下载日期，并写明升级时必须同步更新此清单。验证：清单条目数与 `web/vendor/` 下的资源文件数一致，且每条都有可重新下载的来源 URL

## 2. 页面引用替换

- [x] 2.1 把 9 个使用 Bootstrap 样式的页面（`index.html`、`index-en.html`、`admin.html`、`admin-en.html`、`avatar.html`、`avatar-en.html`、`dashboard.html`、`knowledge-base.html`、`ppt-library.html`）的 `bootstrap.min.css` 引用改为 `/vendor/bootstrap.min.css`。验证：`web/*.html` 中不再出现 jsdelivr 的 Bootstrap 样式地址，且 9 个页面各自包含本地引用
- [x] 2.2 把 10 个使用图标字体的页面（上述 9 页 + `knowtalk-chat.html`）的 `bootstrap-icons.css` 引用改为 `/vendor/bootstrap-icons.css`。验证：`web/*.html` 中不再出现 jsdelivr 的图标样式地址，且 10 个页面各自包含本地引用
- [x] 2.3 把 6 个使用 Bootstrap JS 的页面（`admin.html`、`admin-en.html`、`avatar.html`、`avatar-en.html`、`dashboard.html`、`knowledge-base.html`）的 `bootstrap.bundle.min.js` 引用改为 `/vendor/bootstrap.bundle.min.js`，并保持其在页面中的原有相对顺序。验证：各页面中 bootstrap 样式的 `<link>` 仍在脚本之前，本地 bundle 引用存在
- [x] 2.4 把使用 jQuery 3.6.0 的 3 个页面（`admin.html`、`admin-en.html`、`dashboard.html`）改为引用 `/vendor/jquery-3.6.0.min.js`。验证：这 3 页不再出现 `code.jquery.com`，且引用的是 3.6.0
- [x] 2.5 把使用 jQuery 2.1.1 的 6 个页面（`rtmpapi.html`、`rtmpchat.html`、`rtcpushapi.html`、`rtcpushchat.html`、`webrtcapi.html`、`webrtcapi-asr.html`）改为引用 `/vendor/jquery-2.1.1.min.js`。验证：这 6 页不再出现 `code.jquery.com`，且引用的是 2.1.1

## 3. 清理失效与外部依赖

- [x] 3.1 删除 `web/rtcpushchat.html` 中加载 `http://cdn.sockjs.org/sockjs-0.3.4.js` 的那一行。验证：该文件不再含 `cdn.sockjs.org`，且页面中不存在 `SockJS(` 调用（删除前先确认全仓库无调用，把结论写进提交说明）
- [x] 3.2 删除 `web/admin.html` 与 `web/admin-en.html` 中指向 Google Fonts 的 `<link>`。验证：两页不再出现 `fonts.googleapis.com`，且其 `font-family` 仍保留 `system-ui` / `sans-serif` 回退
- [x] 3.3 全量扫描 `web/` 下的 HTML，确认不再存在指向 CDN 的资源引用（`cdn.jsdelivr.net`、`cdn.sockjs.org`、`fonts.googleapis.com`、`code.jquery.com`），并区分"资源引用"与"文档性质的说明链接"。验证：输出扫描结果，资源引用数为 0，任何保留的说明性外链都在任务中逐条列出并说明理由

## 4. 本地静态资源可用性验证

- [x] 4.1 用 conda 环境 `livetalking` 的解释器启动主服务，逐个请求 `/vendor/bootstrap.min.css`、`/vendor/bootstrap-icons.css`、`/vendor/bootstrap.bundle.min.js`、`/vendor/jquery-3.6.0.min.js`、`/vendor/jquery-2.1.1.min.js`、`/vendor/fonts/bootstrap-icons.woff2`，确认全部返回 200。验证：6 个请求的状态码与内容长度被记录，且 `Content-Type` 分别为 `text/css`、`text/javascript`、`font/woff2`（或等价的二进制类型）
- [x] 4.2 请求 `/index.html` 并确认其中不再包含任何外部资源域名。验证：抓取到的 HTML 中资源引用全部是同源路径
- [x] 4.3 抽查 `admin.html`、`knowledge-base.html`、`dashboard.html` 三个页面，确认它们引用的本地文件全部存在且无 404。验证：对每个页面解析出的同源资源路径逐个请求，状态码全为 200

## 5. 集成验证

- [x] 5.1 运行 `openspec validate --all --strict`，确认变更与全部规格通过严格校验。验证：命令退出码为 0
- [x] 5.2 做一次"断网等价"检查：在浏览器开发者工具的 Network 面板之外，用不解析外部域名的方式确认页面渲染所需请求全部命中本地。验证：把 4.1 与 4.3 的请求清单与页面实际引用清单逐一对应，无遗漏
- [x] 5.3 复核仓库状态：`git status --short` 中只剩本变更预期的新增与修改，`web/vendor/` 下文件均已被跟踪。验证：逐项说明每一项的归属，并确认没有把下载缓存或临时文件带入

## Workflow follow-up

- 用户确认页面正常后归档本变更：`openspec archive vendor-frontend-assets`。
- 归档后确认 `openspec/specs/frontend-asset-delivery/spec.md` 存在且 Purpose 不是 `TBD` 占位。
- 审计记录中已登记的 `fix-frontend-xss-and-cdn` 后续变更，在本变更落地后其"CDN"部分已被覆盖，届时只需处理 XSS 与 jQuery 升级。
