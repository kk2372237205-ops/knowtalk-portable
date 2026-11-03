# Spec Delta

## MODIFIED Requirements

### Requirement: 统一 JSON 响应格式与业务错误

除 WebRTC 信令响应、RAG 代理转发、文件下载与访问口令校验失败外，所有接口 MUST 返回 JSON 对象。成功时 MUST 为 `{"code": 0, "msg": "ok"}`，并在有返回值时附带 `data` 字段；无返回值时 MUST NOT 输出 `data` 键。业务失败时 MUST 返回 `{"code": -1, "msg": "<错误描述>"}`。业务错误 MUST 以 HTTP 状态码 200 返回，MUST NOT 使用 4xx/5xx（RAG 代理转发、文件下载与访问口令校验失败除外）；访问口令校验失败的响应契约由 `access-control` 能力定义。

#### Scenario: 成功响应

- **WHEN** 客户端调用 `POST /human` 且请求被接受
- **THEN** 响应体为 `{"code": 0, "msg": "ok"}`，不含 `data` 键
- **AND** HTTP 状态码为 200

#### Scenario: 业务失败响应

- **WHEN** 客户端请求引用了不存在的会话
- **THEN** 响应体为 `{"code": -1, "msg": "session not found"}`
- **AND** HTTP 状态码为 200

#### Scenario: 访问口令校验失败不套用业务信封

- **WHEN** 客户端在缺少或携带错误访问口令的情况下调用受保护接口
- **THEN** 响应 MUST NOT 使用 `{"code": -1, ...}` 业务信封
- **AND** 响应的状态码与响应体 MUST 遵循 `access-control` 能力定义的契约
