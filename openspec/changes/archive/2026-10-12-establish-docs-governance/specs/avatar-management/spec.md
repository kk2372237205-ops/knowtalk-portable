# Spec Delta

## Purpose

定义 KnowTalk 数字人形象（Avatar）能力对外可观察的 HTTP 契约：形象库的列举与缩略图、重命名与删除，离线生成任务的创建、进度查询、列表与删除，以及视频来源落盘、回调通知与启动期默认形象校验回退。

## ADDED Requirements

### Requirement: 形象库列举与缩略图

`GET /api/avatars` SHALL 返回 `./data/avatars` 下所有非隐藏子目录中、`full_imgs/` 至少含一个 `.png`/`.jpg`/`.jpeg` 的形象；每项 MUST 含 `id`、`name`、`thumbnail`、`ready`、`status`，其中 `ready` 为真当且仅当该目录含 `coords.pkl` 且 `face_imgs/` 至少有一个 `.png`，`status` MUST 为 `ready` 或 `incomplete`。`GET /api/avatars/{avatar_id}/thumbnail` SHALL 返回该形象 `full_imgs/` 排序后第一个图片的内容；无图片或形象不存在时 MUST 返回 `code` 404 与 `msg` "thumbnail not found"。

#### Scenario: 完整形象被标记为可用

- **WHEN** 某形象目录同时含 `coords.pkl`、`full_imgs/`（含图片）与 `face_imgs/`（含 `.png`）
- **THEN** 列举结果中该项 `ready` 为 `true` 且 `status` 为 `ready`

#### Scenario: 缺少 coords.pkl 的形象被标记为不完整

- **WHEN** 某形象目录含 `full_imgs/` 图片但缺少 `coords.pkl`
- **THEN** 该项仍出现在列举结果中，`ready` 为 `false` 且 `status` 为 `incomplete`

#### Scenario: 无图片的目录不出现在形象库

- **WHEN** `./data/avatars/` 下某子目录的 `full_imgs/` 不含任何 `.png`/`.jpg`/`.jpeg`
- **THEN** 该目录 MUST NOT 出现在 `data` 数组中

#### Scenario: 缩略图不存在

- **WHEN** 请求 `GET /api/avatars/{avatar_id}/thumbnail` 且该形象不存在或其 `full_imgs/` 无图片
- **THEN** 响应 `code` 为 404 且 `msg` 为 "thumbnail not found"

### Requirement: avatar_id 合法性约束

所有以 `{avatar_id}` 寻址的接口 SHALL 只接受匹配 `[A-Za-z0-9_.-]+` 的标识；不匹配时 MUST 拒绝请求（响应体 `code` 为 400，`msg` 指明标识非法），且 MUST NOT 读取目标路径、改名或删除任何目录。

#### Scenario: 含路径分隔符的标识被拒绝

- **WHEN** 请求携带含 `/` 或 `\` 的 `avatar_id`（例如 `a/b`）
- **THEN** 响应 `code` 为 400，且 MUST NOT 访问或列举该路径对应的目录

#### Scenario: 重命名目标标识非法

- **WHEN** `PUT /api/avatars/{avatar_id}` 的 `new_id` 含非法字符
- **THEN** 响应 `code` 为 400，且原形象目录名称保持不变

#### Scenario: 删除请求的标识非法

- **WHEN** `DELETE /api/avatars/{avatar_id}` 的 `avatar_id` 含非法字符
- **THEN** 响应 `code` 为 400，且 MUST NOT 删除任何目录

### Requirement: 形象重命名

`PUT /api/avatars/{avatar_id}` SHALL 接受 JSON 请求体字段 `new_id`，成功时 MUST 将磁盘上的形象目录重命名为 `new_id` 并返回 `data` 含 `old_id` 与 `new_id`。原形象不存在时 MUST 返回 `code` 404 与 `msg` "Avatar 不存在"；`new_id` 对应目录已存在时 MUST 返回 `code` 409 与 `msg` "新的 Avatar ID 已存在"，且 MUST NOT 覆盖或合并既有目录。

#### Scenario: 重命名成功

- **WHEN** 原形象目录存在且 `new_id` 未被占用
- **THEN** 响应 `data` 为 `{"old_id": <原标识>, "new_id": <新标识>}`，且磁盘上仅存在新名称的目录

#### Scenario: 目标标识已被占用

- **WHEN** 原形象存在且 `new_id` 对应目录已存在
- **THEN** 响应 `code` 为 409 且 `msg` 为 "新的 Avatar ID 已存在"，两个目录内容均不改变

#### Scenario: 原形象不存在

- **WHEN** `avatar_id` 合法但对应目录不存在
- **THEN** 响应 `code` 为 404 且 `msg` 为 "Avatar 不存在"

### Requirement: 形象删除

`DELETE /api/avatars/{avatar_id}` SHALL 在形象目录存在时递归删除该目录并返回 `data` 含 `deleted_id`；目录不存在时 MUST 返回 `code` 404 与 `msg` "Avatar 不存在"，且 MUST NOT 删除任何内容。

#### Scenario: 删除成功

- **WHEN** 请求删除一个存在的合法形象
- **THEN** 响应 `data` 为 `{"deleted_id": <标识>}`，且该目录及其内容不再存在，随后列举结果中 MUST NOT 再出现该标识

#### Scenario: 删除不存在的形象

- **WHEN** 请求删除一个不存在的合法形象标识
- **THEN** 响应 `code` 为 404 且 `msg` 为 "Avatar 不存在"

### Requirement: 生成任务的必填参数与视频来源

`POST /api/avatar/task` SHALL 同时接受 `application/json` 与 `multipart/form-data`。缺少 `model` 或 `avatar_id` 时 MUST 返回 `msg` 为 "model and avatar_id are required" 的错误响应且 MUST NOT 创建任务；既未上传 `video_file` 也未提供 `video_path` 时 MUST 返回 `msg` 为 "video_file or video_path is required" 的错误响应且 MUST NOT 创建任务；成功时 MUST 返回 `data.task_id`。

#### Scenario: JSON 请求缺少必填字段

- **WHEN** 以 `application/json` 提交且缺少 `model` 或 `avatar_id`
- **THEN** 响应 `code` 非 0、`msg` 为 "model and avatar_id are required"，且任务列表数量不变

#### Scenario: 未提供任何视频来源

- **WHEN** 请求含 `model`、`avatar_id`，但既无 `video_file` 也未提供 `video_path`
- **THEN** 响应 `msg` 为 "video_file or video_path is required"，且 MUST NOT 创建任务

#### Scenario: 以 multipart 上传视频创建任务

- **WHEN** 以 `multipart/form-data` 提交 `model`、`avatar_id` 与 `video_file`
- **THEN** 响应 `data.task_id` 非空，且该任务在 `GET /api/avatar/tasks` 中可见，`status` 为 `pending` 或已进入后续状态

### Requirement: 生成任务的可选参数与默认值

`POST /api/avatar/task` SHALL 接受可选字段 `img_size`、`nosmooth`、`bbox_shift`、`extra_margin`、`parsing_mode`、`version`、`face_det_batch_size`、`pads`、`task_id`、`notifyurl`。缺省值 MUST 为 `img_size`=256、`nosmooth`=false、`bbox_shift`=0、`extra_margin`=10、`parsing_mode`="jaw"、`version`="v15"、`face_det_batch_size`=16、`pads`="0 10 0 0"。`pads` 为字符串时 MUST 按空白拆分为整数序列；`task_id` 显式提供时 MUST 作为任务标识并原样回显。

#### Scenario: 提供自定义 task_id

- **WHEN** 请求含 `task_id` 为 `my-task-001`
- **THEN** 响应 `data.task_id` MUST 为 `my-task-001`，且 `GET /api/avatar/task/my-task-001` 可查询到该任务

#### Scenario: 同一 task_id 重复提交

- **WHEN** 以已存在的 `task_id` 再次提交创建请求
- **THEN** 任务列表中该标识 MUST 只保留最新一次提交的任务记录

#### Scenario: 数值参数无法解析

- **WHEN** `img_size`、`bbox_shift`、`extra_margin` 或 `face_det_batch_size` 为非整数字符串
- **THEN** 响应 `code` 非 0 的错误响应，且 MUST NOT 创建任务

#### Scenario: multipart 中以字符串传布尔值

- **WHEN** `multipart/form-data` 请求中 `nosmooth` 取值为 `"true"`（大小写不敏感）
- **THEN** 该值 MUST 按启用处理；取值为 `"false"` 或其他值时按禁用处理

### Requirement: 视频来源的落盘与路径解析

`video_file` 上传 MUST 落盘到 `./data/tmp/`，落盘文件名 MUST 为服务端生成的随机标识前缀与上传时提供的原始文件名的拼接结果，MUST NOT 因同名文件互相覆盖。`video_path` 为相对路径时 MUST 相对 `./data/avatars` 解析；为绝对路径时 MUST 按该路径直接读取，MUST NOT 追加前缀目录。

#### Scenario: 上传视频被写入临时目录

- **WHEN** 以 `multipart/form-data` 上传 `video_file` 为 `lecture.mp4`
- **THEN** 落盘文件位于 `./data/tmp/` 之内，其文件名由随机标识前缀与 `lecture.mp4` 拼接而成
- **AND** `data/tmp/` 中已存在的同名文件不被覆盖

#### Scenario: 相对路径按形象素材目录解析

- **WHEN** 请求以 `application/json` 提供 `video_path` 为 `alice.mp4`
- **THEN** 任务参数中的视频路径 MUST 被解析为 `./data/avatars/alice.mp4`

#### Scenario: 绝对路径直接使用

- **WHEN** 请求以 `application/json` 提供 `video_path` 为绝对路径
- **THEN** 任务参数中的视频路径 MUST 等于该绝对路径

### Requirement: 生成任务状态流转与进度查询

`GET /api/avatar/task/{task_id}` SHALL 返回 `data` 含 `task_id`、`model_type`、`avatar_id`、`status`、`progress`、`error_msg`、`notify_url`、`start_time`、`end_time`、`duration`。任务创建后 `status` MUST 为 `pending`，开始执行后 MUST 变为 `running`，成功结束 MUST 变为 `completed` 且 `progress` 为 100；执行过程抛出异常 MUST 使 `status` 变为 `failed` 且 `error_msg` 非空。`task_id` 不存在时 MUST 返回 `code` 404 与 `msg` "Task not found"。

#### Scenario: 查询刚创建的任务

- **WHEN** 创建任务后立即查询其状态
- **THEN** `data.status` 为 `pending` 或 `running`，`data.task_id` 与创建时返回的一致

#### Scenario: 不支持的模型类型

- **WHEN** 创建任务时 `model` 不是 `wav2lip` 或 `musetalk`
- **THEN** 该任务最终 `status` 为 `failed` 且 `error_msg` 非空

#### Scenario: 任务成功完成

- **WHEN** 任务执行成功结束
- **THEN** `data.status` 为 `completed` 且 `data.progress` 为 100，`data.end_time` 非空

#### Scenario: 查询不存在的任务

- **WHEN** 查询一个从未创建过的 `task_id`
- **THEN** 响应 `code` 为 404 且 `msg` 为 "Task not found"

### Requirement: 任务列表

`GET /api/avatar/tasks` SHALL 返回 `data.tasks` 数组，包含全部任务的详情对象，且 MUST 按 `start_time` 降序排列（最新创建的任务在最前）。

#### Scenario: 列表按创建时间倒序

- **WHEN** 先后创建多个任务并请求 `GET /api/avatar/tasks`
- **THEN** `data.tasks` 中各项的 `start_time` MUST 不小于其后一项的 `start_time`

### Requirement: 任务删除

`DELETE /api/avatar/task/{task_id}` SHALL 仅删除 `status` 为 `pending` 的任务并返回 `data.msg`；任务不存在时 MUST 返回 `code` 非 0 与 `msg` "Task not found"；任务处于其他状态时 MUST 拒绝删除，返回 `msg` 形如 "Task is in <status> state, cannot delete"，且 MUST NOT 影响该任务的继续执行。

#### Scenario: 删除待执行任务

- **WHEN** 删除一个 `status` 为 `pending` 的任务
- **THEN** 响应 `code` 为 0 且 `data.msg` 为 "Task deleted"，随后 `GET /api/avatar/task/{task_id}` 返回 "Task not found"

#### Scenario: 删除执行中的任务被拒绝

- **WHEN** 删除一个 `status` 为 `running` 或 `completed` 的任务
- **THEN** 响应 `msg` 含该任务当前状态与 "cannot delete"，且该任务仍可被查询到

### Requirement: 任务回调通知

任务创建请求中的 `notifyurl` 为可选字段。提供该字段时，任务每次状态变更（`pending`、`running`，以及最终 `completed` 或 `failed`）MUST 各向该地址发起一次 POST，通知体为任务详情对象；单次通知的超时 MUST 不超过 5 秒。通知发送失败或超时 MUST NOT 改变任务状态，也 MUST NOT 影响任务继续执行。

#### Scenario: 状态变更触发回调

- **WHEN** 创建任务时提供 `notifyurl` 且任务随后开始执行并结束
- **THEN** 该地址在 `pending`、`running` 与最终状态各收到一次 POST
- **AND** 每次通知体包含 `task_id`、`status`、`progress` 与 `notify_url`

#### Scenario: 回调失败不影响任务

- **WHEN** `notifyurl` 指向一个不可达的地址
- **THEN** 任务仍 MUST 正常执行到最终状态，状态不因通知失败而改变

### Requirement: 启动期默认形象校验与回退

启动时若模型为 `wav2lip`，所配置的默认形象目录 MUST 含 `coords.pkl` 且 MUST 含 `full_imgs/` 目录；不满足时 MUST 回退到 `./data/avatars` 下按名称排序的第一个满足条件的形象，并记录告警；不存在任何可用形象时 MUST 以错误终止（抛出错误信息含所需目录结构要求），MUST NOT 静默继续启动。模型不是 `wav2lip` 时 MUST 原样使用所配置的形象标识而不做该校验。

#### Scenario: 默认形象不完整时回退

- **WHEN** 模型为 `wav2lip`，配置的默认形象缺少 `coords.pkl` 或缺少 `full_imgs/`，但存在其他完整形象
- **THEN** 启动 MUST 使用按名称排序的第一个完整形象，并输出告警而非终止

#### Scenario: 无任何可用形象

- **WHEN** 模型为 `wav2lip` 且 `./data/avatars` 下不存在任何同时含 `coords.pkl` 与 `full_imgs/` 的形象
- **THEN** 启动 MUST 失败并抛出错误，错误信息 MUST 说明每个形象目录需包含 `coords.pkl` 和 `full_imgs`

#### Scenario: 非 wav2lip 模型跳过校验

- **WHEN** 模型为 `musetalk` 且所配置的形象目录不满足上述结构要求
- **THEN** 启动 MUST 原样使用该形象标识，MUST NOT 回退或报错
