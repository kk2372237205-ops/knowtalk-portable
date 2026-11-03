# Proposal

## Why

实机探测发现：直接访问服务根路径返回的是裸的 403。

```
GET /             →  HTTP 403   body='403: Forbidden'（14 字节纯文本）
GET /index.html   →  HTTP 200   89122 字节，正常页面
```

根因：`app.py` 用 `app.router.add_static('/', path='web')` 把整个 `web/` 目录挂在根上，而 aiohttp 的静态资源在 `show_index=False`（默认）下对**目录请求**直接抛 `HTTPForbidden`。`/` 正好是 `web/` 这个目录，于是拿到 403。aiohttp 3.x 已移除"目录自动找 index.html"的行为，必须显式注册一条路由。

影响面是真实的：服务启动横幅只打印 `http://localhost:8010/index.html`，但**用户与浏览器默认都会访问不带路径的根**——拿到的是 14 个字节的 `403: Forbidden`，既不知道服务是活的，也不知道该加 `/index.html`。这是新用户的第一印象。

`GET /` 不是任何接口文档或规格覆盖的路径，现有 `frontend-asset-delivery` 能力只管第三方资源自托管，因此需要单独补一条要求。

## What Changes

- **根路径返回入口页面**：`GET /` MUST 返回入口页面的内容（与 `GET /index.html` 一致），MUST NOT 返回目录拒绝。
- **只影响根路径**：其余静态路径（`/settings.html`、`/vendor/...`、`/materials/images/...`）的解析与访问控制 MUST 保持不变；目录列举 MUST 仍然关闭（MUST NOT 因为本变更而暴露 `web/` 的文件列表）。
- **入口页面按运行模式确定**：入口页面的选择沿用现有逻辑（`opt.transport` 决定 `index.html` / `rtmpapi.html` / `rtcpushapi.html`）；本变更只负责让 `/` 指向它，不改变各页面的内容。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `frontend-asset-delivery`：新增"服务根路径必须返回入口页面"的要求，并明确目录列举仍然关闭。

## Impact

- **修改代码**：`app.py` 的静态路由区（在 `add_static('/')` **之前**注册一条 `GET /` 路由，`FileResponse` 指向按 `opt.transport` 选定的入口页面）。注册顺序重要：`add_static('/')` 已注册后，后加的精确路径路由仍优先匹配（aiohttp 优先静态路径），但放在前面更不易被将来的改动破坏。
- **新增测试**：`tests/test_root_index.py`——构造只装本策略的最小 aiohttp 应用，断言 `GET /` 返回 200 与入口页面内容、`GET /index.html` 结果一致、`GET /vendor/` 仍然 403（目录列举未开放）、`GET /不存在的文件` 仍然 404。
- **不影响**：页面内容、第三方资源、`/materials/images` 静态挂载、任何 API 路由。
- **风险**：极低。新增一条精确匹配的路由；唯一需要留意的是它必须排在 `add_static('/')` 之前或在语义上优先，这条由测试固化。
