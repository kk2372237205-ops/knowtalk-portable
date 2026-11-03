# rag-config-exposure Specification

## Purpose
定义 RAG 知识库服务的「技术配置可查询」契约：通过接口暴露其实际使用的向量库、嵌入模型、大模型、切分策略、重排模型与召回参数，使配置可见、可核对。

## ADDED Requirements

### Requirement: RAG 技术配置可经接口查询

`GET /rag/config` MUST 返回 HTTP 200，响应体为 `{"code": 0, "data": {...}}`。`data` MUST 包含以下分组：`vector_store`（含 `type`、`distance_metric`、`hnsw`）、`embedding`（含 `model`、`base_url`、`tokenizer`）、`llm`（含 `model`、`base_url`）、`chunking`（含 `method`、`chunk_size`、`chunk_overlap`、`tokenizer`）、`reranker`（含 `model`、`keep`）、`retrieval`（含 `vector_top_k`、`bm25_top_k`、`vector_threshold`、`fusion`）、`source`（含 `accepted`、`note`）。

#### Scenario: 查询配置

- **WHEN** 客户端请求 `GET /rag/config`
- **THEN** 响应状态码为 200
- **AND** `data.vector_store.type` 含 `chromadb` 与 `bm25` 两个关键字（混合检索）
- **AND** `data.chunking.chunk_size` 与 `data.chunking.chunk_overlap` 为正整数
- **AND** `data.reranker.model` 与 `data.reranker.keep` 均存在

#### Scenario: 配置字段与实际检索一致

- **WHEN** 客户端先后查询 `GET /rag/config` 与 `POST /rag/ask`
- **THEN** `data.retrieval.vector_top_k`、`bm25_top_k`、`vector_threshold` 与 `data.reranker.keep` 必须与 `/rag/ask` 实际使用的召回与重排参数取值一致

### Requirement: 主服务代理 RAG 配置接口

主服务（`aiohttp`，默认 `8010`）MUST 提供 `GET /rag/config` 代理，将请求转发到 RAG sidecar 的 `/rag/config` 并原样回传其响应；sidecar 不可用时 MUST 返回 502 且 `code` 非 0。

#### Scenario: 代理转发成功

- **WHEN** 主服务收到 `GET /rag/config` 且 sidecar 可达
- **THEN** 主服务回传 sidecar 的 `{"code": 0, "data": {...}}`

#### Scenario: sidecar 不可达

- **WHEN** 主服务收到 `GET /rag/config` 且 sidecar 不可达
- **THEN** 主服务返回状态码 502 且 `code` 非 0
