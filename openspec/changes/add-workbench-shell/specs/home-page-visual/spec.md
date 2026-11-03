# Spec Delta

## ADDED Requirements

### Requirement: 首页导航必须分层并常驻出口

首页顶栏 MUST 单行三段排列：品牌在左，页内段落锚点在中间，动作在右。锚点文案 MUST 与对应段落标题同源（`演示`、`零件`、`流程`、`问题` 分别指向「演示面板」「一堂课的四个零件」「从一张照片到一堂课」「常见问题」）。

右侧动作 MUST 分出主次：`设置` MUST 使用比正文更淡的字色与更小一号字；`进入工作台` MUST 复用首屏主按钮的蓝墨水实心块（`#1f3b63`、无圆角、无投影）。

页面滚动时，视口上部的当前段落对应锚点 MUST 被标记为当前项（`aria-current`）并转为墨色，其余锚点 MUST 保持次级灰度。脚本缺席时导航 MUST 退化为普通锚点，且不改变页面可读性。

渲染宽度 ≤900px 时，四个锚点与 `进入工作台` MUST 仍然可见（锚点可横向滚动），`设置` MUST 让位到页脚；页脚 MUST 保留一个 `进入工作台` 入口。

#### Scenario: 桌面端三段对齐

- **WHEN** 在 1440px 视口查看首页顶栏
- **THEN** 品牌、四个锚点、`设置`、`进入工作台` MUST 在同一行
- **AND** 「进入工作台」MUST 呈现为 `#1f3b63` 实心块，高度 MUST NOT 超过 40px
- **AND** 顶栏与首屏正文 MUST 共用同一条左右边线

#### Scenario: 滚动时点出当前段落

- **WHEN** 把「流程」段落滚到视口上部
- **THEN** 「流程」MUST 带 `aria-current`，且字色为墨色 `#211f1a`
- **AND** 其余三个锚点字色 MUST 保持 `#6c6860`

#### Scenario: 窄屏不丢导航

- **WHEN** 在 390px 宽视口查看首页
- **THEN** 四个锚点与 `进入工作台` MUST 全部可见，或可通过横向滚动到达
- **AND** 顶栏 MUST NOT 造成页面横向溢出

## MODIFIED Requirements

### Requirement: 换版不得改变首页控制台的功能契约

首页 MUST NOT 再内嵌控制台表单：数字人连接、预设文本、提问模式三块配置、以及全部表单控件与容器 MUST 迁至独立工作台页 `/workbench.html`。迁移 MUST 保留每一处对外可观察能力——所有元素 id、所有按钮的点击行为、`/quiz-panel.js` 与 `/gesture-tags.js` 的脚本引用，以及指向 `/avatar.html`、`/knowledge-base.html`、`/settings.html`、`/admin.html` 的链接。首页继续保留 `#capabilities`、`#workflow` 等相关页内锚点与页脚链接。

#### Scenario: 换版后核对控制台控件

- **WHEN** 对比迁移前后的控制台 DOM
- **THEN** 迁移前存在的每个控制台元素 id MUST 在 `/workbench.html` 存在且唯一
- **AND** 迁移前引用的脚本 MUST 仍被引用
- **AND** 首页 MUST NOT 残留这些 id

#### Scenario: 通过页内锚点跳转

- **WHEN** 点击导航或首屏按钮上的「进入工作台」以及工作流相关链接
- **THEN** 浏览器 MUST 导航到 `/workbench.html`
- **AND** 工作台 MUST 显示与迁移前一致的连接、讲稿、提问三块配置
