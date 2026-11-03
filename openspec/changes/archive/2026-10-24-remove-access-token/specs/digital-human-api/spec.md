# Spec Delta

## MODIFIED Requirements

### Requirement: 统一 JSON 响应格式与业务错误

除 WebRTC 信令响应、RAG 代理转发与文件下载外，所有接口 MUST 返回 JSON 对象。成功时 MUST 为 `{"code": 0, "msg": "ok"}`，并在有返回值时附带 `data` 字段；无返回值时 MUST NOT 输出 `data` 键。业务失败时 MUST 返回 `{"code": -1, "msg": "<错误描述>"}`。业务错误 MUST 以 HTTP 状态码 200 返回，MUST NOT 使用 4xx/5xx（RAG 代理转发与文件下载除外）。

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

### Requirement: 跨域访问配置

服务 MUST 只对**显式配置的可信来源**返回跨域响应头，可信来源取自环境变量 `CORS_ALLOWED_ORIGINS`（逗号分隔的完整来源，如 `https://a.example`）。对配置内的来源，跨域响应头 MUST 含该来源与 `Access-Control-Allow-Credentials: true`，MUST 暴露响应自身的头部，且预检 MUST 允许携带任意请求头（含任意自定义请求头）。来源不在配置内时 MUST NOT 返回任何跨域响应头。配置值含通配来源 `*` 时 MUST 按"不信任任何来源"处理。`CORS_ALLOWED_ORIGINS` 未配置或为空时，服务 MUST NOT 对任何来源返回跨域响应头，且 MUST NOT 因此拒绝同源请求。

#### Scenario: 同源前端调用

- **WHEN** 由主服务自身页面（同源）调用管理类接口
- **THEN** 请求正常返回统一 JSON 响应信封

#### Scenario: 跨域来源调用管理接口

- **WHEN** 浏览器从 `CORS_ALLOWED_ORIGINS` 中登记过的来源携带凭据调用 `GET /api/admin/config`
- **THEN** 响应含该来源的 `Access-Control-Allow-Origin` 与 `Access-Control-Allow-Credentials: true`
- **AND** 响应体为 `{"code": 0, "msg": "ok", "data": {"config": {...}}}`
- **AND** 来源未登记时 MUST NOT 获得这两个响应头

#### Scenario: 未登记来源不获得跨域响应头

- **WHEN** 浏览器从不在 `CORS_ALLOWED_ORIGINS` 之内的来源调用 `GET /api/admin/config`
- **THEN** 响应 MUST NOT 含 `Access-Control-Allow-Origin`
- **AND** MUST NOT 含值为 `true` 的 `Access-Control-Allow-Credentials`

#### Scenario: 未配置来源时同源请求不受影响

- **WHEN** `CORS_ALLOWED_ORIGINS` 未设置，客户端以同源方式调用接口
- **THEN** 响应 MUST 正常返回统一 JSON 响应信封
