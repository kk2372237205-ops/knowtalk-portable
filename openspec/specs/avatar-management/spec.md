# avatar-management Specification

## Purpose
定义 KnowTalk 数字人形象（Avatar）能力对外可观察的 HTTP 契约：形象库的列举与缩略图、重命名与删除，离线生成任务的创建、进度查询、列表与删除，以及视频来源落盘、回调通知与启动期默认形象校验回退。

## Requirements

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

所有以 `{avatar_id}` 寻址的接口 SHALL 只接受同时满足以下两条的标识：匹配 `[A-Za-z0-9_.-]+`，且**不是纯点段**（`.`、`..`、`...` 以及任何只由点组成的取值）。除字符校验外，标识解析得到的绝对路径 MUST **严格位于**形象素材根目录之内——比较 MUST 在解析符号链接之后进行，且 MUST NOT 接受等于根目录本身的结果。任一条不满足时 MUST 拒绝请求（响应体 `code` 为 400，`msg` 指明标识非法），且 MUST NOT 读取目标路径、改名或删除任何目录。

#### Scenario: 含路径分隔符的标识被拒绝

- **WHEN** 请求携带含 `/` 或 `\` 的 `avatar_id`（例如 `a/b`）
- **THEN** 响应 `code` 为 400，且 MUST NOT 访问或列举该路径对应的目录

#### Scenario: 重命名目标标识非法

- **WHEN** `PUT /api/avatars/{avatar_id}` 的 `new_id` 含非法字符
- **THEN** 响应 `code` 为 400，且原形象目录名称保持不变

#### Scenario: 删除请求的标识非法

- **WHEN** `DELETE /api/avatars/{avatar_id}` 的 `avatar_id` 含非法字符
- **THEN** 响应 `code` 为 400，且 MUST NOT 删除任何目录

#### Scenario: 纯点段标识被拒绝

- **WHEN** 请求以纯点段寻址，例如 `DELETE /api/avatars/...` 或 `DELETE /api/avatars/%2e%2e%2e`
- **THEN** 响应 `code` 为 400
- **AND** 形象素材根目录及其下任何形象目录 MUST 仍然存在且内容不变

#### Scenario: 解析后落在根目录之外或等于根目录的标识被拒绝

- **WHEN** 标识解析得到的绝对路径不是形象素材根目录的严格子目录（落在根目录之外，或等于根目录本身）
- **THEN** 响应 `code` 为 400，且 MUST NOT 删除、改名或列举该路径

#### Scenario: 合法标识不受影响

- **WHEN** 以真实存在的合法标识（例如 `Mr.Chen`）调用列举、缩略图、重命名或删除
- **THEN** 请求 MUST 按原有语义正常处理，状态码与响应结构与改动前一致

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

### Requirement: 手势列举返回帧数与画面尺寸

`GET /api/avatars/{avatar_id}/gestures` SHALL 返回该形象下三件套齐备的手势列表；除既有的 `id` 与 `label` 外，每项 MUST 含 `frames`（`full_imgs/` 的帧数）与 `width`/`height`（首帧的像素尺寸）。形象存在但没有任何可用手势时 MUST 返回空数组而不是错误。`avatar_id` 不合法时 MUST 在读取文件系统之前拒绝（`code` 400）；形象目录不存在时 MUST 返回 `code` 404。

#### Scenario: 每项带帧数与尺寸

- **WHEN** 形象 `Mr.Chen` 下有一套 `open` 手势，`full_imgs/` 有 107 张 538×1080 的图片
- **THEN** 列表项为 `{"id": "open", "label": "掌心向上展示", "frames": 107, "width": 538, "height": 1080}`

#### Scenario: 没有手势时返回空数组

- **WHEN** 形象存在但其 `gestures/` 目录为空或不存在
- **THEN** 响应 `code` 为 0，`data.gestures` 为空数组

#### Scenario: 非法 avatar_id

- **WHEN** 请求携带含 `/` 或纯点段的 `avatar_id`
- **THEN** 响应 `code` 为 400，且 MUST NOT 读取目标路径下的任何文件

### Requirement: 手势预览动画按需生成并缓存

`GET /api/avatars/{avatar_id}/gestures/{gesture_id}/preview` SHALL 返回 `Content-Type: image/webp` 的**循环动画**，按 `full_imgs/` 的帧序播放。动画 MUST 在首次请求时由帧序列生成，并缓存到 `data/tmp/gesture_preview/` 下；缓存条目 MUST 在该手势的帧数或最新帧修改时间发生变化后失效并被重建，重建 MUST NOT 影响正在被读取的旧缓存文件。三件套不齐备或手势不存在时 MUST 返回 `code` 404 与说明性 `msg`；`avatar_id` 或 `gesture_id` 不合法时 MUST 返回 `code` 400。

#### Scenario: 首次请求生成缓存

- **WHEN** 首次请求某手势的预览且缓存目录中没有任何对应条目
- **THEN** 响应为 `image/webp` 内容
- **AND** `data/tmp/gesture_preview/` 下出现该手势的缓存文件
- **AND** 再次请求直接复用该缓存，MUST NOT 重新解码全部帧

#### Scenario: 素材变化后重建

- **WHEN** 某手势的 `full_imgs/` 新增了帧或更新了帧文件时间，随后请求预览
- **THEN** 服务重新生成动画缓存，且响应反映新的帧序列

#### Scenario: 素材不完整

- **WHEN** 请求一个缺少 `coords.pkl` 或 `face_imgs/` 的手势目录的预览
- **THEN** 响应 `code` 为 404，且 MUST NOT 在缓存目录中留下文件

### Requirement: 手势显示名可修改

`PUT /api/avatars/{avatar_id}/gestures/{gesture_id}` SHALL 接受 JSON 体 `{"label": "<显示名>"}`，把显示名写入该手势目录的 `gesture.json` 的 `label` 字段；文件中其它键 MUST 被保留。显示名 MUST 去除首尾空白后非空且不超过 40 个字符，否则 MUST 拒绝（`code` 400）且 MUST NOT 写入任何文件。成功时响应的 `data.label` MUST 为写入后的显示名，且随后的列举 MUST 反映新名。

#### Scenario: 改名后列举生效

- **WHEN** 对 `open` 手势提交 `{"label": "掌心展示"}` 并成功
- **THEN** `gesture.json` 中 `label` 为 `掌心展示`
- **AND** 再次列举时该项 `label` 为 `掌心展示`

#### Scenario: 空显示名被拒

- **WHEN** 提交 `{"label": "   "}`
- **THEN** 响应 `code` 为 400
- **AND** `gesture.json` 内容 MUST 保持不变

#### Scenario: 非法 gesture_id

- **WHEN** 请求路径中的 `gesture_id` 含 `..` 或不在 `[A-Za-z0-9_.-]` 之内
- **THEN** 响应 `code` 为 400，且 MUST NOT 写入任何文件

### Requirement: 手势可删除且不越界

`DELETE /api/avatars/{avatar_id}/gestures/{gesture_id}` SHALL 删除该手势目录（含 `full_imgs/`、`face_imgs/`、`coords.pkl` 与 `gesture.json`），并清理它的预览缓存；删除后列举 MUST NOT 再含该项。手势不存在时 MUST 返回 `code` 404。`avatar_id` 或 `gesture_id` 不合法时 MUST 返回 `code` 400，且 MUST NOT 删除任何目录。

#### Scenario: 删除后从列表消失

- **WHEN** 对一个存在的 `chop` 手势发起删除
- **THEN** 响应 `code` 为 0
- **AND** `data/avatars/{avatar_id}/gestures/chop/` 不再存在
- **AND** 该手势的预览缓存被清理

#### Scenario: 路径穿越被拒且不留副作用

- **WHEN** 请求的 `gesture_id` 为 `..`
- **THEN** 响应 `code` 为 400
- **AND** 形象目录下的任何文件与目录 MUST 保持不变

### Requirement: 数字人管理页提供动作管理与预览

`/avatar.html` 的每张形象卡 MUST 提供进入该形象「动作」面板的入口。面板 MUST 为每套手势展示：预览动画、显示名、帧数，以及三项操作——改名、删除、复制 `[GESTURE:<id>]` 标签。删除 MUST 先经二次确认；改名 MUST 在提交前拒绝空白名。对面没有手势的形象，面板 MUST 明确提示"暂无手势"而不是空白。

#### Scenario: 打开动作面板

- **WHEN** 用户在某形象卡上点击「动作」
- **THEN** 面板列出该形象的全部手势，每项带预览动画与帧数

#### Scenario: 删除需要二次确认

- **WHEN** 用户在面板中点击某手势的删除
- **THEN** 浏览器先弹出确认对话框
- **AND** 用户取消时 MUST NOT 发出删除请求

#### Scenario: 复制标签

- **WHEN** 用户点击某手势的复制标签
- **THEN** 剪贴板得到 `[GESTURE:<id>]`，可直接粘贴进预设文本
