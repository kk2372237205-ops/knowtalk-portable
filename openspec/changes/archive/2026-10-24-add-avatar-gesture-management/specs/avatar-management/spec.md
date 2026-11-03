# Spec Delta

## ADDED Requirements

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
