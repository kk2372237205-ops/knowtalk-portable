# -*- coding: utf-8 -*-
"""提问模式的 HTTP 接口。

口令边界（与 `rag-sidecar-control`、`session-control` 的既有划法一致）：
- **课堂面**（学员用，必须免口令）：讲课启动、状态查询、文字作答、跳过；
- **配置面**（教师用，受口令约束）：题库增删改、AI 出题、问答记录查询。
  受保护路径在 `server/auth.py` 中声明。

接口只返回字符串化的阶段值：Enum 不能直接跨 JSON 边界。
"""

import asyncio

from aiohttp import web

from server.blocking import run_blocking
from server.routes import (
    _parse_ppt_segments,
    _parse_segments,
    _resolve_slide_id,
    gesture_unavailable_message,
    json_error,
    json_ok,
    partition_gestures,
)
from utils.logger import logger
from utils.quiz_plan import (
    QuizConfigError,
    assign_inline_questions,
    assign_questions,
    candidate_positions,
    normalize_inline_questions,
    select_points,
    validate_bindings,
)
from utils import quiz_score
from utils.quiz_session import QuizOrchestrator, Stage
from utils.quiz_store import QuizStoreError, get_bank, get_records


class QuizRegistryError(RuntimeError):
    """启动提问式讲课时的编排层错误。"""


def _default_session_lookup(sessionid):
    from server.session_manager import session_manager
    return session_manager.get_session(sessionid)


def _default_session_alive(sessionid):
    """会话是否仍然存在。刻意不用 get_session —— 那会刷新空闲计时，
    让「只是被提问挂着的会话」永远不被回收。"""
    from server.session_manager import session_manager
    return session_manager.has_session(sessionid)


def _default_slide_probe(sessionid, slide_id):
    """当前幻灯片是否已到达（或越过）指定页。

    用 `material_manager.slide_reached` 按页序判定：slide_id 是自增 ID，
    与页码不一定同值，直接比大小会在跳页时误判。
    """
    from utils import material_manager as mat_mgr
    return mat_mgr.slide_reached(mat_mgr.get_current_slide(sessionid), slide_id)


class QuizRegistry(object):
    """按会话保存提问编排器，并提供统一的推进入口。"""

    def __init__(self, session_lookup=None, bank=None, records=None, grader=None,
                 generator=None, answer_timeout=120.0, clock=None, session_alive=None,
                 slide_probe=None):
        self.session_lookup = session_lookup or _default_session_lookup
        # 「会话是否还活着」只在真正接 session_manager 时才可用；注入了
        # session_lookup 的调用方（单测）默认不做存活检查，保持原有行为。
        if session_alive is not None:
            self.session_alive = session_alive
        elif session_lookup is None:
            self.session_alive = _default_session_alive
        else:
            self.session_alive = None
        # 同理：页码门槛需要真实的幻灯片状态；单测注入 lookup 时默认不设门槛。
        if slide_probe is not None:
            self.slide_probe = slide_probe
        elif session_lookup is None:
            self.slide_probe = _default_slide_probe
        else:
            self.slide_probe = None
        self.bank = bank
        self.records = records
        self.grader = grader
        self.generator = generator
        self.answer_timeout = answer_timeout
        self.clock = clock
        self._orchestrators = {}

    # ── 依赖 ────────────────────────────────────────────────
    def bank_store(self):
        return self.bank if self.bank is not None else get_bank()

    def record_store(self):
        return self.records if self.records is not None else get_records()

    def grader_callable(self):
        """判分器。未显式注入时使用真实的 LLM 判分实现。"""
        if self.grader is not None:
            return self.grader
        from utils.quiz_grade import grade_answer
        return grade_answer

    # ── 编排器生命周期 ──────────────────────────────────────
    def start(self, sessionid, segments, points, assignments, questions, **kwargs):
        speaker = self.session_lookup(sessionid)
        if speaker is None:
            raise QuizRegistryError("会话不存在: %s" % sessionid)
        # 提问模式独占朗读：先清空该会话待播队列并作废旧朗读请求。
        # 数字人展示页在「连接后播放预设」时会自动把整篇讲稿投给 /human，
        # 若不清空，编排器再投喂一遍就会出现"两段演讲叠在一起"。
        # flush 失败不得阻止提问模式启动（课堂必须能继续）。
        flush = getattr(speaker, "flush_talk", None)
        if callable(flush):
            try:
                flush()
            except Exception:  # noqa: BLE001
                logger.exception("[提问] 清空朗读队列失败，继续启动提问模式")
        kwargs.setdefault("records", self.record_store())
        kwargs.setdefault("grader", self.grader_callable())
        kwargs.setdefault("answer_timeout", self.answer_timeout)
        kwargs.setdefault("slide_probe", self.slide_probe)
        if self.clock is not None:
            kwargs.setdefault("clock", self.clock)
        orchestrator = QuizOrchestrator(
            sessionid=sessionid, segments=segments, points=points,
            assignments=assignments, questions=questions, speaker=speaker, **kwargs
        )
        # 立即推进一次，马上投喂第一段；否则要等一个后台 tick 周期才开始讲课。
        # 放在这里而不是调用方，任何入口都能得到一致行为。
        orchestrator.tick()
        self._orchestrators[sessionid] = orchestrator
        return orchestrator

    def get(self, sessionid):
        return self._orchestrators.get(sessionid)

    def migrate(self, old_sessionid, new_sessionid, speaker=None):
        """把旧会话上的提问编排迁移到新会话（页面刷新/重连）。

         `/offer` 每次都会新建独立会话，刷新一次课堂就换了 sessionid；
        若不做迁移，编排器会绑在已销毁的会话上，展示页永远查不到提问。
        是否"该迁"由调用方判定（依据是旧页面是否还有活跃连接，而不是会话对象是否还在）。
        """
        orchestrator = self._orchestrators.pop(old_sessionid, None)
        if orchestrator is None:
            return False
        if speaker is None:
            speaker = self.session_lookup(new_sessionid)
        if speaker is None:
            # 新会话也拿不到：放回原位置，保持原有行为不变
            self._orchestrators[old_sessionid] = orchestrator
            return False
        orchestrator.rebind(new_sessionid, speaker)
        self._orchestrators[new_sessionid] = orchestrator
        logger.info("[提问] 已把提问编排迁移到会话 %s", new_sessionid)
        return True

    def remove(self, sessionid):
        return self._orchestrators.pop(sessionid, None) is not None

    def sessions(self):
        return sorted(self._orchestrators)

    def tick_all(self):
        """推进所有编排器。单个会话出错不得影响其它会话。

        会话已消失（页面刷新/断线）时不推进而是**挂起**：此时投喂只会把
        内容念给一个没人接收的通道，还会白白消耗掉后面的段落。挂着等
        `migrate` 把它接到新会话上。
        """
        for sessionid, orchestrator in list(self._orchestrators.items()):
            try:
                if self.session_alive is not None and not self.session_alive(sessionid):
                    continue
                orchestrator.tick()
            except Exception:  # noqa: BLE001
                logger.exception("[提问] 会话 %s 编排推进异常，已停用该会话", sessionid)
                self._orchestrators.pop(sessionid, None)
        return len(self._orchestrators)

    def prune_finished(self, max_age=600.0):
        """清掉结束较久的编排器，避免字典无限增长。"""
        if self.clock is None:
            import time as _time
            now = _time.monotonic()
        else:
            now = self.clock()
        removed = []
        for sessionid, orchestrator in list(self._orchestrators.items()):
            finished_at = getattr(orchestrator, "_finished_at", None)
            if orchestrator.stage == Stage.FINISHED and finished_at is not None:
                if now - finished_at > max_age:
                    removed.append(sessionid)
                    self._orchestrators.pop(sessionid, None)
        return removed


registry = QuizRegistry()

# 便于测试替换的模块级访问器
_default_bank_getter = get_bank
_default_records_getter = get_records


def _stage_value(stage):
    return stage.value if hasattr(stage, "value") else str(stage)


def _require_object(body):
    if not isinstance(body, dict):
        raise QuizConfigError("请求体必须是 JSON 对象")
    return body


# ─── 讲课 ──────────────────────────────────────────────────────────────────

def _build_segments(body, required=True):
    """接受显式 segments，或接受带 [PPT:N] / [GESTURE:<id>] 标签的 text。

    显式 segments 的每一项是 `[文本, 幻灯片编号]` 或
    `[文本, 幻灯片编号, 手势标识]`；两元素形式视为该段没有手势，保持旧请求兼容。

    `required=False` 时允许两者都缺失，返回空分段——这是「只给问答对、不给讲稿」
    的纯提问模式。缺讲稿且缺少题目时仍报错，绝不会静默启动空课堂。
    """
    segments = body.get("segments")
    if segments:
        result = []
        for item in segments:
            if not isinstance(item, (list, tuple)) or not item:
                raise QuizConfigError(
                    "segments 的每一项必须是 [文本, 幻灯片编号] "
                    "或 [文本, 幻灯片编号, 手势标识]"
                )
            slide_id = item[1] if len(item) > 1 else None
            gesture = item[2] if len(item) > 2 else None
            result.append((str(item[0]), slide_id, str(gesture) if gesture else None))
        return result

    text = body.get("text")
    if not text or not str(text).strip():
        if required:
            raise QuizConfigError("必须提供 text（带 [PPT:N] 标签的讲稿）或 segments")
        return []
    parsed = _parse_segments(str(text))
    return [
        (seg_text, _resolve_slide_id(slide) if slide is not None else None, gesture)
        for seg_text, slide, gesture in parsed
    ]


def _segments_from_stored_lecture(sessionid):
    """讲稿没随请求传上来时，回退到本会话最近一次投喂过的整篇讲稿。

    展示页「连接后播放预设」是把整篇带 `[PPT:N]` 的讲稿**一次性**投给 `/human`
    的，而启动提问式讲课会 `flush_talk()` 清空待播队列。没有这份留底，编排器
    只能以 0 段启动——现场表现就是"一开课就提问、答完就没有下文了"。
    """
    try:
        from utils import material_manager as mat_mgr
        text = mat_mgr.get_lecture(sessionid)
    except Exception:  # noqa: BLE001 - 取不到就按纯提问处理
        logger.exception("[提问] 读取本会话讲稿留底失败")
        return []
    if not text:
        return []
    logger.info("[提问] 请求未带讲稿，改用本会话最近一次投喂的讲稿（%d 字符）", len(text))
    return [
        (seg_text, _resolve_slide_id(slide) if slide is not None else None, gesture)
        for seg_text, slide, gesture in _parse_segments(text)
    ]


def _normalize_pair_slides(pairs):
    """把问答对里的页码归一化成真实 slide_id。

    教师填的是"第几页"，而讲稿分段里的 slide_id 已经过 `_resolve_slide_id`
    换算；两边不归一化就永远对不上，页码形同虚设。
    """
    for item in pairs:
        slide_id = item.get("slide_id")
        if slide_id is not None:
            item["slide_id"] = _resolve_slide_id(slide_id)
    return pairs


def _validate_lecture_gestures(sessionid, segments, warnings):
    """按当前形象校验讲稿引用的手势：不可用的只摘动作并记警告，可用的预热。

    提问模式会 `flush_talk()` 独占朗读通道，讲稿未必再经过 `/human` 的 echo 分支，
    所以 echo 里的那套校验（`server/routes.py::human`）必须在这里补上；否则不可用
    的手势会一路下发到渲染层才在后台加载失败，可用手势也不会预热、容易错过段首。

    返回过滤后的分段；警告追加到调用方给的 `warnings` 列表。
    """
    requested = [
        item[2] for item in segments
        if len(item) > 2 and item[2]
    ]
    if not requested:
        return segments
    speaker = registry.session_lookup(sessionid)
    if speaker is None:
        # 会话不存在由 QuizRegistry.start() 统一报错，这里不重复处理
        return segments
    avatar_id = getattr(getattr(speaker, "opt", None), "avatar_id", None)
    usable, missing, available = partition_gestures(avatar_id, requested)
    if missing:
        message = gesture_unavailable_message(avatar_id, missing, available)
        logger.warning("[手势标签] %s", message)
        warnings.append({
            "code": "GESTURE_UNAVAILABLE",
            "message": message,
            "gestures": missing,
            "available": available,
        })
        # 只摘除这些段的手势指令：讲稿照常朗读，课堂不能因为动作对不上而停住。
        filtered = []
        for item in segments:
            text = item[0]
            slide_id = item[1] if len(item) > 1 else None
            gesture = item[2] if len(item) > 2 else None
            filtered.append((text, slide_id, None if gesture in missing else gesture))
        segments = filtered
    preheat = getattr(speaker, "preheat_gestures", None)
    if usable and callable(preheat):
        try:
            preheat(list(dict.fromkeys(usable)))
        except Exception:  # noqa: BLE001 - 预热失败不影响开课
            logger.exception("[手势标签] 后台预热失败（不影响本次讲课）")
    return segments


async def quiz_lecture(request):
    """启动一次提问式讲课（课堂面，免口令）。"""
    try:
        body = _require_object(await request.json())
    except QuizConfigError as exc:
        return json_error(str(exc))
    except Exception:
        return json_error("请求体必须是合法 JSON")

    sessionid = str(body.get("sessionid") or "").strip()
    if not sessionid:
        return json_error("缺少 sessionid")

    try:
        slide_range = body.get("slide_range")
        if slide_range is not None:
            if not isinstance(slide_range, (list, tuple)) or len(slide_range) != 2:
                raise QuizConfigError("slide_range 必须是 [起始页, 结束页]")
            slide_range = (int(slide_range[0]), int(slide_range[1]))

        bindings = body.get("bindings") or {}
        if bindings and not isinstance(bindings, dict):
            raise QuizConfigError("bindings 必须是「幻灯片编号 -> 题目标识」的对象")
        bindings = {int(key): str(value) for key, value in bindings.items()}

        raw_questions = body.get("questions")
        if raw_questions:
            # 内联问答对：不读题库、也不依赖讲稿（讲稿留空即纯提问）
            if bindings:
                raise QuizConfigError("内联问答对与绑定表不能同时使用，请二选一")
            pairs = normalize_inline_questions(raw_questions)
            _normalize_pair_slides(pairs)
            segments = _build_segments(body, required=False)
            if not segments:
                # 展示页可能已经把整篇讲稿投喂过了（「连接后播放预设」）。
                # 优先复用它，否则页码无从落地、答完也没有下文可讲。
                segments = _segments_from_stored_lecture(sessionid)
            points, assignments = assign_inline_questions(
                segments, pairs,
                mode=body.get("mode") or "random",
                seed=body.get("seed"),
                interval=body.get("interval"),
                slide_range=slide_range,
            )
            questions = {item["id"]: item for item in pairs}
        else:
            segments = _build_segments(body, required=False)
            if not segments:
                segments = _segments_from_stored_lecture(sessionid)
            if not segments:
                raise QuizConfigError("必须提供 text（带 [PPT:N] 标签的讲稿）或 segments")
            candidates = candidate_positions(segments)
            if not candidates:
                raise QuizConfigError("讲稿里没有可用的提问点")

            slide_of = {position: segments[position][1] for position in candidates}
            candidate_slides = sorted({slide for slide in slide_of.values() if slide is not None})

            bank = registry.bank_store()
            bank_questions = bank.list_questions()
            known_ids = [item["id"] for item in bank_questions]

            validate_bindings(bindings, known_ids, candidate_slides, bank)

            points = select_points(
                segments,
                count=body.get("count"),
                mode=body.get("mode") or "random",
                seed=body.get("seed"),
                interval=body.get("interval"),
                explicit=body.get("explicit"),
                slide_range=slide_range,
            )

            assignments = assign_questions(
                points, bindings, known_ids, slide_of=slide_of, seed=body.get("seed")
            )

            questions = {}
            for question_id in set(assignments.values()):
                item = bank.get_question(question_id)
                if item is not None:
                    questions[question_id] = item

        # 手势校验必须在 registry.start() 之前：start() 会立刻 tick 一次、
        # 马上投喂第一段，晚一步第一段就已经带着未校验的手势下发了。
        warnings = []
        segments = _validate_lecture_gestures(sessionid, segments, warnings)

        timeout = body.get("answer_timeout")
        orchestrator = registry.start(
            sessionid, segments=segments, points=points, assignments=assignments,
            questions=questions,
            answer_timeout=float(timeout) if timeout is not None else registry.answer_timeout,
        )
        data = {
            "sessionid": sessionid,
            "total": len(points),
            "points": points,
            "question_ids": [assignments[p] for p in points],
            "assignments": {str(point): assignments[point] for point in points},
            "answer_timeout": orchestrator._answer_timeout,
            # 本次课堂是否由编排器讲授（false = 纯提问，页码即唯一时间线索）
            "has_lecture": bool(segments),
        }
        if warnings:
            data["warnings"] = warnings
        return json_ok(data=data)
    except QuizConfigError as exc:
        return json_error(str(exc))
    except QuizRegistryError as exc:
        return json_error(str(exc))
    except QuizStoreError as exc:
        return json_error(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("[提问] 启动提问式讲课失败")
        return json_error("启动提问式讲课失败: %s" % exc)


async def quiz_status(request):
    """查询提问状态（课堂面，免口令）。未启用时也返回成功。"""
    sessionid = str(request.query.get("sessionid") or "").strip()
    orchestrator = registry.get(sessionid) if sessionid else None
    if orchestrator is None:
        return json_ok(data={"in_quiz": False, "sessionid": sessionid})

    status = orchestrator.status()
    status["in_quiz"] = True
    status["stage"] = _stage_value(status["stage"])
    return json_ok(data=status)


async def quiz_answer(request):
    """提交文字作答（课堂面，免口令）。"""
    try:
        body = _require_object(await request.json())
    except Exception:
        return json_error("请求体必须是合法 JSON")

    sessionid = str(body.get("sessionid") or "").strip()
    answer = body.get("answer")
    orchestrator = registry.get(sessionid) if sessionid else None
    if orchestrator is None:
        return json_error("该会话未启用提问模式: %s" % sessionid)
    # 判分是同步阻塞调用（大模型）。直接在协程里调用会冻住整个事件循环：
    # aiortc 停流、tick 停摆、连状态轮询都排队——现场表现就是"判分超级慢"。
    # 必须卸载到线程池，让协程在等待期间让出事件循环。
    if not await run_blocking(orchestrator.submit_answer, answer or ""):
        return json_error("题目尚未呈现或作答内容为空")
    return json_ok(data={"sessionid": sessionid, "stage": _stage_value(orchestrator.stage)})


async def quiz_skip(request):
    """跳过当前题目（课堂面，免口令）。"""
    try:
        body = _require_object(await request.json())
    except Exception:
        return json_error("请求体必须是合法 JSON")

    sessionid = str(body.get("sessionid") or "").strip()
    orchestrator = registry.get(sessionid) if sessionid else None
    if orchestrator is None:
        return json_error("该会话未启用提问模式: %s" % sessionid)
    if not orchestrator.skip():
        return json_error("当前不处于等待作答或等待页码状态")
    return json_ok(data={"sessionid": sessionid, "stage": _stage_value(orchestrator.stage)})


async def quiz_stop(request):
    """退出提问模式：移除编排器并清空待播朗读（课堂面，免口令）。

    没有这个入口时，"退出提问模式"只会销毁前端面板，服务端编排器仍在推进，
    于是退出后数字人继续朗读——这也是"像有两段"的成因之一。
    """
    try:
        body = _require_object(await request.json())
    except Exception:
        return json_error("请求体必须是合法 JSON")

    sessionid = str(body.get("sessionid") or "").strip()
    if not sessionid:
        return json_error("缺少 sessionid")

    registry.remove(sessionid)
    speaker = registry.session_lookup(sessionid)
    flush = getattr(speaker, "flush_talk", None)
    if callable(flush):
        try:
            flush()
        except Exception:  # noqa: BLE001
            logger.exception("[提问] 退出时清空朗读队列失败")
    logger.info("[提问] 会话 %s 已退出提问模式", sessionid)
    return json_ok(data={"sessionid": sessionid, "in_quiz": False})


async def quiz_pairs_generate(request):
    """依据预设文本生成问答对（课堂面，免口令；不落库）。

    与受口令保护的 `/api/quiz/bank/generate` 的分工：
    - 本接口面向"发起讲课前，从预设讲稿直接生成内联问答对"的课堂面场景，
      不写题库、不需要会话存在（未连接数字人也能预先生成）；
    - 真正要写入题库仍走受保护的 `/api/quiz/bank/generate`。
    """
    try:
        body = _require_object(await request.json())
    except QuizConfigError as exc:
        return json_error(str(exc))
    except Exception:
        return json_error("请求体必须是合法 JSON")

    text = str(body.get("text") or "").strip()
    if not text:
        return json_error("必须提供 text（预设讲稿）")

    raw_count = body.get("count")
    try:
        count = int(raw_count) if raw_count is not None else 1
    except (TypeError, ValueError):
        return json_error("count 必须是整数")
    if count <= 0:
        return json_error("count 必须大于 0")

    generator = registry.generator
    if generator is None:
        try:
            from utils.quiz_generate import generate_questions
        except Exception as exc:  # noqa: BLE001
            return json_error("出题能力不可用: %s" % exc)
        generator = generate_questions

    segments = _parse_ppt_segments(text)
    if not segments:
        segments = [(text, None)]

    pairs = []
    try:
        for seg_text, slide in segments:
            seg_text = (seg_text or "").strip()
            if not seg_text:
                continue
            slide_id = _resolve_slide_id(slide) if slide is not None else None
            generated = generator(seg_text, count, context=body.get("context"))
            if not isinstance(generated, list):
                continue
            for item in generated:
                if not isinstance(item, dict):
                    continue
                question = str(item.get("question") or "").strip()
                answer = str(
                    item.get("reference_answer") or item.get("answer") or ""
                ).strip()
                if not question or not answer:
                    continue
                rubric = item.get("rubric") or []
                if isinstance(rubric, str):
                    rubric = [rubric]
                pair = {
                    "question": question,
                    "reference_answer": answer,
                    "rubric": [str(point).strip() for point in rubric if str(point).strip()],
                }
                if slide_id is not None:
                    pair["slide_id"] = slide_id
                pairs.append(pair)
    except Exception as exc:  # noqa: BLE001
        logger.error("[提问] 依据预设文本生成问答对失败: %s", exc)
        return json_error("生成问答对失败: %s" % exc)

    if not pairs:
        return json_error("未能从预设文本生成可用问答对（请检查文本内容或 LLM 配置）")
    return json_ok(data={"pairs": pairs, "count": len(pairs)})


# ─── 题库（配置面） ────────────────────────────────────────────────────────

async def quiz_bank_list(request):
    try:
        bank = registry.bank_store()
        return json_ok(data={"questions": bank.list_questions()})
    except Exception as exc:  # noqa: BLE001
        logger.exception("[提问] 读取题库失败")
        return json_error("读取题库失败: %s" % exc)


async def quiz_bank_add(request):
    try:
        body = _require_object(await request.json())
        bank = registry.bank_store()
        entry = bank.add_question(
            question=body.get("question"),
            reference_answer=body.get("reference_answer"),
            rubric=body.get("rubric"),
            slide_id=body.get("slide_id"),
            source=body.get("source") or "manual",
        )
        return json_ok(data={"question": entry})
    except (QuizStoreError, QuizConfigError) as exc:
        return json_error(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("[提问] 新增题目失败")
        return json_error("新增题目失败: %s" % exc)


async def quiz_bank_update(request):
    try:
        body = _require_object(await request.json())
        question_id = request.match_info.get("question_id", "")
        bank = registry.bank_store()
        entry = bank.update_question(
            question_id,
            question=body.get("question"),
            reference_answer=body.get("reference_answer"),
            rubric=body.get("rubric"),
            slide_id=body.get("slide_id"),
        )
        return json_ok(data={"question": entry})
    except (QuizStoreError, QuizConfigError) as exc:
        return json_error(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("[提问] 更新题目失败")
        return json_error("更新题目失败: %s" % exc)


async def quiz_bank_delete(request):
    try:
        question_id = request.match_info.get("question_id", "")
        bank = registry.bank_store()
        bank.delete_question(question_id)
        return json_ok(data={"question_id": question_id})
    except QuizStoreError as exc:
        return json_error(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("[提问] 删除题目失败")
        return json_error("删除题目失败: %s" % exc)


async def quiz_bank_generate(request):
    """依据幻灯片文字出题；默认只返回不落库，persist=true 时写入题库。"""
    try:
        body = _require_object(await request.json())
    except Exception:
        return json_error("请求体必须是 JSON 对象")

    slide_text = (body.get("slide_text") or "").strip()
    if not slide_text:
        return json_error("必须提供 slide_text（幻灯片文字）")

    count = body.get("count")
    try:
        count = int(count) if count is not None else 1
    except (TypeError, ValueError):
        return json_error("count 必须是整数")
    if count <= 0:
        return json_error("count 必须大于 0")

    generator = registry.generator
    if generator is None:
        try:
            from utils.quiz_generate import generate_questions
        except Exception as exc:  # noqa: BLE001
            return json_error("出题能力不可用: %s" % exc)
        generator = generate_questions

    try:
        generated = generator(slide_text, count, context=body.get("context"))
    except Exception as exc:  # noqa: BLE001
        logger.error("[提问] 自动出题失败: %s", exc)
        return json_error("自动出题失败: %s" % exc)

    if not isinstance(generated, list) or not generated:
        return json_error("自动出题没有返回可用题目")

    if body.get("persist"):
        try:
            bank = registry.bank_store()
            stored = []
            for item in generated:
                stored.append(bank.add_question(
                    question=item.get("question"),
                    reference_answer=item.get("reference_answer"),
                    rubric=item.get("rubric"),
                    slide_id=body.get("slide_id"),
                    source="generated",
                ))
            return json_ok(data={"questions": stored, "persisted": True})
        except QuizStoreError as exc:
            return json_error(str(exc))

    return json_ok(data={"questions": generated, "persisted": False})


# ─── 问答记录（配置面） ────────────────────────────────────────────────────

async def quiz_records(request):
    try:
        sessionid = request.query.get("sessionid") or None
        records = registry.record_store()
        return json_ok(data={"records": records.list_records(sessionid)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("[提问] 读取问答记录失败")
        return json_error("读取问答记录失败: %s" % exc)


# ─── 注册与后台推进 ────────────────────────────────────────────────────────

async def tick_quiz_sessions(interval=0.2):
    """后台循环：推进所有提问编排器。"""
    logger.info("[提问] 提问编排推进任务已启动（间隔 %.2fs）", interval)
    while True:
        try:
            registry.tick_all()
        except Exception:  # noqa: BLE001
            logger.exception("[提问] 提问编排推进循环异常")
        await asyncio.sleep(interval)


async def quiz_release(request):
    """页面卸载（刷新/关页）时声明"这个会话我不再观看"。

    刷新后旧 PeerConnection 在服务端往往长时间仍是 connected，仅凭连接状态
    无法判断页面已经走了，提问编排就永远迁不到新会话（表现为刷新后不再提问）。
    这里接收浏览器的 sendBeacon，只影响迁移判定，不动会话本身。
    """
    try:
        raw = await request.json()
    except Exception:
        raw = {}
    sessionid = str((raw or {}).get("sessionid") or "").strip()
    if not sessionid:
        return json_error("缺少 sessionid")
    from server.rtc_manager import mark_session_released
    mark_session_released(sessionid)
    return json_ok(data={"sessionid": sessionid, "released": True})


async def quiz_report(request):
    """取某次课堂的总评分（课堂面，免口令）。

    评分一律由该会话的**问答记录**算出，因此即使编排器已经销毁
    （课堂结束后被移除、或服务重启后只剩落盘记录），结果仍然一致。
    """
    sessionid = str(request.query.get("sessionid") or "").strip()
    if not sessionid:
        return json_error("缺少 sessionid")

    records = registry.record_store().list_records(sessionid)
    summary = quiz_score.summarize(records)
    orchestrator = registry.get(sessionid)
    finished = orchestrator is None or _stage_value(orchestrator.stage) == "finished"
    return json_ok(data={
        "sessionid": sessionid,
        "summary": summary,
        "finished": finished,
        "text": quiz_score.format_summary(summary),
    })


def setup_quiz_routes(app):
    """注册提问模式路由。

    必须在 `server/routes.py` 的 `add_static('/')` 之前调用，
    否则可能被静态资源规则吞掉。
    """
    app.router.add_post("/api/quiz/lecture", quiz_lecture)
    app.router.add_get("/api/quiz/status", quiz_status)
    app.router.add_post("/api/quiz/answer", quiz_answer)
    app.router.add_post("/api/quiz/skip", quiz_skip)
    app.router.add_post("/api/quiz/stop", quiz_stop)
    app.router.add_post("/api/quiz/release", quiz_release)

    # 课堂面：由预设文本生成内联问答对（不落库、不需要会话、免口令）
    app.router.add_post("/api/quiz/pairs/generate", quiz_pairs_generate)

    app.router.add_get("/api/quiz/bank", quiz_bank_list)
    app.router.add_post("/api/quiz/bank", quiz_bank_add)
    app.router.add_post("/api/quiz/bank/generate", quiz_bank_generate)
    app.router.add_put("/api/quiz/bank/{question_id}", quiz_bank_update)
    app.router.add_delete("/api/quiz/bank/{question_id}", quiz_bank_delete)

    app.router.add_get("/api/quiz/records", quiz_records)
    app.router.add_get("/api/quiz/report", quiz_report)
