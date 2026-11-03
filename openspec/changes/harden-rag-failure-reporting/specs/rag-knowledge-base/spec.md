# Spec Delta

## MODIFIED Requirements

### Requirement: 外部依赖不可用时返回错误

当 Ollama 服务不可达或返回错误状态时，`POST /rag/ask` 与 `POST /rag/index` MUST 返回 HTTP 500，响应体 MUST 含 `detail` 字段说明失败原因；MUST NOT 返回 `code` 为 0 的成功结构，MUST NOT 编造答案。

`detail` MUST 是**可操作**的：MUST 点名是哪个外部依赖（生成模型 / 嵌入模型 / 向量库）、失败原因（不可达 / 显存不足 / 内存不足 / 超时），并 MUST 给出处置办法（释放显存、换更小的模型、确认服务已启动）。MUST NOT 只回传底层 HTTP 客户端的原始异常文本（例如 `500 Server Error: Internal Server Error for url: ...`）。

#### Scenario: Ollama 未启动

- **WHEN** Ollama 不可达时对已建立索引的课程提问
- **THEN** 返回状态码 500
- **AND** 响应体含 `detail`
- **AND** 不返回任何 `answer` 内容
- **AND** `detail` 点名 Ollama 不可达并给出「确认 Ollama 已启动」的处置办法

#### Scenario: 生成模型显存不足

- **WHEN** 调用生成模型时因显存不足失败
- **THEN** 返回状态码 500
- **AND** `detail` 说明是生成模型显存不足
- **AND** `detail` 给出释放显存或改用更小模型的具体办法

## ADDED Requirements

### Requirement: 大模型占用 MUST 可回收

调用 Ollama 的嵌入与生成请求 MUST 携带 `keep_alive`，使模型在本次调用结束后即被卸载，MUST NOT 默认长期驻留显存与内存。`keep_alive` 的取值 MUST 可经配置调整。

#### Scenario: 问答结束后释放显存

- **WHEN** 一次问答完成
- **THEN** 生成模型 MUST 不再常驻显存
- **AND** 主程序与侧车 MUST NOT 因模型常驻而出现内存不足

#### Scenario: 嵌入模型不与生成模型同时常驻

- **WHEN** 一次问答先做嵌入再调用生成
- **THEN** 嵌入模型 MUST 在嵌入完成后即被卸载
- **AND** 生成模型加载 MUST NOT 因嵌入模型占位而失败

### Requirement: 问答失败 MUST NOT 被当作资料未覆盖

调用方 MUST 能区分「知识库没有这个内容」与「问答服务失败」：

- 检索结果为空时，MUST 呈现资料未覆盖的固定回答。
- 请求失败（网络错误、HTTP 错误、响应格式错误）时，MUST 呈现「知识库服务暂时不可用」一类的**失败提示**，并 MUST NOT 声称知识库未覆盖该内容。

#### Scenario: 服务不可用时的提示

- **WHEN** 知识库问答请求失败
- **THEN** 页面 MUST 提示服务不可用
- **AND** MUST NOT 提示「知识库未覆盖」或同义说法

#### Scenario: 资料确实没覆盖时的提示

- **WHEN** 知识库问答成功但检索结果为空
- **THEN** 页面 MUST 呈现资料未覆盖的固定回答
- **AND** MUST NOT 提示服务不可用

### Requirement: 生成的答案 MUST 是面向学员的答案正文

大模型生成的答案 MUST 是可以直接给学员听、读的**正文**，MUST NOT 包含生成过程与内部结构。具体地：

- MUST NOT 复述提示词里的规则或条款
- MUST NOT 出现检索片段的编号或标记（如「片段1」「片段4」）
- MUST NOT 出现「好的」「让我整理一下」一类开场白
- MUST NOT 使用 Markdown（星号、井号、反引号、项目符号、表格分隔线）
- MUST NOT 输出超出课程资料的说法（不得推断、不得补充资料里没有的结论）

当答案因资料不支持而无从给出时，MUST 只给出那一句固定回答，MUST NOT 附加原因说明。

#### Scenario: 答案不夹带生成过程

- **WHEN** 课程资料能回答问题
- **THEN** `data.answer` MUST 只含面向学员的答案正文
- **AND** MUST NOT 含「片段N」之类的编号或标记
- **AND** MUST NOT 复述提示词里的规则

#### Scenario: 资料不支持时不附加解释

- **WHEN** 课程资料里没有直接回答该问题
- **THEN** `data.answer` MUST 只等于那一句固定回答
- **AND** MUST NOT 附加「资料中没有提到…」「可以推断出…」之类的说明
