# 主题化界面、应用设置面板与形象/动作管理

## 为什么

四件事都指向同一个问题：界面是给「讲师课前配置」用的，但配置入口散、控件是系统原生样式、
外观只有一套米白配色。

1. **外观不可选**：颜色是 14 个模块级常量（`C_BG` / `C_INK` / `C_GREEN`…），写死在代码里。
   想要科技风或蓝白风只能改源码。
2. **密钥没有统一入口**：LLM 的 key 在设置文件里、MiniMax 的 key 只能手改 json，
   讲师看不到自己配了什么、也测不了通不通。
3. **单选框/复选框是系统灰白样式**：语音引擎三选一用 Radiobutton，开关用 Checkbutton，
   在彩色卡片上一眼看不出「当前是哪个」，深色主题下小方框几乎和背景同色。
4. **参考音色是几十项长下拉**：把 `/api/voices/system` 返回的语音全塞进下拉框，
   名字是「普通话 · 女声 2」这种，选音色等于开盲盒。
5. **形象不能改名/删除**：后端 `PUT /api/avatars/{id}`、`DELETE /api/avatars/{id}` 早就有了，
   但桌面端没有按钮；动作制作接口（`/api/gestures/make`）同样没有入口。

## 做什么

1. **`utils/theme.py`：4 套语义色板**（蓝白 / 科技 / 商务 / 米白），
   `desktop_app` 启动时按 `data/settings/theme.json` 取色；默认蓝白风。
   语义键（bg/card/line/ink/accent/muted/danger/stage/nav_on/hover/sel/…）与业务代码解耦，
   新增主题只改配色不动业务。
2. **右上角「设置」改为「应用设置」窗口**（`AppSettingsDialog`）：
   - API 密钥：LLM（Key / 接口地址 / 模型名，可测连通性）、MiniMax（Key / 视频模型）
   - 外观主题：4 张带色块预览的卡片，选中后「应用并重启界面」
   - 关于：主服务地址、配置文件位置、当前主题
3. **自绘控件替代原生勾选**：
   - `SegmentedControl` 分段选择器（替代 Radiobutton，也让设置窗口内部做分栏）
   - `ToggleSwitch` + `make_switch`（替代 Checkbutton，共替换 9 处）
4. **参考音色改卡片墙**：`VoicePicker` 只保留 6 个经典音色（晓晓/云希/云健/晓伊/云扬/晓辰，
   各带一句特征描述）+ 前 3 个克隆音色；保留手填 `S_xxx` 复刻 ID 的入口。
   开播页（2 列紧凑）与设置页（3 列）共用同一控件。
5. **形象库补齐操作**：「重命名」（改目录名并同步 `avator_info.json` 的 `avatar_id`）、
   「删除」（二次确认）、「制作新动作」（选预设 → 提交作业 → 轮询进度）。
6. **动作预设 +2**：`idle_sway`（待机微摆，极轻的重心移动、末帧回位便于循环）、
   `wave_one`（单手打招呼，只抬右臂、左臂全程不动）。

## 影响

- 新增 `utils/theme.py`、`data/settings/theme.json`。
- `desktop_app.py`：新增 `SegmentedControl` / `ToggleSwitch` / `make_switch` / `VoicePicker` /
  `ask_text` / `ask_confirm` / `AppSettingsDialog` / `GestureMakeDialog` / `DesktopApp.restart_ui`；
  `SettingsPage` 改为滚动容器（卡片变高后一屏放不下）；9 处 `Checkbutton`/`Radiobutton` 全部替换。
- `server/avatar_routes.py`：`rename_avatar` 改名后同步 `avator_info.json` 里的 `avatar_id`。
- `utils/gesture_presets.py`：预设 16 → 18。
- 不改任何对外接口路径与字段；`material` / `gen` / `avatar` 三个旧页 key 仍兼容。
- 主题切换需重启界面进程（Tk 颜色创建即固定），但**不重启主服务**，
  故不会重新加载 MuseTalk 模型。
