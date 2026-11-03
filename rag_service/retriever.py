from .config import BM25_TOP_K, VECTOR_THRESHOLD, VECTOR_TOP_K
from .index_store import store
from .ollama_client import embed_query


def _pdf_only(hits):
    """RAG 最终白名单：只看 source_file 扩展名是不是 .pdf。

    source_type 字段历史上可能不可靠（PPT 曾被标记为 'ppt'），所以这里
    不再依赖 source_type，只看文件后缀——这是 RAG 唯一可信的过滤线。
    """
    kept = []
    for hit in hits:
        src = str(hit.get("metadata", {}).get("source_file", "")).lower()
        if src.endswith(".pdf"):
            kept.append(hit)
    return kept


def hybrid_retrieve(question: str, course_id: str, top_k: int = 10):
    if not store.course_has_data(course_id):
        return [], {"vector_candidates": 0, "bm25_candidates": 0}

    candidates = {}
    vector_hits = _pdf_only(store.vector_search(course_id, embed_query(question), max(top_k, VECTOR_TOP_K)))
    bm25_hits = _pdf_only(store.bm25_search(course_id, question, max(top_k, BM25_TOP_K)))

    for rank, hit in enumerate(vector_hits):
        if hit["score"] < VECTOR_THRESHOLD:
            continue
        item = candidates.setdefault(hit["chunk_id"], dict(hit))
        item["vector_rank"] = min(item.get("vector_rank", 999), rank)

    for rank, hit in enumerate(bm25_hits):
        item = candidates.setdefault(hit["chunk_id"], dict(hit))
        item["bm25_rank"] = min(item.get("bm25_rank", 999), rank)

    for item in candidates.values():
        item["fusion_score"] = (
            1 / (60 + item.get("vector_rank", 999)) +
            1 / (60 + item.get("bm25_rank", 999))
        )
    results = sorted(candidates.values(), key=lambda item: item["fusion_score"], reverse=True)[:20]
    return results, {
        "vector_candidates": len(vector_hits),
        "bm25_candidates": len(bm25_hits),
        "fusion_candidates": len(results),
    }
