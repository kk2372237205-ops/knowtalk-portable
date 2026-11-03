# Spec Delta

## MODIFIED Requirements

### Requirement: 声音克隆样音管理

`POST /api/voice/upload` MUST 接受 `multipart/form-data`，字段为 `file`（音频文件）与 `name`（声音名称，缺省为 `未命名`），并返回 `data.path`、`data.name`、`data.size`；缺少文件时 MUST 返回 `请上传音频文件` 业务错误。`name` MUST 通过名称合法性校验，落盘扩展名 MUST 限定在 `.wav`、`.mp3`、`.ogg`、`.m4a`、`.flac`，落盘路径 MUST 位于 `data/voices/` 之内；任一不满足 MUST 返回业务错误且 MUST NOT 落盘。`GET /api/voices` MUST 返回已登记声音数组（`data` 直接为数组），每项含 `name`、`path`、`size`。`POST /api/voice/apply` MUST 校验 `sessionid` 与 `ref_file` 并把该音色应用到会话，返回 `ref_file`、`ref_text`、`engine` 回显。

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
