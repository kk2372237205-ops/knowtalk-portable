# Spec Delta

## MODIFIED Requirements

### Requirement: 形象库列举与缩略图

`GET /api/avatars` SHALL 返回 `./data/avatars` 下所有非隐藏子目录中、`full_imgs/` 至少含一个 `.png`/`.jpg`/`.jpeg` 的形象；每项 MUST 含 `id`、`name`、`thumbnail`、`ready`、`status`。`ready` 为真当且仅当该目录含 `coords.pkl`，且其素材形态与**当前运行的模型**匹配：模型为 `wav2lip` 时 MUST 含至少一个 `.png` 的 `face_imgs/`；模型为 `musetalk` 时 MUST 同时含 `latents.pt`、`mask_coords.pkl` 与至少含一张 `.png` 的 `mask/`。当前模型无法确定时（例如未完成启动），任一形态齐备即为真。`status` MUST 为 `ready` 或 `incomplete`。`GET /api/avatars/{avatar_id}/thumbnail` SHALL 返回该形象 `full_imgs/` 排序后第一个图片的内容；无图片或形象不存在时 MUST 返回 `code` 404 与 `msg` "thumbnail not found"。

#### Scenario: 完整形象被标记为可用

- **WHEN** 当前模型为 `wav2lip`，某形象目录同时含 `coords.pkl`、`full_imgs/`（含图片）与 `face_imgs/`（含 `.png`）
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

#### Scenario: MuseTalk 素材在 musetalk 模型下被判为可用

- **WHEN** 当前模型为 `musetalk`，某形象目录含 `coords.pkl`、`full_imgs/`（含图片）、`latents.pt`、`mask_coords.pkl` 与含 `.png` 的 `mask/`，但没有 `face_imgs/`
- **THEN** 列举结果中该项 `ready` MUST 为 `true` 且 `status` MUST 为 `ready`

#### Scenario: wav2lip 素材在 musetalk 模型下被判为不完整

- **WHEN** 当前模型为 `musetalk`，某形象目录只含 `coords.pkl`、`full_imgs/` 与含 `.png` 的 `face_imgs/`
- **THEN** 该项 `ready` MUST 为 `false` 且 `status` 为 `incomplete`

## ADDED Requirements

### Requirement: MuseTalk 素材生成不依赖 mmpose

以 `model=musetalk` 创建生成任务时，任务 MUST 在**未安装 mmpose / mmcv** 的环境下完成，缺少该依赖 MUST NOT 使任务失败。

人脸裁切框 MUST 由 68 个面部关键点按以下规则计算：水平方向取关键点的最小与最大 `x`，下边界取关键点的最大 `y`，上边界取与鼻尖上下对称的位置（`2 × 鼻尖 y − 下边界 y`，且 MUST NOT 小于 0）。

关键点来源 MUST 优先使用本机 mmpose；mmpose 不可用时 MUST 回退到等效的**离线** 68 点实现（iBUG 点序），MUST NOT 因回退而改变上述裁切框的算法。任务成功时，形象目录 MUST 含 `full_imgs/`、`mask/`、`coords.pkl`、`mask_coords.pkl` 与 `latents.pt`，且 `coords.pkl` 每项 MUST 为 `(x1, y1, x2, y2)`。

#### Scenario: 无 mmpose 环境下完成任务

- **WHEN** 环境未安装 mmpose / mmcv，以 `model=musetalk` 创建生成任务
- **THEN** 该任务 `status` 最终为 `completed` 且 `progress` 为 100
- **AND** 形象目录同时含 `full_imgs/`、`mask/`、`coords.pkl`、`mask_coords.pkl` 与 `latents.pt`

#### Scenario: 裁切框取自关键点而非检测框

- **WHEN** 素材中能检测到人脸并得到 68 点关键点
- **THEN** `coords.pkl` 中的框宽度 MUST 等于关键点 `x` 的极差
- **AND** 其上边界 MUST 等于 `max(0, 2 × 鼻尖 y − 关键点最大 y)`

#### Scenario: 检测不到人脸时任务失败

- **WHEN** 源素材中检测不到人脸
- **THEN** 该任务 `status` 为 `failed` 且 `error_msg` 非空
- **AND** 响应 MUST NOT 声称任务已完成
