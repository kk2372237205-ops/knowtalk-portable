# home-page-visual Specification

## Purpose

约束首页的视觉语言：纸面底色、衬线展示字、单栏左对齐的营销排版，以及导航的分层与常驻出口。首页是整站视觉基准，其余页面的令牌与字号都以它为源。

## Requirements

### Requirement: 首页营销层必须是纸面单栏排版

首页 `.hero`、`.sec`、`.foot` 三类区块 MUST 共享同一条内容左边界（栏宽上限 680px，窄屏退化为两侧各留 20–24px 边距），区块之间 MUST 只用纵向留白分隔，MUST NOT 用整段背景色块、卡片栅格或分隔装饰线切分。

展示字（h1 / h2 / 段落小标题）MUST 使用系统衬线字体栈（Georgia 打头、中文回退 Songti SC / STSong / SimSun），正文与界面片段 MUST 使用系统无衬线字体栈。页面 MUST NOT 通过外链请求任何字体服务。

#### Scenario: 在 1440 宽视口打开首页

- **WHEN** 浏览器视口宽度为 1440px，打开首页
- **THEN** 首屏标题、引导段、各区块标题与页脚文字 MUST 左对齐于同一条竖直基准线
- **AND** 内容栏宽度 MUST NOT 超过 680px
- **AND** 相邻区块之间 MUST 只有留白，没有横贯整页的分隔带或背景色块

#### Scenario: 缺少衬线字体时回退

- **WHEN** 访问的机器上不存在 Georgia、Songti SC 与 STSong
- **THEN** 展示字 MUST 回退到 SimSun 或通用 serif，不得回退为无衬线字体
- **AND** 正文 MUST NOT 因字体缺失改变行高与栏宽

### Requirement: 首页营销层不得使用装饰性组件

首页 MUST NOT 出现以下装饰：区块上方的小标签（eyebrow / kicker）、圆形或方块形状的步骤编号徽章、带彩色底色的图标块、带勾选图标的清单、悬浮在内容之上的卡片、模拟浏览器窗口的三色圆点与标题栏、渐变按钮或带投影的悬停位移。

#### Scenario: 审查首页营销层 DOM

- **WHEN** 检查首页 hero、能力、流程、常见问题四个区块的标记与样式
- **THEN** MUST NOT 存在 eyebrow/kicker 元素
- **AND** MUST NOT 存在图标字体渲染的装饰图标或彩色底色图标容器
- **AND** 步骤编号 MUST NOT 呈现为圆形徽章或强调色块

#### Scenario: 悬停在按钮与链接上

- **WHEN** 指针悬停在主按钮或文本链接上
- **THEN** 变化 MUST 只发生在颜色、下划线或光标上
- **AND** MUST NOT 出现位移、投影或缩放

### Requirement: 首页必须能动手看到提问逻辑

首页 MUST 提供一块讲课演示面板，包含播放区（页码、当页标题、该页要点、正在念的一句）与控件区（讲稿位置、提问点、手势、语速、判分、输出）。拖动「讲稿位置」时，播放区 MUST 随位置更新页码、标题、要点与念稿文字；当位置等于预设的提问页时，播放区 MUST 同时呈现「已停下等人作答」状态与题卡，离开该页 MUST 恢复为讲课中且题卡消失。

#### Scenario: 把讲稿位置拖到提问页

- **WHEN** 使用者把「讲稿位置」拖到预设提问页（第 3 页）
- **THEN** 播放区 MUST 显示该页标题与该页念稿
- **AND** MUST 显示「已停下 · 等人作答」状态
- **AND** MUST 显示一道题干、三个选项与一条判定依据

#### Scenario: 把讲稿位置拖离提问页

- **WHEN** 使用者把「讲稿位置」拖到第 3 页以外的位置
- **THEN** 题卡 MUST 隐藏
- **AND** 状态 MUST 变为讲课中

### Requirement: 首页不得因脚本缺席而不可读

首页内容 MUST 在脚本未执行、执行失败或被禁用时完整可读。任何入场动效 MUST 由脚本确认可以运行后才隐藏元素；当 `prefers-reduced-motion: reduce` 生效时 MUST 不启用入场动效。

#### Scenario: 浏览器禁用 JavaScript

- **WHEN** 使用者禁用了 JavaScript 并打开首页
- **THEN** 标题、引导段、能力段、流程段、常见问题与页脚 MUST 全部可见
- **AND** 演示面板 MUST 显示第 3 页停下出题这一状态，而不是空白

#### Scenario: 系统开启减少动态效果

- **WHEN** 操作系统设置了减少动态效果
- **THEN** 页面 MUST NOT 播放入场位移与渐显
- **AND** 所有内容 MUST 一次性可见

### Requirement: 换版不得改变首页控制台的功能契约

首页控制台区域 MUST 保留换版前的全部对外可观察能力：所有表单控件与容器的元素 id、所有按钮的点击行为、`/quiz-panel.js` 与 `/gesture-tags.js` 的脚本引用、`#capabilities`、`#workflow`、`#console` 三个页内锚点，以及指向 `/avatar.html`、`/knowledge-base.html`、`/settings.html`、`/admin.html` 的链接。

#### Scenario: 换版后核对控制台控件

- **WHEN** 对比换版前后的首页 DOM
- **THEN** 换版前存在的每个控制台元素 id MUST 仍然存在且唯一
- **AND** 换版前引用的脚本 MUST 仍被引用

#### Scenario: 通过页内锚点跳转

- **WHEN** 点击导航或首屏按钮上的「进入工作台」以及工作流相关链接
- **THEN** 页面 MUST 滚动到控制台区域
- **AND** 控制台 MUST 显示与换版前一致的连接、讲稿、提问三块配置

### Requirement: 首页在常见视口下不得横向溢出

首页 MUST 在 1440px、768px、390px 三个视口宽度下不发生横向溢出，即文档滚动宽度 MUST NOT 超过客户端宽度。

#### Scenario: 在三个视口宽度下量测

- **WHEN** 分别以 1440px、768px、390px 宽度渲染首页
- **THEN** 每一档的文档滚动宽度 MUST 等于客户端宽度
- **AND** MUST NOT 存在右边界超出客户端的可见元素
