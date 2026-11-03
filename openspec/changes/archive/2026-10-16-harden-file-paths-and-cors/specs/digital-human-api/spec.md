# Spec Delta

## MODIFIED Requirements

### Requirement: 声音克隆样音管理

`POST /api/voice/upload` MUST 接受 `multipart/form-data`，字段为 `file`（音频文件）与 `name`（声音名称，缺省为 `未命名`），并返回 `data.path`（相对路径）、`data.name`、`data.size`；缺少文件时 MUST 返回 `请上传音频文件` 业务错误。`name` MUST 通过文件名合法性校验：MUST NOT 含路径分隔符、控制字符或 Windows 保留字符（`< > : " / \ | ? *`），MUST NOT 为 `.` 或 `..`，校验失败 MUST 返回业务错误且 MUST NOT 落盘任何文件。落盘扩展名 MUST 取自 `file` 的扩展名并 MUST 落在 `.wav`、`.mp3`、`.ogg`、`.m4a`、`.flac` 之内，越出该集合时 MUST 返回业务错误且 MUST NOT 落盘。落盘路径 MUST 位于 `data/voices/` 之内。`GET /api/voices` MUST 返回已登记声音数组（`data` 直接为数组），每项含 `name`、`path`、`size`，且只包含上述五种后缀。`POST /api/voice/apply` MUST 校验 `sessionid` 与 `ref_file`，并把该音色应用到会话，返回 `ref_file`、`ref_text`、`engine` 回显。

#### Scenario: 上传并应用音色

- **WHEN** 客户端上传名为"讲师甲"的 WAV，再以该文件的 `path` 调用 `POST /api/voice/apply`
- **THEN** 首次响应 `data.name` 为"讲师甲"且 `data.path` 指向 `data/voices/` 下的文件
- **AND** 应用响应回显相同的 `ref_file`，该会话后续朗读使用新音色

#### Scenario: 列出声音

- **WHEN** 客户端调用 `GET /api/voices`
- **THEN** `data` 为按文件名排序的声音对象数组，且不包含非音频文件

#### Scenario: 未指定声音文件

- **WHEN** 客户端调用 `POST /api/voice/apply` 但请求体不含 `ref_file`
- **THEN** 服务返回 `{"code": -1, "msg": "请指定声音文件"}`

#### Scenario: 名称含路径分隔符

- **WHEN** 客户端以 `name` 为 `../../server/routes` 调用 `POST /api/voice/upload`
- **THEN** 响应 `code` 非 0 的业务错误
- **AND** `data/voices/` 之内与之外均 MUST NOT 产生新文件

#### Scenario: 扩展名不在允许集合内

- **WHEN** 客户端上传名为 `payload.py` 的文件调用 `POST /api/voice/upload`
- **THEN** 响应 `code` 非 0 的业务错误，且 MUST NOT 落盘

### Requirement: 声音样本删除

`POST /api/voice/delete` MUST 从请求体读取 `path`；`path` 缺失时 MUST 返回业务错误 `请指定文件路径`。相对路径 MUST 相对项目根目录解析。解析后的绝对路径 MUST 位于 `data/voices/` 之内（比较 MUST 在解析符号链接之后进行，使指向目录之外的符号链接同样被拒绝）；越出该目录时 MUST 返回业务错误且 MUST NOT 删除任何文件。目标位于目录内且存在时 MUST 删除该文件并返回 `{"code": 0, "msg": "ok"}`；目标不存在时 MUST 返回业务错误 `文件不存在`。

#### Scenario: 删除已登记声音

- **WHEN** 客户端提交 `GET /api/voices` 返回的某个 `path`
- **THEN** 该文件被删除，响应为 `{"code": 0, "msg": "ok"}`
- **AND** 再次列举声音时不再包含该项

#### Scenario: 目标文件不存在

- **WHEN** 客户端提交一个在 `data/voices/` 内不存在的 `path`
- **THEN** 服务返回 `{"code": -1, "msg": "文件不存在"}`

#### Scenario: 未提供 path

- **WHEN** 请求体不含 `path`
- **THEN** 服务返回 `{"code": -1, "msg": "请指定文件路径"}`

#### Scenario: 越出声音目录的路径被拒绝

- **WHEN** 客户端提交 `path` 为 `../../README.md`，或为 `data/voices/` 之外的绝对路径
- **THEN** 响应 `code` 非 0 的业务错误
- **AND** 该目标文件 MUST 仍然存在且内容不变

### Requirement: 跨域访问配置

服务 MUST 只对**显式配置的可信来源**返回跨域响应头，可信来源取自环境变量 `CORS_ALLOWED_ORIGINS`（逗号分隔的完整来源，如 `https://a.example`）。对配置内的来源，跨域响应头 MUST 含该来源与 `Access-Control-Allow-Credentials: true`，MUST 暴露响应自身的头部，且预检 MUST 允许携带任意请求头（含管理接口所需的 `X-Admin-Token`）。来源不在配置内时 MUST NOT 返回任何跨域响应头。配置值含通配来源 `*` 时 MUST 按"不信任任何来源"处理。`CORS_ALLOWED_ORIGINS` 未配置或为空时，服务 MUST NOT 对任何来源返回跨域响应头，且 MUST NOT 因此拒绝同源请求。

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
