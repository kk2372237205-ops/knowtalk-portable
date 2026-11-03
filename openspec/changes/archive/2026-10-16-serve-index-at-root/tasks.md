# Tasks

## 1. 根路径路由

- [x] 1.1 `app.py` 在静态挂载处新增 `GET /` 路由，返回按 `opt.transport` 选定的入口页面文件（复用已有的 `pagename` 取值），放在 `add_static('/')` 之前注册。验证：起服务后 `GET /` 返回 200、`Content-Type` 为 `text/html`，且响应体与 `GET /index.html` 逐字节一致
- [x] 1.2 确认没有把 `web/` 的目录列举打开。验证：`GET /vendor/` 仍不返回文件列表；`GET /definitely-missing.html` 仍为未找到

## 2. 测试

- [x] 2.1 新增 `tests/test_root_index.py`：构造只装本策略的最小 aiohttp 应用（`add_static('/')` + 新的 `GET /` 路由），用真实测试客户端断言——`GET /` 200 且内容等于入口页面；`GET /index.html` 与改动前一致；`GET /vendor/` 不返回文件列表；`GET /missing.html` 为未找到。验证：`python -m pytest tests/test_root_index.py -q` 全绿
- [x] 2.2 用变异检查证明测试有效：把根路径路由移除，断言 `GET /` 重新变回目录拒绝（即测试会失败）。验证：该变异被测试抓住，变异后源码正确还原

## 3. 回归

- [x] 3.1 全量回归无新增失败。验证：`python -m pytest tests/ -q` 通过；`openspec validate serve-index-at-root --type change --strict` 通过
- [x] 3.2 实机复验：起服务后 `GET /` 返回 200 与入口页面内容。验证：实测状态码与响应体长度，且 `GET /index.html` 结果不变
