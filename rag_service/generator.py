from .cleaning import clean_markdown, speech_text
from .ollama_client import chat
import re


RAG_PROMPT = """你是微课课程的课程助教。

请严格依据下面提供的课程资料回答用户问题。

规则：
1. 只能使用课程资料中明确出现的信息，不得凭常识补充课程资料没有提供的结论。
2. 课程资料无法支持答案时，只回答：课程资料中未涉及该内容。
3. 不得编造数据、公式、页码、章节或实验结果。
4. 回答简洁、口语化，适合数字人朗读。
5. 不要输出 Markdown，包括星号、井号、反引号、项目符号和表格分隔线。
6. 先给出直接答案，再给出必要的简短解释。
7. 不要在回答正文中输出来源、页码或章节信息，来源由系统单独展示。
8. 只能使用提供的课程资料，不得虚构信息。

用户问题：
{question}

课程资料：
{context}

输出要求（务必遵守）：
1. 只输出面向学员的答案正文，最多两三句话。
2. 不要输出你的分析过程，不要说“好的”“让我整理一下”这类开场白。
3. 不要出现“片段”“第几页”“来源”等编号或引用标记。
4. 只能直接引用课程资料里写过的内容，不得推断、不得补充资料里没有的说法。
5. 如果课程资料里没有直接回答这个问题，就按第 2 条只回复那一句话，不要解释原因。
"""


def _format_source(metadata):
    source = metadata.get("source_file", "未知文件")
    page = metadata.get("page")
    section = metadata.get("section") or "未标注章节"
    if page is not None:
        return f"{source}，第{page}页，章节：{section}"
    start = metadata.get("video_start")
    end = metadata.get("video_end")
    if start is not None and end is not None:
        return f"{source}，视频 {start:.1f}-{end:.1f} 秒，章节：{section}"
    return f"{source}，章节：{section}"


def _citation_key(metadata):
    return (
        metadata.get("source_file", ""),
        metadata.get("page"),
        metadata.get("section"),
        metadata.get("video_start"),
        metadata.get("video_end"),
    )


def format_context(chunks):
    blocks = []
    for index, item in enumerate(chunks, 1):
        blocks.append(f"[片段{index}]\n内容：{item['text']}\n来源：{_format_source(item['metadata'])}")
    return "\n\n".join(blocks)


def generate_answer(question, chunks):
    # 二次防线：即使调用方传入了错误来源，也绝不让非 PDF 内容进入提示词。
    chunks = [
        item for item in chunks
        if str(item.get("metadata", {}).get("source_file", "")).lower().endswith(".pdf")
    ]
    if not chunks:
        text = "课程资料中未涉及该内容。"
        return {"answer": text, "speak_text": text, "citations": []}

    response = chat([{
        "role": "user",
        "content": RAG_PROMPT.format(question=question, context=format_context(chunks)),
    }])
    answer = clean_markdown(response) or "课程资料中未涉及该内容。"
    # 模型有时会在正常答案后面又补一句「课程资料中未涉及该内容。」
    # （把提示词里第 2 条的字面话也照抄出来）。既然已经给出了实质答案，
    # 这句就是噪声，去掉；只有整段就只有这一句时保留它。
    stripped = re.sub(
        r"\s*课程资料中未涉及该内容[。.]?\s*$", "", answer, count=1).strip()
    if stripped:
        answer = stripped
    citations = []
    seen_citations = set()
    for item in chunks:
        metadata = item["metadata"]
        key = _citation_key(metadata)
        if key in seen_citations:
            continue
        seen_citations.add(key)
        citations.append({
            "source_file": metadata.get("source_file", ""),
            "source_type": metadata.get("source_type", ""),
            "page": metadata.get("page"),
            "section": metadata.get("section"),
            "video_start": metadata.get("video_start"),
            "video_end": metadata.get("video_end"),
            "score": round(float(item.get("rerank_score", item.get("score", 0))), 4),
        })
    # 来源由前端以可点击标签单独展示；模型偶尔会把来源接在正文末尾，
    # 因此从第一个来源标记处截断，而不只过滤单独一行。
    # 模型可能把来源接在正文末尾，也可能单独换行；统一截掉来源标记之后的全部内容。
    answer_body = re.split(r"(?:参考来源|来源)\s*[:：]", answer, maxsplit=1)[0].strip()
    # 页面和 TTS 都只使用正文；来源由 citations 字段单独渲染。
    display_answer = answer_body
    return {
        "answer": display_answer,
        "speak_text": speech_text(answer_body),
        "citations": citations,
    }
