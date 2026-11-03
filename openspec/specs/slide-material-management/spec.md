# slide-material-management Specification

## Purpose
定义 KnowTalk 幻灯片与素材能力对外可观察的 HTTP 契约：幻灯片条目的增删改查与批量更新、图片与 PPT 素材上传及 PPT 分页转换、按会话隔离的当前幻灯片读写，以及 `[PPT:N]` 标签与关键词驱动的幻灯片联动规则。

## Requirements

### Requirement: 幻灯片列表与条目结构

`GET /api/slides` SHALL 返回 `data` 为幻灯片数组，并按配置中的存储顺序排列；每个条目 MUST 含 `id`、`name`、`image`、`keywords`、`ppt_id`、`ppt_name`，其中 `id` MUST 为整数、`keywords` MUST 为数组。当幻灯片配置文件不存在时，服务 MUST 以空数组初始化该配置并返回空数组，而不是返回错误。

#### Scenario: 尚无任何幻灯片

- **WHEN** 幻灯片配置文件不存在时请求 `GET /api/slides`
- **THEN** 响应 `code` 为 0 且 `data` 为空数组，同时配置文件被初始化为空数组

#### Scenario: 返回完整条目结构

- **WHEN** 已存在若干幻灯片时请求 `GET /api/slides`
- **THEN** 每个条目 MUST 含 `id`、`name`、`image`、`keywords`、`ppt_id`、`ppt_name` 六个字段

### Requirement: 新增幻灯片

`POST /api/slides` SHALL 接受 JSON 字段 `name`、`image`、`keywords`、`ppt_id`、`ppt_name`；`name` 缺失或为空时 MUST 返回 `code` 非 0 与 `msg` "name 不能为空"，且 MUST NOT 写入配置。成功时新条目的 `id` MUST 为现有条目最大 `id` 加 1（无条目时为 1），`ppt_id` 缺省为 `single`，`ppt_name` 缺省为 `name`，并 MUST 返回该新条目。

#### Scenario: 名称为空被拒绝

- **WHEN** 请求体缺少 `name` 或 `name` 为空字符串
- **THEN** 响应 `msg` 为 "name 不能为空"，且后续 `GET /api/slides` 的条目数量不变

#### Scenario: 新增成功并返回条目

- **WHEN** 请求含非空 `name` 与 `image`
- **THEN** 响应 `data` 为该新条目，其 `id` 为当前最大 `id` 加 1（无条目时为 1），且该条目出现在后续 `GET /api/slides` 的结果中

#### Scenario: 默认字段填充

- **WHEN** 新增请求未提供 `ppt_id` 与 `ppt_name`
- **THEN** 新条目的 `ppt_id` 为 `single`，`ppt_name` 等于 `name`

### Requirement: 幻灯片信息更新

`PUT /api/slides/{id}` SHALL 只修改请求体中出现的 `name`、`keywords`、`ppt_name` 字段，MUST 保留未提供字段的原值；`id` 不存在时 MUST 返回 `code` 非 0 与 `msg` "幻灯片不存在"。`PUT /api/slides/batch` SHALL 接受 `updates` 数组，每项含 `id` 与可选的 `name`、`keywords`；`updates` 缺失或为空时 MUST 返回 `msg` "updates 不能为空"；成功时 MUST 返回 `data.updated`（被更新的条目数）与更新后的完整 `data.slides`，`updates` 中未匹配任何条目的项 MUST 被忽略且 MUST NOT 计入 `updated`。

#### Scenario: 更新单个幻灯片的部分字段

- **WHEN** 请求体只含 `name`
- **THEN** 该条目 `name` 被更新，`keywords` 与 `ppt_name` 保持原值，且响应 `data` 为更新后的条目

#### Scenario: 更新不存在的幻灯片

- **WHEN** 目标 `id` 不存在
- **THEN** 响应 `code` 非 0 且 `msg` 为 "幻灯片不存在"，配置文件 MUST 保持不变

#### Scenario: 批量更新混合有效与无效条目

- **WHEN** `updates` 含 2 个存在的 `id` 与 1 个不存在的 `id`
- **THEN** 响应 `data.updated` 为 2，`data.slides` 为更新后的完整列表

#### Scenario: 批量更新缺少更新项

- **WHEN** 请求体缺少 `updates` 或 `updates` 为空数组
- **THEN** 响应 `code` 非 0 且 `msg` 为 "updates 不能为空"，配置文件 MUST 保持不变

### Requirement: 幻灯片删除

`DELETE /api/slides/{id}` SHALL 删除该条目并同时删除其 `image` 字段指向的本地图片文件，成功时返回成功响应；`id` 不存在时 MUST 返回 `code` 非 0 与 `msg` "幻灯片不存在"，且 MUST NOT 修改配置。`DELETE /api/slides` SHALL 清空全部条目并删除素材图片目录下的所有文件，并返回 `data.deleted` 为被清除的条目数量。

#### Scenario: 删除单个幻灯片

- **WHEN** 删除一个存在的 `id`
- **THEN** 响应 `code` 为 0，该条目从 `GET /api/slides` 中消失，其对应图片文件 MUST 不再存在

#### Scenario: 删除不存在的幻灯片

- **WHEN** 目标 `id` 不存在
- **THEN** 响应 `code` 非 0 且 `msg` 为 "幻灯片不存在"，其余条目 MUST 保持不变

#### Scenario: 清空全部幻灯片

- **WHEN** 请求 `DELETE /api/slides` 且当前有 N 个条目
- **THEN** 响应 `data.deleted` 为 N，`GET /api/slides` 返回空数组，素材图片目录 MUST 不再残留文件

### Requirement: 素材上传请求约束

`POST /api/materials/upload-image` 与 `POST /api/materials/upload-ppt` SHALL 以 `multipart/form-data` 的 `file` 字段接收文件；`file` 缺失时 MUST 返回 `code` 非 0 与 `msg` "未收到文件"。请求体超过 `UPLOAD_MAX_MB`（默认 512，单位 MB）时 MUST 被拒绝且 MUST NOT 写入任何素材文件；其中 `upload-ppt` 超限时 MUST 返回 `code` 为 413 且 `msg` 含当前上限值与 `UPLOAD_MAX_MB` 提示。`upload-image` MUST 只取上传文件名中最后一个 `/` 或 `\` 之后的部分作为落盘文件名，落盘路径 MUST 位于 `data/materials/images/` 之内。

#### Scenario: 表单未携带文件

- **WHEN** 请求表单中不存在 `file` 字段
- **THEN** 响应 `code` 非 0 且 `msg` 为 "未收到文件"，素材图片目录 MUST 无新增文件

#### Scenario: 上传体积超过上限

- **WHEN** 上传的 PPT 体积超过 `UPLOAD_MAX_MB` 所设上限
- **THEN** `upload-ppt` 响应 `code` 为 413 且 `msg` 含当前上限值与 `UPLOAD_MAX_MB`，素材图片目录 MUST 无新增文件

#### Scenario: upload-image 文件名包含路径片段

- **WHEN** 向 `POST /api/materials/upload-image` 上传的文件名为 `../evil.png`
- **THEN** 文件 MUST 落在 `data/materials/images/` 之内
- **AND** 返回的 `data.path` MUST 只含基础文件名 `evil.png`

### Requirement: 图片素材上传语义

`POST /api/materials/upload-image` SHALL 在写入新图片之前删除 `data/materials/images/` 下已存在的所有文件；保存成功后 MUST 返回 `data.path`，其值 MUST 形如 `/materials/images/<文件名>`，且该路径 MUST 可通过静态路由 `/materials/images` 访问到同一文件。

#### Scenario: 上传成功返回可访问路径

- **WHEN** 上传一个图片文件
- **THEN** 响应 `data.path` 以 `/materials/images/` 开头，且该路径对应的文件已落盘

#### Scenario: 新上传替换旧图片

- **WHEN** 素材图片目录中已存在先前上传的文件，此时再次上传图片
- **THEN** 目录中 MUST 不再保留先前上传的文件

### Requirement: PPT 上传与分页输出

`POST /api/materials/upload-ppt` SHALL 接受扩展名为 `.ppt` 或 `.pptx` 的文件，其他扩展名 MUST 按 `.pptx` 处理；成功时 MUST 返回 `data.pages`，为按页序升序排列的图片相对路径数组，文件名 MUST 形如 `<PPT基础名>_<8位随机串>_page_<三位页序>.png` 并 MUST 位于 `data/materials/images/` 下。转换流程正常结束后 MUST 删除为本次上传创建的临时 PPT 文件。

#### Scenario: 上传多页 PPT

- **WHEN** 上传一个含 N 页的 `.pptx` 文件
- **THEN** `data.pages` MUST 含 N 个路径，按页序升序排列，每项以 `/materials/images/` 开头

#### Scenario: 非 .ppt/.pptx 扩展名

- **WHEN** 上传的文件扩展名不是 `.ppt` 或 `.pptx`
- **THEN** 服务 MUST 按 `.pptx` 处理，且响应结构与成功转换一致

#### Scenario: 转换后清理临时文件

- **WHEN** 一次 PPT 上传成功完成转换
- **THEN** 本次上传产生的临时 PPT 文件 MUST 不再存在于素材图片目录中

### Requirement: PPT 转换回退链

PPT 转图片 MUST 依次尝试 PowerPoint 自动化、LibreOffice、PIL 渲染三个环节；前一环节不可用、失败或超时（LibreOffice 单次转换超时即视为该环节失败）时 MUST 继续尝试下一环节；只要后续任一环节成功，响应 MUST 仍为成功并返回该环节产出的页图片。三个环节均无法产出结果或缺少 `python-pptx`/`Pillow` 依赖时 MUST 返回 `code` 非 0 的错误响应而非成功，缺少依赖时 `msg` MUST 提示安装 `python-pptx` 与 `Pillow`。

#### Scenario: PowerPoint 不可用时回退

- **WHEN** 运行环境未安装或无法启动 PowerPoint 自动化组件
- **THEN** 服务 MUST 继续尝试 LibreOffice 或 PIL，并在其成功时返回 `data.pages`

#### Scenario: 高位环节失败不影响最终结果

- **WHEN** PowerPoint 与 LibreOffice 环节均失败，但 PIL 环节成功渲染出页面
- **THEN** 响应 MUST 为成功且 `data.pages` 为 PIL 产出的页图片路径

#### Scenario: 缺少依赖导致整体失败

- **WHEN** `python-pptx` 或 `Pillow` 未安装，导致全部转换环节失败
- **THEN** 响应 `code` 非 0，`msg` MUST 提示安装 `python-pptx` 与 `Pillow`，且 MUST NOT 返回成功状态

### Requirement: 幻灯片按会话隔离与当前幻灯片读写

`GET /api/current_slide` SHALL 以查询参数 `sessionid` 寻址会话（缺省值为 `"0"`），返回 `data.slide_id` 与 `data.slide`，`data.slide` MUST 为对应幻灯片条目对象，找不到条目时 MUST 为 `null`。从未被设置过当前幻灯片的会话 MUST 返回 `slide_id` 为 1。不同 `sessionid` 的当前幻灯片 MUST 互不影响。

#### Scenario: 会话尚未切换过幻灯片

- **WHEN** 查询一个从未被设置过当前幻灯片的 `sessionid`
- **THEN** `data.slide_id` 为 1，`data.slide` 为 `id` 等于 1 的条目或 `null`

#### Scenario: 会话之间互不影响

- **WHEN** 会话 A 的当前幻灯片被切换为其他页，随后查询会话 B
- **THEN** 会话 B 的 `data.slide_id` MUST 仍为其原有取值（默认 1）

#### Scenario: 当前幻灯片指向不存在的条目

- **WHEN** 当前 `slide_id` 对应的条目已被删除
- **THEN** 响应 `data.slide_id` 仍为该编号，而 `data.slide` MUST 为 `null`

### Requirement: PPT 标签解析与页序兼容

演讲稿中的 `[PPT:N]` 与 `[slide:N]` 标签 SHALL 被识别并从朗读文本中剔除；标签在原文本中的先后顺序 MUST 决定分段与播放顺序，每段 MUST 携带其之前最近一个标签的编号，无标签段 MUST 携带空值。编号 `N` MUST 先按幻灯片 `id` 解释：存在 `id` 等于 `N` 的条目时使用 `N`；否则当 `1 ≤ N ≤` 幻灯片总数时使用按存储顺序第 `N` 个条目（1-based）的 `id`；其余情况 MUST 原样保留 `N`。

#### Scenario: 标签取值为实际 slide_id

- **WHEN** 标签数字等于某个已存在幻灯片的 `id`
- **THEN** 该段对应的当前幻灯片 MUST 为该 `id` 的条目

#### Scenario: 标签取值为 1-based 页序

- **WHEN** 标签数字不是任何幻灯片的 `id`，但落在 1 至幻灯片总数之间
- **THEN** 该段对应的当前幻灯片 MUST 为按存储顺序第 `N` 个条目的 `id`

#### Scenario: 标签取值超出范围

- **WHEN** 标签数字既不是任何 `id`，也超出 1 至幻灯片总数的范围
- **THEN** `slide_id` MUST 原样保留该数字，此时查询当前幻灯片 MUST 得到 `slide` 为 `null`

### Requirement: 标签驱动的幻灯片切换时机

`echo` 模式的文本请求中，带标签的分段 MUST 在该段文本的音频开始播放时才把所属会话的当前幻灯片切换为该段编号；尚未开始播放的分段 MUST NOT 提前改变当前幻灯片。`chat` 模式的文本请求中，标签 MUST 在请求被接受的时刻立即切换所属会话的当前幻灯片，且交给模型的文本 MUST 已剔除标签。

#### Scenario: echo 模式提交后尚未播放

- **WHEN** 提交含 `[PPT:2]` 的 echo 请求，该段音频尚未开始播放
- **THEN** 该会话的当前幻灯片 MUST 保持切换前的取值

#### Scenario: echo 模式播放到该段

- **WHEN** 含 `[PPT:2]` 的分段音频开始播放
- **THEN** 该会话的当前幻灯片 MUST 切换为 `2`（或按页序兼容规则解析出的编号）

#### Scenario: chat 模式立即切换

- **WHEN** 提交含 `[PPT:3]` 的 chat 请求
- **THEN** 请求被接受后该会话的当前幻灯片 MUST 立即为 `3`（或按页序兼容规则解析出的编号），且送入模型的文本 MUST 不含该标签

### Requirement: 演讲稿重复粘贴检测

当同一套带标签的演讲稿被整体重复粘贴时，系统 MUST 只播放最后一套。判定 MUST 同时满足：存在某个标签位置其编号等于第一个标签的编号、该位置之前出现过的标签编号去重后不少于 2 个、从该位置起（含）的带标签段数不少于它之前带标签的段数、且从该位置起的编号序列单调不减；查找 MUST 从最后一个标签位置向前进行并取最先命中的位置。满足时 MUST 丢弃该位置之前的所有分段并从该位置重新开始，不满足时 MUST 保留全部分段。

#### Scenario: 整套演讲稿被粘贴两遍

- **WHEN** 文本形如 `[PPT:1] A [PPT:2] B [PPT:1] A2 [PPT:2] B2`，两套均从第 1 页开始且后半段单调不减
- **THEN** 播放 MUST 从后半段的起始分段开始，前半段 MUST 被忽略

#### Scenario: 后半段不构成完整一套

- **WHEN** 页码重新回到起始编号，但该位置之后的分段数少于之前的分段数，或编号序列出现下降
- **THEN** MUST NOT 丢弃任何分段，全部内容按原文顺序播放

### Requirement: 关键词驱动的自动翻页

`chat` 模式的模型回答被切分为句子送入语音合成时，系统 SHALL 用各幻灯片的 `keywords` 与该句子做不区分大小写的子串匹配；命中时 MUST 以"所有命中关键词长度之和"作为该幻灯片的得分，并把所属会话的当前幻灯片切换为得分最高的幻灯片；无任何命中时 MUST NOT 改变当前幻灯片；得分相同时 MUST 保留在幻灯片存储顺序中先出现的条目。

#### Scenario: 句子命中某幻灯片关键词

- **WHEN** 模型回答的某个句子包含某一幻灯片 `keywords` 中的关键词
- **THEN** 该会话的当前幻灯片 MUST 切换为该幻灯片

#### Scenario: 句子未命中任何关键词

- **WHEN** 句子不包含任何幻灯片的关键词
- **THEN** 该会话的当前幻灯片 MUST 保持不变

#### Scenario: 多个幻灯片同时命中

- **WHEN** 句子同时命中多个幻灯片的关键词，且命中关键词总长度不同
- **THEN** 当前幻灯片 MUST 切换为命中关键词总长度最大者；长度相同时保留存储顺序靠前的条目
