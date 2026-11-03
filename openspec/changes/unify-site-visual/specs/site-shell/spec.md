# Spec Delta

## ADDED Requirements

### Requirement: 全站顶栏必须同构

首页以外的所有页面 MUST 使用同一套顶栏结构：品牌在左，四项导览（工作台、数字人、知识库、接口与参数）在中间，用户位在右。当前页面在顶栏 MUST 呈现为选中态。

顶栏 MUST 与首页顶栏同高、同字号、同右端强调按钮，且 MUST NOT 使用深色底。

#### Scenario: 从工作台跳到数字人页

- **WHEN** 在 `/workbench.html` 点击顶栏「数字人」
- **THEN** 浏览器 MUST 跳到 `/avatar.html`
- **AND** 该页顶栏 MUST 与工作台顶栏结构一致，「数字人」MUST 呈选中态

#### Scenario: 顶栏在窄视口不折行

- **WHEN** 以 390px 宽度打开任一工具页
- **THEN** 顶栏 MUST 不换行、不横向溢出
- **AND** 四项导览 MUST 仍可横向滚动到达

### Requirement: 工具页必须复用纸面皮肤

`/avatar.html`、`/knowledge-base.html`、`/settings.html` MUST 复用首页的纸面令牌：底色 `#fbf8f1`、正文 `#211f1a`、次级文字 `#6c6860`、强调 `#1f3b63`、展示字 Georgia 打头并回退宋体。

这些页面 MUST NOT 保留深色顶栏、蓝紫渐变背景、玻璃拟态卡片、Inter 外链字体。

#### Scenario: 比对工具页与首页

- **WHEN** 依次打开首页与数字人页
- **THEN** 两页的 `body` 底色 MUST 相同
- **AND** 两页的主按钮 MUST 同为 `#1f3b63` 实心、无圆角
- **AND** 数字人页 MUST NOT 存在 `background-image` 为渐变或圆点纹理的 `body`

#### Scenario: 控件外观一致

- **WHEN** 在知识库页与工作台各看到一个文本输入框
- **THEN** 两者的边框颜色、圆角、聚焦环 MUST 一致

### Requirement: 管理后台必须并入接口与参数

`/settings.html` MUST 提供两个分段：「运行期设置」与「管理后台」，用一个分段切换器切换，当前分段 MUST 写进地址 hash（`#runtime`、`#admin`）。

一级导航 MUST NOT 再出现「管理后台」入口。`/admin.html` MUST 仍可直接访问，但它的顶栏 MUST 标出它已并入设置页，并提供回到 `/settings.html#admin` 的链接。

#### Scenario: 从设置页进入管理后台

- **WHEN** 打开 `/settings.html#admin`
- **THEN** 页面 MUST 直接呈现管理后台分段
- **AND** 地址 hash MUST 保持 `#admin`

#### Scenario: 直接访问旧的独立地址

- **WHEN** 直接打开 `/admin.html`
- **THEN** 页面 MUST 正常可用
- **AND** MUST 提供一条回到 `/settings.html#admin` 的链接

### Requirement: 沉浸式展示页必须共用纸面皮肤

`/knowtalk-chat.html` MUST 复用同一套纸面令牌（底色 `#fbf8f1`、正文 `#211f1a`、强调 `#1f3b63`、宋体展示字），并 MUST NOT 保留蓝紫渐变顶栏或深色顶栏。

该页 MUST 保持单条页头：课程名、切换课程、暂停播放、连接状态，右端提供一条「返回工作台」。窄视口（小于 720px）允许把这条链接收起，此时靠浏览器后退返回。MUST NOT 出现后台四项导览（工作台、数字人、知识库、接口与参数），因为它面向学生。

三栏结构（人像、课件、对话）与全屏不滚动的特性 MUST 保持不变。

#### Scenario: 比对展示页与首页

- **WHEN** 依次打开首页与展示页
- **THEN** 两页 `body` 底色 MUST 相同
- **AND** 展示页顶栏 MUST NOT 存在渐变或深色底

#### Scenario: 学生端看不到后台入口

- **WHEN** 学生打开展示页
- **THEN** 页头 MUST NOT 出现「数字人」「知识库」「接口与参数」
- **AND** MUST 有一条「返回工作台」链接

#### Scenario: 展示页窄视口不溢出

- **WHEN** 以 900px 与 600px 宽度打开展示页
- **THEN** 文档滚动宽度 MUST 等于客户端宽度
- **AND** 页头 MUST 不折行、不遮挡画面

### Requirement: 静态资源必须带版本号

所有页面引用 `/home.css`、`/workbench.css`、`/shell.css` MUST 带上版本查询参数。修改样式后 MUST 提升该参数，使浏览器不会沿用旧缓存。

#### Scenario: 样式更新后不再错配

- **WHEN** 浏览器已缓存过旧版 `/home.css`
- **THEN** 页面引用的地址 MUST 带版本参数，使其取到新文件
- **AND** 顶栏结构 MUST NOT 出现新结构配旧样式导致的错位
