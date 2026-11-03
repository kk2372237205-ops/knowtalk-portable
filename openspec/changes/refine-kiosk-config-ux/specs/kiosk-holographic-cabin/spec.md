# Spec Delta

## ADDED Requirements

### Requirement: 导航必须以「开播」为第一项

演讲模式与展览模式的左侧导航 MUST 把「开播」渲染为第一项，
MUST NOT 让用户在分组里寻找开播入口。模式切换后各模式 MUST 记住上次停留的页面。

#### Scenario: 演讲模式的导航顺序

- **WHEN** 用户切到「演讲模式」
- **THEN** 导航第一行 MUST 是「开播」
- **AND** 其后依次为「背景设置」「讲稿」「提问」「课件」「课程知识库」，最后是「通用」分组

#### Scenario: 展览模式的导航顺序

- **WHEN** 用户切到「展览模式」
- **THEN** 导航第一行 MUST 是「开播」
- **AND** 「全息舱」分组 MUST 包含「背景设置」「唤醒与打断」「菜单库」「待机与迎宾」「问答记录」

### Requirement: 画面背景必须是独立页面

画面背景 MUST 由独立页面（`BackgroundPage`）维护，MUST NOT 嵌在开播页的配置栏里。
该页 MUST 提供形象选择、纯色色板、已上传图片、上传入口与预览。

#### Scenario: 切换纯色背景

- **WHEN** 用户在「背景设置」点选一个色板
- **THEN** 前端 MUST 调 `PUT /api/avatar/background`（`type=color`）
- **AND** MUST 把该色板标为选中态，并提示「重新连接后生效」

#### Scenario: 形象未抠像

- **GIVEN** 当前形象没有抠像素材
- **WHEN** 打开背景设置或改选背景
- **THEN** 页面 MUST 提示需要先跑抠像脚本，MUST NOT 静默失败

### Requirement: 参考音色只在形象与设置中维护

开播页 MUST NOT 提供参考音色选择器；音色 MUST 只在「形象与设置 → 语音与音频」修改。
开播页 MUST 显示当前生效的音色，并提供跳到该设置的入口。

#### Scenario: 开播沿用已保存音色

- **WHEN** 用户在开播页点「开始连接」
- **THEN** 连接请求 MUST NOT 覆盖 `tts.ref_file`
- **AND** 数字人 MUST 使用「形象与设置」里已保存的音色

### Requirement: 接近检测参数必须使用自绘控件并可一键启停

「待机与迎宾」页的接近检测参数 MUST 使用主题化控件（圆角下拉 / 分段选择器 / 自绘滑块），
MUST NOT 使用 `tk.Scale` 或裸 `ttk.Combobox`。灵敏度 MUST 显示当前数值与定性描述。

检测总开关 MUST 直接启停检测进程：打开即落盘 `enabled=true` 并拉起
`scripts/proximity_detect.py`，关闭即落盘 `enabled=false` 并按 pid 结束进程。

#### Scenario: 拨动总开关

- **WHEN** 用户把「启用人体接近检测」拨到开
- **THEN** MUST 立即写入 `data/kiosk/proximity.json` 的 `enabled`
- **AND** MUST 拉起检测进程
- **AND** 页面状态行 MUST 在一轮轮询内显示「感应运行中」

#### Scenario: 只改参数不动开关

- **WHEN** 用户改了灵敏度后点「保存感应参数」
- **THEN** MUST 只写入参数与当前开关值，MUST NOT 额外启停进程

### Requirement: 闲时必须以数字人动作代替静态视频

`idle.action` MUST 描述「哪个形象的哪个动作」：`{"avatar": "<id>", "gesture": "<id>"}`。
界面 MUST 提供形象与动作两级选择，动作下拉 MUST 随形象变化重新拉取
`GET /api/avatars/{id}/gestures`。

`idle.video` MUST 保留为历史字段但不再由界面写入（该字段没有任何消费方）。

#### Scenario: 选择闲时动作

- **WHEN** 用户选中形象 `musetalk_ke` 与动作 `idle_sway` 并保存
- **THEN** `GET /api/kiosk/idle` MUST 返回
  `action = {"avatar": "musetalk_ke", "gesture": "idle_sway"}`

#### Scenario: 有人离开后进入闲时

- **GIVEN** 闲时动作已配置且当前有会话
- **WHEN** 感应器上报 `present` 由 true 变为 false
- **THEN** 主服务 MUST 请求该动作并**循环播放**（播满一遍不清回默认素材）

### Requirement: 接近检测必须能触发指定动作

`proximity.trigger_action` MUST 描述「检测到有人时播哪个形象的哪个动作」。
有人走近时主服务 MUST 先请求该动作（播放一遍），再按 `auto_welcome` 播报迎宾语。

配置形象与当前会话形象不一致时 MUST 跳过动作播放并记一条日志，MUST NOT 强行播放。

#### Scenario: 有人走近

- **GIVEN** `trigger_action = {"avatar": "musetalk_ke", "gesture": "wave_one"}`
- **WHEN** 感应器上报 `present` 由 false 变为 true
- **THEN** 主服务 MUST 调用该会话的 `request_gesture("wave_one")`
- **AND** 返回体 `action` MUST 为 `gesture` 或 `welcome`（两者都发生时以 `welcome` 为准）

#### Scenario: 形象不匹配

- **GIVEN** 配置的动作属于形象 A，当前会话是形象 B
- **WHEN** 检测到有人
- **THEN** MUST NOT 在形象 B 上播放形象 A 的动作
- **AND** MUST 继续播报迎宾语
