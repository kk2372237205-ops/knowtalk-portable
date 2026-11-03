# 微课数字人 RAG 知识库技术方案

> **状态**：部分实现。本文是 RAG 知识库的设计稿，落地时与设计存在若干偏差，已知差异包括：上传实际只放行 `.pdf`（设计稿列了 PPTX/DOCX/SRT/VTT/MD/TXT）；嵌入实际用 Ollama `qwen3-embedding:4b`（设计稿推荐 `BAAI/bge-base-zh-v1.5`）；分片为自研实现（设计稿推荐 LangChain `RecursiveCharacterTextSplitter`）；生成模型为 Ollama `qwen3:8b`（设计稿示例为 OpenAI 模型）；在线编辑后重新索引当前无法成功（编辑落盘为 `.md`，而索引只接受 `.pdf`）。
> **最后核对日期**：2026-10-12
> **规范性内容**：系统的实际行为契约以 `openspec/specs/rag-knowledge-base/` 与 `openspec/specs/knowledge-base-management/` 下的能力规格为准，本文仅作为设计资料与背景说明保留。

## 1. 文档概述

### 1.1 建设目标

为 KnowTalk 微课数字人增加一个基于课程资料的检索增强生成（Retrieval-Augmented Generation，RAG）问答能力，使数字人可以围绕《铁路数据管理与客流量分析》等微课内容回答问题，并给出可追溯的课程来源。

目标包括：

- 只基于已入库的课程资料回答问题，降低模型编造风险
- 支持 PPT、讲义、视频字幕、课程说明等多种资料来源
- 返回答案、引用章节、页码或视频时间戳
- 通过旁路服务接入现有 KnowTalk，不改动 WebRTC、TTS、Avatar 核心链路
- 在资料不存在时明确返回“课程资料中未涉及该内容”

### 1.2 设计原则

- 低侵入：RAG 服务独立运行，原 `/human`、WebRTC 和 TTS 接口保持兼容
- 可替换：嵌入模型、向量库、重排模型和 LLM 均通过配置切换
- 可验证：每个回答保留召回片段、重排得分和引用来源
- 先轻量后生产：开发阶段使用 Chroma，生产阶段可切换 Qdrant 或 Milvus
- 安全配置：API Key 使用环境变量或密钥管理系统，不提交到代码仓库

## 2. 总体架构

```text
                         课程资料导入
      PPT / PDF / DOCX / 字幕 / Markdown / 手工文本
                              |
                              v
                 +---------------------------+
                 | 文档解析与清洗器           |
                 | 标题识别、页码、时间戳保留 |
                 +-------------+-------------+
                               |
                               v
                 +---------------------------+
                 | 语义分片 Chunking          |
                 | 256-512 tokens             |
                 | 10%-15% overlap             |
                 +-------------+-------------+
                               |
                               v
                 +---------------------------+
                 | Embedding + 索引构建        |
                 | 向量库 + BM25 倒排索引      |
                 +-------------+-------------+
                               |
                               v
     用户问题 --> +---------------------------+
                  | RAG API /rag/ask          |
                  +-------------+-------------+
                                |
             +------------------+------------------+
             v                  v                  v
       问题改写             向量召回             BM25召回
             +------------------+------------------+
                                |
                                v
                       候选合并与去重
                                |
                                v
                       中文 Reranker 重排
                                |
                                v
                         Top-3 / Top-5
                                |
                                v
                     LLM 基于上下文生成
                                |
                                v
                      答案 + 引用 + 置信信息
                                |
             +------------------+------------------+
             v                                     v
        前端问答气泡                         可选调用 /human
        显示文字答案                         让数字人朗读
```

### 2.1 旁路集成方式

建议新增独立目录或独立进程：

```text
KnowTalk-main/
├─ rag_service/
│  ├─ api.py                 # FastAPI 服务
│  ├─ config.py              # 环境变量配置
│  ├─ ingest.py              # 文档导入与分片
│  ├─ index_store.py         # 向量库和 BM25
│  ├─ retriever.py           # 混合召回
│  ├─ reranker.py            # 重排
│  ├─ generator.py           # LLM 生成
│  └─ data/
│     ├─ chunks.jsonl
│     └─ chroma/
└─ docs/
   └─ rag_knowledge_base_technical方案.md
```

原项目只需要两个可选侵入点：

1. 在 `server/routes.py` 新增 `/rag/ask` 代理接口，或直接由前端请求独立 RAG 服务。
2. 在 `web/knowtalk-chat.html` 的 `postHuman()` 中，将 `chat` 类型请求改为先调用 `/rag/ask`，把返回的 `answer` 显示在原 AI 气泡中；如需朗读，再将答案传给现有 `/human` 的 `echo` 模式。

原 `/human`、`/offer`、TTS、PPT 播放和 Avatar 生成流程不需要修改。

## 3. 分片 Chunking

### 3.1 文档解析

不同资料建议使用不同解析器：

| 来源 | 推荐解析方式 | 必须保留的元数据 |
|---|---|---|
| PPT/PPTX | `python-pptx` 提取标题和正文；已有渲染图片可作为附件 | 文件名、页码、标题 |
| PDF | PyMuPDF 或 `unstructured` | 文件名、页码、章节 |
| DOCX/讲义 | `python-docx` 或 `unstructured` | 文件名、标题层级、段落序号 |
| 视频字幕 | SRT/VTT 解析器 | 视频名、开始时间、结束时间 |
| Markdown | Markdown AST 或按标题解析 | 文件名、标题路径、段落序号 |

推荐先将不同格式统一为中间结构：

```text
Document
  -> Section
      -> Element(type=title|paragraph|table|caption, text, metadata)
```

### 3.2 分片策略

分片不按固定字符数直接截断，而是按以下优先级寻找边界：

1. 一级、二级、三级标题
2. PPT 页边界
3. 段落边界
4. 列表或表格行边界
5. 句号、问号、感叹号、分号等句末标点
6. 只有在单个段落过长时，才在逗号或句子边界进行兜底切分

建议配置：

- 目标大小：256-512 tokens
- 中文近似字符数：200-400 字
- 相邻分片重叠：10%-15%，约 30-60 tokens
- 标题路径写入每一个分片，避免正文脱离章节语境
- 表格尽量按完整行或逻辑小表切分，不拆开字段和值
- 代码示例作为一个完整块保留，避免切断代码语义
- 每个 PPT 页面至少形成一个带页码的分片

### 3.3 工具选型

- 通用首选：LangChain `RecursiveCharacterTextSplitter`
- 语义边界更强：LangChain `SemanticChunker`
- 复杂文档：`unstructured` + 自定义标题/页码切分器
- 生产环境建议先使用规则分片，再通过评测数据调参；不建议一开始完全依赖黑盒语义切分

伪代码：

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    separators=["\n## ", "\n### ", "\n\n", "。", "！", "？", "；", "，", " ", ""],
    chunk_size=420,       # 中文场景需按实际 tokenizer 校准
    chunk_overlap=55,
    length_function=token_count,
)
chunks = splitter.create_documents(
    texts=[section.text],
    metadatas=[section.metadata],
)
```

### 3.4 分片 JSON 结构

```json
{
  "chunk_id": "rail-passenger-traffic-v1-p009-c002",
  "course_id": "rail-passenger-traffic",
  "course_title": "铁路数据管理与客流量分析",
  "version": "v1",
  "text": "同比增长率可以反映客流量相对于上一年度同期的变化速度。计算公式为本期值减去同期值，再除以同期值。",
  "token_count": 52,
  "metadata": {
    "source_type": "ppt",
    "source_file": "铁路数据管理与客流量分析.pptx",
    "page": 9,
    "section": "项目实施/同比增长率",
    "heading_path": ["项目实施", "计算指标", "同比增长率"],
    "video_start": null,
    "video_end": null,
    "language": "zh-CN",
    "content_hash": "sha256:...",
    "updated_at": "2026-09-04T10:00:00Z"
  }
}
```

视频字幕分片示例：

```json
{
  "chunk_id": "lecture-01-t0120-c004",
  "text": "本节介绍如何根据2017年至2019年的客流量计算同比增长量。",
  "metadata": {
    "source_type": "subtitle",
    "source_file": "lecture-01.vtt",
    "section": "客流量统计指标",
    "video_start": 120.4,
    "video_end": 138.8,
    "page": null
  }
}
```

## 4. 索引 Indexing

### 4.1 嵌入模型

推荐优先测试以下中文模型：

| 模型 | 特点 | 适用情况 |
|---|---|---|
| `BAAI/bge-base-zh-v1.5` | 中文语义检索效果稳定，体量适中，社区成熟 | 本地部署、开发和中小规模课程库首选 |
| `text2vec-large-chinese` | 中文句向量经验丰富，中文语义相似度表现较好 | 中文长文本和传统知识库场景 |
| `BAAI/bge-m3` | 支持多语言和较长上下文，能力更全面 | 中英混合课程或后续扩展多语言 |
| 云端 embedding API | 无需本地 GPU，运维简单 | 数据允许出网且更关注上线速度 |

建议第一阶段使用 `BAAI/bge-base-zh-v1.5`。如果课程资料规模较大、中文长段落较多，再评估 `text2vec-large-chinese` 或 `bge-m3`。

注意：建立索引和查询必须使用同一个嵌入模型及同一套文本预处理规则。

### 4.2 向量数据库

- Chroma：部署最轻量，适合单机原型、课程数量较少和 5-7 天内快速闭环
- Qdrant：推荐生产单机或中小规模集群，REST/API 完整，过滤能力好，运维成本低
- Milvus：适合大规模向量和多节点生产集群，但部署和运维更重

推荐路线：开发阶段 Chroma，验收后若向量规模超过 10 万或需要多实例部署，迁移 Qdrant。迁移只影响 `index_store.py`，不影响上层召回接口。

### 4.3 HNSW 参数建议

> 本节参数**已按建议落地**：当前实现对每门课程创建向量集合时显式设置 `hnsw:space=cosine`、`hnsw:M=32`、`hnsw:construction_ef=200`（`rag_service/index_store.py:69-77`）；`ef_search` 未显式设置，使用向量库默认值。参数的**规范性描述**见 `openspec/specs/rag-knowledge-base/` 能力规格，本节只保留设计取舍说明，不再重复参数清单。

设计取舍：`M` 取 32 是为中文课程库在图连接数与内存占用之间取中；`ef_construction` 取 200 是建图质量与索引速度的折中；相似度用 cosine 是因为文本向量已归一化。课程库规模较小时优先保证精度，不必过早调优索引参数；`ef_search` 保持库默认值，等有召回评测数据后再决定是否提高。

### 4.4 增量更新策略

每个源文件计算 `content_hash`，每个分片计算 `chunk_hash`。导入时执行：

1. 扫描资料目录并计算文件 hash
2. 文件 hash 未变化则跳过
3. 文件变化后，只重新解析该文件
4. 根据旧 `source_file + page/section` 定位旧分片
5. 删除该文件旧分片的向量和 BM25 文档
6. 对新分片向量化并写入索引
7. 更新 manifest，记录版本、时间和删除/新增数量

不需要重建其他课程或其他文件的索引。若一个 PPT 只修改第 9 页，也可以按页 hash 进一步做到页级增量更新。

### 4.5 向量化与入库伪代码

```python
from sentence_transformers import SentenceTransformer
import chromadb

model = SentenceTransformer("BAAI/bge-base-zh-v1.5")
client = chromadb.PersistentClient(path="./data/chroma")
collection = client.get_or_create_collection(
    name="rail_passenger_traffic",
    metadata={"hnsw:space": "cosine"},
)

def build_index(chunks):
    texts = [item["text"] for item in chunks]
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        batch_size=32,
        show_progress_bar=True,
    ).tolist()

    collection.upsert(
        ids=[item["chunk_id"] for item in chunks],
        documents=texts,
        embeddings=vectors,
        metadatas=[item["metadata"] for item in chunks],
    )

def delete_source(source_file):
    old = collection.get(where={"source_file": source_file})
    if old["ids"]:
        collection.delete(ids=old["ids"])
```

BM25 索引可使用 `rank_bm25`，并将 `chunk_id` 与向量库保持一致：

```python
from rank_bm25 import BM25Okapi

tokenized = [list(jieba.cut(item["text"])) for item in chunks]
bm25 = BM25Okapi(tokenized)
# 持久化 chunks、tokenized 和 BM25 参数，启动时恢复
```

## 5. 召回 Retrieval

### 5.1 混合召回

单纯向量检索擅长语义相似，但可能遗漏“2019”“同比增长率”等精确数字和术语；单纯 BM25 擅长关键词，但对用户换一种说法不够鲁棒。因此采用：

```text
向量召回 Top-10
       +
BM25 召回 Top-10
       +
可选的问题改写召回 Top-10
       |
       v
按 chunk_id 去重
       |
加权融合 / RRF
       |
过滤低于阈值结果
```

推荐初始参数：

- 向量 Top-K：10
- BM25 Top-K：10
- 改写问题：最多 2 个
- 融合候选上限：20-30 个
- 向量相似度阈值：初始 `0.55`，需使用验证集校准
- 最终送重排：10-20 个候选
- 没有结果达到阈值时，不调用 LLM，直接返回资料未涉及

由于不同向量库的距离和相似度定义可能不同，阈值必须通过实际数据校准，不应直接照搬。

### 5.2 多路召回

例如用户问题：

```text
为什么不能只看2017到2019年的客流量原始数字？
```

可以生成一个规范化检索问题：

```text
原始客流量、同比增长量、同比增长率、季节性、趋势判断之间有什么关系？
```

原始问题用于保留用户表达，改写问题用于扩展课程术语。问题改写本身也必须限制为“只改写，不回答”。

### 5.3 召回伪代码

```python
def hybrid_retrieve(question, top_k=10, threshold=0.55):
    queries = [question]
    rewritten = rewrite_query(question)  # 可选；失败时返回空列表
    queries.extend(rewritten[:2])

    candidates = {}
    for query in queries:
        q_vector = embedder.encode(query, normalize_embeddings=True).tolist()
        vector_hits = vector_store.search(q_vector, limit=top_k)
        bm25_hits = bm25_search(query, limit=top_k)

        for rank, hit in enumerate(vector_hits):
            if hit.score >= threshold:
                item = candidates.setdefault(hit.chunk_id, hit.to_dict())
                item["vector_rank"] = min(item.get("vector_rank", 999), rank)

        for rank, hit in enumerate(bm25_hits):
            item = candidates.setdefault(hit.chunk_id, hit.to_dict())
            item["bm25_rank"] = min(item.get("bm25_rank", 999), rank)

    # Reciprocal Rank Fusion，降低不同分数体系不可直接相加的问题
    for item in candidates.values():
        item["fusion_score"] = (
            1 / (60 + item.get("vector_rank", 999)) +
            1 / (60 + item.get("bm25_rank", 999))
        )
    return sorted(
        candidates.values(),
        key=lambda item: item["fusion_score"],
        reverse=True,
    )[:20]
```

### 5.4 性能估算

单机 CPU、课程规模 1-5 万分片时的经验目标：

| 阶段 | 目标耗时 |
|---|---:|
| 查询 embedding | 30-150 ms（GPU 更低） |
| 向量检索 | 5-30 ms |
| BM25 检索 | 5-30 ms |
| 多路合并 | 1-10 ms |
| 召回总耗时 | 50-250 ms |

建议监控 P50/P95 延迟、Top-K 命中率、阈值过滤率和空召回率。

## 6. 重排 Reranking

### 6.1 为什么需要重排

召回阶段的目标是“广”，需要尽可能不遗漏相关内容；如果直接把召回结果送入 LLM，可能出现：

- 关键词相同但上下文不相关
- 相似片段重复占用上下文
- PPT 标题命中但正文不能回答问题
- 多路召回分数不可比较

重排阶段使用更精细的交叉编码器同时阅读“问题 + 候选片段”，重新评估相关性，在召回广度和生成精度之间取得平衡。

### 6.2 模型选型

首选：`BAAI/bge-reranker-base`。

- 支持中文语义相关性判断
- 输入问题和候选片段对，输出精细相关性分数
- 比大型重排模型占用资源更低，适合单机部署
- 若精度不足，可测试 `bge-reranker-v2-m3` 或云端 rerank API

### 6.3 输入输出

输入：

```json
{
  "query": "同比增长率反映什么？",
  "candidate": "同比增长率可以反映客流量相对于上一年度同期的变化速度。"
}
```

输出：

```json
{
  "chunk_id": "rail-passenger-traffic-v1-p009-c002",
  "score": 0.91
}
```

最终保留 Top-3 作为默认值；课程概念较多或问题复杂时使用 Top-5。通常不建议超过 5 个，避免上下文冗余。

### 6.4 重排伪代码

```python
from FlagEmbedding import FlagReranker

reranker = FlagReranker("BAAI/bge-reranker-base", use_fp16=True)

def rerank(question, candidates, keep=3):
    pairs = [[question, item["text"]] for item in candidates]
    scores = reranker.compute_score(pairs, normalize=True)
    if not isinstance(scores, list):
        scores = [scores]

    ranked = []
    for item, score in zip(candidates, scores):
        result = dict(item)
        result["rerank_score"] = float(score)
        ranked.append(result)

    ranked.sort(key=lambda item: item["rerank_score"], reverse=True)
    return ranked[:keep]
```

重排初始性能目标：

- CPU：10-20 个候选约 300-1200 ms
- 单张消费级 GPU：约 50-250 ms
- P95 目标：不超过 2 秒

如果重排延迟过高，可以将候选数从 20 降到 10，或使用更小模型；不要直接取消重排。

## 7. 生成 Generation

### 7.1 LLM 选型

| 方案 | 优点 | 缺点 | 推荐场景 |
|---|---|---|---|
| GPT-4o-mini API | 速度快、中文能力稳定、接入简单 | 需要出网和 API 费用 | 首次上线、追求效果 |
| 通义千问 API | 中文问答和国内访问体验较好 | 依赖云端服务 | 国内部署和中文课程 |
| Ollama + Qwen2.5 | 数据留在本地，无 API 费用 | 需要显存/内存，维护模型 | 内网、隐私要求高 |
| Qwen3 本地模型 | 中文与指令能力较强 | 部署资源要求更高 | 后续本地精度升级 |

建议第一阶段使用云端 API 快速验证 RAG 链路；如果课程资料不得出网，再使用 Ollama + Qwen2.5。LLM provider 使用环境变量配置，不在代码中写死 Key。

推荐参数：

- `temperature=0.3`
- `top_p=0.8`
- `max_tokens=600-1000`
- 非流式返回用于首版验收；稳定后可增加流式输出

### 7.2 微课数字人 Prompt 模板

```text
你是“微课数字人：铁路数据管理与客流量分析”的课程助教。

你的任务是严格依据【课程资料】回答【用户问题】。

必须遵守以下规则：
1. 只能使用课程资料中明确出现的信息，不得凭常识补充课程资料没有提供的结论。
2. 如果课程资料无法支持答案，必须明确回答：“课程资料中未涉及该内容。”
3. 不要编造数据、公式、页码、章节或实验结果。
4. 回答尽量简洁、口语化，适合数字人朗读。
5. 不要输出 Markdown 符号，包括星号、井号、反引号、项目符号和表格分隔线。
6. 涉及计算时，说明公式、已知数据和结论；不要只给最终结果。
7. 回答末尾必须给出引用，格式为：“来源：第X页，章节：Y”。如果有多个来源，最多列出3个。
8. 不要把“来源”中的元数据当成课程事实，不要引用无法提供的来源。

【用户问题】
{question}

【课程资料】
{context}

【输出格式】
先给出直接答案。
如果需要，再补充简短解释。
最后输出：来源：...
```

上下文格式示例：

```text
[片段1]
内容：同比增长率等于同比增长量除以同期值，可以反映增长速度。
来源：铁路数据管理与客流量分析，第9页，章节：项目实施/计算指标

[片段2]
内容：2018年客流量为337495万人，2019年为366002万人。
来源：铁路数据管理与客流量分析，第4页，章节：项目背景
```

### 7.3 生成伪代码

```python
def generate_answer(question, ranked_chunks):
    if not ranked_chunks:
        return {
            "answer": "课程资料中未涉及该内容。",
            "citations": [],
        }

    context = "\n\n".join(format_context(item) for item in ranked_chunks)
    prompt = RAG_PROMPT.format(question=question, context=context)
    response = llm.chat.completions.create(
        model=os.getenv("RAG_LLM_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        top_p=0.8,
        max_tokens=800,
    )
    answer = clean_markdown(response.choices[0].message.content)
    return {
        "answer": answer,
        "citations": [citation_of(item) for item in ranked_chunks],
    }
```

## 8. RAG API 与原项目集成

### 8.1 API 设计

建议独立 RAG 服务监听 `127.0.0.1:8090`，生产环境通过 Nginx 或原项目后端反向代理。

请求：

```http
POST /rag/ask
Content-Type: application/json
```

```json
{
  "question": "为什么不能只看客流量原始数字？",
  "course_id": "rail-passenger-traffic",
  "session_id": "optional-session-id",
  "top_k": 10,
  "speak": true
}
```

返回：

```json
{
  "code": 0,
  "data": {
    "answer": "原始数字只能反映客流量的绝对水平，而同比增长量和同比增长率能够反映客流量的变化幅度和增长速度。\n\n来源：第5页，章节：项目实施。",
    "citations": [
      {
        "source_file": "铁路数据管理与客流量分析.pptx",
        "page": 5,
        "section": "项目实施",
        "score": 0.91
      }
    ],
    "retrieval": {
      "vector_candidates": 10,
      "bm25_candidates": 10,
      "rerank_candidates": 3,
      "latency_ms": 840
    }
  }
}
```

### 8.2 FastAPI 示例

```python
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="Micro-course RAG Service")

class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    course_id: str = "rail-passenger-traffic"
    session_id: str | None = None
    top_k: int = Field(default=10, ge=1, le=20)
    speak: bool = False

@app.post("/rag/ask")
def ask(req: AskRequest):
    candidates = hybrid_retrieve(req.question, top_k=req.top_k)
    ranked = rerank(req.question, candidates, keep=3)
    result = generate_answer(req.question, ranked)
    return {"code": 0, "data": result}
```

### 8.3 原项目最小接入方式

方案 A：前端直接请求 RAG 服务。

```javascript
fetch('http://127.0.0.1:8090/rag/ask', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
        question: text,
        course_id: 'rail-passenger-traffic',
        session_id: sessionid,
        speak: true
    })
}).then(r => r.json()).then(data => {
    const answer = data.data.answer;
    setReplyText(answer);
    // 如需数字人朗读，再调用现有 /human echo 接口。
});
```

方案 B：由 KnowTalk 的 `server/routes.py` 反向代理。推荐生产使用方案 B，可避免浏览器跨域和暴露内部服务地址。

```python
async def rag_ask(request):
    params = await request.json()
    async with aiohttp.ClientSession() as client:
        async with client.post(
            "http://127.0.0.1:8090/rag/ask",
            json=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            return web.Response(
                status=response.status,
                content_type="application/json",
                text=await response.text(),
            )
```

前端只需把 `postHuman(text, 'chat')` 的聊天分支改为调用 `/rag/ask`，原有气泡、连接状态和语音输入逻辑可以继续复用。

### 8.4 数字人朗读策略

推荐将“显示答案”和“朗读答案”分开：

1. `/rag/ask` 返回完整答案，前端立即更新原 AI 气泡
2. 删除引用行或将引用行保留为文字但不朗读
3. 将纯答案文本以 `type=echo` 调用现有 `/human`
4. 通过已有 `/is_speaking` 观察播报状态

这样可以让用户同时看到完整答案和引用来源，但数字人只朗读自然语言内容。

## 9. 端到端数据流转示例

用户提问：

```text
为什么不能只看2017到2019年的客流量原始数字？
```

完整链路：

1. 用户在 `knowtalk-chat.html` 输入问题。
2. 前端调用 `/rag/ask`，携带 `question`、`course_id` 和 `session_id`。
3. RAG 服务对问题做规范化和可选改写。
4. 嵌入模型将原问题和改写问题转成向量。
5. Chroma/Qdrant 执行向量 Top-10 召回。
6. BM25 对“原始数字、同比增长量、同比增长率”等词做关键词召回。
7. 服务按 `chunk_id` 去重，并使用 RRF 融合到 10-20 个候选。
8. `bge-reranker-base` 对“问题 + 片段”重新评分。
9. 保留最相关的 3 个片段。
10. Prompt 将问题、课程片段和页码元数据交给 LLM。
11. LLM 只基于片段生成答案，并输出来源。
12. RAG API 返回 `answer` 和 `citations`。
13. 前端将答案写入原 AI 气泡框。
14. 如果启用朗读，前端把纯答案文本调用现有 `/human` 的 `echo` 模式。
15. 现有 TTS 和数字人继续完成音频、视频和 PPT 播放，RAG 不参与 WebRTC 帧处理。

## 10. 技术栈汇总

| 模块名称 | 推荐方案 | 备选方案 | 说明/理由 |
|---|---|---|---|
| 文档解析 | `python-pptx`、PyMuPDF、`python-docx`、SRT/VTT parser | `unstructured` | 保留页码、章节和时间戳 |
| 分片 | LangChain RecursiveCharacterTextSplitter | SemanticChunker、自定义规则切分 | 先保证边界可解释和可调试 |
| 中文分词 | jieba | pkuseg、THULAC | BM25 关键词检索使用 |
| Embedding | `BAAI/bge-base-zh-v1.5` | `text2vec-large-chinese`、`bge-m3` | 中文语义检索稳定，部署成本适中 |
| 向量库 | Chroma | Qdrant、Milvus | Chroma 快速落地，Qdrant 适合生产 |
| 索引算法 | HNSW + cosine | IVF、Flat | 查询快，适合中小规模知识库 |
| 关键词检索 | BM25 / `rank_bm25` | Elasticsearch/OpenSearch | 精确匹配课程术语、数字和年份 |
| 混合融合 | RRF | 加权分数融合 | 不要求不同检索器分数同尺度 |
| 重排 | `BAAI/bge-reranker-base` | `bge-reranker-v2-m3` | 兼顾中文精度和本地资源消耗 |
| LLM | GPT-4o-mini 或通义千问 API | Ollama + Qwen2.5 | 首版快速验证，后续可本地化 |
| API 框架 | FastAPI | Flask/aiohttp | 类型校验、异步和文档支持较好 |
| 缓存 | Redis，可选 | 本地 TTLCache | 缓存相同问题和改写结果 |
| 配置 | `.env` + Pydantic Settings | Docker Secrets | 禁止将 API Key 写入代码和文档 |
| 监控 | Prometheus + 日志 | 结构化 JSON 日志 | 统计召回、重排和生成延迟 |
| 部署 | Docker Compose | Windows 服务/进程管理器 | 与原项目低耦合，便于启停和回滚 |

## 11. 落地实施步骤

总工时预计 5-7 个工作日，按 1 名开发人员估算。若已有课程资料清洗脚本和模型运行环境，可压缩到 5 天；若需要大量人工标注评测集，建议增加 1-2 天。

| 阶段 | 任务 | 工时 | 依赖条件 | 验收标准 |
|---|---|---:|---|---|
| 第1阶段 | 梳理课程资料、确定课程 ID、解析 PPT/讲义/字幕格式 | 0.5-1 天 | 获得原始资料和文件权限 | 至少成功解析一套《铁路数据管理与客流量分析》资料，页码/时间戳不丢失 |
| 第2阶段 | 开发语义分片、元数据结构和 JSONL 导出 | 1 天 | 第1阶段解析结果 | 分片达到 256-512 tokens，重叠 10%-15%，标题和来源完整 |
| 第3阶段 | 接入 Embedding、Chroma 和 BM25，完成索引构建 | 1 天 | Python 模型环境 | 可重复执行索引，支持新增、删除和增量更新 |
| 第4阶段 | 实现向量+BM25 混合召回和可选问题改写 | 0.5-1 天 | 第3阶段索引 | Top-10 能召回课程中的目标片段，输出召回分数和来源 |
| 第5阶段 | 接入中文 Reranker，完成 Top-3/Top-5 筛选 | 0.5 天 | 第4阶段召回接口 | 相关片段排序优于单路召回，P95 重排耗时不超过 2 秒 |
| 第6阶段 | 接入 LLM、Prompt、引用和无答案拒答 | 0.5-1 天 | 第5阶段候选结果 | 答案只引用资料内容，无答案时返回固定拒答语 |
| 第7阶段 | 开发 `/rag/ask`、接入前端气泡和可选 TTS 朗读 | 0.5-1 天 | 第6阶段生成服务 | 原问答功能可用，RAG 服务异常时可切回 `/human` |
| 第8阶段 | 联调、性能测试、日志、部署和回滚演练 | 0.5-1 天 | 前述阶段完成 | 完成 20-50 条测试问题，记录准确率、延迟和异常降级结果 |

### 11.1 验收数据集建议

首版至少准备 30 条问题：

- 15 条课程内直接事实问题
- 8 条需要跨页综合的问题
- 4 条同义改写问题
- 3 条课程外问题

验收目标：

- 课程内问题 Top-3 召回命中率不低于 85%
- 引用页码正确率不低于 90%
- 课程外问题拒答准确率不低于 90%
- 本地服务不含 LLM 网络耗时时，召回+重排 P95 不超过 2 秒
- 完整问答 P95 目标不超过 8 秒，云端模型耗时单独记录

## 12. 与原项目关系说明

### 12.1 代码侵入点

最小侵入方案只涉及：

- 新增 `rag_service/` 独立目录和进程
- `server/routes.py` 可选新增 `/rag/ask` 代理
- `web/knowtalk-chat.html` 的 `postHuman()` 中增加 RAG 请求分支
- 若需要朗读，只复用现有 `/human` 的 `echo` 调用，不改 TTS 实现

不修改：

- `server/rtc_manager.py`
- `server/webrtc.py`
- Avatar 推理和视频帧处理
- TTS 具体引擎
- 现有 PPT 播放和素材接口

### 12.2 数据侵入情况

- 不触碰原有 `data/materials/slides_config.json` 的结构
- 不修改原有 Avatar、会话和录制数据
- RAG 使用独立的 `rag_service/data/` 存储 chunks、manifest、BM25 和向量索引
- 可选择从 `data/materials/slides_config.json` 读取 PPT 图片/页码元数据，但不写回原文件
- 课程原文建议单独放置并纳入版本管理或对象存储

### 12.3 新增运行依赖

开发/单机版新增：

- 一个 RAG API 进程，例如 `uvicorn rag_service.api:app --port 8090`
- Embedding 模型文件
- Reranker 模型文件
- Chroma 持久化目录和 BM25 索引文件
- 可选的 LLM API 网络访问

生产版可增加：

- Qdrant 或 Milvus 服务
- Redis 缓存
- Prometheus/Grafana 监控

### 12.4 降级和回滚方案

RAG 服务异常时按以下策略降级：

1. 前端请求 `/rag/ask` 超时或返回错误，保留用户问题气泡。
2. 显示“课程知识库暂时不可用，正在使用普通问答模式”。
3. 将问题回退到现有 `/human` 的 `chat` 模式，保证数字人仍能工作。
4. 如果 LLM 可用但召回为空，不让模型自由发挥，返回“课程资料中未涉及该内容”。
5. RAG 服务可以单独停止，原 KnowTalk 继续启动。
6. 回滚时删除前端 RAG 分支或设置 `RAG_ENABLED=false`，无需恢复原核心代码。

推荐配置：

```text
RAG_ENABLED=true
RAG_BASE_URL=http://127.0.0.1:8090
RAG_TIMEOUT_SECONDS=20
RAG_FALLBACK_TO_HUMAN=true
```

## 13. 风险与后续优化

### 13.1 主要风险

- PPT 文本提取顺序异常，导致上下文不完整
- 课程资料中存在重复页或多个版本，召回结果重复
- 相似度阈值没有通过真实问题校准，造成误拒答或误召回
- 引用元数据在清洗时丢失
- LLM 输出仍包含格式符号，需要生成前后双重清理
- TTS 播报时间大于文字生成时间，需要继续使用 `/is_speaking` 同步状态

### 13.2 后续优化

- 增加课程、章节和版本过滤器
- 支持按当前 PPT 页码作为检索过滤条件
- 对公式、表格和代码增加专用解析器
- 引入问题分类：事实查询、计算题、解释题、课程外问题
- 增加答案可信度分级和人工反馈按钮
- 使用用户问题、命中片段、最终答案建立离线评测集
- 对相同问题做 Redis 缓存，降低重复请求延迟
- 未来可将 RAG 答案以结构化事件发送给现有 PPT 切换逻辑，例如附带 `slide_id`

## 14. 结论

推荐采用“独立 RAG 服务 + Chroma/Qdrant + BGE 中文 Embedding + BM25 混合召回 + BGE Reranker + 云端或本地 Qwen LLM”的方案。

首版只在前端问答调用和后端可选代理处增加少量代码，不改变 KnowTalk 的 WebRTC、数字人推理、TTS 和素材管理主流程。按照本文实施步骤，5-7 个工作日可以完成从课程资料导入、索引、召回、重排、生成到数字人问答展示和朗读的可验证闭环。
