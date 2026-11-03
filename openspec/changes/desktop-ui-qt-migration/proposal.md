# 桌面端界面迁移到 Qt（PySide6）

## Why

Tkinter 版 `desktop_app.py` 8246 行，实际跑起来有两个绕不过去的问题：

1. **启动时一片黑块**。根因不是 Tk 渲染引擎老，而是 UI 线程被堵：`MainPage.__init__`
   一次构造 13 个页面，其中 `StatusPage` / `CoursePage` / `GenAvatarPage` /
   `KnowledgePage` / `WakePage` / `MenuPage` / `IdlePage` 七个类的构造函数末尾都有
   同步 HTTP（`timeout=3~5`）。主服务冷启动要加载模型（1-2 分钟），这段窗口里
   主线程不处理重绘，没画到的区域就露出底色。历史处理（`desktop_app.py:2071`
   把窗口底色刷成内容色）只是遮盖。
2. **样式改不动**。Tk 的颜色在控件创建时固化，换主题必须重启界面进程；圆角、阴影、
   悬停过渡都要手绘 Canvas。已经为此写过 `SegmentedControl`、`ToggleSwitch`、
   `ThemedSlider`、`RoundedCombo` 四个自绘控件，维护成本高。

Qt 在这两点上是结构性改善：QSS 运行期生效、布局是约束式、渲染与业务线程天然分离。

## What Changes

- 新增 `ui_qt/` 包（PySide6），与现有 `desktop_app.py` 并存，不替换：
  - `theme.py`：语义色板 → QSS，复用 `utils/theme.py` 的四套主题，**换主题即时生效**
  - `widgets.py`：`Card` / `button` / `line_edit` / `Segmented` / `Switch` /
    `FormRow` / `SwitchRow` / `VideoStage` / `NavButton`
  - `pages.py`：`BroadcastPage` / `IdlePage` / `AppearancePage` 三个原型页
  - `app.py`：主窗口（侧边导航 + 懒加载 `QStackedWidget` + 主题热切换）
- **页面懒加载**：点进某页才构造，启动只画骨架。这条同时是旧版黑屏的修法。
- 视频上屏不再走 `PIL.ImageTk.PhotoImage` 每帧重建位图，改为 numpy 缓冲直接包成
  `QImage`，在 `paintEvent` 里绘到目标矩形（不在每帧做 `QImage.scaled` 全图重采样）。

## Impact

- Affected specs: `desktop-ui-shell`（新增）
- Affected code: `ui_qt/`（新增）；`desktop_app.py` 不动
- 依赖：`PySide6-Essentials==6.8.3`（装进 `livetalking` 环境，约 350MB）
- 风险：Qt 打包体积（PyInstaller 产物约 80-120MB）；WebView2 未采用，无额外运行时依赖
- 已做：开播页真实链路（形象列表 → 主服务设置 → aiortc 收流 → 声音输出），
  端到端截图验证 20fps（= 服务端输出帧率，非 UI 上限）
- 未做：**登录页**（主服务开着 `require_login` 时 Qt 版连不上）、其余 10 个页面、
  待机与迎宾的数据绑定、打包流程
