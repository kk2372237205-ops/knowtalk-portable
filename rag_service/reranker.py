from .config import RERANKER_NAME, RERANK_TOP_K


# 关闭跨编码器重排（RAG_RERANK_TOP_K<=0）时，用融合分（fusion_score）做轻量精排、
# 取前 N 条喂给大模型。取 5 条：既保证上下文聚焦（避免弱相关片段稀释答案），
# 又比原重排的 3 条多留一点召回余量。可按需调整。
FUSION_FALLBACK_K = 5

# 送进跨编码器的候选上限。融合召回到 20 条，但重排模型在 CPU 上逐条编码很慢
# （实测 20 条约 7s，是问答延迟的大头）。按融合分取前 N 条再送进去，
# 正确片段本来就排在最前面几条，截断几乎不损召回，延迟能砍掉近一半。
RERANK_CANDIDATE_LIMIT = 12


_reranker = None


def _get_reranker():
    global _reranker
    if _reranker is None:
        from FlagEmbedding import FlagReranker
        # 重排模型固定在 CPU 上跑：本机 GPU（RTX 4060 8GB）已要同时容纳
        # Ollama 的嵌入模型与 qwen3-gpu:8b 大模型，重排再占 GPU 会间歇性
        # CUDA OOM（表现为 /rag/ask 偶发 500）。重排只打 10~20 个候选对，
        # CPU 完全够用，且能彻底消除与 LLM 的显存争用。
        _reranker = FlagReranker(RERANKER_NAME, use_fp16=False, devices=["cpu"])
    return _reranker


def rerank(question, candidates, keep=RERANK_TOP_K):
    if not candidates:
        return []
    # 重排关闭（keep<=0）：不加载重排模型，改用混合召回的融合分（fusion_score，
    # 即向量+BM25 的 RRF 倒数排名融合）做轻量精排，取前 FUSION_FALLBACK_K 条。
    # 什么时候会走到这里：显存/内存实在腾不出来时（RAG_RERANK_TOP_K=0）。
    # 注意：此处必须截断，不能把全部候选（最多 20 条）一股脑塞进提示词，否则
    # 大量弱相关片段会稀释上下文、让大模型答非所问。
    # ⚠️ 融合分几乎是平的（实测 0.0176/0.0176/0.0173/0.0173），正确片段常排在
    # 第 3~4 位，所以这条降级路径的准确率明显不如开着重排，能开就开。
    if keep <= 0:
        fused = sorted(
            candidates,
            key=lambda item: float(item.get("fusion_score", 0.0)),
            reverse=True,
        )
        return fused[:FUSION_FALLBACK_K]
    # 只送前 RERANK_CANDIDATE_LIMIT 条进跨编码器：CPU 上逐条编码很慢，
    # 而正确片段本来就排在融合分最前面，截断几乎不损召回。
    pool = candidates[:RERANK_CANDIDATE_LIMIT]
    scores = _get_reranker().compute_score(
        [[question, item["text"]] for item in pool],
        normalize=True,
    )
    if not isinstance(scores, list):
        scores = [scores]
    ranked = []
    for item, score in zip(pool, scores):
        value = dict(item)
        value["rerank_score"] = float(score)
        ranked.append(value)
    ranked.sort(key=lambda item: item["rerank_score"], reverse=True)
    return ranked[:keep]
