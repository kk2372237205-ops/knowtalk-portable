# Tasks

## 1. 变更前置

- [x] 1.1 归档已完成的 `refresh-home-visual`，使 `home-page-visual` 进入基线规格（6 需求 12 场景）。验证：`openspec archive refresh-home-visual --yes --json` 返回 `specsUpdated: true`
- [x] 1.2 核对内联样式与营销段无交集。验证：脚本比对 `<style>` 定义的类与首页营销区使用的 48 个类，交集为空

## 2. 工作台页面

- [x] 2.1 新增 `web/workbench.html`：顶部静默条（品牌、返回首页、设置、连接状态、会话号）、左栏导航、右侧三个面板，并引用 `quiz-panel.js` / `gesture-tags.js`。验证：三面板在同一时刻只有一个可见（`hidden` 属性驱动）
- [x] 2.2 迁入控制台 DOM：连接块进「开播」、预设文本块进「讲稿」、提问模式块进「提问」，不删改任何元素 id 与 `onclick`。验证：迁移前后 id 差集仅 `console`（有意移除的锚点容器）
- [x] 2.3 迁入主脚本（连接、WebRTC、讲稿、提问、语音）与 `homeStatusBanner` 提示条。验证：探针确认 15 个函数全部定义、9 个关键元素全部存在、`errs=none`
- [x] 2.4 实现面板切换与 hash 路由（`#start`/`#script`/`#quiz`，默认开播）。验证：点击后 `vis=quiz#quiz`、`script#script`、`start#start`；`#script` 与 `#quiz` 直达时对应面板是唯一无 `hidden` 的面板

## 3. 工作台皮肤

- [x] 3.1 新增 `web/workbench.css`，按工作台实际用到的类重写（不整段搬运原内联样式，删掉录音、音色管理、深色导航等 11 组死规则）。验证：卡片、表单、按钮、课件页选择器、手势标签、问答对、提问卡片与弹窗全部有对应规则
- [x] 3.2 编写工作台布局：左栏 228px 常驻 + 右栏面板，900px 以下折叠为顶部横排切换条。验证：1440/1280/1024/768/500 五档 `scrollWidth == clientWidth`、越界节点 0
- [x] 3.3 面板标题与导航项套用首页排版语言（衬线标题、墨色字重、无序号无图标块、选中态用面底色）。验证：`lint.mjs` 对 `workbench.css` **0 FAIL / 7 WARN**（7 条均为卡片与输入框的 1px 实际边界）
- [x] 3.4 修复迁移带出的 `#mainStatus` 颜色变量缺失（`--page-muted` 从未定义）。验证：连接状态文字使用 `--hp-muted` 正常着色

## 4. 首页改造

- [x] 4.1 `web/index.html` 移除控制台段、`#console` 容器与 338 行内联样式，保留营销区与演示脚本（2129 行 → 302 行）。验证：首页 id 集合收缩到 12 个营销相关元素
- [x] 4.2 三处「进入工作台」入口改为 `/workbench.html`；页脚保留页外链接。验证：`grep '#console'` 全项目 0 命中
- [x] 4.3 清理 `web/home.css` 中仅服务控制台的 44 行规则。验证：首页截图无样式塌陷，三档视口无溢出

## 5. 验证

- [x] 5.1 id 对账：迁移前 52 个 id = 工作台 42 + 首页 12 − 3 个新增面板 id − 1 个 `console`，`missing=['console']`
- [x] 5.2 功能探针：15 个函数存在、9 个元素存在、三面板切换与 hash 同步、零 JS 错误
- [x] 5.3 六档视口溢出：工作台 1440/1280/1024/768/500 全部 `scrollWidth == clientWidth`；首页 1440/768/500 同样通过
- [x] 5.4 无脚本可读：删除全部 `<script>` 的副本截图，开播面板完整可读，导航项仍在（无 JS 时不可切换，属预期）
- [x] 5.5 设计门禁：`inspect.mjs` **0 FAIL / 4 WARN**（hairline 卡片头分界、outlined-box 卡片边、off-centre 左侧固定栏导致的视觉重心、hero-void 内容不足一屏）
- [x] 5.6 主服务可访问：静态服务 `GET /index.html`、`GET /workbench.html` 均 200

## 6. 收尾

- [x] 6.1 记录门禁对照：`lint` 由迁移前 23 FAIL（旧内联样式）降到 0 FAIL；`inspect` 首轮 4 FAIL → 0 FAIL
- [x] 6.2 写入当日 `.workbuddy/memory/` 日志

## 7. 首页导航优化

- [x] 7.1 顶栏改三段结构（品牌 / 锚点 / 动作），锚点文案与段落标题同源，`三步` 改为 `流程`。验证：锚点 `href` 与 `#demo`、`#capabilities`、`#workflow`、`#questions` 一一对应，无断链
- [x] 7.2 右侧动作分出主次：`设置` 降到 `#918b7e` / 15px，`进入工作台` 改为蓝墨水实心块（`#1f3b63`、高 36px、无圆角）。验证：计算样式为 `rgba(31,59,99)` 底 + `rgb(251,248,241)` 字，高 36
- [x] 7.3 新增导航按钮独立类 `.hp-cta`。验证：正文流程段三处链接仍用 `.hp-go`，样式未被按钮规则污染
- [x] 7.4 滚动高亮：页尾脚本按 `getBoundingClientRect().top <= innerHeight * 0.42` 判定当前段落，加 `aria-current`。验证：注入 `aria-current` 后「流程」字色变为墨色，其余保持 `#6c6860`；无脚本时降级为普通锚点
- [x] 7.5 窄屏（≤900px）不再整条隐藏锚点：锚点可横向滚动、`设置` 隐藏、按钮保留；≤560px 收紧间距与字号。验证：1440/1024/768/500/390 五档 `scrollWidth == clientWidth`、越界节点 0
- [x] 7.6 页脚补一个 `进入工作台` 入口（窄屏 `设置` 让位后的替代路径）。验证：页脚链接 5 条，首条指向 `/workbench.html`
- [x] 7.7 删除首页已无对应元素的状态样式（`hp-status` 及其 `#topSessionIdDisplay` 断点规则）。验证：`grep hp-status web/home.css` 0 命中
- [x] 7.8 门禁复跑：`lint.mjs web/home.css` **0 FAIL / 0 WARN**；`inspect.mjs` 首页 **2 FAIL / 3 WARN**，其中 `empty-screens`（首屏刻意近空）与 `eyebrow`（页脚说明句被误判）为改动前既有，`off-centre`（380/398）经实测为滚动条占宽导致的测量偏差（`.hp-col` 在 1395 可用宽下为 358→1038，完全对称）

## 8. 三栏布局（参考创作台）

- [x] 8.1 骨架改三列：左 232 / 中央弹性 / 右 296，左右两栏 sticky 各自滚动。验证：1440 与 1280 三栏并排；1180 起右栏收起、900 起左栏折叠为横排
- [x] 8.2 左栏新增形象／课件／讲稿三组资源列表，内容取自 `offerAvatar`、`offerPpt`、`presetSavedList`，下拉异步填充时靠 `MutationObserver` 重建。验证：探针注入 2/1/1 项后 `picks=2/1/1`，点击第二项后 `offerAvatar=v1`
- [x] 8.3 点讲稿条目等价于该下拉自身的 `onchange`：载入正文并切到 `#script`。验证：探针点击后 `presetText` 有内容、`hash=#script`、`panel=panel-script`
- [x] 8.4 底部常驻操作条：把 `btnStart`／`btnStop`／`statusBadge`／`sessionIdDisplay`／`mainStatus`／`chatWindowLink` 从连接卡片移入 `.wb-dock`，id 一个不改。验证：9 个关键函数全在、`errs=none`
- [x] 8.5 右栏「开播实况」七项全部镜像真实值：连接、会话、播报、形象、音色、讲稿字数（剔除标签后按 150 字/分钟折算）、提问组数（数 `.quiz-pair-row`）。验证：`live=乙形象`、`script=19 字 · 约 1 分钟`、`quiz=1 组`
- [x] 8.6 左栏点击后派发 `wb:sync`，右栏立即重算，不必等 1.2s 轮询。验证：点击讲稿后同一次探针即读到 `script=19 字`
- [x] 8.7 无脚本降级：三栏结构、右栏占位值、底部按钮均可见。验证：删掉全部 `<script>` 的副本截图
- [x] 8.8 门禁：`lint` 0 FAIL / 7 WARN（与之前同口径）；`inspect` 0 FAIL / 4 WARN；1440/1280/1180/1024/900/768/500 七档 `scrollWidth == clientWidth`、越界节点 0

## 9. 左栏导航层级（2026-10-25 追加）

- [x] 9.1 左栏由「八组平铺」改为「四个一级分组 + 缩进子项」：工作区 / 本次素材 / 流程 / 站点。验证：`wb-nav-title` 4 条、`wb-nav-kids` 4 个，`test_workbench_nav.py` 断言通过
- [x] 9.2 组标题是不可点击的 `<p>`，右侧接一根细线收到栏边；子项比标题缩进 18px 并串在一条竖向导引线上。验证：`.wb-nav-title::after` 与 `.wb-nav-kids { border-left }` 生效，截图 `docs/design/navhier-1440.png`
- [x] 9.3 三个面板项加两位序号 `01` `02` `03`，选中态只在子项上出现（组标题永不带 `.is-on`）。验证：`body.wb` 下选中项序号转蓝墨水，组标题字色不变
- [x] 9.4 形象 / 课件 / 讲稿 从与站点链接平级改为「本次素材」的第三层（`wb-nav-sub` + `wb-picks`），资源列表逻辑与 `data-pick` 绑定不变。验证：点击仍同步 `offerAvatar`；`#pickAvatar` 等 id 未变
- [x] 9.5 「当前流程」由独立分组改为「流程」分组下的一个子项，`#wbFlowGroup` 容器删除，`#wbFlowName` 默认 `hidden`、按图开播时显示。验证：`?workflow=<id>` 下左栏「流程」组出现流程名并可点回画布
- [x] 9.6 窄屏横排重排：组标题隐藏、序号隐藏、组间补竖线；被收起的「本次素材」后面不再留孤立分隔线。验证：500px 截图中分隔线只出现在可见分组之间
- [x] 9.7 门禁与回归：`lint.mjs web/workbench.css` **0 FAIL / 7 WARN**；8 页 × 5 档视口 `over=0`
