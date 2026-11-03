# -*- coding: utf-8 -*-
"""学员作答的 LLM 判分。

设计要点：
- **复用 `llm-configuration` 的配置**（`utils/settings_store.py`），不新增一套 LLM 配置；
- **非流式**调用：判分需要完整结论才能解析，流式反而增加复杂度；
- **失败即抛出** `QuizGradeError`，由编排器统一降级（降级逻辑只写在一处）。
  这样"判分不可用"只有一条路径会影响到课堂，易于验证。
- 只输出三档结论：正确 / 部分正确 / 未覆盖。
"""

import json
import re
import threading
import time

from utils.logger import logger
from utils.settings_store import get_store

VERDICT_CORRECT = "correct"
VERDICT_PARTIAL = "partial"
VERDICT_MISSING = "missing"
VERDICTS = (VERDICT_CORRECT, VERDICT_PARTIAL, VERDICT_MISSING)

# 判分是课堂内的同步等待：宁可快速降级，也不能把课堂挂住。
# 实测正常 1s 左右，12s 足够覆盖慢网络；配合 1 次重试，总等待上界 24s。
LLM_TIMEOUT = 12.0
LLM_MAX_RETRIES = 1
LLM_MAX_TOKENS = 512
MAX_ANSWER_CHARS = 2000

# 客户端缓存：每次判分都新建 OpenAI 会重新做 TLS 握手（实测 +2s）。
# 键为 (api_key, base_url)，配置变更后自然换新实例。
_client_lock = threading.Lock()
_client_cache = {}


def reset_client_cache():
    """清空客户端缓存（测试用；配置变更时由缓存键自然区分）。"""
    with _client_lock:
        _client_cache.clear()


def _get_client(api_key, base_url):
    """按配置复用判分客户端。"""
    from openai import OpenAI

    key = (api_key, base_url)
    with _client_lock:
        client = _client_cache.get(key)
        if client is None:
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=LLM_TIMEOUT,
                max_retries=LLM_MAX_RETRIES,
            )
            _client_cache.clear()  # 只保留当前配置，避免换 key 后旧实例堆积
            _client_cache[key] = client
        return client

# 模型可能返回中文或近义词，统一归一到三档
_VERDICT_ALIASES = {
    "correct": VERDICT_CORRECT,
    "right": VERDICT_CORRECT,
    "true": VERDICT_CORRECT,
    "正确": VERDICT_CORRECT,
    "完全正确": VERDICT_CORRECT,
    "partial": VERDICT_PARTIAL,
    "partially": VERDICT_PARTIAL,
    "part": VERDICT_PARTIAL,
    "部分正确": VERDICT_PARTIAL,
    "部分": VERDICT_PARTIAL,
    "missing": VERDICT_MISSING,
    "wrong": VERDICT_MISSING,
    "incorrect": VERDICT_MISSING,
    "false": VERDICT_MISSING,
    "未覆盖": VERDICT_MISSING,
    "错误": VERDICT_MISSING,
    "不正确": VERDICT_MISSING,
}

_DEFAULT_FEEDBACK = {
    VERDICT_CORRECT: "回答正确。",
    VERDICT_PARTIAL: "回答对了一部分，再想想还有哪些要点。",
    VERDICT_MISSING: "这次没有答到要点，我们继续往下讲。",
}


class QuizGradeError(RuntimeError):
    """判分失败。消息面向调用方，可被记录与回显。"""


SYSTEM_PROMPT = (
    "你是一名公正的课堂评委。你只输出 JSON，不要输出解释或 Markdown 代码块标记。"
    'JSON 形如 {"verdict": "...", "feedback": "..."}。'
    "verdict 只能是 correct（完全答到要点）、partial（答到一部分）、missing（没有答到要点）三者之一。"
    "feedback 是给学员看的一句话，口语化、适合朗读，不超过 60 字，不要使用 Markdown 标记。"
)

_USER_TEMPLATE = """请判定学员的回答是否覆盖了评判要点。

题目：{question}
参考答案：{reference_answer}
评判要点：
{rubric}

学员的回答：{answer}

请只输出 JSON。"""


def _strip_code_fence(text):
    fenced = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", text, re.S)
    return fenced.group(1) if fenced else text


def _load_json_object(text):
    """从模型输出里取出一个 JSON 对象；取不到就抛出。"""
    if not text or not str(text).strip():
        raise QuizGradeError("模型没有返回内容")

    raw = _strip_code_fence(str(text).strip())
    try:
        data = json.loads(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise QuizGradeError("模型返回的不是合法 JSON 对象")
        try:
            data = json.loads(raw[start:end + 1])
        except Exception as exc:  # noqa: BLE001
            raise QuizGradeError("模型返回的 JSON 无法解析: %s" % exc)

    if not isinstance(data, dict):
        raise QuizGradeError("模型返回的 JSON 不是对象")
    return data


def parse_verdict(raw_text):
    """解析模型输出，返回 (verdict, feedback)。无法解析时抛出。"""
    data = _load_json_object(raw_text)

    raw_verdict = data.get("verdict")
    if raw_verdict is None:
        raise QuizGradeError("模型输出缺少 verdict 字段")
    key = str(raw_verdict).strip().lower()
    verdict = _VERDICT_ALIASES.get(key)
    if verdict is None:
        raise QuizGradeError("无法识别的判分结论: %s（应为 correct/partial/missing）" % raw_verdict)

    feedback = str(data.get("feedback") or "").strip()
    if not feedback:
        # 反馈语不能为空，否则学员什么都听不到
        feedback = _DEFAULT_FEEDBACK[verdict]
    return verdict, feedback


def build_grading_prompt(question, answer):
    rubric = question.get("rubric") or []
    if isinstance(rubric, str):
        rubric = [rubric]
    rubric_text = "\n".join("- %s" % str(point) for point in rubric) or "- （未提供，按参考答案自行判断）"
    return _USER_TEMPLATE.format(
        question=str(question.get("question") or "").strip(),
        reference_answer=str(question.get("reference_answer") or "").strip(),
        rubric=rubric_text,
        answer=str(answer).strip()[:MAX_ANSWER_CHARS],
    )


def grade_answer(sessionid, question, answer, **kwargs):
    """判定一次作答。成功返回 {verdict, feedback, degraded}；失败抛出 QuizGradeError。

    签名与编排器的注入约定一致：`grader(sessionid, question, answer) -> dict`。
    """
    answer = (answer or "").strip()
    if not answer:
        raise QuizGradeError("作答内容为空，无法判分")
    if not isinstance(question, dict):
        raise QuizGradeError("题目结构不合法")

    store = get_store()
    api_key = store.get_str("llm.api_key")
    if not api_key:
        raise QuizGradeError("未配置 LLM API Key，无法判分（见设置页或 DEEPSEEK_API_KEY）")

    try:
        from openai import OpenAI  # noqa: F401 - 只用于探测依赖是否可用
    except Exception as exc:  # noqa: BLE001
        raise QuizGradeError("缺少 openai 依赖: %s" % exc)

    base_url = store.get_str("llm.base_url")
    client = _get_client(api_key, base_url)
    prompt = build_grading_prompt(question, answer)
    started = time.time()
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
        raise QuizGradeError("调用模型判分失败: %s" % exc)

    if not getattr(completion, "choices", None):
        raise QuizGradeError("模型没有返回任何候选结果")

    content = completion.choices[0].message.content or ""
    verdict, feedback = parse_verdict(content)
    logger.info("[提问] 判分完成 session=%s question=%s verdict=%s elapsed=%.2fs",
                sessionid, question.get("id"), verdict, time.time() - started)
    return {"verdict": verdict, "feedback": feedback, "degraded": False}
