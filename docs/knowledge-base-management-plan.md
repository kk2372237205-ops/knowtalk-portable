# 微课数字人知识库管理功能规划方案

> **状态**：已实现。本文规划的知识库管理页面已落地为 `web/knowledge-base.html`，后端接口位于 `rag_service/admin_api.py`，经主服务 `/rag/admin/{tail}` 代理暴露。
> **最后核对日期**：2026-10-12
> **规范性内容**：系统的实际行为契约以 `openspec/specs/knowledge-base-management/` 下的能力规格为准，本文仅作为设计资料与背景说明保留。本文第 1 节所称"支持 PDF、PPTX、DOCX、SRT、VTT、Markdown、TXT 上传"与当前实现不符——上传实际只接受 `.pdf`。

## 1. 项目背景

当前 KnowTalk 已具备基础 RAG 能力：

- 通过 `rag_service` 独立进程提供知识库问答
- 支持 PDF、PPTX、DOCX、SRT、VTT、Markdown、TXT 上传
- 上传后自动执行解析、分片、Ollama Embedding、Chroma 和 BM25 索引
- 通过 `/rag/ask` 返回答案、引用来源和检索信息
- `web/knowtalk-chat.html` 可显示答案并调用现有数字人 TTS 朗读

目前首页只有“Avatar 生成”和“管理后台”两个快捷入口，知识库资料需要通过接口或首页上传区域维护，缺少独立的管理、编辑、预览和版本控制能力。

本方案在首页快捷入口中增加“知识库管理”，用于统一管理多门课程的资料和知识库索引。

## 2. 建设目标

### 2.1 功能目标

建设一个独立的 `knowledge-base.html` 页面，支持：

- 浏览所有课程和课程资料
- 创建、编辑和删除课程
- 上传多种格式的知识库文档
- 在线编辑 Markdown、TXT 和系统抽取出的纯文本内容
- 浏览 PDF/PPTX/DOCX 原文件或文本预览
- 查看文档解析状态、分片数量、索引状态和更新时间
- 手动重新索引单个文档或整门课程
- 查看文档分片内容和元数据
- 查看索引失败原因
- 通过搜索快速定位课程、文档和分片
- 保留历史版本，支持恢复到上一版本
- 与现有 RAG 问答使用同一套 `course_id`

### 2.2 非目标

首期不包含以下能力：

- 不在浏览器中编辑二进制 PDF/PPTX/DOCX 原始排版
- 不允许前端直接修改 Chroma 数据文件
- 不修改 WebRTC、Avatar、TTS 和数字人推理流程
- 不自动切换 PPT 页面
- 不在首期实现复杂的用户权限系统和多人协作编辑
- 不在首期实现视频内容自动转写，视频字幕以 SRT/VTT 文件形式导入

## 3. 总体设计

### 3.1 页面入口

首页 `web/index.html` 的快捷入口增加第三项：

```text
Avatar 生成 | 管理后台 | 知识库管理
```

建议入口：

```html
<a href="/knowledge-base.html" class="quick-link" target="_blank">
    <div class="glass-card p-3 text-center">
        <i class="bi bi-database-fill"></i>
        <div class="fw-bold mt-2">知识库管理</div>
        <div class="small text-muted">课程资料与索引维护</div>
    </div>
</a>
```

### 3.2 系统架构

```text
┌──────────────────────────────────────────┐
│ KnowTalk 首页                          │
│ Avatar 生成 | 管理后台 | 知识库管理       │
└─────────────────┬────────────────────────┘
                  │ /knowledge-base.html
                  v
┌──────────────────────────────────────────┐
│ 知识库管理页面                             │
│ 课程列表 | 文档列表 | 文档编辑 | 预览 | 索引 │
└─────────────────┬────────────────────────┘
                  │ HTTP API
                  v
┌──────────────────────────────────────────┐
│ KnowTalk /rag/admin/* 代理接口          │
└─────────────────┬────────────────────────┘
                  │ HTTP
                  v
┌──────────────────────────────────────────┐
│ rag_service 管理接口                       │
│ 课程管理 | 文档管理 | 版本管理 | 索引任务   │
└──────────────┬──────────────┬──────────────┘
               │              │
               v              v
┌────────────────────┐  ┌───────────────────┐
│ 原始资料与版本目录   │  │ Chroma + BM25      │
│ sources/versions/   │  │ 向量与关键词索引   │
└────────────────────┘  └───────────────────┘
```

### 3.3 低侵入原则

集成只涉及：

- 首页新增一个快捷入口
- 新增 `web/knowledge-base.html`
- `rag_service` 新增管理 API 和管理页面所需的文件元数据接口
- `server/routes.py` 新增 RAG 管理代理接口
- 不修改原有 `/human`、`/offer`、TTS、Avatar 和 WebRTC 核心逻辑

## 4. 页面信息架构

### 4.1 页面布局

建议采用“左侧课程导航 + 中央文档区域 + 右侧详情抽屉”的工作台布局。

```text
┌──────────────────────────────────────────────────────────────┐
│ 顶部导航：KnowTalk / 知识库管理 / 返回工作台                 │
├───────────────┬──────────────────────────────┬───────────────┤
│ 课程导航       │ 文档列表与筛选                 │ 文档详情抽屉   │
│               │                              │               │
│ 全部课程       │ 搜索框  类型  状态  新建/上传    │ 文件信息       │
│ 铁路客流分析   │                              │ 解析统计       │
│ Python 入门    │ 文档行                         │ 索引状态       │
│ + 新建课程     │ 文档行                         │ 操作按钮       │
│               │ 文档行                         │               │
└───────────────┴──────────────────────────────┴───────────────┘
```

桌面端使用三栏，平板端缩减为两栏，移动端采用页面切换：课程列表 -> 文档列表 -> 文档详情。

### 4.2 顶部导航

包含：

- KnowTalk 品牌标识
- 当前页面名称“知识库管理”
- 当前服务状态：RAG 在线/离线
- 返回数字人工作台
- 刷新按钮

RAG 服务离线时，页面仍允许浏览已经缓存的文档元数据，但禁用上传、保存和索引按钮，并明确显示离线状态。

### 4.3 左侧课程列表

每个课程项目显示：

- 课程名称
- `course_id`
- 文档数量
- 分片数量
- 索引状态
- 最近更新时间

左侧功能：

- 全部课程
- 课程搜索
- 新建课程
- 编辑课程信息
- 删除课程

课程列表示例：

```text
全部课程                         3 门课程

铁路数据管理与客流量分析          2 个文档  已完成
rail-passenger-traffic

Python 编程入门                  1 个文档  处理中
python-basic

新建课程
```

删除课程必须二次确认，并显示将删除的文档数和索引数据量。首期建议采用软删除或回收站，不立即物理删除。

### 4.4 文档列表

文档列表字段：

| 字段 | 说明 |
|---|---|
| 文档名称 | 原始文件名或用户定义标题 |
| 类型 | PDF、PPTX、DOCX、字幕、Markdown、TXT |
| 状态 | 未索引、解析中、索引中、已完成、失败 |
| 分片数量 | 当前文档生成的 chunk 数量 |
| 文件大小 | 原文件大小 |
| 版本 | 当前版本号 |
| 更新时间 | 最近修改时间 |
| 操作 | 浏览、编辑、重新索引、版本、删除 |

支持的筛选条件：

- 文档名称关键词
- 文档类型
- 处理状态
- 是否存在索引错误
- 更新时间范围

文档列表需要分页，首期每页 20 或 50 条。

### 4.5 文档详情抽屉

点击文档后打开详情抽屉或详情页，显示：

- 文档名称
- 所属课程
- 原始文件名
- 文件类型和大小
- 文件哈希
- 当前版本
- 上传时间和更新时间
- 解析器类型
- 分片数量
- 向量维度
- 索引状态
- 最后一次错误信息
- 最近一次索引耗时

操作按钮：

- 浏览原文
- 编辑文本
- 重新索引
- 查看分片
- 查看版本
- 下载原文件
- 删除文档

## 5. 文档上传与编辑设计

### 5.1 上传方式

支持两种方式：

1. 在指定课程下点击“上传文档”。
2. 将文件拖拽到文档列表上传区域。

上传字段：

```text
课程 ID：rail-passenger-traffic
课程名称：铁路数据管理与客流量分析
文档标题：可选
文件：PDF/PPTX/DOCX/SRT/VTT/MD/TXT
是否立即索引：是/否
```

上传时执行：

1. 校验扩展名和文件大小
2. 使用安全文件名保存
3. 计算 SHA-256
4. 判断是否为同一文件
5. 创建文档记录
6. 解析文本
7. 生成语义分片
8. 调用 Ollama Embedding
9. 更新 Chroma 和 BM25
10. 返回处理状态

### 5.2 文件大小限制

建议初始限制：

- 单文件最大 100 MB
- 文本文件最大 20 MB
- 课程单次批量上传最多 20 个文件
- 超过限制时在前端立即提示，不提交到服务端

限制应通过环境变量配置：

```text
RAG_MAX_UPLOAD_MB=100
RAG_MAX_BATCH_FILES=20
```

### 5.3 文档浏览

浏览功能需要根据文档类型提供不同体验：

| 类型 | 浏览方式 |
|---|---|
| PDF | 内嵌 PDF 浏览器，支持页码跳转和下载 |
| PPTX | 显示页面缩略图和抽取文本；可选显示已渲染图片 |
| DOCX | 显示结构化纯文本和标题层级 |
| SRT/VTT | 按时间轴显示字幕段落 |
| Markdown | Markdown 预览与纯文本切换 |
| TXT | 纯文本阅读器 |

浏览器不直接编辑 PDF、PPTX 和 DOCX 的二进制排版。编辑这些格式时，提供“编辑抽取文本并生成知识库新版本”的能力，并明确标记不会修改原始文件排版。

### 5.4 文本编辑器

编辑区域包含：

- 标题编辑
- 纯文本或 Markdown 内容编辑
- 只读的来源元数据
- 保存为新版本
- 保存并重新索引
- 取消编辑
- 查看当前版本和上一版本差异

编辑器要求：

- 支持中文输入
- 支持自动保存草稿，但草稿不自动进入生产索引
- 未保存离开时弹出确认
- 显示字符数、token 估算和预计分片数
- 禁止修改 `course_id`、`chunk_id` 和原始文件哈希
- 保存时使用乐观锁，防止覆盖其他版本

### 5.5 编辑后的索引策略

保存文档后：

```text
保存文本版本
    |
    v
计算 content_hash
    |
    +-- 未变化 --> 不重新索引
    |
    +-- 已变化 --> 生成新版本
                       |
                       v
                  删除旧文档分片
                       |
                       v
                  新分片 + Embedding
                       |
                       v
                  更新 Chroma + BM25
                       |
                       v
                  状态变为已完成
```

索引失败时保留旧版本索引，文档状态标记为“索引失败”，保证线上问答不被半成品索引破坏。

## 6. 文档版本管理

### 6.1 版本模型

每次保存编辑内容或重新上传同名文件，创建一个新版本：

```json
{
  "document_id": "doc-01J...",
  "course_id": "rail-passenger-traffic",
  "title": "客流量分析讲义",
  "current_version": 3,
  "versions": [
    {
      "version": 1,
      "content_hash": "sha256:...",
      "source_file": "lecture.pdf",
      "created_at": "2026-09-05T09:00:00Z",
      "index_status": "indexed",
      "chunk_count": 107
    },
    {
      "version": 2,
      "content_hash": "sha256:...",
      "source_file": "lecture.pdf",
      "created_at": "2026-09-05T10:00:00Z",
      "index_status": "failed",
      "chunk_count": 0,
      "error": "Embedding 服务不可用"
    },
    {
      "version": 3,
      "content_hash": "sha256:...",
      "source_file": "lecture-edited.md",
      "created_at": "2026-09-05T11:00:00Z",
      "index_status": "indexed",
      "chunk_count": 112
    }
  ]
}
```

### 6.2 版本操作

- 查看版本列表
- 查看版本详情
- 查看两个版本的文本差异
- 恢复指定版本
- 将指定版本重新索引
- 删除非当前版本

恢复版本不直接覆盖当前文件，而是创建一个新的版本记录，保证审计链完整。

### 6.3 回滚原则

索引必须采用“先构建、后切换”策略：

1. 新版本写入临时索引或临时 collection
2. 新版本索引构建成功并通过检查
3. 更新课程的 active version 指针
4. 新查询切换到新索引
5. 旧索引保留一段时间后清理

如果新版本索引失败，继续使用旧版本，不影响现有问答。

## 7. 分片浏览功能

### 7.1 分片列表

点击“查看分片”后显示当前文档的所有分片：

| 字段 | 说明 |
|---|---|
| 序号 | 分片顺序 |
| Chunk ID | 唯一标识 |
| 内容摘要 | 前 120-200 字 |
| Token 数 | 精确 tokenizer 统计 |
| 页码 | PDF/PPT 页码 |
| 章节 | 标题路径 |
| 视频时间 | 字幕来源时间段 |
| 索引状态 | 是否已经写入索引 |
| 操作 | 查看完整内容 |

### 7.2 分片详情

分片详情显示：

- 完整分片文本
- 上一个分片和下一个分片
- 来源文件
- 页码或时间戳
- 标题路径
- token 数量
- content hash
- 向量索引状态
- BM25 索引状态

分片原则上不单独编辑。需要修改时跳转到文档编辑器，避免“文档内容”和“分片内容”不一致。

### 7.3 分片质量检查

管理页显示以下质量告警：

- 分片低于 30 tokens
- 分片超过 512 tokens
- 分片没有来源页码或章节
- 文档解析为空
- 分片重复率过高
- 同一来源页存在大量重复内容

这些告警不一定阻止索引，但应帮助管理员发现资料质量问题。

## 8. 索引任务与状态

### 8.1 状态定义

```text
uploaded       已上传，等待解析
parsing        正在解析
chunking       正在分片
embedding      正在生成向量
indexing       正在写入索引
indexed        索引完成
failed         处理失败
stale          原文已变化，索引过期
deleted        已删除，保留历史记录
```

### 8.2 任务展示

页面显示：

- 当前阶段
- 进度百分比
- 已处理文件大小
- 已生成分片数
- Embedding 调用数量
- 已耗时
- 预计剩余时间，可选
- 错误信息

首期可以使用前端轮询：

```text
GET /rag/admin/tasks/{task_id}
```

后续再升级为 SSE 或 WebSocket。

### 8.3 并发策略

首期建议：

- 同一课程同时只允许一个索引任务
- 同一文档同时只允许一个保存任务
- 不同课程可以串行处理，避免 Ollama Embedding 占满资源
- 用户点击重复索引时返回已有任务 ID

## 9. API 设计

建议由独立 RAG 服务实现真实管理 API，再由 KnowTalk 后端代理。

### 9.1 课程 API

```http
GET /rag/admin/courses
POST /rag/admin/courses
GET /rag/admin/courses/{course_id}
PUT /rag/admin/courses/{course_id}
DELETE /rag/admin/courses/{course_id}
```

创建课程请求：

```json
{
  "course_id": "rail-passenger-traffic",
  "title": "铁路数据管理与客流量分析",
  "description": "铁路客流量数据管理、同比增长量和同比增长率分析",
  "enabled": true
}
```

课程返回：

```json
{
  "course_id": "rail-passenger-traffic",
  "title": "铁路数据管理与客流量分析",
  "description": "铁路客流量数据管理、同比增长量和同比增长率分析",
  "document_count": 2,
  "chunk_count": 219,
  "index_status": "indexed",
  "updated_at": "2026-09-05T12:00:00Z"
}
```

### 9.2 文档 API

```http
GET /rag/admin/courses/{course_id}/documents
POST /rag/admin/courses/{course_id}/documents/upload
GET /rag/admin/documents/{document_id}
GET /rag/admin/documents/{document_id}/content
PUT /rag/admin/documents/{document_id}/content
DELETE /rag/admin/documents/{document_id}
POST /rag/admin/documents/{document_id}/reindex
GET /rag/admin/documents/{document_id}/download
```

编辑内容请求：

```json
{
  "title": "客流量分析讲义（修订版）",
  "content": "本课程介绍铁路客流量数据的处理方法……",
  "base_version": 2,
  "reindex": true
}
```

### 9.3 版本和分片 API

```http
GET /rag/admin/documents/{document_id}/versions
GET /rag/admin/documents/{document_id}/versions/{version}
POST /rag/admin/documents/{document_id}/versions/{version}/restore
GET /rag/admin/documents/{document_id}/chunks
GET /rag/admin/documents/{document_id}/chunks/{chunk_id}
```

### 9.4 任务 API

```http
GET /rag/admin/tasks/{task_id}
POST /rag/admin/courses/{course_id}/reindex
POST /rag/admin/rebuild
```

### 9.5 KnowTalk 代理 API

在 `server/routes.py` 注册：

```text
/rag/admin/courses
/rag/admin/courses/{course_id}
/rag/admin/courses/{course_id}/documents
/rag/admin/documents/{document_id}/content
/rag/admin/documents/{document_id}/reindex
/rag/admin/tasks/{task_id}
```

代理层只负责转发请求、超时、错误码和鉴权，不实现文档解析和索引逻辑。

## 10. 数据存储设计

### 10.1 推荐目录结构

```text
rag_service/data/
├─ sources/
│  └─ {course_id}/
│     ├─ original/
│     │  └─ lecture.pdf
│     ├─ extracted/
│     │  └─ lecture.v1.json
│     └─ versions/
│        ├─ lecture.v1.json
│        └─ lecture.v2.json
├─ metadata/
│  ├─ courses.json
│  ├─ documents.json
│  └─ tasks.json
├─ chunks.jsonl
├─ bm25.pkl
└─ chroma/
```

后续如果数据量增大，可将元数据迁移到 SQLite，不改变管理 API。

### 10.2 课程记录

```json
{
  "course_id": "rail-passenger-traffic",
  "title": "铁路数据管理与客流量分析",
  "description": "",
  "enabled": true,
  "document_ids": ["doc-001"],
  "active_index_version": 3,
  "created_at": "2026-09-05T09:00:00Z",
  "updated_at": "2026-09-05T12:00:00Z"
}
```

### 10.3 文档记录

```json
{
  "document_id": "doc-001",
  "course_id": "rail-passenger-traffic",
  "title": "Python编程入门与实践",
  "file_name": "Python编程入门与实践.pdf",
  "source_path": "sources/rail-passenger-traffic/original/Python编程入门与实践.pdf",
  "source_type": "pdf",
  "size": 1512345,
  "content_hash": "sha256:...",
  "current_version": 1,
  "index_status": "indexed",
  "chunk_count": 107,
  "last_error": null,
  "created_at": "2026-09-05T09:00:00Z",
  "updated_at": "2026-09-05T09:10:00Z"
}
```

### 10.4 与现有索引的关系

当前实现使用：

- Chroma collection：`course_{course_id}`
- `bm25.pkl`
- `manifest.json`
- `sources/{course_id}` 原始文件目录

管理功能应逐步把现有 manifest 扩展为文档和版本元数据，但不要改变已有 chunk 的 `chunk_id` 和核心字段，避免历史索引失效。

## 11. 现有代码改造点

### 11.1 前端

新增：

```text
web/knowledge-base.html
```

修改：

```text
web/index.html
```

修改内容仅为增加快捷入口。

### 11.2 RAG 服务

新增或扩展：

```text
rag_service/admin_api.py       # 管理 API
rag_service/repository.py      # 课程、文档、版本元数据
rag_service/tasks.py           # 索引任务状态
rag_service/storage.py         # 文件和版本存储
rag_service/diff.py            # 文本版本差异
```

修改：

```text
rag_service/api.py             # 注册管理 API
rag_service/ingest.py          # 支持文档 ID、版本和任务状态
rag_service/index_store.py     # 支持 active version 和安全切换
```

### 11.3 KnowTalk 后端

修改：

```text
server/routes.py
```

新增 RAG 管理代理，不改动当前 `/human` 行为。

## 12. 安全设计

### 12.1 文件安全

- 使用 `Path(filename).name`，禁止路径穿越
- 只允许白名单扩展名
- 限制文件大小和批量数量
- 上传文件保存到 RAG 专用目录
- 原始文件下载接口进行路径校验
- 禁止通过文件名覆盖其他课程文件
- 临时文件处理完成后清理

### 12.2 内容安全

- 编辑内容限制最大长度
- 保存前重新计算哈希
- 不信任文档中嵌入的脚本和 HTML
- Markdown 预览必须进行 HTML 清洗，防止 XSS
- 文档内容不直接作为系统 Prompt 指令执行
- 课程资料作为不可信上下文传给 LLM

### 12.3 管理权限

首期单机内网可以不接复杂账号系统，但应预留：

- `RAG_ADMIN_TOKEN`
- 上传、编辑、删除、重建索引分别授权
- 查询接口和写操作接口区分
- 记录操作日志：操作者、时间、文档、版本和结果

## 13. 异常处理和降级

### 13.1 RAG 服务离线

- 首页快捷入口仍可打开页面
- 页面显示 RAG 服务离线
- 文档缓存数据只读展示
- 禁用上传、编辑保存和索引按钮
- 问答页继续回退到原 `/human` 普通问答

### 13.2 Ollama 不可用

- 文档原文和编辑内容仍可保存为未索引版本
- 状态标记为“等待索引”
- 支持稍后手动重试
- 不删除旧的可用索引

### 13.3 解析失败

- 保留原始文件
- 记录解析错误
- 显示失败页码或错误阶段
- 允许重新上传或改用手工文本编辑

### 13.4 Chroma/BM25 写入失败

- 不切换 active version
- 保留旧索引
- 新版本标记失败
- 管理员可查看错误并重试

### 13.5 回滚

```text
RAG_ENABLED=false
```

或访问：

```text
/knowtalk-chat.html?rag=0
```

即可暂时关闭 RAG 问答。知识库管理服务可以单独停止，不影响数字人主服务。

## 14. 页面交互细节

### 14.1 删除确认

删除文档前显示：

```text
确定删除“Python编程入门与实践.pdf”吗？
将删除当前文档、版本记录和对应索引。
此操作可以在回收站中恢复。
```

### 14.2 保存确认

保存并索引时显示：

```text
正在保存第 3 个版本并建立索引。
保存期间线上问答继续使用当前第 2 个有效版本。
```

### 14.3 失败提示

失败提示必须包含：

- 失败阶段
- 简短错误原因
- 是否保留旧索引
- 重试按钮
- 查看详细日志入口

不要只显示“操作失败”。

### 14.4 防重复操作

- 上传、保存、重建索引按钮提交后立即禁用
- 显示任务进度
- 重复点击返回已有任务
- 浏览和下载不受索引任务影响

## 15. 观测与日志

### 15.1 管理指标

建议统计：

- 课程总数
- 文档总数
- 已索引文档数
- 失败文档数
- 分片总数
- 最近一次索引耗时
- Embedding 调用耗时
- 文档解析失败率
- 文档版本数量
- RAG 服务可用率

### 15.2 关键日志

```text
[RAG_ADMIN] upload course_id=... document_id=... filename=...
[RAG_ADMIN] parse success document_id=... chunks=...
[RAG_ADMIN] index start document_id=... version=...
[RAG_ADMIN] index success document_id=... version=... elapsed_ms=...
[RAG_ADMIN] index failed document_id=... version=... error=...
[RAG_ADMIN] restore document_id=... from_version=... to_version=...
```

日志中不记录 API Key，也不默认记录完整课程原文。

## 16. 实施阶段和工时

在现有 RAG 基础上实现知识库管理，预计 4-6 个工作日；如果加入权限、SQLite、SSE 进度和复杂版本差异，预计 6-8 个工作日。

| 阶段 | 工作内容 | 工时 | 验收标准 |
|---|---|---:|---|
| 第1阶段 | 设计元数据结构，兼容现有 manifest、Chroma 和 BM25 | 0.5 天 | 不影响现有 `/rag/ask` 和已有索引 |
| 第2阶段 | 实现课程和文档列表 API | 0.5 天 | 可分页、搜索、按课程过滤 |
| 第3阶段 | 实现上传、删除、原文下载和预览 API | 0.5-1 天 | 支持全部已定义格式，路径安全 |
| 第4阶段 | 实现文本编辑、版本保存和恢复 | 1 天 | 新版本可保存，旧版本可恢复 |
| 第5阶段 | 接入索引任务、状态、失败保留旧索引 | 1 天 | 索引失败不影响线上问答 |
| 第6阶段 | 开发知识库管理页面和首页入口 | 1-1.5 天 | 桌面和移动端可用，上传/编辑/浏览闭环 |
| 第7阶段 | 联调、异常测试和回滚验证 | 0.5-1 天 | 完成验收用例和服务降级演练 |

## 17. 验收标准

### 17.1 课程和文档管理

- 可以创建、编辑、搜索和删除课程
- 可以在指定课程下上传全部支持格式
- 文档列表显示正确的类型、大小、版本和索引状态
- 删除操作不会误删其他课程文件

### 17.2 浏览和编辑

- PDF 可翻页浏览
- PPTX 可查看页面文本和页码
- DOCX 可查看标题和正文
- SRT/VTT 显示时间戳
- Markdown/TXT 可在线编辑
- 编辑保存后生成新版本
- 未保存离开有提示

### 17.3 索引和问答

- 上传后可以自动建立索引
- 可以手动重建索引
- 分片数量和索引状态可查看
- 索引失败时旧版本继续可问答
- 问答接口仍能按 `course_id` 返回答案和引用
- RAG 服务离线时原普通问答仍可用

### 17.4 数据安全

- 文件名不能穿越目录
- Markdown 预览不存在脚本执行
- API Key 不写入前端和日志
- 原始文件、版本和索引数据互相隔离

## 18. 推荐开发顺序

推荐按照以下顺序落地：

1. 先扩展元数据和管理 API，不先做复杂页面。
2. 实现课程列表、文档列表和状态查询。
3. 实现上传、下载、预览和删除。
4. 实现 Markdown/TXT 在线编辑和版本保存。
5. 实现 PDF/PPTX/DOCX 抽取文本编辑。
6. 实现索引任务和旧版本保留机制。
7. 开发三栏管理页面。
8. 接入首页快捷入口。
9. 完成 RAG 问答回归测试和异常回滚测试。

这样可以先验证后端数据模型和索引安全性，再投入页面交互，降低返工风险。

## 19. 结论

知识库管理功能应作为 RAG 服务的管理平面建设，而不是直接修改现有 Chroma 文件或把文档编辑逻辑塞进 KnowTalk 主服务。

最终形态为：

```text
首页快捷入口
    -> 知识库管理页面
    -> KnowTalk 管理代理
    -> 独立 RAG 管理 API
    -> 文档、版本、分片和索引管理
    -> 现有 /rag/ask 使用 active version
```

该方案保留现有数字人主链路，支持多课程、多文档、版本回滚和索引失败降级，适合在当前项目基础上逐步实施。
