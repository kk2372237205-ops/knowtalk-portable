# Spec Delta

## ADDED Requirements

### Requirement: 服务根路径必须返回入口页面

`GET /` MUST 返回入口页面的完整内容，MUST NOT 返回目录请求被拒的响应。入口页面按运行模式选定（与现有 `opt.transport` 的映射一致），其内容 MUST 与通过该页面文件名直接访问所得一致。目录列举 MUST 保持关闭：除根路径外，对 `web/` 子目录的请求 MUST NOT 返回文件列表。不存在的静态路径 MUST 仍然返回未找到。

#### Scenario: 直接访问根路径

- **WHEN** 客户端请求 `GET /`（不带任何路径）
- **THEN** 响应状态码为 200，`Content-Type` 为 `text/html`
- **AND** 响应体与 `GET /index.html`（或当前运行模式对应的入口页面）一致
- **AND** 响应体 MUST NOT 是目录拒绝或文件列表

#### Scenario: 入口页面可直接访问

- **WHEN** 客户端请求 `GET /index.html`
- **THEN** 响应与改动前一致，行为 MUST NOT 因本变更改变

#### Scenario: 目录列举仍然关闭

- **WHEN** 客户端请求一个静态目录，例如 `GET /vendor/`
- **THEN** 响应 MUST NOT 是文件列表
- **AND** 根路径的放行 MUST NOT 被解释为"整个 `web/` 目录可列举"

#### Scenario: 不存在的静态路径仍然未找到

- **WHEN** 客户端请求一个不存在的静态文件
- **THEN** 响应为未找到，MUST NOT 回退到入口页面
