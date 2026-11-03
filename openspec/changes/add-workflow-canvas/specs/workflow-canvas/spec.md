# Spec Delta

## ADDED Requirements

### Requirement: 流程必须是一张可存盘的节点连线图

一张流程 MUST 存为 `data/workflows/{id}.json`，含 `id`、`name`、`nodes`、`edges`、`updated_at`。
节点 MUST 含 `id`、`type`、`x`、`y`、`config`；连线 MUST 含 `id`、`from`、`to`。
节点 `type` MUST 取自 `avatar`、`ppt`、`kb`、`gesture` 四者之一。
写入 MUST 用临时文件加原子替换，MUST NOT 出现半截文件。目录 MUST 在版本控制忽略范围内。

#### Scenario: 新建流程落盘

- **WHEN** 用户新建一张流程并加入一个数字人节点
- **THEN** `data/workflows/` 下 MUST 出现同名 JSON 文件
- **THEN** 该文件 MUST 能原样读回同样的节点与坐标

#### Scenario: 中途断电不留半截

- **WHEN** 保存过程中进程被强杀
- **THEN** 该流程文件 MUST 要么是旧内容，要么是新内容
- **THEN** MUST NOT 出现无法解析的 JSON

### Requirement: 画布必须支持四类节点与拖拽连线

画布页 `/workflow.html` MUST 提供节点库，包含数字人、PPT、知识库、动作四类；
新增节点 MUST 落在画布可视区内，且 MUST 自动连到数字人。节点 MUST 可用指针拖动改位置，
松手后坐标 MUST 被持久化。从数字人节点右侧的连线端点拖到另一节点并松开 MUST 生成一条连线；
点击已有连线 MUST 选中它的下游节点。
连线是关系的派生物、不是独立对象：**删除节点时挂在它上面的连线 MUST 一并删除**，
界面 MUST NOT 提供「只删线、留下孤立节点」的操作（那会让图必然无法保存）。

#### Scenario: 拖出一个连线

- **WHEN** 用户从数字人节点的连线端点按下，拖到 PPT 节点上松开
- **THEN** 画布上 MUST 出现一条从数字人指向 PPT 的线
- **THEN** 刷新页面后该连线 MUST 仍在

#### Scenario: 删除节点带走连线

- **WHEN** 用户删除一个已有连线的 PPT 节点
- **THEN** 该节点 MUST 消失
- **THEN** 与它相连的连线 MUST 同时消失，MUST NOT 留下悬空线

#### Scenario: 点线选中下游节点

- **WHEN** 用户点击「数字人 → 知识库」这条线
- **THEN** 知识库节点 MUST 被选中，属性面板 MUST 显示它的课程

### Requirement: 节点必须呈现真实素材

画布上的四类节点 MUST 用各自的真实素材呈现，MUST NOT 只是文字标签：
`avatar` 节点 MUST 显示该形象的首帧缩略图；`ppt` 节点 MUST 显示该课件首页；
`gesture` 节点 MUST 显示该手势的循环预览帧，并在节点上标明它是循环内容。
`kb` 节点没有图片素材，MUST 用书册图形配合资料数与索引状态呈现。
素材取不到时（未选、文件缺失、预览生成失败）MUST 退回同类型的线描图标占位，
MUST NOT 显示破图或空白块。各类型节点 MUST 按自身素材比例决定尺寸，MUST NOT 一律套同一张卡片。
属性面板 MUST 在选中节点时同步展示同一份素材预览。

#### Scenario: 形象节点显示首帧

- **WHEN** 流程里的数字人节点选中了形象 `Mr.Chen`
- **THEN** 该节点 MUST 显示该形象的首帧缩略图
- **THEN** MUST 按竖版人像的比例呈现，MUST NOT 把脸裁掉

#### Scenario: 课件节点显示首页

- **WHEN** PPT 节点选中了一套 12 页的课件
- **THEN** 该节点 MUST 显示这套课件第 1 页的缩略图
- **THEN** 节点上 MUST 能看到页数

#### Scenario: 手势节点显示循环帧

- **WHEN** 动作节点选中了所属形象下的某个手势
- **THEN** 该节点 MUST 显示该手势的循环预览帧
- **THEN** 节点上 MUST 有标识说明它是循环内容

#### Scenario: 素材缺失退回图标

- **WHEN** 图里的 PPT 引用的课件已被删除，或形象缩略图取不到
- **THEN** 该节点 MUST 显示对应类型的线描图标与文字
- **THEN** MUST NOT 出现破图图标或空白方块

### Requirement: 连线必须由后端统一裁决

保存时后端 MUST 校验：一张图最多一个 `avatar` 节点；每个非 `avatar` 节点 MUST 有且只有一条来自
`avatar` 的入边；`avatar` 节点 MUST NOT 有入边；MUST NOT 出现自环、重复边、指向不存在节点的边。
违反时 MUST 返回 400 与可读的中文原因，并 MUST NOT 写入文件。
前端 MUST 在连线动作发生时先行拦截明显违规，但最终判定 MUST 以后端为准。

#### Scenario: 两个数字人被拒

- **WHEN** 用户在一张图里放了两个数字人节点并保存
- **THEN** MUST 返回 400，提示只允许一个数字人
- **THEN** 磁盘上的旧内容 MUST 保持不变

#### Scenario: 未连线的课件被拒

- **WHEN** 图中存在一个没有入边的 PPT 节点并保存
- **THEN** MUST 返回 400，提示该节点未连接到数字人

#### Scenario: 自环被拒

- **WHEN** 用户把 PPT 节点连到自己
- **THEN** MUST 被拒绝，MUST NOT 生成该连线

### Requirement: 改动必须自动保存

画布 MUST 在改动后自动保存，MUST NOT 要求用户点「保存」才落盘。
首次自动保存 MUST 先创建流程并回填 `id`，后续 MUST 走覆盖写。
保存状态 MUST 在页面上可见（保存中 / 已保存 / 保存失败），失败时 MUST 显示原因并保留改动。

#### Scenario: 改完就走

- **WHEN** 用户拖动一个节点后直接关闭页面
- **THEN** 重新打开该流程时节点 MUST 停在拖动后的位置

#### Scenario: 保存失败不吞改动

- **WHEN** 自动保存返回 400
- **THEN** 页面 MUST 显示失败原因
- **THEN** 画布上用户刚做的改动 MUST 仍然可见

### Requirement: 开播必须能按流程加载参数

`GET /api/workflows/{id}/plan` MUST 把图解析成开播参数：形象 ID、参考音色、参考文本、课件 ID、
课程 ID 与课程名、动作清单。解析时 MUST 标注已不存在的资源到 `warnings`，MUST NOT 静默丢弃。
工作台打开 `/workbench.html?workflow=<id>` 时 MUST 拉取该计划并灌入表单对应控件；
资源缺失时 MUST 在界面上提示缺了哪一项。

#### Scenario: 按图灌参数

- **WHEN** 打开 `/workbench.html?workflow=wf_demo`
- **THEN** 形象、音色、课件三个下拉 MUST 变成该流程里配的值
- **THEN** 左栏 MUST 显示该流程名

#### Scenario: 图里的形象被删了

- **WHEN** 流程引用的形象已从磁盘删除，用户仍按该流程打开工作台
- **THEN** 工作台 MUST 提示该形象不可用
- **THEN** MUST NOT 把表单静默设成空值而不作声

### Requirement: 流程页必须与站点视觉一致

`/workflow.html` MUST 使用 `.site-bar` 顶栏与纸面设计令牌（`--hp-*`），MUST 在顶栏标出「流程」。
工作台左栏 MUST 提供进入流程画布的入口。画布区 MUST 在 1440、1280、1024、768、500 五档视口下
`scrollWidth` 等于 `clientWidth`。窄屏下节点库与属性面板 MUST 折叠到画布上下，MUST NOT 互相遮挡。

#### Scenario: 窄屏不溢出

- **WHEN** 在 500px 宽视口打开 `/workflow.html`
- **THEN** 页面 MUST 不出现横向滚动条
- **THEN** 画布 MUST 仍可拖动节点

#### Scenario: 从工作台进画布

- **WHEN** 在工作台左栏点「流程」
- **THEN** MUST 打开 `/workflow.html`
- **THEN** 该页顶栏 MUST 与工作台同款，并标出「流程」
