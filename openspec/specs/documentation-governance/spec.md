# documentation-governance Specification

## Purpose
定义 KnowTalk 项目文档的唯一存放位置、命名与编码规则、状态标注方式、可达性要求以及与代码保持一致的机制，使 OpenSpec 规格成为规范性内容的单一来源，`docs/` 只承担面向人的操作指南职责。

## Requirements

### Requirement: 规范性内容以 OpenSpec 规格为单一来源

项目对外可观察行为（接口契约、参数、返回结构、状态流转、检索与生成约束）MUST 只在 `openspec/specs/<capability>/spec.md` 中定义一次。任何其它文档 MUST NOT 复制这些规范性内容，只能引用对应能力规格。

#### Scenario: 接口文档与规格同时存在

- **WHEN** 某接口的请求字段发生变化
- **THEN** 只需修改该接口所属能力规格的对应 Requirement
- **AND** `docs/` 下不存在需要同步修改的第二份字段清单

#### Scenario: 操作指南需要描述接口

- **WHEN** `docs/` 下的操作指南需要告诉读者调用哪个接口
- **THEN** 该指南只给出接口路径与用途，并链接到对应能力规格
- **AND** 指南中不出现参数表或返回结构表

### Requirement: 规格与操作指南的职责边界

`openspec/specs/` MUST 只存放规范性的行为契约；`docs/` MUST 只存放非规范性的面向人的材料：安装、配置、运行、排障、二次开发指南，以及评审与审计记录。设计论证与方案取舍 MUST 放在变更的 `design.md` 或 `docs/` 中，MUST NOT 作为 Requirement 写入规格。

#### Scenario: 新增一篇说明文档

- **WHEN** 贡献者要新增一篇文档
- **THEN** 若内容是"系统必须表现成什么样"，写入 `openspec/specs/` 的对应能力
- **AND** 若内容是"人该怎么安装、配置或操作"，写入 `docs/`

#### Scenario: 方案稿中的设计论证

- **WHEN** 一篇方案稿包含选型理由、备选方案对比等论证性内容
- **THEN** 论证性内容迁移到指南或变更的设计文档
- **AND** 只有其中可校验的行为约束进入能力规格

### Requirement: 文档统一使用 UTF-8 编码

所有纳入版本库的文本文件（`.md`、`.py`、`.html`、`.js`、`.json`、`.yaml`、`.txt`、`.bat`）MUST 使用 UTF-8 编码，MUST NOT 使用 GBK/GB18030 等本地编码。带 BOM 的 UTF-8 MUST 被避免。

#### Scenario: 用 Windows 默认编码保存文档

- **WHEN** 贡献者在 Windows 上保存一个 `docs/` 下的 Markdown 文件
- **THEN** 文件内容是按 UTF-8 解码可读的中文
- **AND** 该文件不以 `EF BB BF` 开头

#### Scenario: 发现非 UTF-8 文件

- **WHEN** 审查发现某个文本文件不是合法 UTF-8
- **THEN** 该文件被转码为 UTF-8（无 BOM）
- **AND** 转码不改变其可见内容

### Requirement: 文档文件名使用小写 ASCII 且不得中英混排

新建或重命名的文档文件名 MUST 只包含小写 ASCII 字母、数字、连字符与下划线，MUST NOT 在同一文件名中混用中文与英文。目录名 MUST 遵循同一规则。

#### Scenario: 现有中英混排文件名

- **WHEN** `docs/` 下存在形如 `rag_knowledge_base_technical方案.md` 的文件
- **THEN** 该文件被重命名为纯 ASCII 名称
- **AND** 原文件中的所有引用被同步更新为新的路径

#### Scenario: 新建能力规格

- **WHEN** 新增一个能力规格
- **THEN** 其目录名是小写 kebab-case 的 ASCII 段
- **AND** 规格文件名是 `spec.md`

### Requirement: 能力规格的结构

每个能力 MUST 有独立目录 `openspec/specs/<capability>/spec.md`，内容 MUST 包含 Purpose 与至少一个 Requirement；每个 Requirement MUST 使用 `### Requirement: <名称>` 标题，并且 MUST 至少包含一个 `#### Scenario:` 场景，场景 MUST 使用 WHEN/THEN 形式。

#### Scenario: 归档一个变更后校验规格

- **WHEN** 变更被归档并写入 `openspec/specs/`
- **THEN** `openspec validate --specs --strict` 通过
- **AND** 每个能力规格都有非占位的 Purpose

### Requirement: 规划与设计类文档必须标注状态与核对日期

任何仍留在仓库中的规划稿、设计稿 MUST 在文档开头标注状态（草稿 / 已实现 / 部分实现 / 已废弃）与最后核对日期。MUST NOT 存在无法判断实现进度的方案文档。

#### Scenario: 功能已落地的方案稿

- **WHEN** 一篇方案稿描述的功能已经在代码中实现
- **THEN** 文档头部标注状态为"已实现"与核对日期
- **AND** 其中可校验的行为约束已迁入对应能力规格

#### Scenario: 功能不再维护的方案稿

- **WHEN** 一篇方案稿描述的方向已被放弃
- **THEN** 文档被标注为"已废弃"并说明替代方案，或被删除
- **AND** 文档不会被静默保留成与实现不符的描述

### Requirement: 文档必须可达且不存在失效链接

`README.md` MUST 链接到文档索引；文档索引 MUST 列出 `docs/` 下的全部文档并说明用途。仓库中的相对链接 MUST 指向真实存在的文件；`docs/` 与 `assets/` 中 MUST NOT 保留没有任何文档引用的孤立文件。

#### Scenario: 新增一篇文档

- **WHEN** 向 `docs/` 新增一篇文档
- **THEN** 该文档出现在 `docs/README.md` 的索引中
- **AND** `README.md` 的文档导航能够到达该索引

#### Scenario: 检查链接有效性

- **WHEN** 对仓库内 Markdown 的相对链接做一次检查
- **THEN** 所有链接目标都存在
- **AND** 不存在被引用但缺失、或存在却无人引用的资源文件

### Requirement: 接口文档与实际路由必须一致

规格中记录的 HTTP 端点集合 MUST 与实际注册的路由集合一致。新增、删除或重命名路由时，MUST 在同一个变更内更新对应能力规格。

#### Scenario: 新增一个路由

- **WHEN** 代码中注册了一个新的 HTTP 路由
- **THEN** 其所属能力规格中存在覆盖该路由的 Requirement 与 Scenario
- **AND** 不存在只在代码中注册、规格里查不到的接口

#### Scenario: 审查发现规格与代码不符

- **WHEN** 审查发现规格记录的端点在实际路由中不存在，或实际路由未出现在规格中
- **THEN** 差异在同一变更内被修正
- **AND** 修正方向以实际代码行为为准，除非该行为本身被判定为缺陷并单独记录

### Requirement: 行为变更必须通过 OpenSpec 变更进行

任何改变对外可观察行为的改动 MUST 通过一个 OpenSpec 变更提出，并在实现完成后同步更新受影响能力规格。MUST NOT 只修改代码而不更新规格。

#### Scenario: 提交一个行为变更

- **WHEN** 开发者要修改某个接口的返回结构
- **THEN** 先创建变更并在其 delta 规格中写清新的 Requirement 或 MODIFIED 内容
- **AND** 实现完成后受影响的能力规格反映新行为

#### Scenario: 纯文档或工具改动

- **WHEN** 改动不改变任何对外可观察行为（例如新增操作指南、修正错别字）
- **THEN** 该变更可以不产生规格 delta，并在变更元数据中显式声明跳过规格

### Requirement: 文档语言与英文版同步状态

项目文档 MUST 以简体中文为主要语言；代码、路径、命令、字段名、报错原文 MUST 保持原样不翻译。英文版文档 MUST 在开头标注其与中文版的同步状态；与中文版内容不一致时 MUST 明确标注为待同步，MUST NOT 以无提示的过时内容呈现。

#### Scenario: 打开英文版 README

- **WHEN** 读者打开 `README-EN.md`
- **THEN** 文档开头标注了它与 `README.md` 的同步状态
- **AND** 若内容已落后，文档明确说明落后范围或指向中文版

#### Scenario: 英文版描述的是另一个系统

- **WHEN** `README-EN.md` 的内容描述的功能列表与当前系统不符
- **THEN** 该文档被重写、或标注为归档并说明原因
- **AND** 不会在没有任何提示的情况下继续发布该内容
