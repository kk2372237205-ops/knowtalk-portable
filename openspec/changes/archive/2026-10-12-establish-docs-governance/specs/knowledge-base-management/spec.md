# Spec Delta

## Purpose

定义 KnowTalk 知识库管理能力对外可观察的契约：课程与文档的增删改查、文档上传、原文下载与预览、文本在线编辑、版本与分片查看、索引任务查询与重新索引，以及这些接口经主服务 `/rag/admin/` 前缀暴露时的代理与超时行为。

## ADDED Requirements

### Requirement: 管理接口的代理路径与超时契约

主服务 MUST 把 `/rag/admin/{tail}` 上的 GET、POST、PUT、DELETE 请求转发到 RAG 服务的同名路径，MUST 保留查询串与 `Content-Type`。代理超时 MUST 默认为 600 秒并可由 `RAG_PROXY_TIMEOUT` 覆盖；超时 MUST 返回 504 与 `{"code": 504, "msg": "知识库处理超时，索引任务可能仍在后台执行，请稍后刷新状态。"}`；RAG 服务不可达 MUST 返回 503 与 `{"code": 503, "msg": "RAG 管理服务不可用: ..."}`。

#### Scenario: 长耗时上传未被短超时中断

- **WHEN** 一次文档上传的解析与索引耗时超过问答接口的默认超时但在代理超时之内
- **THEN** 代理不返回超时错误
- **AND** 请求最终返回 RAG 服务的响应

#### Scenario: 处理时间超过代理超时

- **WHEN** 管理请求耗时超过 `RAG_PROXY_TIMEOUT`
- **THEN** 返回状态码 504
- **AND** 提示文案说明索引任务可能仍在后台执行、请稍后刷新状态

#### Scenario: RAG 服务未启动

- **WHEN** 管理页面向 `/rag/admin/courses` 发起请求而 RAG 服务不可用
- **THEN** 返回状态码 503
- **AND** 前端可据此提示"知识库服务不可用"

### Requirement: 课程列表与关键字搜索

`GET /rag/admin/courses` MUST 返回 `{"code": 0, "data": [...]}`；每个课程 MUST 含 `course_id`、`title`、`description`、`enabled`、`document_ids`、`created_at`、`updated_at`，并 MUST 含聚合字段 `document_count`、`chunk_count` 与 `index_status`。可选参数 `keyword` MUST 对 `title` 与 `course_id` 做不区分大小写的子串匹配。

#### Scenario: 尚无任何课程

- **WHEN** 知识库中没有任何课程时请求课程列表
- **THEN** 返回 `code` 为 0
- **AND** `data` 为空数组

#### Scenario: 按关键字过滤

- **WHEN** 以 `keyword` 请求课程列表
- **THEN** 只返回 `title` 或 `course_id` 包含该关键字的课程

#### Scenario: 课程索引状态

- **WHEN** 课程下所有文档均已索引完成
- **THEN** 该课程的 `index_status` 为 `indexed`
- **AND** 该字段取值只能是 `empty`、`processing`、`indexed`、`failed` 之一

### Requirement: 创建课程与 course_id 规则

`POST /rag/admin/courses` MUST 接受 JSON `{course_id, title, description, enabled}`；`course_id` MUST 非空且只含字母、数字、下划线与短横线，不合法时 MUST 返回 400 且文案为 `课程 ID 只能包含字母、数字、下划线和短横线`；`course_id` 已存在时 MUST 返回 400 且文案为 `课程 ID 已存在`。创建成功 MUST 返回新课程对象。

#### Scenario: 重复创建同一课程 ID

- **WHEN** 用已存在的 `course_id` 再次调用创建接口
- **THEN** 返回状态码 400
- **AND** 错误文案为 `课程 ID 已存在`
- **AND** 原有课程数据不被覆盖

#### Scenario: 课程 ID 含非法字符

- **WHEN** `course_id` 为空或包含斜杠、空格等字符
- **THEN** 返回状态码 400
- **AND** 错误文案为 `课程 ID 只能包含字母、数字、下划线和短横线`

### Requirement: 课程详情查询与更新

`GET /rag/admin/courses/{course_id}` MUST 返回该课程对象，课程不存在时 MUST 返回 404 与 `课程不存在`。`PUT /rag/admin/courses/{course_id}` MUST 只更新 `title`、`description`、`enabled` 三个字段并刷新 `updated_at`，课程不存在时 MUST 返回 400 与 `课程不存在`，且 MUST NOT 通过该接口改写 `course_id`。

#### Scenario: 查询不存在的课程

- **WHEN** 请求一个从未创建的 `course_id` 详情
- **THEN** 返回状态码 404
- **AND** 错误文案为 `课程不存在`

#### Scenario: 更新课程名称

- **WHEN** 以 `{"title": "新名称"}` 调用课程更新接口
- **THEN** 返回的课程对象 `title` 为新名称
- **AND** `course_id` 保持不变
- **AND** `updated_at` 被刷新

### Requirement: 课程删除与级联清理

`DELETE /rag/admin/courses/{course_id}` MUST 删除该课程记录、其全部文档与版本记录、磁盘上的原始资料目录以及对应的向量与 BM25 索引，并返回 `{"code": 0, "data": {"course_id": ...}}`；课程不存在时 MUST 返回 400 与 `课程不存在`；该操作 MUST NOT 影响其它课程的文档与索引。

#### Scenario: 删除课程

- **WHEN** 对已有文档的课程调用删除接口
- **THEN** 返回 `code` 为 0
- **AND** 课程列表不再包含该课程
- **AND** 该课程的文档详情返回 404
- **AND** 其它课程的文档仍可查询与检索

### Requirement: 课程文档列表与筛选

`GET /rag/admin/courses/{course_id}/documents` MUST 返回该课程下的文档数组并按 `updated_at` 倒序；可选 `keyword` MUST 对 `file_name` 与 `title` 做子串匹配，可选 `status` MUST 精确匹配 `index_status`。每个文档 MUST 含 `document_id`、`course_id`、`title`、`file_name`、`source_type`、`size`、`current_version`、`index_status`、`chunk_count`、`last_error`、`updated_at`。

#### Scenario: 按索引状态筛选

- **WHEN** 以 `status=failed` 请求文档列表
- **THEN** 只返回 `index_status` 为 `failed` 的文档

#### Scenario: 课程之间不混数据

- **WHEN** 请求课程 A 的文档列表
- **THEN** 结果中不含课程 B 的任何文档

#### Scenario: 未知课程

- **WHEN** 请求一个不存在课程的文档列表
- **THEN** 返回 `code` 为 0 与空数组

### Requirement: 文档上传与后台索引状态流转

`POST /rag/admin/courses/{course_id}/documents/upload` MUST 接受 multipart 的 `course_title`（可选）与 `file`；文件名无扩展名 MUST 返回 400 与 `上传文件必须带有扩展名`；扩展名不是 `.pdf` MUST 返回 400 并说明当前只把 PDF 接入问答。成功时 MUST 立即返回 `{"code": 0, "data": {"document": ..., "task": ...}}`，文档初始 `index_status` 为 `uploaded`，任务初始 `status` 为 `queued`。

#### Scenario: 上传非 PDF 文件

- **WHEN** 上传 `.pptx` 或 `.docx` 文件
- **THEN** 返回状态码 400
- **AND** 错误文案说明当前只把 PDF 接入问答
- **AND** 不创建文档记录

#### Scenario: 上传 PDF 并等待后台索引

- **WHEN** 上传一个 `.pdf` 文件
- **THEN** 响应中的 `task.status` 为 `queued`
- **AND** 轮询任务接口后任务进入 `completed`
- **AND** 该文档的 `index_status` 变为 `indexed` 且 `chunk_count` 为分片数量

#### Scenario: 后台索引失败

- **WHEN** 后台索引过程抛出异常
- **THEN** 任务 `status` 为 `failed` 且 `error` 记录原因
- **AND** 该文档的 `index_status` 为 `failed`

### Requirement: 文档详情与索引失败原因可见

`GET /rag/admin/documents/{document_id}` MUST 返回单个文档对象，文档不存在时 MUST 返回 404 与 `文档不存在`。当索引失败时，该文档的 `index_status` MUST 为 `failed` 且 `last_error` MUST 包含可读的失败原因，供管理页面直接展示。

#### Scenario: 查询不存在的文档

- **WHEN** 请求一个不存在的 `document_id`
- **THEN** 返回状态码 404
- **AND** 错误文案为 `文档不存在`

#### Scenario: 索引失败后查看详情

- **WHEN** 某文档索引失败后请求其详情
- **THEN** `index_status` 为 `failed`
- **AND** `last_error` 为非空字符串

### Requirement: 文档原文下载与 PDF 内嵌预览

`GET /rag/admin/documents/{document_id}/download` MUST 以附件形式返回原始文件并携带文件名；文档不存在或原始文件缺失 MUST 返回 404 与 `原始文件不存在`。`GET /rag/admin/documents/{document_id}/preview` MUST 只对存在且扩展名为 `.pdf` 的文档返回 `Content-Type: application/pdf` 与内嵌（`inline`）的响应头，其它情况 MUST 返回 404 与 `只有已存在的 PDF 文件支持内嵌预览`。

#### Scenario: 下载原始资料

- **WHEN** 请求一个已登记文档的下载地址
- **THEN** 返回状态码 200
- **AND** 响应头包含原始文件名
- **AND** 响应体为该文件的原始字节

#### Scenario: 预览非 PDF 文档

- **WHEN** 对扩展名不是 `.pdf` 的文档请求内嵌预览
- **THEN** 返回状态码 404
- **AND** 错误文案为 `只有已存在的 PDF 文件支持内嵌预览`

### Requirement: 文档内容读取与在线编辑

`GET /rag/admin/documents/{document_id}/content` MUST 返回 `{"code": 0, "data": {"content": "..."}}`：`.md`、`.txt`、`.srt`、`.vtt` MUST 返回文件原文，其它格式 MUST 返回抽取出的纯文本。`PUT` 同一路径 MUST 接受 `{title, content, base_version, reindex}`；`base_version` 与当前版本不一致时 MUST 返回 400 与 `文档版本已变化，请刷新后再保存`；保存 MUST 生成新版本、递增 `current_version`。

#### Scenario: 版本冲突

- **WHEN** 保存时提交的 `base_version` 不等于文档当前版本
- **THEN** 返回状态码 400
- **AND** 错误文案为 `文档版本已变化，请刷新后再保存`
- **AND** 磁盘上的文件与版本记录不被修改

#### Scenario: 保存成功并跳过重新索引

- **WHEN** 以 `reindex=false` 保存编辑内容
- **THEN** 返回 `code` 为 0 与更新后的文档对象
- **AND** `current_version` 比保存前大 1
- **AND** `index_status` 为 `stale`

#### Scenario: 保存后新版本无法进入问答索引

- **WHEN** 保存的内容以 `.md` 版本文件落盘，而当前版本只把 PDF 接入问答
- **THEN** 接口仍返回 `code` 为 0 与更新后的文档对象
- **AND** `index_status` 为 `failed` 且 `last_error` 包含失败原因

### Requirement: 文档版本列表与版本恢复

`GET /rag/admin/documents/{document_id}/versions` MUST 返回版本数组并按 `version` 倒序，每项 MUST 含 `version`、`content_hash`、`source_file`、`created_at`、`index_status`、`chunk_count`、`error`。`POST /rag/admin/documents/{document_id}/versions/{version}/restore` MUST 把指定版本内容保存为新版本并重新索引，MUST NOT 覆盖或删除历史版本记录；版本不存在 MUST 返回 400 与 `版本不存在`。

#### Scenario: 恢复历史版本

- **WHEN** 对非当前版本调用恢复接口
- **THEN** 返回 `code` 为 0 与更新后的文档对象
- **AND** `current_version` 比恢复前大 1
- **AND** 原有历史版本记录仍可在版本列表中查到

#### Scenario: 恢复不存在的版本

- **WHEN** 请求一个不存在的版本号
- **THEN** 返回状态码 400
- **AND** 错误文案为 `版本不存在`

### Requirement: 文档分片查看

`GET /rag/admin/documents/{document_id}/chunks` MUST 返回该课程 BM25 快照中 `metadata.source_file` 与该文档文件名一致的分片数组，每项 MUST 含 `chunk_id`、`text` 与 `metadata`，其中 `metadata` MUST 至少含 `source_file` 与 `section`。文档不存在时 MUST 返回 400 与 `文档不存在`；该文档当前没有已索引分片时 MUST 返回空数组。

#### Scenario: 查看已索引文档的分片

- **WHEN** 对已完成索引的文档请求分片列表
- **THEN** 每个分片都含 `chunk_id`、`text` 与 `metadata.source_file`
- **AND** 分片的 `metadata.source_file` 与该文档的文件名一致

#### Scenario: 文档尚未完成索引

- **WHEN** 对没有已索引分片的文档请求分片列表
- **THEN** 返回 `code` 为 0 与空数组

### Requirement: 文档删除

`DELETE /rag/admin/documents/{document_id}` MUST 删除该文档的原始文件与全部版本文件、移除文档与版本记录、并从课程文档列表和索引中移除其分片，返回 `{"code": 0, "data": {"document_id": ...}}`；文档不存在时 MUST 返回 400 与 `文档不存在`；同一课程的其它文档 MUST NOT 受影响。

#### Scenario: 删除文档

- **WHEN** 删除课程下的一个文档
- **THEN** 返回 `code` 为 0
- **AND** 该文档详情接口返回 404
- **AND** 课程文档列表不再包含它
- **AND** 同课程其它文档的分片仍可被检索

### Requirement: 索引任务查询与课程级重新索引

`POST /rag/admin/documents/{document_id}/reindex` 与 `POST /rag/admin/courses/{course_id}/reindex` MUST 立即返回 `{"code": 0, "data": {task}}`（`status` 为 `queued`），实际索引在后台执行。`GET /rag/admin/tasks/{task_id}` MUST 返回含 `task_id`、`kind`、`document_id`、`course_id`、`status`、`progress`、`error`、`created_at`、`updated_at` 的任务对象，并可含 `stage`、`chunk_count`、`result`；任务不存在 MUST 返回 404 与 `任务不存在`；课程不存在时创建课程级任务 MUST 返回 404 与 `课程不存在`。

#### Scenario: 任务状态流转

- **WHEN** 轮询一个由重新索引创建的任务
- **THEN** `status` 按 `queued`、`running`、`completed` 或 `failed` 流转
- **AND** `progress` 在 `completed` 时为 100
- **AND** `failed` 时 `error` 记录失败原因

#### Scenario: 对不存在的课程发起重新索引

- **WHEN** 对未创建的 `course_id` 调用课程级重新索引
- **THEN** 返回状态码 404
- **AND** 错误文案为 `课程不存在`

#### Scenario: 查询不存在的任务

- **WHEN** 请求一个不存在的 `task_id`
- **THEN** 返回状态码 404
- **AND** 错误文案为 `任务不存在`
