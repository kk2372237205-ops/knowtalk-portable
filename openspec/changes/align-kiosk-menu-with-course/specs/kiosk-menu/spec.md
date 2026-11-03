# Spec Delta

## ADDED Requirements

### Requirement: 未指定课程时问答必须落到可用课程上

`rag_service` 的 `POST /rag/ask` 在请求未携带 `course_id` 时 MUST 自动选择课程，
MUST NOT 因默认值指向不存在的课程而返回空检索结果。

选择规则：取 `enabled` 为真且 `chunk_count > 0` 的课程；
**恰好一门时**使用该课程，**多门并存时**MUST NOT 猜测，改以
「课程资料中未涉及该内容」并附 `retrieval.error` 说明原因。

`AskRequest.course_id` MUST NOT 再硬编码具体课程名。

#### Scenario: 端侧菜单按钮点击

- **WHEN** 端侧点击菜单按钮触发 `/api/kiosk/menus/{id}/activate`，该请求不带 `course_id`
- **THEN** RAG MUST 使用当前唯一可用的课程（`python-base`）检索
- **AND** 响应 `retrieval.course_id` MUST 回填实际使用的课程 ID
- **AND** 菜单提示词能命中资料时，返回 `matched: true` 与引用片段

#### Scenario: 课程库存在多门课

- **GIVEN** 知识库中有两门及以上已索引且启用的课程
- **WHEN** 请求未指定 `course_id`
- **THEN** MUST NOT 返回任一课程的答案
- **AND** `retrieval.error` MUST 说明「未指定 course_id 且启用课程不唯一」

### Requirement: 端侧菜单提示词必须能在课程知识库上命中

菜单项的 `question` 是检索提示词，MUST 针对知识库中确实存在的章节提问，
MUST NOT 使用「请介绍一下这门课程」这类教材里没有对应段落的空泛问法。

单条提示词 MUST 聚焦单一主题；需要讲两个概念时，问法 MUST 保证答案合并后仍不超过
约 90 字，便于数字人口播。

#### Scenario: 点击「语法基础」

- **WHEN** 端侧点击「语法基础」（提示词 `Python 有哪些基本数据类型`）
- **THEN** 命中页 MUST 落在第 3 章附近（数据类型）
- **AND** 答案 MUST 列举具体类型，长度不超过 90 字

#### Scenario: 知识库换课程后

- **GIVEN** 菜单仍指向旧课程的提示词，而知识库已换成另一门课
- **THEN** 点击按钮 MUST 得到「课程资料中未涉及该内容」
- **AND** 该结果 MUST 在问答记录里留痕（`matched: false`），供运营发现提示词需要重写

### Requirement: 菜单按钮数量增加时不得被舞台裁切

`desktop_app` 的 `_rebuild_menu_bar()` 在渲染端侧菜单按钮条时，
按钮间水平间距 MUST 随按钮数量收紧：4 个及以下用常规间距，5 个及以上用更小间距，
以保证按钮条总宽不超出舞台可见宽度。

#### Scenario: 菜单库配置了 5 条启用项

- **WHEN** 菜单库有 5 条启用项，端侧进入开播页
- **THEN** 5 个按钮 MUST 全部完整显示在舞台底部
- **AND** 每个按钮 MUST 无外描边（深色舞台下不得出现浅色方框轮廓）
