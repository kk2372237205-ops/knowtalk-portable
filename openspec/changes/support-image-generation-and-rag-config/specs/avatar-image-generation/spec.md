# avatar-image-generation Specification

## Purpose
扩展「创建新生成任务」接口，使其除了视频外也能接受单张图片作为数字人生成素材来源，并约束视频与图片二选一的输入契约。

## ADDED Requirements

### Requirement: 生成任务接受图片素材

`POST /api/avatar/task` MUST 接受两类素材来源：视频（`video_file` 上传或 `video_path` 本地路径）或图片（`video_file` 上传且为图片扩展名）。当素材为图片时，服务 MUST 将其作为单帧来源构建数字人，并返回 `{"code": 0, "data": {"task_id": "..."}}`。

#### Scenario: 上传视频生成

- **WHEN** 客户端以 `multipart/form-data` 提交 `model=wav2lip`、`avatar_id=foo`、`video_file=<一段 mp4>`
- **THEN** 服务返回 `code=0` 且 `data.task_id` 非空
- **AND** 任务以视频方式进入生成管线

#### Scenario: 上传图片生成

- **WHEN** 客户端提交 `model=wav2lip`（或 `musetalk`）、`avatar_id=foo`、`video_file=<一张 png/jpg>`
- **THEN** 服务返回 `code=0` 且 `data.task_id` 非空
- **AND** 该图片被作为单帧素材复制进形象的 `full_imgs` 目录参与生成

#### Scenario: 视频与图片均未提供

- **WHEN** 请求既不含 `video_file`/`video_path`，也不含图片素材
- **THEN** 服务返回业务错误 `{"code": -1, "msg": "video_file 或 video_path 是必填的（支持视频或图片）"}`

### Requirement: wav2lip 图像输入与 musetalk 对齐

`avatars/wav2lip/genavatar.generate_avatar` 在 `video_path` 指向图片文件时 MUST 将其复制为 `full_imgs/00000000.png` 单帧，而不是调用视频解码；行为与 `avatars/musetalk/genavatar.generate_avatar` 对图片的处理保持一致。

#### Scenario: 图片被当作单帧

- **WHEN** `video_path` 的文件扩展名属于图片（png/jpg/jpeg/webp/bmp）
- **THEN** 函数的 `full_imgs` 目录下仅生成 `00000000.png` 这一帧
- **AND** 后续人脸检测与素材切分按单帧正常进行
