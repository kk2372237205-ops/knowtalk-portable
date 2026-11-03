# Proposal

## Why

KnowTalk 的前端页面把样式与脚本框架全部挂在外网 CDN 上。在当前实际运行环境里，`cdn.jsdelivr.net` **完全不可达**（TLS 连接被重置 / 8 秒超时），导致全站 **10 个页面**丢掉 Bootstrap 5.3.0 的样式和 bootstrap-icons 图标字体：

- `web/index.html` 只引用两行外链（第 7、8 行），却使用了 **81 处** Bootstrap 类名（`col-md-3`、`d-flex`、`p-3`、`text-center`…）和 **29 个** `<i class="bi bi-...">` 图标——CSS 一丢，栅格与间距全部塌陷、图标消失，用户看到的就是"排版完全变了、像渲染插件掉了"。
- `cdn.sockjs.org` 同样不可达，且 `web/rtcpushchat.html:50` 还在以**明文 HTTP** 加载它。
- `code.jquery.com` 可达但需要 4–6 秒，且是 3 个管理页 + 6 个旧页面的硬依赖。

这是个教学场景的产品：机器可能没有外网，或外网被限制。前端不能把"页面能不能正常显示"押在第三方 CDN 上。

## What Changes

- 新增能力规格 `frontend-asset-delivery`：前端页面的渲染框架资源（CSS 框架、图标字体、必需脚本）MUST 由主服务自身的静态目录提供，页面 MUST 在不访问外网的情况下完整渲染。
- 把下列资源落到仓库内的 `web/vendor/`，随主服务 `/` 静态路由一起提供：
  - Bootstrap 5.3.0：`bootstrap.min.css`、`bootstrap.bundle.min.js`
  - bootstrap-icons 1.10.0：`bootstrap-icons.css` + `fonts/bootstrap-icons.woff2` / `.woff`
  - jQuery：`jquery-3.6.0.min.js`（admin/dashboard 用）与 `jquery-2.1.1.min.js`（rtmp/rtcpush/webrtcapi 旧页用）
- 把 10 个页面的样式/脚本引用从 CDN 改为本地 `web/vendor/` 路径，并保持引用顺序与版本不变。
- 删除 `web/rtcpushchat.html:50` 的 sockjs 引用：全仓库**从未调用过** `SockJS(...)`，该标签只是明文 HTTP 的死依赖。
- 删除 `web/admin.html:8` 与 `web/admin-en.html:8` 的 Google Fonts 外链。两页的 `font-family` 已经是 `'Inter', system-ui, -apple-system, sans-serif`（`admin.html:26`），去掉后优雅回退到系统字体，不再有任何外网字体请求。
- 在 `web/vendor/README.md` 记录每个文件的**版本、来源 URL 与许可证**，使本地资产可追溯、可复现。
- **BREAKING**：无。页面路由、交互、DOM 结构与后端接口均不变，只改变资源从哪里加载。

## Capabilities

### New Capabilities

- `frontend-asset-delivery`: 前端页面的渲染框架资源必须由服务自身提供、页面必须在无外网环境下完整渲染、本地资产必须记录版本与来源以便复现。

### Modified Capabilities

无。现有 7 个能力规格描述的是后端接口、RAG、文档与仓库规则，没有任何一条覆盖前端静态资源的来源与渲染前提。

## Impact

- **新增文件**：`web/vendor/` 下的 6 个资源文件（合计约 600 KB）与 `web/vendor/README.md`。
- **修改文件**：`web/index.html`、`web/index-en.html`、`web/admin.html`、`web/admin-en.html`、`web/avatar.html`、`web/avatar-en.html`、`web/dashboard.html`、`web/knowledge-base.html`、`web/ppt-library.html`、`web/knowtalk-chat.html`、`web/rtcpushchat.html`、`web/rtmpapi.html`、`web/rtmpchat.html`、`web/rtcpushapi.html`、`web/webrtcapi.html`、`web/webrtcapi-asr.html`。
- **不受影响**：`app.py`、`server/`、`rag_service/`、`tts/`、`streamout/`、`utils/` 的任何 Python 代码；所有 HTTP 接口契约；页面自身的 `onclick` / DOM id / 交互逻辑。
- **用户可见变化**：页面在无外网环境下恢复完整排版与图标；首次加载不再等待外网 CDN（本地 600 KB 资源走同源请求）。
- **依赖新增**：无新增 Python 或 npm 依赖，资源以静态文件形式入库。
