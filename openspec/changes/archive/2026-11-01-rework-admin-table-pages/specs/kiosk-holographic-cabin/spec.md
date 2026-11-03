# 全息舱（配置答案 / 问答记录维度）

## ADDED Requirements

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

## MODIFIED Requirements

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
