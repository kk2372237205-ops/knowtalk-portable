# course-library-page Specification

## Purpose
定义「课程知识库」页（`/knowledge-base.html`）作为课程资料唯一准备入口的对外可观察契约：课程文档、PPT 课件、图片素材三类资料在同一页面内分区管理；独立的 PPT 库页面退役；首页控制台不再提供资料上传入口。后端 `/rag/admin/*`、`/api/slides`、`/api/materials/*` 接口契约不在本规格范围（分别由 `knowledge-base-management` 与 `slide-material-management` 定义）。

## Requirements

### Requirement: 课程知识库页的三分区结构

`/knowledge-base.html` MUST 在同一页面内提供三个可切换的分区：「课程文档」「PPT 课件」「图片素材」。三个分区 MUST 通过页内 tab 切换，MUST NOT 跳转到其它页面。页面标题 MUST 体现其为课程资料统一入口。

#### Scenario: 三个分区可切换

- **WHEN** 用户访问 `/knowledge-base.html`
- **THEN** 页面展示「课程文档」「PPT 课件」「图片素材」三个 tab
- **AND** 点击任一 tab 切换到对应分区，URL 不发生变化

#### Scenario: 课程文档分区功能不减少

- **WHEN** 用户处于「课程文档」分区
- **THEN** 课程增删改查、文档上传与索引、版本与分片查看等原有功能全部可用
- **AND** 这些功能与合并前完全一致

### Requirement: PPT 课件分区

「PPT 课件」分区 MUST 提供：上传 PPT 并自动分页（调用 `POST /api/materials/upload-ppt` 后将每页注册为幻灯片）、按册分组展示页面预览、整册改名、删除整个 PPT。分区 MUST 同时提供分页标签命名能力（逐页设置名称与关键词，调用 `PUT /api/slides/batch` 保存）——该能力从首页迁入本页。

#### Scenario: 上传 PPT 并命名分页

- **WHEN** 用户在「PPT 课件」分区上传一个 PPT 文件
- **THEN** 系统将 PPT 转换为逐页图片并注册为幻灯片
- **AND** 展示分页标签命名表单，用户可为每页设置名称与关键词并保存

#### Scenario: 删除整个 PPT

- **WHEN** 用户对某一册 PPT 执行删除
- **THEN** 系统在确认后删除该册全部页面

### Requirement: 图片素材上传

「图片素材」分区 MUST 提供图片上传表单（调用 `POST /api/materials/upload-image`）并展示上传结果状态。该入口从首页「素材上传」卡片迁入本页。

#### Scenario: 上传图片素材

- **WHEN** 用户在「图片素材」分区选择图片并上传
- **THEN** 系统上传图片并反馈成功状态

### Requirement: PPT 库独立页面退役

`/ppt-library.html` MUST NOT 再作为独立页面提供。站内所有指向 PPT 库的入口（首页工作流段、页脚、README 页面说明表）MUST 指向 `/knowledge-base.html`。后端幻灯片 API 契约不变。

#### Scenario: 旧入口失效

- **WHEN** 用户请求 `/ppt-library.html`
- **THEN** 页面不存在（404）

#### Scenario: 站内入口一致

- **WHEN** 查看首页工作流段、页脚或 README 页面说明表
- **THEN** 不存在指向 `/ppt-library.html` 的链接
- **AND** PPT 管理入口指向 `/knowledge-base.html`

### Requirement: 首页上传入口收敛

首页（`/index.html`）控制台 MUST NOT 包含资料上传类卡片（课程知识库上传、素材上传、PPT 分页标签命名）。资料准备动作统一引导至 `/knowledge-base.html`。首页讲稿区的「选择 PPT 插入标签」控件 MUST 保留——它服务于讲授过程中的标签插入，不属于资料准备。

#### Scenario: 首页无上传卡片

- **WHEN** 用户查看首页控制台
- **THEN** 不存在课程知识库上传、素材上传、PPT 分页标签命名三类卡片
- **AND** 控制台仅包含开播区与讲授区

#### Scenario: 讲稿区 PPT 标签选择保留

- **WHEN** 用户在首页讲授区编辑预设文本
- **THEN** 「选择 PPT 插入标签」控件可用，可从已上传的 PPT 中选择页面插入标签
