# -*- coding: utf-8 -*-
"""一次提问式讲课的总评分。

计分口径（写死在规格里，改这里必须改规格）：

- 完全正确 `correct`  = 100 分
- 部分正确 `partial`  = 60 分
- 未覆盖 `missing` / 未作答 / 判分不可用 = 0 分
- 总分 = 各题得分算术平均，四舍五入取整

为什么用平均而不是累加：一次课堂的题数是可变的（1~N 题），
累加分在不同课堂之间不可比，学员看到"60 分"也不知道是好是坏。

没有作答记录时不给分（返回 ``None``），避免"0 分"被误读成"全错"。
"""

VERDICT_POINTS = {
    "correct": 100,
    "partial": 60,
    "missing": 0,
}


def _points_of(record):
    """单题得分。未作答、判分不可用、未知结论一律 0 分。"""
    if not isinstance(record, dict):
        return 0
    if record.get("unanswered"):
        return 0
    verdict = record.get("verdict")
    return VERDICT_POINTS.get(verdict, 0)


def summarize(records):
    """把一次会话的问答记录汇总成评分。

    返回 ``None`` 表示这次课堂没有任何作答记录（不评分）。
    否则返回 ``{total, answered, correct, partial, missing, unanswered, degraded, score}``。
    """
    items = [item for item in (records or []) if isinstance(item, dict)]
    if not items:
        return None

    summary = {
        "total": len(items),
        "answered": 0,
        "correct": 0,
        "partial": 0,
        "missing": 0,
        "unanswered": 0,
        "degraded": 0,
        "score": 0,
    }
    earned = 0
    for record in items:
        earned += _points_of(record)
        if record.get("degraded"):
            summary["degraded"] += 1
        if record.get("unanswered"):
            summary["unanswered"] += 1
            continue
        summary["answered"] += 1
        verdict = record.get("verdict")
        if verdict == "correct":
            summary["correct"] += 1
        elif verdict == "partial":
            summary["partial"] += 1
        else:
            summary["missing"] += 1

    summary["score"] = int(round(float(earned) / summary["total"]))
    return summary


def format_summary(summary):
    """生成一句适合朗读的中文评分。没有评分时返回空串。"""
    if not summary:
        return ""
    parts = ["本次课堂共 %d 题" % summary["total"]]
    if summary["correct"]:
        parts.append("答对 %d 题" % summary["correct"])
    if summary["partial"]:
        parts.append("部分正确 %d 题" % summary["partial"])
    if summary["missing"]:
        parts.append("未答到要点 %d 题" % summary["missing"])
    if summary["unanswered"]:
        parts.append("未作答 %d 题" % summary["unanswered"])
    text = "，".join(parts) + "，本次得分 %d 分。" % summary["score"]
    if summary.get("degraded"):
        text += "（其中 %d 题因判分服务不可用未能评分）" % summary["degraded"]
    return text
