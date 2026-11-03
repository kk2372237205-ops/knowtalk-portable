# -*- coding: utf-8 -*-
"""提问点选取与题目绑定。

职责边界：
- **候选提问点**由讲稿分段推导，默认落在幻灯片边界（讲完一页算一个候选点）；
  整篇没有 `[PPT:N]` 标签时退化为段落边界。
- **选取方式**支持随机抽取、固定间隔、显式指定三种；随机抽取用独立随机源，
  给定相同（候选集、数量、种子）必然得到相同结果，不消耗全局随机状态。
- **绑定表**以幻灯片编号为键，作用于“幻灯片等于该编号”的那个候选提问点；
  绑定优先于选取方式，且冲突必须在启动前被拒绝并指明冲突项。

本模块不依赖 HTTP 层，输入是已经解析好的 `[(段文本, slide_id), ...]`
（讲稿带手势时第三项为 `gesture`；本模块只用前两项，第三个字段对它是透明的）。
"""

import random

from utils.logger import logger

MODE_RANDOM = "random"
MODE_INTERVAL = "interval"
MODE_EXPLICIT = "explicit"
MODES = (MODE_RANDOM, MODE_INTERVAL, MODE_EXPLICIT)


class QuizConfigError(ValueError):
    """提问模式的配置不合法。消息中必须指出冲突项，便于调用方直接回显。"""


def candidate_positions(segments):
    """从讲稿分段推导候选提问点（教学段索引）。

    一个候选点 `i` 表示“朗读完第 i 段之后提问”。
    - 有 `[PPT:N]` 标签时，每张幻灯片取其**最后一个**分段（讲完一页算一个候选点）；
    - 整篇都没有标签时，退化为每一段的边界。
    """
    if not segments:
        return []

    last_index = {}
    order = []
    for index, item in enumerate(segments):
        slide_id = item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else None
        if slide_id not in last_index:
            order.append(slide_id)
        last_index[slide_id] = index

    # 整篇无标签（只出现过一个 None）→ 段落边界
    if len(order) == 1 and order[0] is None:
        return list(range(len(segments)))

    return sorted({last_index[slide] for slide in order})


def slide_of_positions(segments):
    """候选点索引 -> 该点所属幻灯片编号（无标签为 None）。"""
    result = {}
    for index in candidate_positions(segments):
        item = segments[index]
        result[index] = item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else None
    return result


def _looks_like_segments(value):
    if not value:
        return False
    first = value[0]
    return isinstance(first, (tuple, list))


def _resolve_pool(candidates, slide_range=None):
    """允许传入候选位置列表，或直接传入讲稿分段。"""
    if _looks_like_segments(candidates):
        segments = candidates
        pool = candidate_positions(segments)
        if slide_range:
            low, high = slide_range
            pool = [
                position for position in pool
                if segments[position][1] is not None and low <= segments[position][1] <= high
            ]
        return pool
    return sorted(set(candidates))


def _deterministic_sample(pool, count, seed):
    """用独立随机源取样，不动全局随机状态。"""
    ordered = sorted(pool)
    generator = random.Random(seed) if seed is not None else random.Random()
    return generator.sample(ordered, count)


def select_points(candidates, count=None, mode=MODE_RANDOM, seed=None,
                  interval=None, explicit=None, slide_range=None):
    """选取提问点，返回升序的候选位置列表。

    - 随机：`count` 必填；`seed` 给定时结果可复现。
    - 固定间隔：按 `interval` 取第 interval、2*interval…… 个候选位置；`count` 给定时截断。
    - 显式：使用 `explicit` 给出的位置，必须全部落在候选范围内。
    """
    if mode not in MODES:
        raise QuizConfigError("未知的提问点选取方式: %s（可选: %s）" % (mode, "/".join(MODES)))

    pool = _resolve_pool(candidates, slide_range)
    if not pool:
        raise QuizConfigError("当前范围内没有可用的提问点（幻灯片范围 %s）" % (slide_range,))

    if mode == MODE_RANDOM:
        if count is None:
            raise QuizConfigError("随机选取提问点必须给出数量")
        if count <= 0:
            raise QuizConfigError("提问点数量必须大于 0，收到 %s" % count)
        if count > len(pool):
            raise QuizConfigError(
                "可选提问点只有 %d 个，无法抽取 %d 个" % (len(pool), count)
            )
        return sorted(_deterministic_sample(pool, count, seed))

    if mode == MODE_INTERVAL:
        if not interval or interval <= 0:
            raise QuizConfigError("按固定间隔选取时必须给出大于 0 的间隔，收到 %s" % interval)
        picked = pool[interval - 1::interval]
        if not picked:
            raise QuizConfigError(
                "间隔 %s 在 %d 个候选提问点上取不到任何位置" % (interval, len(pool))
            )
        if count is not None:
            if count <= 0:
                raise QuizConfigError("提问点数量必须大于 0，收到 %s" % count)
            if count > len(picked):
                raise QuizConfigError(
                    "按间隔 %s 只能取到 %d 个提问点，无法满足 %d 个"
                    % (interval, len(picked), count)
                )
            picked = picked[:count]
        return list(picked)

    # 显式
    if explicit is None:
        raise QuizConfigError("显式选取提问点必须给出位置列表")
    allowed = set(pool)
    duplicates = [position for position in set(explicit) if explicit.count(position) > 1]
    if duplicates:
        raise QuizConfigError("显式位置列表中存在重复项: %s" % sorted(duplicates))
    outside = [position for position in explicit if position not in allowed]
    if outside:
        raise QuizConfigError(
            "以下位置不在候选提问点范围内: %s（候选: %s）" % (sorted(outside), sorted(allowed))
        )
    return sorted(explicit)


def _normalize_rubric(rubric):
    """评判要点：允许省略、允许单个字符串，统一成字符串数组。"""
    if rubric is None:
        return []
    if isinstance(rubric, str):
        rubric = [rubric]
    if not isinstance(rubric, (list, tuple)):
        raise QuizConfigError("评判要点必须是字符串或字符串数组")
    return [str(item).strip() for item in rubric if str(item).strip()]


def _normalize_slide_id(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise QuizConfigError("问答对的幻灯片编号必须是整数，收到 %r" % (value,))


def normalize_inline_questions(pairs):
    """规范化发起请求里内联给出的问答对。

    返回项均带 `id`（`inline-N`，按给出顺序）、`question`、`reference_answer`、
    `rubric`、`slide_id`、`source="inline"`。题干与参考答案必填，
    缺哪一个都必须指明是第几对——否则教师无从定位自己漏填了哪一行。
    """
    if not isinstance(pairs, (list, tuple)) or not pairs:
        raise QuizConfigError("questions 必须是非空的问答对数组")

    result = []
    for index, item in enumerate(pairs, start=1):
        if not isinstance(item, dict):
            raise QuizConfigError("第 %d 个问答对必须是对象" % index)
        question = item.get("question")
        if not isinstance(question, str) or not question.strip():
            raise QuizConfigError("第 %d 个问答对缺少题干" % index)
        reference = item.get("reference_answer")
        if not isinstance(reference, str) or not reference.strip():
            raise QuizConfigError("第 %d 个问答对缺少参考答案" % index)
        result.append({
            "id": "inline-%d" % index,
            "question": question.strip(),
            "reference_answer": reference.strip(),
            "rubric": _normalize_rubric(item.get("rubric")),
            "slide_id": _normalize_slide_id(item.get("slide_id")),
            "source": "inline",
        })
    return result


def assign_inline_questions(segments, questions, mode=MODE_RANDOM, seed=None,
                            interval=None, slide_range=None):
    """把内联问答对分配到提问点，返回 `(升序位置列表, {位置: 题目标识})`。

    - **没有教学段**（讲稿留空）时退化为「纯提问」：每个问答对自成一个提问点，
      严格按给出顺序提问。
    - 有教学段时：指定了幻灯片的问答对绑定到该页对应的提问点；其余问答对按
      给出顺序分配到由选取方式选出的剩余提问点。
    - 提问数量恒等于问答对数量，与调用方传入的数量字段无关。
    """
    items = list(questions or [])
    if not items:
        raise QuizConfigError("内联问答对不能为空")

    if not segments:
        # 纯提问（没有讲稿）：页码是唯一的时间线索，必须按页码升序提问；
        # 未指定页码的排在最后，并保持填写顺序。不能按填写顺序直接开问——
        # 否则第 3 页的题会在开课第一秒被念出来（现场就是这么提前的）。
        order = sorted(
            range(len(items)),
            key=lambda i: (items[i].get("slide_id") is None,
                           items[i].get("slide_id") or 0,
                           i),
        )
        assignments = {position: items[source]["id"]
                       for position, source in enumerate(order)}
        logger.info("[提问] 纯提问模式按页码排序后提问: %s",
                    [items[source].get("slide_id") for source in order])
        return list(range(len(items))), assignments

    candidates = candidate_positions(segments)
    if not candidates:
        raise QuizConfigError("讲稿里没有可用的提问点")
    slide_of = {position: segments[position][1] for position in candidates}

    pool = list(candidates)
    if slide_range:
        low, high = slide_range
        pool = [position for position in pool
                if slide_of.get(position) is not None
                and low <= slide_of[position] <= high]
    if not pool:
        raise QuizConfigError("当前范围内没有可用的提问点（幻灯片范围 %s）" % (slide_range,))

    assignments = {}
    free = []
    slide_owner = {}
    for index, item in enumerate(items, start=1):
        slide_id = item.get("slide_id")
        if slide_id is None:
            free.append(item)
            continue
        if slide_id in slide_owner:
            raise QuizConfigError(
                "第 %d 个与第 %d 个问答对都指定了幻灯片 %s"
                % (slide_owner[slide_id], index, slide_id)
            )
        slide_owner[slide_id] = index
        matches = [position for position in candidates if slide_of.get(position) == slide_id]
        if not matches:
            raise QuizConfigError(
                "第 %d 个问答对指定的幻灯片 %s 不是本次的候选提问点（候选: %s）"
                % (index, slide_id, sorted(s for s in slide_of.values() if s is not None))
            )
        point = matches[0]
        if point not in pool:
            raise QuizConfigError(
                "第 %d 个问答对指定的幻灯片 %s 不在本次提问点范围内（范围 %s）"
                % (index, slide_id, slide_range)
            )
        assignments[point] = item["id"]

    free_pool = [position for position in pool if position not in assignments]
    if len(free) > len(free_pool):
        raise QuizConfigError(
            "讲稿里可用的提问点只有 %d 个，无法容纳 %d 个未指定幻灯片的问答对"
            % (len(free_pool), len(free))
        )
    if not free:
        points = sorted(assignments)
        logger.info("[提问] 内联问答对全部分配到指定幻灯片: %s", points)
        return points, assignments

    if mode == MODE_EXPLICIT:
        raise QuizConfigError(
            "内联问答对不支持显式位置选取；请改用随机或固定间隔，"
            "或直接在问答对上指定幻灯片"
        )

    picked = select_points(free_pool, count=len(free), mode=mode, seed=seed,
                           interval=interval)
    for point, item in zip(sorted(picked), free):
        assignments[point] = item["id"]
    points = sorted(assignments)
    logger.info(
        "[提问] 内联问答对已分配: 指定幻灯片 %d 个，按顺序 %d 个，共 %d 个提问点",
        len(slide_owner), len(free), len(points),
    )
    return points, assignments


def validate_bindings(bindings, known_question_ids, candidates, bank=None):
    """校验“幻灯片编号 -> 题目标识”绑定表；冲突必须指明冲突项。

    题目的所属幻灯片（`slide_id`）只用于筛选与出题，**不参与**绑定校验：
    允许把第 1 页的复习题绑到第 5 页。
    """
    if not bindings:
        return
    known = set(known_question_ids)
    bound_slides = sorted(candidates)
    seen_question = {}
    for slide_id in sorted(bindings):
        question_id = bindings[slide_id]
        if question_id not in known:
            raise QuizConfigError("绑定引用了不存在的题目标识: %s" % question_id)
        if slide_id not in set(bound_slides):
            raise QuizConfigError(
                "绑定的幻灯片 %s 不是本次的候选提问点（候选: %s）" % (slide_id, bound_slides)
            )
        if question_id in seen_question:
            raise QuizConfigError(
                "题目标识 %s 被绑定到多个幻灯片: %s 与 %s"
                % (question_id, seen_question[question_id], slide_id)
            )
        seen_question[question_id] = slide_id


def assign_questions(points, bindings, available_question_ids, slide_of=None, seed=None):
    """为每个提问点分配题目，返回 {候选位置: 题目标识}。

    绑定优先：幻灯片命中绑定表的提问点使用绑定题目；其余提问点从未被占用的
    题目中按（可复现的）随机选取。一次讲课内题目不得重复。
    """
    available = list(available_question_ids)
    slide_map = dict(slide_of or {})

    bound_by_point = {}
    for slide_id, question_id in (bindings or {}).items():
        if question_id not in available:
            raise QuizConfigError("绑定引用了不存在的题目标识: %s" % question_id)
        for point in points:
            if slide_map.get(point) == slide_id:
                bound_by_point[point] = question_id
                break
        else:
            raise QuizConfigError(
                "绑定的幻灯片 %s 在本次提问点里找不到对应位置" % slide_id
            )

    used = set(bound_by_point.values())
    remaining = [question_id for question_id in available if question_id not in used]
    free_points = [point for point in points if point not in bound_by_point]

    if len(free_points) > len(remaining):
        raise QuizConfigError(
            "可用题目只有 %d 个，无法为 %d 个未绑定的提问点分配（绑定已占用 %d 个）"
            % (len(remaining), len(free_points), len(used))
        )

    result = dict(bound_by_point)
    if free_points:
        picked = _deterministic_sample(remaining, len(free_points), seed)
        for point, question_id in zip(sorted(free_points), picked):
            result[point] = question_id
    logger.info(
        "[提问] 已分配题目: 绑定 %d 个，随机 %d 个，共 %d 个提问点",
        len(bound_by_point), len(free_points), len(points),
    )
    return result
