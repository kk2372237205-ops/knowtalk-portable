# 统一全站视觉与页面归属

## 1. 变更前置

- [x] 1.1 建 `unify-site-visual`，`validate --strict` 通过
- [x] 1.2 盘点四个工具页的既有样式变量与顶栏 DOM，确认冲突面
      （四页各有一套 `--primary/--line/--bg/--card/--ink/--muted`，在 `body.site` 上重定向即可整体换皮）

## 2. 全站外壳

- [x] 2.1 新增 `web/shell.css`：纸面令牌、`.site-bar` 顶栏、`.site-body` 容器、页面标题区
- [x] 2.2 覆盖 Bootstrap 控件为纸面语言：按钮、输入框、下拉、卡片、表格、标签、开关、分段器
- [x] 2.3 首页与工作台的样式引用加版本号（`home.css?v=3`、`workbench.css?v=3`、`shell.css?v=2`）

## 3. 页面接入

- [x] 3.1 `avatar.html`：换顶栏、去深色底与圆点纹理、接 shell.css
- [x] 3.2 `knowledge-base.html`：换顶栏、去深蓝 topbar、接 shell.css
- [x] 3.3 `settings.html`：换顶栏、加「运行期设置 / 管理后台」分段、管理后台内嵌
- [x] 3.4 `admin.html`：换顶栏、标注已并入设置页（`?embed=1` 时隐藏二级页头）

## 4. 验证

- [x] 4.1 四页与首页底色、字体、主按钮一致
- [x] 4.2 六档视口无横向溢出
- [x] 4.3 `/settings.html#admin` 直达管理后台；`/admin.html` 仍可用并有回跳链接
- [x] 4.4 前端与后端测试与基线一致
- [x] 4.5 门禁 `lint` / `inspect` 不退步

## 5. 数字人展示页接入（2026-10-25 追加）

- [x] 5.1 新增 `web/display.css`：本页变量重定向到纸面令牌 + 逐块收口
- [x] 5.2 `knowtalk-chat.html` 接 `body.site`，按 内联样式 → `shell.css` → `display.css` 的顺序加载
- [x] 5.3 顶栏由蓝紫渐变改纸面单条页头：课程名（宋体）+ 切换课程 + 暂停播放 + 状态 + 返回工作台
- [x] 5.4 三栏、字幕条、对话气泡、提问面板、引用标签统一到纸面令牌
- [x] 5.5 窄屏：页头收纳（暂停按钮只留图标、隐藏返回工作台），折叠开关钉到「课件 / 对话」分隔线上
- [x] 5.6 不引入后台四项导览（学生端只看到课程与状态）

## 6. 实测记录（2026-10-25）


### 6.1 跨页溢出（iframe 同源探针，8 页 × 5 档）

| 视口 | index | workbench | avatar | knowledge-base | workflow | settings | knowtalk-chat | login |
|---|---|---|---|---|---|---|---|---|
| 1440 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 1280 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 1024 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 768 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 500 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

数值为 `scrollWidth - clientWidth`；四个工具页 `.site-bar` 全部存在。探针页 `web/_probe.html` 用完即删。

### 6.2 窄屏收尾修的两个问题

- 设置页 `#mainArea` 的 `.page-head` 里还留着一个重复的「工作台」回跳按钮，
  500px 下被 flex 压成竖排三行、压在正文右缘 → 直接删掉（顶栏已有入口）。
- 窄屏页头对齐失控：各页自带媒体查询只改 `flex-direction: column`，没管 `align-items`，
  而 `shell.css` 的 `body.site .page-head { align-items: flex-end }` 特异性更高 → 标题被推到右侧。
  补 `@media (max-width: 720px)` 显式 `align-items: flex-start` + `text-align: left`。
- 数字人卡的状态标签在 flex 纵列里被拉伸成整行 → `align-self: flex-start` 收紧。

### 6.3 门禁与测试

- `shell.css`：0 FAIL / 3 WARN
- `display.css`：0 FAIL / 1 WARN（`height: 100vh` 兜底，紧跟一行 `100svh`）
- `workbench.css` + `home.css`：0 FAIL / 7 WARN
- 后端：849 passed（820 基线 + 29 新增）
- 前端：187/188（唯一失败为预存在的 `hook-gate-shell`）
