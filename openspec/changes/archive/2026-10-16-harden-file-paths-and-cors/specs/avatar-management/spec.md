# Spec Delta

## MODIFIED Requirements

### Requirement: 视频来源的落盘与路径解析

`video_file` 上传 MUST 落盘到 `./data/tmp/`，落盘文件名 MUST 为服务端生成的随机标识前缀与**净化后的**原始文件名 basename 的拼接结果，MUST NOT 因同名文件互相覆盖。净化 MUST 去掉目录成分与路径分隔符，并去除非文件名允许的字符；原始文件名无可用 basename 时 MUST 回退为服务端生成的默认名。`video_path` 为相对路径时 MUST 相对 `./data/avatars` 解析；为绝对路径时 MUST 保留该路径。无论相对还是绝对，解析后的绝对路径 MUST 落在允许根目录之内，比较 MUST 在解析符号链接之后进行。允许根 MUST 至少覆盖服务自己的 `data/` 目录（含形象素材与上传落盘），并 MAY 由环境变量 `AVATAR_VIDEO_ROOTS`（逗号分隔）**追加**更多根；该变量 MUST NOT 收窄掉服务自己的 `data/` 目录。越出允许根时 MUST 返回业务错误且 MUST NOT 创建任务。

#### Scenario: 上传视频被写入临时目录

- **WHEN** 以 `multipart/form-data` 上传 `video_file` 为 `lecture.mp4`
- **THEN** 落盘文件位于 `./data/tmp/` 之内，其文件名由随机标识前缀与 `lecture.mp4` 拼接而成
- **AND** `data/tmp/` 中已存在的同名文件不被覆盖

#### Scenario: 上传文件名含上跳片段

- **WHEN** 以 `multipart/form-data` 上传 `video_file`，其文件名为 `..\..\evil.mp4`
- **THEN** 落盘文件 MUST 仍位于 `./data/tmp/` 之内
- **AND** 项目根之外的目录 MUST NOT 出现该文件

#### Scenario: 相对路径按形象素材目录解析

- **WHEN** 请求以 `application/json` 提供 `video_path` 为 `alice.mp4`
- **THEN** 任务参数中的视频路径 MUST 被解析为 `./data/avatars/alice.mp4`

#### Scenario: 绝对路径直接使用

- **WHEN** 请求以 `application/json` 提供 `video_path` 为位于允许根之内的绝对路径
- **THEN** 任务参数中的视频路径 MUST 等于该绝对路径（MUST NOT 追加前缀目录），且任务 MUST 被创建

#### Scenario: 允许根之外的绝对路径被拒绝

- **WHEN** 请求以 `application/json` 提供 `video_path` 为允许根之外的绝对路径（如 `C:\Windows\win.ini`）
- **THEN** 响应 `code` 非 0 的业务错误且 MUST NOT 创建任务
- **AND** 错误信息 MUST 指明可配置的允许根环境变量

#### Scenario: 配置允许根不影响服务自己的上传与素材

- **WHEN** `AVATAR_VIDEO_ROOTS` 被设置为项目 `data/` 之外的目录，随后上传一个视频或提交相对 `video_path`
- **THEN** 该次上传 MUST 仍然成功，相对路径 MUST 仍被解析为 `./data/avatars/<name>`
- **AND** 二者都 MUST NOT 因为该配置被判成越界

### Requirement: 任务回调通知

任务创建请求中的 `notifyurl` 为可选字段。提供该字段时，其 URL 协议 MUST 为 `http` 或 `https`，且主机 MUST 落在环境变量 `AVATAR_NOTIFY_ALLOWED_HOSTS` 给出的允许列表内（逗号分隔的主机名，或 `主机:端口`）。校验不通过时 MUST 返回业务错误且 MUST NOT 创建任务；错误信息 MUST 指明该环境变量。环境变量未设置或为空时，任何 `notifyurl` MUST 被拒绝。校验通过后，任务每次状态变更（`pending`、`running`，以及最终 `completed` 或 `failed`）MUST 各向该地址发起一次 POST，通知体为任务详情对象；单次通知的超时 MUST 不超过 5 秒。通知发送失败或超时 MUST NOT 改变任务状态，也 MUST NOT 影响任务继续执行。

#### Scenario: 状态变更触发回调

- **WHEN** `notifyurl` 的主机已登记在允许列表内，任务随后开始执行并结束
- **THEN** 该地址在 `pending`、`running` 与最终状态各收到一次 POST
- **AND** 每次通知体包含 `task_id`、`status`、`progress` 与 `notify_url`

#### Scenario: 回调失败不影响任务

- **WHEN** `notifyurl` 指向一个已登记主机但不可达的地址
- **THEN** 任务仍 MUST 正常执行到最终状态，状态不因通知失败而改变

#### Scenario: 未登记主机被拒绝

- **WHEN** `notifyurl` 的主机不在允许列表内（如 `http://169.254.169.254/latest/meta-data`）
- **THEN** 响应 `code` 非 0 的业务错误且 MUST NOT 创建任务
- **AND** MUST NOT 向该地址发起任何请求

#### Scenario: 未配置允许列表时拒绝全部回调

- **WHEN** `AVATAR_NOTIFY_ALLOWED_HOSTS` 未设置，请求提供任意 `notifyurl`
- **THEN** 响应 `code` 非 0 的业务错误且 MUST NOT 创建任务

#### Scenario: 非 http 协议被拒绝

- **WHEN** `notifyurl` 为 `file:///etc/passwd` 或 `ftp://host/x`
- **THEN** 响应 `code` 非 0 的业务错误且 MUST NOT 创建任务
