# Design: connect-hy3d-real-backend

## 背景：真实协议（已核实 2026-10）

腾讯云混元生3D（专业版）OpenAI 兼容接口：

- `base_url`：`https://api.ai3d.cloud.tencent.com`（环境变量 `HY3D_API_BASE` 可覆盖）
- 提交：`POST {base}/v1/ai3d/submit`，JSON body，图生3D 用 `ImageUrl.Url`（图片链接或 `data:image/jpeg;base64,...`）
- 查询：`GET {base}/v1/ai3d/query?job_id=...`，轮询到 `status=completed`
- 鉴权：文档示例为 `Authorization: sk-A*****ZZZ`（部分指南用 `Bearer`）；实现做容错——key 以 `sk-` 开头则直填，否则加 `Bearer ` 前缀
- 模型版本：`Model` 可选 `3.0` / `3.1`（`HY3D_MODEL` 默认 `3.1`）
- 结果：完成后的模型文件 URL（或 base64），下载为 GLB

## 决策

### 决策 1：同步管线 + executor，不重写 async 客户端

`run_pipeline` 是同步函数，内部用标准库 `urllib` 做 HTTP。真实生成要分钟级轮询，若直接在 async 路由里调用会阻塞事件循环（routes.py:112 当前就是直接调同步函数）。最小改动且稳妥：**路由侧用 `await loop.run_in_executor(None, run_pipeline, ...)`** 把同步管线放进默认线程池，事件循环继续服务其他请求。前端协议不变（仍同步等 generate 返回，页面显示"生成中"）。

### 决策 2：字段容错而非硬编码

官方字段名在不同文档/版本间有出入（`JobId`/`job_id`、`MeshUrl`/`mesh_url`/`outputs`）。`_generate_via_hy3d_api` 对 submit 返回的 JobID、query 返回的 status 与 mesh_url 均做多路径 `.get` 提取，任一命中即用；全空则抛明确错误。这样真实 Key 接入时即使字段名微调也能跑，且单元测试用替身覆盖主路径。

### 决策 3：超时与轮询参数

- 轮询间隔 5s，最长等待 300s（5 分钟）；超时抛 `RuntimeError('混元3D 生成超时')`。
- 单次 HTTP `timeout=300s`。
- 图片以 base64 `data:image/png;base64,...` 内联上传（避免要求公网 URL）。

### 决策 4：mock 降级保持

`generate_backend=mock` 路径完全不动；`hy3d_api` 仅在 `HY3D_API_KEY` 存在时 `ready=true`，否则 `img3d_status` 仍标 `demo_mode` 且 generate 标 `ready=false`，并在调用时抛明确错误。

## 风险与缓解

- **[字段名与我实现不符]** → 决策 2 的容错；真 Key 接入若仍失败，用户贴返回 JSON 即可定位，改动局限在 `_generate_via_hy3d_api` 的提取逻辑，不波及路由/前端。
- **[生成过久前端断开]** → aiohttp 无默认请求超时，executor 后台跑；前端"生成中"提示覆盖等待。若后续需要更友好，再开变更改为前端轮询 job 状态（不在本次范围）。
- **[Key 泄露]** → Key 仅从环境变量读取，绝不入库、不打印到日志（仅打印掩码或存在性）。

## Migration / 回滚

- 上线：`IMG3D_GENERATE_BACKEND=hy3d_api` + `HY3D_API_KEY=...` 环境变量；不设则维持 mock 演示。
- 回滚：清空 `HY3D_API_KEY` 或置 `IMG3D_GENERATE_BACKEND=mock`，即回到立方体占位，零代码回滚。
