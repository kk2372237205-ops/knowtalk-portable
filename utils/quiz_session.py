# -*- coding: utf-8 -*-
"""提问式讲课的编排状态机。

设计要点（见 `openspec/changes/add-quiz-mode/design.md`）：

- **不修改 `/human` 的契约**。本编排器只是"按需投喂段落"，用的还是
  `put_msg_txt` 这条既有通路；到达提问点时**不再投喂下一段**，朗读自然结束、
  幻灯片自然停在原页。
- **切页交给下游**：`slide_id` 只放进 `datainfo`，由 TTS 合并进该段音频的首帧，
  再由 `avatars/base_avatar.py` 的渲染循环在**该段开始播放时**真正切页。
  编排层 MUST NOT 直接调用 `mat_mgr.set_current_slide`，否则会在投喂时刻提前切页。
- **手势同理**：教学段可选的第三个字段 `gesture` 也只放进 `datainfo`，随该段音频
  首帧交给渲染层；手势不继承到下一段，题干/反馈/总结不带手势。
- **阻塞必须走 tick**：推进由外部周期性调用 `tick()` 驱动，不在请求处理函数里
  等待朗读结束，避免占住服务的事件循环。
- 判分与记录通过注入的 `grader` / `records` 完成，本模块不直接依赖 LLM 或存储。
"""

import time
from enum import Enum

from utils import quiz_score
from utils.logger import logger


class Stage(Enum):
    IDLE = "idle"
    TEACHING = "teaching"
    # 到提问点了，但幻灯片还没翻到该题指定的页码：站在这里等。
    # 学员看到的现场问题是"第 3 页的题在开课第一秒就念了"，根因就是缺这个阶段。
    WAITING_SLIDE = "waiting_slide"
    ASKING = "asking"
    AWAITING_ANSWER = "awaiting_answer"
    # 判分进行中。判分是阻塞调用，跑在线程池里，这期间编排器必须站住不动：
    # 既不能投喂下一段（会把反馈挤到后面），也不能触发作答超时。
    GRADING = "grading"
    FEEDBACK = "feedback"
    FINISHED = "finished"


# 判分不可用时的中性过渡语：课堂必须继续，不能让学员挂在等待状态
DEGRADED_FEEDBACK = "这个问题我先记下来，我们继续往下讲。"
SKIP_FEEDBACK = ""

# 等页码期间"仍在等待"日志的节流间隔。
# 只影响日志频率，**不是**等待时限：等不到页码就一直等，没有任何超时。
SLIDE_WAIT_LOG_INTERVAL = 15.0

_LOOP_GUARD_MAX_STEPS = 10000


def build_steps(segments, points):
    """把讲稿分段与提问点编排成有序步骤：[(teach, i), (ask, i), ...]。

    没有对应教学段的提问点（讲稿留空时的「纯提问」，或超出讲稿范围的独立提问点）
    会被追加为纯提问步骤，因此「只给问答对、不给讲稿」也能走同一条编排通路。
    """
    point_set = set(points or ())
    steps = []
    for index in range(len(segments)):
        steps.append(("teach", index))
        if index in point_set:
            steps.append(("ask", index))
    for index in sorted(point_set - set(range(len(segments)))):
        steps.append(("ask", index))
    return steps


class QuizOrchestrator(object):
    """单个会话的提问式讲课编排。

    `speaker` 需提供 `put_msg_txt(text, datainfo)` 与 `is_speaking()`；
    `grader(sessionid, question, answer)` 返回 `{"verdict":..., "feedback":...}`；
    `records` 需提供 `add_record(**kwargs)`；
    `clock` 用于超时判定，可注入以便测试。
    """

    def __init__(self, sessionid, segments, points, assignments, questions,
                 speaker, records=None, grader=None, answer_timeout=120.0,
                 clock=None, slide_probe=None):
        self._sessionid = sessionid
        self._segments = list(segments or [])
        self._points = sorted(points or [])
        self._assignments = dict(assignments or {})
        self._questions = dict(questions or {})
        self._speaker = speaker
        self._records = records
        self._grader = grader
        self._answer_timeout = answer_timeout
        self._clock = clock or time.monotonic
        # slide_probe(sessionid, slide_id) -> bool：当前幻灯片是否已到达该页。
        # 为 None 时不设门槛（等价于本次变更前的行为，便于单测与旧调用方）。
        # 注意：这里**没有**等待时限参数——页码是触发条件，等不到就一直等，
        # 出口只有"翻到该页"或学员手动跳过（skip）。
        self._slide_probe = slide_probe

        self._steps = build_steps(self._segments, self._points)
        self._cursor = 0
        self._stage = Stage.IDLE
        self._completed = 0
        self._current_point = None
        self._current_question = None
        self._awaiting_since = None
        self._finished_at = None
        # 等页码期间先记下待抛的题目（point, question）
        self._pending_ask = None
        self._waiting_since = None
        # 下一条"仍在等待"日志的时间点（仅用于日志节流，不参与任何判定）
        self._wait_log_at = None
        # 结束评分（课堂讲完后由 _finish 填），供 /api/quiz/status 直接带回
        self._summary = None
        logger.info(
            "[提问] 会话 %s 启动提问式讲课: %d 段, %d 个提问点, 超时 %.0fs",
            sessionid, len(self._segments), len(self._points), answer_timeout,
        )

    # ── 查询 ────────────────────────────────────────────────
    @property
    def stage(self):
        return self._stage

    @property
    def segments(self):
        """讲稿分段（只读访问，供调用方推导幻灯片范围等）。"""
        return list(self._segments)

    @property
    def points(self):
        return list(self._points)

    def status(self):
        return {
            "sessionid": self._sessionid,
            "stage": self._stage,
            "point": self._current_point,
            "question": dict(self._current_question) if self._current_question else None,
            "completed": self._completed,
            "total": len(self._points),
            # 本次课堂是否由编排器讲授：false 表示纯提问模式，
            # 展示页据此决定要不要停掉自己那份预设演讲（停了就没有"剩下的课"）。
            "has_lecture": bool(self._segments),
            # 正在等哪一页（不在等待阶段时为 None），供展示页显示"等待翻到第 N 页"
            "waiting_slide": self._waiting_target(),
            # 已经等了多少秒（不在等待阶段时为 None）。只是给现场看"等多久了"，
            # 没有任何上限：等不到就一直等。
            "waiting_seconds": self._waiting_seconds(),
            # 只有课堂结束后才有评分；进行中为 None
            "summary": dict(self._summary) if self._summary else None,
        }

    # ── 推进 ────────────────────────────────────────────────
    def rebind(self, sessionid, speaker):
        """把编排器接到新的会话上（页面刷新 / 重连时保住课堂进度）。

        页面刷新会新建一条 WebRTC 会话，朗读通道（speaker）随之更换，但
        「讲到哪一段、当前是哪道题、从何时开始等作答」都属于这堂课本身，
        必须跟着迁移；否则刷新一次，提问就断了。
        """
        previous = self._sessionid
        self._sessionid = sessionid
        self._speaker = speaker
        logger.info(
            "[提问] 提问编排从会话 %s 迁移到 %s（已完成 %d/%d，阶段 %s）",
            previous, sessionid, self._completed, len(self._points),
            getattr(self._stage, "value", self._stage),
        )

    def tick(self):
        """推进一步。由外部周期性调用，未到可推进时机时立即返回。"""
        if self._stage == Stage.FINISHED:
            return

        if self._stage == Stage.AWAITING_ANSWER:
            self._check_timeout()
            return

        if self._stage == Stage.WAITING_SLIDE:
            self._check_slide_ready()
            return

        if self._stage == Stage.GRADING:
            # 判分还没回来：不投喂、不超时、不推进。判分线程结束时自己会把
            # 状态推到 feedback/teaching，不需要 tick 参与。
            return

        if self._stage == Stage.ASKING:
            if self._speaker.is_speaking():
                return
            self._enter_awaiting()
            return

        if self._speaker.is_speaking():
            # 还在播放：既不投喂下一段，也不做任何推进。
            # 这正是"提问点停下"的实现方式——不发送下一段即可。
            return

        self._advance()

    def _advance(self):
        guard = 0
        while self._cursor < len(self._steps):
            guard += 1
            if guard > _LOOP_GUARD_MAX_STEPS:
                logger.error("[提问] 会话 %s 编排推进异常，已中止", self._sessionid)
                self._finish()
                return
            kind, index = self._steps[self._cursor]
            self._cursor += 1
            if kind == "teach":
                self._deliver_segment(index)
                self._stage = Stage.TEACHING
                return
            question = self._question_for_point(index)
            if question is None:
                # 该提问点没有可用题目（例如被绑定校验拦下后仍走到这里）：
                # 跳过而不是让课堂停住。
                logger.warning("[提问] 提问点 %s 没有分配题目，跳过", index)
                continue
            if self._should_wait_slide(index, question):
                # 页码还没到：站在这里等，绝不提前把题目念出来。
                # 没有任何时限，等不到就一直是这个阶段（出口：翻到该页 / 手动 skip）。
                self._pending_ask = (index, question)
                self._waiting_since = self._clock()
                self._wait_log_at = self._waiting_since + SLIDE_WAIT_LOG_INTERVAL
                self._stage = Stage.WAITING_SLIDE
                logger.info("[提问] 会话 %s 等待幻灯片到达第 %s 页后再提问（题目 %s）",
                            self._sessionid, self._target_slide(index, question),
                            question.get("id"))
                return
            self._ask(index, question)
            self._stage = Stage.ASKING
            return

        self._finish()

    # ── 投喂 ────────────────────────────────────────────────
    def _deliver_segment(self, index):
        item = self._segments[index]
        text = item[0] if len(item) > 0 else ""
        slide_id = item[1] if len(item) > 1 else None
        gesture = item[2] if len(item) > 2 else None
        datainfo = {}
        if slide_id is not None:
            # 只传递，不在此处切页：真正的切页发生在该段音频首帧播放时
            datainfo["slide_id"] = slide_id
        if gesture:
            # 与 slide_id 同一时机生效：渲染层在该段音频首帧播放时切换手势，
            # 且手势只属于本段，不沿用上一段（解析器已保证不继承）。
            datainfo["gesture"] = gesture
        self._speaker.put_msg_txt(text, datainfo)
        logger.info("[提问] 投喂教学段 %s（slide=%s, gesture=%s）", index, slide_id, gesture)

    def _ask(self, point, question):
        self._current_point = point
        self._current_question = question
        self._deliver_question(question)
        logger.info("[提问] 会话 %s 在提问点 %s 抛出题目 %s",
                    self._sessionid, point, question.get("id"))

    def _deliver_question(self, question):
        text = question.get("question") or ""
        # 优先用提问点所属幻灯片；纯提问模式下没有教学段，退回题目自带的幻灯片。
        slide_id = self._slide_of_point(self._current_point)
        if slide_id is None:
            slide_id = question.get("slide_id")
        datainfo = {}
        if slide_id is not None:
            # 仍然只传递、不在此处切页：切页发生在该段音频首帧播放时
            datainfo["slide_id"] = slide_id
        self._speaker.put_msg_txt(text, datainfo)

    def _clear_pending_speech(self):
        """清掉朗读通道里还没播完的内容（念题）。

        念题没念完就收到作答时用：否则判分反馈会排在念题之后才播，学员答完了
        还在听题目。speaker 可能没有 `flush_talk`（测试替身、精简实现），缺失时
        静默跳过——这里只是让反馈播得更及时，不是正确性依赖。
        """
        flush = getattr(self._speaker, "flush_talk", None)
        if callable(flush):
            flush()

    def _slide_of_point(self, point):
        if point is None or point >= len(self._segments):
            return None
        item = self._segments[point]
        return item[1] if len(item) > 1 else None

    # ── 页码门槛 ────────────────────────────────────────────
    def _target_slide(self, point, question):
        """该提问点指定的目标页码。

        优先取提问点所属教学段的幻灯片（编排器自己讲授时，讲到哪页就是哪页）；
        纯提问模式没有教学段，退回题目自带的 `slide_id`。
        两者都为空则不设门槛。
        """
        slide_id = self._slide_of_point(point)
        if slide_id is not None:
            return slide_id
        return (question or {}).get("slide_id")

    def _waiting_target(self):
        if self._stage != Stage.WAITING_SLIDE or not self._pending_ask:
            return None
        point, question = self._pending_ask
        return self._target_slide(point, question)

    def _waiting_seconds(self):
        if self._stage != Stage.WAITING_SLIDE or self._waiting_since is None:
            return None
        return round(self._clock() - self._waiting_since, 1)

    def _slide_is_ready(self, target):
        if target is None or self._slide_probe is None:
            return True
        try:
            return bool(self._slide_probe(self._sessionid, target))
        except Exception:  # noqa: BLE001 - 判定器出错不能让课堂停住
            logger.exception("[提问] 页码判定失败，按已到达处理")
            return True

    def _should_wait_slide(self, point, question):
        target = self._target_slide(point, question)
        return not self._slide_is_ready(target)

    def _check_slide_ready(self):
        """等待阶段每轮调用：到页则抛题；未到则继续等（**不设时限**）。"""
        pending = self._pending_ask
        if not pending:
            # 状态异常（不该发生）：直接回到推进，避免永久卡在等待阶段
            self._stage = Stage.TEACHING
            return
        point, question = pending
        target = self._target_slide(point, question)
        if self._slide_is_ready(target):
            self._raise_pending(point, question)
            return

        # 还没到：继续等。只做日志节流，让现场能区分"在等翻页"与"卡死"——
        # 绝不因为等久了就自己把题目念出来。
        now = self._clock()
        if self._wait_log_at is None or now >= self._wait_log_at:
            self._wait_log_at = now + SLIDE_WAIT_LOG_INTERVAL
            logger.info(
                "[提问] 会话 %s 仍在等待第 %s 页（已等 %.0f 秒，题目 %s）",
                self._sessionid, target, now - (self._waiting_since or now),
                question.get("id"),
            )

    def _raise_pending(self, point, question):
        """离开等待阶段并抛出题目。"""
        self._pending_ask = None
        self._waiting_since = None
        self._wait_log_at = None
        self._ask(point, question)
        self._stage = Stage.ASKING

    def _question_for_point(self, point):
        question_id = self._assignments.get(point)
        if question_id is None:
            return None
        return self._questions.get(question_id)

    # ── 等待与作答 ──────────────────────────────────────────
    def _enter_awaiting(self):
        self._awaiting_since = self._clock()
        self._stage = Stage.AWAITING_ANSWER
        logger.info("[提问] 会话 %s 进入等待作答（题目 %s）",
                    self._sessionid, (self._current_question or {}).get("id"))

    def _check_timeout(self):
        if not self._answer_timeout or self._answer_timeout <= 0:
            return
        elapsed = self._clock() - (self._awaiting_since or self._clock())
        if elapsed < self._answer_timeout:
            return
        logger.warning("[提问] 会话 %s 等待作答超时（%.0fs），记为未作答",
                       self._sessionid, elapsed)
        self._finish_question(answer="", verdict=None, degraded=False, unanswered=True,
                              feedback=SKIP_FEEDBACK, record=True)

    def submit_answer(self, text):
        """提交文字作答。题目尚未呈现或内容为空时返回 False 且不改变状态。

        判分是阻塞调用（由调用方用线程池卸载）。为了在判分期间挡住重复提交与
        `tick` 推进，这里**先切到 `grading` 再判分`：第二次提交会看到阶段不是
        `asking`/`awaiting_answer` 而直接被拒，该题只会产生一条记录。

        `asking`（念题还没念完）也接受：题目已经显示在对话栏，展示页那一刻就
        静音了，让学员干等到念完没有任何意义。但要先把没念完的部分清掉，
        否则判分反馈会排在念题后面，学员答完了还在听题目。
        """
        if self._stage == Stage.ASKING:
            self._clear_pending_speech()
        elif self._stage != Stage.AWAITING_ANSWER:
            return False
        text = (text or "").strip()
        if not text:
            return False

        self._stage = Stage.GRADING
        self._awaiting_since = None
        question = self._current_question or {}
        verdict, feedback, degraded = self._grade(question, text)
        logger.info("[提问] 会话 %s 提交作答: verdict=%s degraded=%s",
                    self._sessionid, verdict, degraded)
        self._finish_question(answer=text, verdict=verdict, degraded=degraded,
                              unanswered=False, feedback=feedback, record=True)
        return True

    def skip(self):
        """跳过当前题目，记为未作答并继续推进讲课。

        等待页码阶段没有"当前题目"可记，这里的语义退化为**不等了，直接出题**：
        学员点了「继续播放」却停在等翻页，课堂会永远没有下文，必须给这条手动出口。
        """
        if self._stage == Stage.WAITING_SLIDE and self._pending_ask:
            point, question = self._pending_ask
            logger.info("[提问] 会话 %s 手动跳过等待第 %s 页，立即抛出题目 %s",
                        self._sessionid, self._target_slide(point, question),
                        question.get("id"))
            self._raise_pending(point, question)
            return True
        if self._stage != Stage.AWAITING_ANSWER:
            return False
        logger.info("[提问] 会话 %s 跳过题目 %s",
                    self._sessionid, (self._current_question or {}).get("id"))
        self._finish_question(answer="", verdict=None, degraded=False, unanswered=True,
                              feedback=SKIP_FEEDBACK, record=True)
        return True

    def _grade(self, question, answer):
        """返回 (verdict, feedback, degraded)。判分不可用时降级，绝不抛出。"""
        if self._grader is None:
            return None, DEGRADED_FEEDBACK, True
        try:
            result = self._grader(sessionid=self._sessionid, question=question, answer=answer)
        except Exception as exc:  # noqa: BLE001 - 判分失败不得卡住课堂
            logger.error("[提问] 判分失败，降级为只记录: %s", exc)
            return None, DEGRADED_FEEDBACK, True
        if not isinstance(result, dict):
            logger.error("[提问] 判分返回结构异常，降级为只记录: %r", result)
            return None, DEGRADED_FEEDBACK, True
        degraded = bool(result.get("degraded"))
        return result.get("verdict"), result.get("feedback"), degraded

    def _finish_question(self, answer, verdict, degraded, unanswered, feedback, record):
        question = self._current_question or {}
        if record and self._records is not None:
            try:
                self._records.add_record(
                    sessionid=self._sessionid,
                    question_id=question.get("id"),
                    answer=answer,
                    verdict=verdict,
                    degraded=degraded,
                    unanswered=unanswered,
                    feedback=feedback or "",
                )
            except Exception:  # noqa: BLE001 - 记录失败不得影响课堂
                logger.exception("[提问] 记录问答结果失败")

        self._completed += 1
        self._awaiting_since = None
        self._current_point = None
        self._current_question = None

        if feedback:
            self._speaker.put_msg_txt(feedback, {})
            self._stage = Stage.FEEDBACK
        else:
            self._stage = Stage.TEACHING
            # 立即推进，避免多等一个 tick 周期
            self._advance()

    def _finish(self):
        if self._stage != Stage.FINISHED:
            self._stage = Stage.FINISHED
            self._finished_at = self._clock()
            self._pending_ask = None
            self._waiting_since = None
            logger.info("[提问] 会话 %s 提问式讲课结束（作答 %d/%d）",
                        self._sessionid, self._completed, len(self._points))
            self._announce_summary()

    def _announce_summary(self):
        """课堂结束时把总评分念出来（有作答记录才念）。

        评分必须"被听到"：学员在展示页看得到文字，但课堂是数字人在讲，
        只弹一行字会显得课没讲完。没有记录时（例如一句都没问到）不评分也不念。
        """
        summary = self._summarize_records()
        self._summary = summary
        if not summary:
            return
        text = quiz_score.format_summary(summary)
        if not text:
            return
        try:
            self._speaker.put_msg_txt(text, {})
            logger.info("[提问] 会话 %s 结束评分: %s", self._sessionid, text)
        except Exception:  # noqa: BLE001 - 评分朗读失败不得影响结束流程
            logger.exception("[提问] 朗读结束评分失败")

    def _summarize_records(self):
        if self._records is None:
            return None
        try:
            return quiz_score.summarize(self._records.list_records(self._sessionid))
        except Exception:  # noqa: BLE001 - 记录读不出来就当没有评分
            logger.exception("[提问] 汇总评分失败")
            return None
