# kiosk-holographic-cabin Specification

## Purpose
面向悦滔 75 寸 AI 圆形全息舱（9:16 竖屏 / I5-11·8G·1050Ti）展项的端到端能力：本地 JSON 配置存储、五项能力（唤醒/打断词、菜单、闲时视频、问答记录、接近迎宾）的 REST 接口、桌面端（Tkinter）菜单上屏与连接/声音体验，以及参考音频声音克隆服务的自启与兜底。本规范定义这些可观察行为的契约，后续任何改动都 MUST 先经 OpenSpec 变更再落地。

## Requirements

### Requirement: 全息舱配置必须落本地 JSON 存储且线程安全

系统 MUST 把全息舱（kiosk）相关配置与记录落在本仓忽略路径 `data/kiosk/*.json`，由 `utils/kiosk_store.py` 以原子替换 + 线程锁读写，MUST NOT 依赖外部数据库。各能力（唤醒词、菜单、闲时视频、问答记录、接近迎宾）MUST 通过统一存储接口读写，MUST NOT 把状态写进代码或环境变量。`KIOSK_DIR` MUST 固定指向 `data/kiosk`，MUST NOT 读环境变量决定。

#### Scenario: 唤醒/打断词库读写

- **WHEN** 运营在桌面端新增一个唤醒词或打断词
- **THEN** 系统 MUST 通过 `add_wakeword(kind, word)` 写入 `data/kiosk/*.json`
- **AND** 之后 `match_wakeword(kind, text)` MUST 能命中该词，`load_wakewords()` MUST 返回全部词条

#### Scenario: 菜单库读写

- **WHEN** 运营新增、移动或启停一个菜单项
- **THEN** 系统 MUST 通过 `add_menu` / `move_menu` / `enabled_menus` 持久化
- **AND** `match_menu` MUST 能按标题/别名命中，`get_menu(item_id)` MUST 能按 id 取单项

#### Scenario: 问答记录落盘

- **WHEN** 数字人完成一次应答（无论由菜单/唤醒词/自由对话触发）
- **THEN** 系统 MUST 通过 `append_qa` 写入问答记录，含可匹配的锚点（菜单 id / 唤醒词类别）
- **AND** `query_qa` MUST 能按锚点检索，`qa_stats` / `export_qa_csv` MUST 能汇总导出

### Requirement: 全息舱 REST 接口必须覆盖五项能力并支持按 id 直达

系统 MUST 通过 `server/kiosk_routes.py` 的 `setup_kiosk_routes(app)` 在 `/api/kiosk/*` 下暴露唤醒词、菜单、闲时、问答、接近五类接口，且 MUST 提供 `GET /api/kiosk/menus/enabled`（已启用菜单列表）与 `POST /api/kiosk/menus/{id}/activate`（按 id 直达：按「固定答复 → 预置问题检索 → 标题」取答复 → 播报 → 记 qa）；命中菜单时 MUST 记录本次的 `source_name`、`course`、`material` 与 `device`。

#### Scenario: 拉取已启用菜单

- **WHEN** 桌面端 BroadcastPage 加载或每 15s 刷新
- **THEN** 系统 MUST 返回 `enabled_menus()` 的结果，MUST NOT 包含被禁用的菜单项

#### Scenario: 按 id 激活菜单并播报

- **WHEN** 用户点击舞台上的某个菜单按钮，前端 `POST /api/kiosk/menus/{id}/activate`
- **THEN** 系统 MUST 按该 id 取菜单、按固定顺序取答复、调用 `_speak` 播报，并把本次交互记入 qa_log
- **AND** MUST NOT 用标题文本模糊匹配其它菜单项导致串台

#### Scenario: 激活记录带来源

- **WHEN** 通过 `activate` 命中菜单「课程介绍」
- **THEN** 写入的记录 `source_name` MUST 为 `课程介绍`

### Requirement: 菜单库必须能上数字人屏幕并可点击介绍

桌面端 `desktop_app.py` 的 BroadcastPage MUST 在舞台底部（及全屏时独立叠加层）渲染已启用菜单的按钮条，点击按钮 MUST 触发对应菜单的 `activate` 并提示"正在介绍…/（已播报）"；MenuPage MUST 提供预览按钮能触发同样的激活流程。菜单项多于约 6 个时当前为单行，不强制换行/横滚。

#### Scenario: 舞台按钮上屏

- **WHEN** BroadcastPage 加载且存在已启用菜单
- **THEN** 舞台底部 MUST 出现这些菜单的按钮，且每 15s 线程刷新以反映后台变更

#### Scenario: 点击按钮触发介绍

- **WHEN** 用户点击某个菜单按钮
- **THEN** 前端 MUST 调 `activate_menu` 并在按钮旁显示"正在介绍…"，播报完成后显示"（已播报）"

### Requirement: 桌面端必须无白屏且按钮具备 tooltip

`desktop_app.py` 启动后 MUST 先显示"正在检测主服务…"的连接提示，再按事件泵 `_drain_ui_q` 的状态（busy/ready/fail/login）切到登录页或主界面，MUST NOT 出现整窗白屏。所有 `make_btn` 创建的按钮 MUST 支持 `tooltip` 参数，鼠标悬停时在按钮下方显示无边框提示，`None`/空不显示。

#### Scenario: 启动不白屏

- **WHEN** 启动 `desktop_app.py` 且主服务尚未就绪
- **THEN** 窗口 MUST 显示居中连接提示，MUST NOT 全白且无任何控件

#### Scenario: 按钮 tooltip

- **WHEN** 鼠标悬停在带 `tooltip` 的按钮上
- **THEN** MUST 在按钮下方显示该提示；无 tooltip 或空字符串时 MUST NOT 显示任何浮层

### Requirement: 参考音频 TTS 失败时必须有声兜底且克隆服务可自启

`tts/xtts.py` 在 speaker 无效、无音频产出或连接异常时 MUST 抛出 `TTSSynthesisError`，由上层回退到 sapi 等兜底引擎，MUST NOT 静默收尾一帧导致永久无声。当 `settings.tts.engine == xtts`（或本次会话选择 xtts）时，`desktop_app.py` 的 `ServiceLauncher` MUST 自动拉起 `scripts/voice_clone_server.py --port 50000`（端口已监听则跳过），使克隆服务随桌面端自启。

#### Scenario: 克隆服务未起时有声兜底

- **WHEN** XTTS 克隆服务未运行，数字人尝试用参考音频播报
- **THEN** `tts/xtts.py` MUST 抛错并触发 sapi 兜底，用户 MUST 能听到声音
- **AND** MUST NOT 出现"界面显示已播报但无声音"

#### Scenario: 克隆服务随桌面端自启

- **WHEN** 桌面端以 xtts 启动且 50000 端口尚未监听
- **THEN** `ServiceLauncher` MUST `Popen` `voice_clone_server.py --port 50000`
- **AND** 端口已监听时 MUST 跳过，MUST NOT 重复拉起

### Requirement: 摄像头接近必须能触发迎宾

`scripts/proximity_detect.py` MUST 作为守护进程运行，基于摄像头检测人体接近，并在检测到接近时写 proximity 状态并触发迎宾问候。接近阈值与开关 MUST 可通过 `get_proximity/set_proximity` 读写。

#### Scenario: 检测到接近

- **WHEN** 摄像头检测到人体进入接近范围
- **THEN** 系统 MUST 更新 proximity 状态并触发一次迎宾问候，MUST NOT 在连续帧中重复触发

#### Scenario: 接近开关可配置

- **WHEN** 运营关闭接近迎宾
- **THEN** `set_proximity(enabled=False)` 后守护进程 MUST 停止触发问候，已存状态 MUST 保持可查

### Requirement: 菜单项必须支持固定答复与展示素材

菜单项 MUST 支持 `answer`（固定答复）与 `material`（展示素材）两个可空字段，
并 MUST 随菜单一起持久化；`update_menu` 的字段白名单 MUST 包含这两项，
传入空串 MUST 表示清除而不是忽略。

#### Scenario: 新增菜单时带上固定答复

- **WHEN** 以 `{"title": "薪资新政", "answer": "分五档", "material": "7"}` 创建菜单
- **THEN** 返回的菜单项 `answer` 为 `分五档` 且 `material` 为 `7`
- **AND** 重新读取菜单库时这两个字段仍在

#### Scenario: 清空固定答复

- **WHEN** 对已有 `answer` 的菜单项以 `{"answer": ""}` 更新
- **THEN** 该菜单项的 `answer` 变为空串

### Requirement: 菜单命中后的作答顺序必须固定

命中菜单后的作答 MUST 按固定顺序取：① 配了 `answer` 就照 `answer` 播报；
② 否则若配了 `question` 就用它去知识库检索；③ 都没有时把菜单标题当介绍语播报。
走了 ① 时 MUST NOT 再发起知识库检索。命中后若配了 `material`，MUST 在播报前切换展示素材。

#### Scenario: 配了固定答复

- **WHEN** 菜单项 `answer` 非空且 `question` 也非空
- **THEN** 播报内容 MUST 等于 `answer`
- **AND** MUST NOT 调用知识库检索

#### Scenario: 只有预置问题

- **WHEN** 菜单项 `answer` 为空且 `question` 非空
- **THEN** 系统 MUST 用 `question` 检索知识库并按检索结果播报

#### Scenario: 两者都没有

- **WHEN** 菜单项 `answer` 与 `question` 均为空
- **THEN** 播报内容 MUST 为菜单标题
- **AND** MUST NOT 调用知识库检索

#### Scenario: 命中时切换素材

- **WHEN** 菜单项 `material` 非空
- **THEN** 系统 MUST 请求把展示画面切到该素材

### Requirement: 问答记录必须记录答案来源与设备

写一条问答记录时 MUST 记录 `source_name`（命中的菜单标题 / 唤醒词）、`course`
（命中的知识库 id）、`material`（本次展示的素材）与 `device`（设备名）。
`device` MUST 默认取主机名，并 MUST 可用环境变量 `KIOSK_DEVICE_NAME` 覆盖。

#### Scenario: 菜单命中

- **WHEN** 一次问答来自菜单「课程介绍」的激活
- **THEN** 该记录 `source` 为 `menu` 且 `source_name` 为 `课程介绍`

#### Scenario: 设备名可覆盖

- **WHEN** 环境变量 `KIOSK_DEVICE_NAME` 为 `HOLO-01`
- **THEN** 新记录的 `device` 为 `HOLO-01`，而不是主机名的默认值

### Requirement: 问答记录必须能按来源维度筛选并单条删除

`query_qa` MUST 支持按 `source_name`、`course`、`device` 过滤（空值表示不过滤）。
`GET /api/kiosk/qa_log/facets` MUST 返回这些维度在**全量记录**上的去重候选值。
`DELETE /api/kiosk/qa_log/{id}` MUST 只删除指定那一条并返回成功；id 不存在时 MUST 返回错误。

#### Scenario: 按设备筛选

- **WHEN** 以 `device=HOLO-01` 查询记录
- **THEN** 只返回该设备产生的记录

#### Scenario: 候选值覆盖分页之外的记录

- **WHEN** 某个知识库 id 只出现在按时间倒序的第 2 页之后
- **THEN** `facets` 的 `course` 候选值 MUST 仍包含它

#### Scenario: 删除单条

- **WHEN** 删除某一条记录的 id
- **THEN** 该记录 MUST 不再出现在查询结果中
- **AND** 同一批次的其它记录 MUST 全部保留
