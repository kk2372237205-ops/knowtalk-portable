# -*- coding: utf-8 -*-
"""依据幻灯片文字出题。

复用 `llm-configuration` 已建立的对话模型配置（`utils/settings_store.py`），
不新增一套 LLM 配置。出题结果**不落库**——是否入库由调用方决定，避免
"生成即污染题库"。

本模块只做"文字 -> 结构化题目"的转换，不碰题库存储与 HTTP 层。
"""

import json
import re

from utils.logger import logger
from utils.settings_store import get_store

LLM_TIMEOUT = 90.0
LLM_MAX_TOKENS = 2048
MAX_COUNT = 10


class QuizGenerateError(RuntimeError):
    """出题失败。消息面向调用方，应可直接回显。"""


SYSTEM_PROMPT = (
    "你是一名严格的教学设计助手。你只输出 JSON，不输出任何解释、Markdown 代码块标记"
    "或多余文字。JSON 必须是数组，每个元素含 question、reference_answer、rubric 三个字段。"
    "rubric 是字符串数组，列出判定学员回答是否覆盖的要点，每项一句话。"
    "题目必须是开放式问答（不是选择题），语言与给定内容一致。"
)

_USER_TEMPLATE = """请依据下面的幻灯片内容出 {count} 道课堂提问。

要求：
1. 只针对幻灯片里出现的知识点提问，不要引入幻灯片之外的内容。
2. reference_answer 要给出标准答案；rubric 给出 2–4 条判定要点。
3. 直接输出 JSON 数组，不要任何额外文字。

幻灯片内容：
---
{slide_text}
---{context_block}"""

_CONTEXT_TEMPLATE = """

可参考的知识库检索内容（用于校准术语与深度，不要直接照抄）：
---
{context}
---"""


def _build_prompt(slide_text, count, context=None):
    context_block = ""
    if context and str(context).strip():
        context_block = _CONTEXT_TEMPLATE.format(context=str(context).strip()[:4000])
    return _USER_TEMPLATE.format(
        count=count, slide_text=str(slide_text).strip()[:8000], context_block=context_block
    )


def _strip_code_fence(text):
    """模型常把 JSON 包在 ```json ... ``` 里，这里去掉围栏。"""
    fenced = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", text, re.S)
    return fenced.group(1) if fenced else text


def parse_questions(raw_text, count=None):
    """把模型输出解析成题目列表。解析失败必须抛出，不得返回半成品。"""
    if not raw_text or not str(raw_text).strip():
        raise QuizGenerateError("模型没有返回内容")

    text = _strip_code_fence(str(raw_text).strip())
    try:
        data = json.loads(text)
    except Exception:
        # 退一步：截取第一个 '[' 到最后一个 ']' 之间的内容再试
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            raise QuizGenerateError("模型返回的不是合法 JSON 数组")
        try:
            data = json.loads(text[start:end + 1])
        except Exception as exc:  # noqa: BLE001
            raise QuizGenerateError("模型返回的 JSON 无法解析: %s" % exc)

    if isinstance(data, dict):
        data = data.get("questions") or data.get("items")
    if not isinstance(data, list):
        raise QuizGenerateError("模型返回的 JSON 不是数组")

    questions = []
    for item in data:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        answer = str(item.get("reference_answer") or item.get("answer") or "").strip()
        if not question or not answer:
            continue
        rubric = item.get("rubric") or []
        if isinstance(rubric, str):
            rubric = [rubric]
        rubric = [str(point).strip() for point in rubric if str(point).strip()]
        questions.append({
            "question": question,
            "reference_answer": answer,
            "rubric": rubric,
        })

    if not questions:
        raise QuizGenerateError("模型返回的内容里没有可用的题目（缺少题干或参考答案）")
    if count is not None:
        questions = questions[:int(count)]
    return questions


def generate_questions(slide_text, count, context=None):
    """用对话 LLM 依据幻灯片文字出题，返回 [{question, reference_answer, rubric}]。"""
    if not slide_text or not str(slide_text).strip():
        raise QuizGenerateError("必须提供幻灯片文字")
    count = int(count or 1)
    if count <= 0 or count > MAX_COUNT:
        raise QuizGenerateError("出题数量必须在 1 到 %d 之间" % MAX_COUNT)

    store = get_store()
    api_key = store.get_str("llm.api_key")
    if not api_key:
        raise QuizGenerateError("未配置 LLM API Key，无法自动出题（见设置页或 DEEPSEEK_API_KEY）")

    try:
        from openai import OpenAI
    except Exception as exc:  # noqa: BLE001
        raise QuizGenerateError("缺少 openai 依赖: %s" % exc)

    client = OpenAI(
        api_key=api_key,
        base_url=store.get_str("llm.base_url"),
        timeout=LLM_TIMEOUT,
    )
    prompt = _build_prompt(slide_text, count, context)
    try:
        completion = client.chat.completions.create(
            model=store.get_str("llm.model"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=LLM_MAX_TOKENS,
            stream=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise QuizGenerateError("调用模型失败: %s" % exc)

    content = ""
    if completion.choices:
        content = completion.choices[0].message.content or ""
    questions = parse_questions(content, count=count)
    logger.info("[提问] 自动出题成功，共 %d 道", len(questions))
    return questions
