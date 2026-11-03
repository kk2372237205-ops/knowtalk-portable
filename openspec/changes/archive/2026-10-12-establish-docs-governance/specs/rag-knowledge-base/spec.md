# Spec Delta

## Purpose

定义 KnowTalk RAG 知识库服务对外可观察的行为契约：健康检查、课程问答接口的请求与响应结构、检索与重排约束、分片与来源元数据、资料上传与重新索引语义，以及课程隔离和外部依赖不可用时的表现。

## ADDED Requirements

### Requirement: RAG 服务健康检查

`GET /health` MUST 返回 HTTP 200，响应体 MUST 为 `{"code": 0, "data": {"status": "ok"}}`，并且 MUST NOT 依赖 Ollama、向量库或重排模型是否可用。

#### Scenario: 服务已启动但 Ollama 未运行

- **WHEN** 客户端请求 `GET /health`
- **THEN** 响应状态码为 200
- **AND** 响应体为 `{"code": 0, "data": {"status": "ok"}}`

### Requirement: 课程问答请求的字段约束

`POST /rag/ask` MUST 接受 JSON 请求体，字段为 `question`（必填，长度 1–2000）、`course_id`（默认 `rail-passenger-traffic`，MUST 匹配 `^[a-zA-Z0-9_-]+$`）、`session_id`（可选）、`top_k`（默认 10，取值 1–20）、`speak`（默认 false）。字段缺失、超长或不符合规则时 MUST 返回 422，且 MUST NOT 执行检索与生成。

#### Scenario: question 为空

- **WHEN** 请求体为 `{"question": ""}`
- **THEN** 服务返回状态码 422

#### Scenario: course_id 含非法字符

- **WHEN** 请求体的 `course_id` 为 `course/001`
- **THEN** 服务返回状态码 422

#### Scenario: 只提供 question

- **WHEN** 请求体仅包含 `{"question": "什么是同比增长率"}`
- **THEN** 服务按 `course_id=rail-passenger-traffic`、`top_k=10` 执行问答
- **AND** 返回状态码 200

### Requirement: 课程问答的响应结构

`POST /rag/ask` 成功时 MUST 返回 HTTP 200 与 `{"code": 0, "data": {...}}`；`data` MUST 包含 `answer`、`speak_text`、`citations` 与 `retrieval`。`speak_text` MUST 是去掉 Markdown 标记与来源标记后的纯文本，供数字人朗读使用。

#### Scenario: 正常回答

- **WHEN** 课程索引中存在可召回片段
- **THEN** `code` 为 0
- **AND** `data.answer` 为不含来源标记的正文
- **AND** `data.speak_text` 不含 `*`、`#`、反引号等 Markdown 字符

### Requirement: citations 的来源字段

`data.citations` 的每个元素 MUST 包含 `source_file`、`source_type`、`score`，并 MAY 包含 `page`、`section`、`video_start`、`video_end`；`score` MUST 为重排得分并保留 4 位小数。`source_file`、`page`、`section` 与视频区间完全相同的来源 MUST 只出现一次。

#### Scenario: 同一来源被多个片段命中

- **WHEN** 重排结果中多个片段来自同一文件的同一页
- **THEN** `citations` 中该来源只出现一条
- **AND** 其 `score` 为浮点数

### Requirement: 检索元数据字段

`data.retrieval` MUST 包含 `vector_candidates`、`bm25_candidates`、`pdf_only`（固定为 `true`）、`pdf_candidates`、`rerank_candidates`、`latency_ms`，并 MAY 包含 `fusion_candidates`。`vector_candidates` 与 `bm25_candidates` MUST 分别为两路检索经 PDF 白名单过滤后的命中条数；`pdf_candidates` MUST 为去重合并并经向量阈值过滤后的候选数；`fusion_candidates` MUST 为按融合得分截取后进入重排的候选数。`latency_ms` MUST 为从进入请求到生成结束的毫秒数，保留 2 位小数。

#### Scenario: 课程没有任何索引数据

- **WHEN** 请求的 `course_id` 未建立索引
- **THEN** 响应状态码为 200
- **AND** `retrieval.vector_candidates` 与 `retrieval.bm25_candidates` 为 0
- **AND** `retrieval.pdf_candidates` 与 `retrieval.rerank_candidates` 为 0

### Requirement: 资料未涉及时的固定回答

当合并后的候选片段为空时，服务 MUST NOT 调用大模型自由作答，MUST 返回 `answer` 与 `speak_text` 均为 `课程资料中未涉及该内容。`，且 `citations` MUST 为空数组。

#### Scenario: 课程无索引数据

- **WHEN** 对没有索引数据的 `course_id` 提问
- **THEN** `data.answer` 等于 `课程资料中未涉及该内容。`
- **AND** `data.citations` 为空数组

#### Scenario: 大模型返回空内容

- **WHEN** 清除 Markdown 后模型回复为空
- **THEN** `data.answer` 退化为 `课程资料中未涉及该内容。`

### Requirement: 资料上传只接受 PDF

`POST /rag/upload` MUST 以 multipart 表单接收 `course_id`、`course_title`（可选）与 `file`；`course_id` MUST 匹配 `^[a-zA-Z0-9_-]+$`；文件名扩展名（转小写后）MUST 为 `.pdf`。其它扩展名 MUST 返回 HTTP 400，且错误文案 MUST 说明当前只接收 `.pdf`。

#### Scenario: 上传 pptx

- **WHEN** 以 `.pptx` 文件调用 `POST /rag/upload`
- **THEN** 返回状态码 400
- **AND** 错误文案包含"只接收 .pdf"
- **AND** 该文件不进入索引

#### Scenario: course_id 不符合规则

- **WHEN** 表单中的 `course_id` 为包含空格的字符串
- **THEN** 服务返回状态码 422

### Requirement: 上传成功的返回与文档登记

上传成功后 MUST 返回 `{"code": 0, "data": {...}}`，`data` MUST 包含 `source_file`、`path`、`changed` 与 `chunk_count`；服务 MUST 同步把该文件登记为该课程的文档记录，其 `index_status` 为 `indexed`。解析、分片与索引 MUST 在本次请求内完成。

#### Scenario: 首次上传 PDF

- **WHEN** 上传一个此前未登记过的 `.pdf`
- **THEN** `data.changed` 为 `true`
- **AND** `data.chunk_count` 为生成的分片数量
- **AND** 该课程的文档列表中可查到该文件且索引状态为已索引

### Requirement: 课程重新索引语义

`POST /rag/index` MUST 接受 `{"course_id": "..."}`（MUST 匹配 `^[a-zA-Z0-9_-]+$`），对课程资料目录中的 PDF 逐个重建索引，并返回 `{"code": 0, "data": {...}}`；`data` MUST 含 `course_id`、`files` 与 `chunks`，课程资料目录存在时还 MUST 含 `skipped`（目录中存在但未接入问答的资料数）；`files` MUST 为内容发生变化而重新索引的文件数。索引过程出错 MUST 返回 500 并给出错误原因。

#### Scenario: 内容未变化的课程重新索引

- **WHEN** 对已索引且文件内容未变化的课程调用 `POST /rag/index`
- **THEN** `data.files` 为 0
- **AND** 不重复写入向量与分片

#### Scenario: 课程目录不存在

- **WHEN** 对从未上传过资料的 `course_id` 调用 `POST /rag/index`
- **THEN** 返回 `code` 为 0
- **AND** `data.files` 与 `data.chunks` 均为 0

#### Scenario: 索引失败

- **WHEN** 索引过程中抛出异常（例如 Ollama 不可达）
- **THEN** 返回状态码 500
- **AND** 响应体 `detail` 中包含失败原因

### Requirement: 混合检索的召回与阈值约束

单次问答 MUST 同时执行向量检索与 BM25 检索，两路召回条数 MUST 分别不少于 `max(top_k, RAG_VECTOR_TOP_K)` 与 `max(top_k, RAG_BM25_TOP_K)`；向量候选相似度 MUST 达到 `RAG_VECTOR_THRESHOLD`（默认 0.55），BM25 候选得分 MUST 大于 0。两路结果 MUST 按片段标识去重合并，按融合得分排序后最多取 20 条进入重排。

#### Scenario: 向量分数低于阈值

- **WHEN** 某片段的向量相似度低于 `RAG_VECTOR_THRESHOLD`
- **THEN** 该片段不进入去重合并后的候选集合
- **AND** 该片段 MUST NOT 计入 `retrieval.fusion_candidates`

#### Scenario: 同一片段被两路同时召回

- **WHEN** 向量检索与 BM25 检索返回同一片段
- **THEN** 该片段在候选集合中只出现一次
- **AND** 进入重排的候选不超过 20 条

### Requirement: 重排保留条数与 PDF 白名单

候选片段 MUST 按"问题 + 片段"重新打分并降序排列，`POST /rag/ask` MUST 最多保留 3 条片段供生成。只有 `source_file` 以 `.pdf` 结尾的片段 MUST 进入重排与答案生成，其它来源的片段 MUST 被丢弃且 MUST NOT 出现在 `citations` 中。

#### Scenario: 候选多于 3 条

- **WHEN** 重排前的候选为 10 条
- **THEN** `retrieval.rerank_candidates` 不大于 3
- **AND** 只有被保留的片段参与答案与引用生成

#### Scenario: 索引中残留非 PDF 来源

- **WHEN** 索引中存在 `source_file` 不以 `.pdf` 结尾的分片
- **THEN** 该分片不参与重排与生成
- **AND** `retrieval.pdf_only` 为 `true`

### Requirement: 分片参数与来源元数据

分片长度 MUST 以 `RAG_CHUNK_SIZE`（默认 420 token）为目标，相邻同章节分片之间 MUST 保留 `RAG_CHUNK_OVERLAP`（默认 55 token）的内容重叠。每个分片 MUST 携带 `source_file`、`source_type`、`section`、`heading_path`、`course_id`、`course_title`、`version`、`content_hash` 与 `updated_at`；PDF 分片 MUST 额外携带 `page`。

#### Scenario: PDF 分片来源信息

- **WHEN** 一段 PDF 文本被切分为分片
- **THEN** 该分片的元数据包含文件名 `source_file` 与对应页码 `page`
- **AND** 分片文本内容非空

#### Scenario: 长文本被切成多个分片

- **WHEN** 一段超过 `RAG_CHUNK_SIZE` 的同章节文本被切分
- **THEN** 相邻分片之间保留 `RAG_CHUNK_OVERLAP` 指定的重叠内容
- **AND** 所有分片都保留同一 `source_file` 与 `section`

### Requirement: 向量索引的集合配置

每门课程的向量集合 MUST 按 `course_id` 独立创建，相似度度量 MUST 为余弦距离。集合 MUST 显式设置 HNSW 参数 `M` 为 32、`construction_ef` 为 200；查询期的 `ef_search` 不显式设置，MUST 使用底层向量库的默认值。

#### Scenario: 为课程创建集合

- **WHEN** 课程 `rail-passenger-traffic` 首次建立索引
- **THEN** 为其创建的集合以余弦距离为度量，且 `M` 为 32、`construction_ef` 为 200

#### Scenario: 不同课程使用不同集合

- **WHEN** 两门不同 `course_id` 的课程分别建立索引
- **THEN** 它们使用彼此独立的向量集合

### Requirement: 课程隔离

检索 MUST 只在请求 `course_id` 对应的索引中进行，MUST NOT 返回其它课程的片段；`citations` 中的来源 MUST 属于该课程。不同课程 MUST NOT 共享分片或答案。

#### Scenario: 相关片段只存在于另一个课程

- **WHEN** 用 `course_id=A` 提问，而相关片段只存在于课程 B
- **THEN** 检索结果中不含课程 B 的片段
- **AND** 回答为 `课程资料中未涉及该内容。`

### Requirement: 同一来源重复上传的增量语义

同一课程下重复上传同名文件时，服务 MUST 比较文件内容哈希：内容未变化时 MUST 跳过解析、分片与向量写入并返回 `changed` 为 `false` 与既有 `chunk_count`；内容变化时 MUST 只替换该来源的既有分片，其它来源文件的分片 MUST 保持可检索。

#### Scenario: 重复上传相同文件

- **WHEN** 上传与已索引文件字节完全相同的 PDF
- **THEN** `data.changed` 为 `false`
- **AND** `data.chunk_count` 与上次一致

#### Scenario: 同名文件内容已更新

- **WHEN** 上传同名但内容变化的 PDF
- **THEN** `data.changed` 为 `true`
- **AND** 该来源的旧分片被替换
- **AND** 其它来源文件的分片仍可被检索

### Requirement: 外部依赖不可用时返回错误

当 Ollama 服务不可达或返回错误状态时，`POST /rag/ask` 与 `POST /rag/index` MUST 返回 HTTP 500，响应体 MUST 含 `detail` 字段说明失败原因；MUST NOT 返回 `code` 为 0 的成功结构，MUST NOT 编造答案。

#### Scenario: Ollama 未启动

- **WHEN** Ollama 不可达时对已建立索引的课程提问
- **THEN** 返回状态码 500
- **AND** 响应体含 `detail`
- **AND** 不返回任何 `answer` 内容
