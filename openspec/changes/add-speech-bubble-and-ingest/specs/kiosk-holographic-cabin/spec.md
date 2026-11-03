# Spec Delta

## ADDED Requirements

### Requirement: 本机进程的上报入口 MUST 免登录放行

检测器（`scripts/proximity_detect.py`）是独立进程，没有、也不该有登录态。
`POST /api/kiosk/proximity/event` MUST 在**来源为本机回环地址**时放行，
不因登录保护返回 401。

放行 MUST 同时满足「路径命中白名单」与「来源是回环地址」两个条件：
其它 kiosk 接口（读状态、改配置）与来自局域网的外部请求 MUST 照旧要求登录。

#### Scenario: 检测器上报能进来

- **WHEN** 已开启登录保护，本机检测器向 `/api/kiosk/proximity/event` POST 一条 `present=true`
- **THEN** 服务 MUST 返回 200

#### Scenario: 外部来源仍被拦

- **WHEN** 局域网内另一台机器向同一路径 POST
- **THEN** 服务 MUST 返回 401

#### Scenario: 其它接口不跟着放行

- **WHEN** 未登录的本机请求访问 `GET /api/kiosk/state` 或 `PUT /api/kiosk/idle`
- **THEN** 服务 MUST 返回 401

### Requirement: 动作播放结果 MUST 可观测

主服务 MUST 把最近一次动作请求的结果写进 `/api/kiosk/state` 的 `last_gesture`，
含动作名、是否发出、原因与时间。桌面端 MUST 把它显示在感应状态行上。

原因 MUST 至少区分：未配置动作、没有可用会话、配置形象与当前会话形象不匹配、请求异常。

#### Scenario: 形象不匹配时能看出原因

- **WHEN** 配置的 `trigger_action.avatar` 与当前会话的形象不一致，人走近触发迎宾
- **THEN** 迎宾语照常播报
- **THEN** `last_gesture.ok` 为 false，且 `note` 写明「形象不匹配：配置 X，当前 Y」

#### Scenario: 动作真的发出去

- **WHEN** 配置完整、会话在线、形象一致，检测器上报 `present=true`
- **THEN** 主服务 MUST 在会话上调用 `request_gesture(<gesture>)`
- **THEN** `last_gesture.ok` 为 true

### Requirement: 动作素材 MUST 生成后即可播

动作生成流程 MUST 在拆帧、归一化、写元数据之后自动补齐 MuseTalk 资产
（`gestures/<gid>/musetalk/` 下的 latent / 遮罩 / 遮罩框 / 人脸框）。

补齐失败 MUST NOT 让整次生成判失败，但 MUST 记一条 warning 说明该动作在当前形象上无法播放。

#### Scenario: 新生成的动作立刻可用

- **WHEN** 为 MuseTalk 形象生成一个新动作并完成
- **THEN** 该动作的 `musetalk/` 资产 MUST 已就绪
- **THEN** 把它配成闲时动作或迎宾动作后播放 MUST 生效，不需要再手工点「准备」

### Requirement: 屏幕对话气泡 MUST 贴在画面右下角并流式增长

数字人开口时，桌面端 MUST 在 **9:16 画面矩形**（不是画布）的右下角显示对话气泡，
气泡 MUST 随文本增长而重新换行与增高，MUST 只保留最后若干行，未说完时 MUST 带光标提示。

气泡内容 MUST 取「谁后开始念的那句」：LLM 流式回答或直接朗读的整句（欢迎语 / 菜单介绍）。
说话结束且不再出声一段时间后 MUST 自动收起。
气泡可见期间 MUST NOT 再绘制底部提示语与端侧菜单条（它们和气泡抢同一块地方）。

#### Scenario: 流式显示

- **WHEN** 一轮回答正在逐句生成
- **THEN** 气泡 MUST 在每次文本增长后重绘，末尾显示光标
- **THEN** 气泡 MUST 落在画面右下角内，MUST NOT 压到画面两侧的空白

#### Scenario: 欢迎语也进气泡

- **WHEN** 检测到有人，主服务直接朗读迎宾语（没有 LLM 问答）
- **THEN** 气泡 MUST 显示这句迎宾语，MUST NOT 留着上一轮问答的旧文本

#### Scenario: 说完收起

- **WHEN** 回答结束且数字人已停止发声超过 8 秒
- **THEN** 气泡 MUST 消失，端侧菜单条与提示语 MUST 恢复显示

## MODIFIED Requirements

### Requirement: 摄像头接近必须能触发迎宾

`scripts/proximity_detect.py` MUST 作为守护进程运行，基于摄像头检测人体接近，并在检测到接近时写 proximity 状态并触发迎宾问候。接近阈值与开关 MUST 可通过 `get_proximity/set_proximity` 读写。

#### Scenario: 检测到接近

- **WHEN** 摄像头检测到人体进入接近范围
- **THEN** 系统 MUST 更新 proximity 状态并触发一次迎宾问候，MUST NOT 在连续帧中重复触发

#### Scenario: 接近开关可配置

- **WHEN** 运营关闭接近迎宾
- **THEN** `set_proximity(enabled=False)` 后守护进程 MUST 停止触发问候，已存状态 MUST 保持可查

#### Scenario: 配了触发动作就要真的动

- **WHEN** 已配置 `trigger_action` 且会话在线、形象一致，检测到有人
- **THEN** 主服务 MUST 先请求该动作再念迎宾语
- **THEN** 动作 MUST 播完整一遍后回到默认素材；配置为闲时动作时 MUST 循环播放
