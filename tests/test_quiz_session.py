# -*- coding: utf-8 -*-
"""提问编排状态机的测试（TDD：这些断言先于实现存在）。

关键约定（来自既有实现，不得被编排层破坏）：
- 段落经 `put_msg_txt(text, datainfo)` 送入，`slide_id` 放在 `datainfo` 里；
  真正的切页发生在**该段音频首帧开始播放时**，由 `avatars/base_avatar.py` 的
  渲染循环依据帧的 `userdata['status'] == 'start'` 完成。
  因此编排层 MUST NOT 自己调用 `mat_mgr.set_current_slide`。
"""

import threading
import time
import unittest

from utils.quiz_session import QuizOrchestrator, Stage, build_steps


class FakeSpeaker(object):
    """可控的会话替身：记录投喂内容，说话状态由测试显式控制。"""

    def __init__(self):
        self.delivered = []
        self.speaking = False

    def put_msg_txt(self, text, datainfo=None):
        self.delivered.append((text, dict(datainfo or {})))
        self.speaking = True          # 一投喂即视为开始播放

    def is_speaking(self):
        return self.speaking

    def finish(self):
        """模拟当前这一段播完。"""
        self.speaking = False

    @property
    def texts(self):
        return [text for text, _ in self.delivered]


class FakeRecords(object):
    def __init__(self):
        self.items = []

    def add_record(self, **kwargs):
        self.items.append(kwargs)
        return kwargs

    def list_records(self, sessionid=None):
        items = [dict(item) for item in self.items]
        if sessionid is None:
            return items
        return [item for item in items if item.get("sessionid") == sessionid]


class FakeClock(object):
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


SEGMENTS = [("第一段讲稿", 1), ("第二段讲稿", 2), ("第三段讲稿", 3)]
QUESTIONS = {
    "q1": {"id": "q1", "question": "第一题题干？", "reference_answer": "答案一", "rubric": ["要点"]},
    "q2": {"id": "q2", "question": "第二题题干？", "reference_answer": "答案二", "rubric": ["要点"]},
}


def make_orchestrator(points, assignments, grader=None, timeout=30.0, segments=None):
    speaker = FakeSpeaker()
    records = FakeRecords()
    clock = FakeClock()
    orchestrator = QuizOrchestrator(
        sessionid="s1",
        segments=segments or SEGMENTS,
        points=points,
        assignments=assignments,
        questions=QUESTIONS,
        speaker=speaker,
        records=records,
        grader=grader,
        answer_timeout=timeout,
        clock=clock,
    )
    return orchestrator, speaker, records, clock


class TeachingFlowTestCase(unittest.TestCase):
    """4.2 段落按序投喂，且 slide_id 只经由 datainfo 传递。"""

    def test_segments_are_delivered_in_order(self):
        orch, speaker, _, _ = make_orchestrator(points=[], assignments={})
        for _ in range(10):
            orch.tick()
            speaker.finish()
        self.assertEqual(speaker.texts, ["第一段讲稿", "第二段讲稿", "第三段讲稿"])
        self.assertEqual(orch.status()["stage"], Stage.FINISHED)

    def test_slide_id_is_forwarded_via_datainfo(self):
        orch, speaker, _, _ = make_orchestrator(points=[], assignments={})
        orch.tick()
        _, datainfo = speaker.delivered[0]
        self.assertEqual(datainfo.get("slide_id"), 1, "slide_id 必须放在 datainfo 里交给下游切页")

    def test_untagged_segment_has_no_slide_id_key(self):
        orch, speaker, _, _ = make_orchestrator(
            points=[], assignments={}, segments=[("无标签段", None)]
        )
        orch.tick()
        _, datainfo = speaker.delivered[0]
        self.assertNotIn("slide_id", datainfo, "无标签段不得伪造 slide_id")

    def test_tick_does_not_advance_while_speaking(self):
        orch, speaker, _, _ = make_orchestrator(points=[], assignments={})
        orch.tick()
        self.assertEqual(len(speaker.delivered), 1)
        orch.tick()
        orch.tick()
        self.assertEqual(len(speaker.delivered), 1, "还在播放时不得投喂下一段")

    def test_finished_after_last_segment(self):
        orch, speaker, _, _ = make_orchestrator(
            points=[], assignments={}, segments=[("唯一一段", 1)]
        )
        orch.tick()
        speaker.finish()
        orch.tick()
        self.assertEqual(orch.status()["stage"], Stage.FINISHED)


class QuizStopTestCase(unittest.TestCase):
    """4.3 到达提问点时讲课停住：不再投喂后续段落。"""

    def test_question_is_asked_after_the_point_segment(self):
        orch, speaker, _, _ = make_orchestrator(points=[0], assignments={0: "q1"})
        orch.tick()                     # 投喂第一段
        speaker.finish()
        orch.tick()                     # 应提问，而不是投喂第二段
        self.assertEqual(speaker.texts[1], "第一题题干？")
        self.assertNotIn("第二段讲稿", speaker.texts)

    def test_later_segments_are_not_delivered_before_answering(self):
        orch, speaker, _, _ = make_orchestrator(points=[0], assignments={0: "q1"})
        orch.tick(); speaker.finish()
        orch.tick(); speaker.finish()
        orch.tick(); orch.tick()
        self.assertNotIn("第二段讲稿", speaker.texts, "未作答前不得继续讲课")

    def test_stage_becomes_awaiting_answer(self):
        orch, speaker, _, _ = make_orchestrator(points=[0], assignments={0: "q1"})
        orch.tick(); speaker.finish()
        orch.tick()
        self.assertEqual(orch.status()["stage"], Stage.ASKING)
        speaker.finish()
        orch.tick()
        self.assertEqual(orch.status()["stage"], Stage.AWAITING_ANSWER)

    def test_status_exposes_current_question(self):
        orch, speaker, _, _ = make_orchestrator(points=[0], assignments={0: "q1"})
        orch.tick(); speaker.finish(); orch.tick(); speaker.finish(); orch.tick()
        status = orch.status()
        self.assertEqual(status["question"]["id"], "q1")
        self.assertEqual(status["question"]["question"], "第一题题干？")
        self.assertEqual(status["total"], 1)
        self.assertEqual(status["completed"], 0)


class AnswerResumeTestCase(unittest.TestCase):
    """4.4 作答、跳过、超时都应继续推进讲课。"""

    def _grader(self, **kwargs):
        return {"verdict": "correct", "feedback": "回答得很好。"}

    def test_answer_is_graded_then_feedback_is_spoken_then_teaching_resumes(self):
        orch, speaker, _, _ = make_orchestrator(
            points=[0], assignments={0: "q1"}, grader=self._grader
        )
        orch.tick(); speaker.finish()      # 第一段
        orch.tick(); speaker.finish()      # 题干
        orch.tick()                        # 进入等待作答

        self.assertTrue(orch.submit_answer("学员的回答"))
        orch.tick()
        self.assertIn("回答得很好。", speaker.texts, "反馈必须被朗读")
        speaker.finish()
        orch.tick()
        self.assertIn("第二段讲稿", speaker.texts, "反馈后必须继续讲课")

    def test_answer_text_reaches_the_grader(self):
        seen = {}

        def grader(sessionid, question, answer, **kwargs):
            seen["answer"] = answer
            seen["question_id"] = question["id"]
            return {"verdict": "partial", "feedback": "部分正确"}

        orch, speaker, _, _ = make_orchestrator(
            points=[0], assignments={0: "q1"}, grader=grader
        )
        orch.tick(); speaker.finish(); orch.tick(); speaker.finish(); orch.tick()
        orch.submit_answer("我的回答内容")
        orch.tick()
        self.assertEqual(seen["answer"], "我的回答内容")
        self.assertEqual(seen["question_id"], "q1")

    def test_answer_is_recorded(self):
        orch, speaker, records, _ = make_orchestrator(
            points=[0], assignments={0: "q1"}, grader=self._grader
        )
        orch.tick(); speaker.finish(); orch.tick(); speaker.finish(); orch.tick()
        orch.submit_answer("回答")
        orch.tick()
        self.assertEqual(len(records.items), 1)
        self.assertEqual(records.items[0]["question_id"], "q1")
        self.assertEqual(records.items[0]["verdict"], "correct")
        self.assertFalse(records.items[0]["degraded"])

    def test_feedback_text_reaches_the_record(self):
        """反馈语既要被朗读，也要落库——否则前端提问卡片没有数据源。"""
        orch, speaker, records, _ = make_orchestrator(
            points=[0], assignments={0: "q1"}, grader=self._grader
        )
        orch.tick(); speaker.finish(); orch.tick(); speaker.finish(); orch.tick()
        orch.submit_answer("回答")
        orch.tick()
        self.assertIn("回答得很好。", speaker.texts, "反馈必须被朗读")
        self.assertEqual(records.items[0]["feedback"], "回答得很好。",
                         "反馈语必须落库，朗读通道无法回读")

    def test_blank_answer_is_rejected_and_stays_awaiting(self):
        orch, speaker, _, _ = make_orchestrator(
            points=[0], assignments={0: "q1"}, grader=self._grader
        )
        orch.tick(); speaker.finish(); orch.tick(); speaker.finish(); orch.tick()
        self.assertFalse(orch.submit_answer("   "))
        self.assertEqual(orch.status()["stage"], Stage.AWAITING_ANSWER)

    def test_skip_records_unanswered_and_resumes(self):
        orch, speaker, records, _ = make_orchestrator(points=[0], assignments={0: "q1"})
        orch.tick(); speaker.finish(); orch.tick(); speaker.finish(); orch.tick()
        self.assertTrue(orch.skip())
        orch.tick()
        self.assertTrue(records.items[0]["unanswered"])
        self.assertIn("第二段讲稿", speaker.texts, "跳过后必须继续讲课")

    def test_timeout_records_unanswered_and_resumes(self):
        orch, speaker, records, clock = make_orchestrator(
            points=[0], assignments={0: "q1"}, timeout=30.0
        )
        orch.tick(); speaker.finish(); orch.tick(); speaker.finish(); orch.tick()
        clock.advance(31.0)
        orch.tick()
        self.assertTrue(records.items[0]["unanswered"])
        self.assertIn("第二段讲稿", speaker.texts, "超时后必须继续讲课")

    def test_submit_when_not_awaiting_is_rejected(self):
        orch, speaker, _, _ = make_orchestrator(points=[0], assignments={0: "q1"})
        orch.tick()
        self.assertFalse(orch.submit_answer("太早了"))
        self.assertFalse(orch.skip())

    def test_grading_failure_degrades_and_does_not_stall(self):
        def broken_grader(**kwargs):
            raise RuntimeError("LLM 不可用")

        orch, speaker, records, clock = make_orchestrator(
            points=[0], assignments={0: "q1"}, grader=broken_grader
        )
        orch.tick(); speaker.finish(); orch.tick(); speaker.finish(); orch.tick()
        orch.submit_answer("回答")
        orch.tick()
        self.assertTrue(records.items[0]["degraded"], "判分失败必须记为降级")
        speaker.finish()
        orch.tick()
        self.assertIn("第二段讲稿", speaker.texts, "判分失败不得卡住课堂")

    def test_progress_counts_after_answer(self):
        orch, speaker, _, _ = make_orchestrator(
            points=[0, 2], assignments={0: "q1", 2: "q2"}, grader=self._grader
        )
        orch.tick(); speaker.finish(); orch.tick(); speaker.finish(); orch.tick()
        orch.submit_answer("回答")
        orch.tick()
        self.assertEqual(orch.status()["completed"], 1)
        self.assertEqual(orch.status()["total"], 2)


class IsolationTestCase(unittest.TestCase):
    """4.1 编排必须按会话隔离。"""

    def test_two_orchestrators_do_not_interfere(self):
        first, first_speaker, first_records, _ = make_orchestrator(points=[0], assignments={0: "q1"})
        second, second_speaker, _, _ = make_orchestrator(points=[], assignments={})

        first.tick()                      # 会话 A 投喂第一段
        second.tick()                     # 会话 B 也应能独立推进
        first_speaker.finish()
        second_speaker.finish()

        self.assertEqual(len(first_speaker.delivered), 1)
        self.assertEqual(len(second_speaker.delivered), 1)

        # A 停在等待作答，B 继续讲完
        first.tick(); first_speaker.finish(); first.tick()
        for _ in range(8):
            second.tick()
            second_speaker.finish()
        self.assertEqual(first.status()["stage"], Stage.AWAITING_ANSWER)
        self.assertEqual(second.status()["stage"], Stage.FINISHED)

    def test_speaking_in_one_session_does_not_block_another(self):
        first, first_speaker, _, _ = make_orchestrator(points=[], assignments={})
        second, second_speaker, _, _ = make_orchestrator(points=[], assignments={})
        first.tick()                      # A 正在说话
        second.tick()
        self.assertTrue(first_speaker.speaking)
        self.assertEqual(len(second_speaker.delivered), 1, "B 不应被 A 的说话状态阻塞")


class AllQuestionsAnsweredFlowTestCase(unittest.TestCase):
    def test_last_quiz_at_last_segment_then_finishes(self):
        grader = lambda **kwargs: {"verdict": "correct", "feedback": "好"}
        orch, speaker, _, _ = make_orchestrator(
            points=[2], assignments={2: "q2"}, grader=grader,
            segments=[("第一段讲稿", 1), ("第二段讲稿", 2), ("第三段讲稿", 3)],
        )
        for _ in range(20):
            orch.tick()
            if orch.status()["stage"] == Stage.AWAITING_ANSWER:
                orch.submit_answer("答")
            speaker.finish()
        self.assertEqual(orch.status()["stage"], Stage.FINISHED)
        self.assertIn("第三段讲稿", speaker.texts)
        self.assertIn("第二题题干？", speaker.texts)
        # 题目必须出现在最后一段之后
        self.assertLess(speaker.texts.index("第三段讲稿"),
                        speaker.texts.index("第二题题干？"))
        self.assertEqual(orch.status()["completed"], 1)


class SlideSwitchTimingTestCase(unittest.TestCase):
    """切页必须发生在**段落开始播放时**，而不是投喂时（任务 4.2 的核心要求）。

    编排层只把 `slide_id` 放进 `datainfo`；真正的切页由
    `avatars/base_avatar.py` 在收到该段音频首帧（`status == 'start'`）时完成。
    因此投喂之后当前幻灯片必须保持不变。
    """

    def test_delivering_a_segment_does_not_switch_the_slide(self):
        from utils import material_manager as mat_mgr
        sessionid = "quiz-timing-t1"
        mat_mgr.set_current_slide(sessionid, 1)

        orch, speaker, _, _ = make_orchestrator(
            points=[], assignments={}, segments=[("带着第二页标签的段落", 2)]
        )
        orch.tick()

        _, datainfo = speaker.delivered[0]
        self.assertEqual(datainfo.get("slide_id"), 2, "slide_id 必须交给下游")
        self.assertEqual(
            mat_mgr.get_current_slide(sessionid), 1,
            "投喂时不得切页——切页必须等到该段音频首帧开始播放",
        )

    def test_slide_stays_put_while_waiting_for_an_answer(self):
        """停在提问点时幻灯片必须保持在停止时刻的取值（任务 4.3）。"""
        from utils import material_manager as mat_mgr
        sessionid = "quiz-timing-t2"
        mat_mgr.set_current_slide(sessionid, 1)

        orch, speaker, _, _ = make_orchestrator(
            points=[0], assignments={0: "q1"},
            segments=[("第一页讲稿", 1), ("第二页讲稿", 2)],
        )
        orch.tick(); speaker.finish()      # 第一段
        orch.tick(); speaker.finish()      # 题干
        orch.tick()                        # 进入等待作答
        self.assertEqual(orch.status()["stage"], Stage.AWAITING_ANSWER)

        # 后续段落（携带 slide_id=2）没有被投喂，因此幻灯片不可能被切走
        self.assertNotIn("第二页讲稿", speaker.texts)
        self.assertEqual(mat_mgr.get_current_slide(sessionid), 1)


class FeedbackResumeTestCase(unittest.TestCase):
    """7.4 反馈朗读后继续推进：不重复已讲段落，末尾问完即结束。"""

    def _grader(self, **kwargs):
        return {"verdict": "correct", "feedback": "回答得很好。"}

    def test_already_taught_segments_are_not_repeated(self):
        orch, speaker, _, _ = make_orchestrator(
            points=[1], assignments={1: "q2"}, grader=self._grader
        )
        for _ in range(20):
            orch.tick()
            if orch.status()["stage"] == Stage.AWAITING_ANSWER:
                orch.submit_answer("回答")
            speaker.finish()

        self.assertEqual(orch.status()["stage"], Stage.FINISHED)
        self.assertEqual(speaker.texts.count("第一段讲稿"), 1, "已讲过的段落不得重播")
        self.assertEqual(speaker.texts.count("第二段讲稿"), 1, "已讲过的段落不得重播")
        self.assertEqual(speaker.texts.count("第三段讲稿"), 1)
        self.assertEqual(speaker.texts.count("第二题题干？"), 1, "题目不得重复提问")

    def test_feedback_comes_before_the_next_segment(self):
        orch, speaker, _, _ = make_orchestrator(
            points=[0], assignments={0: "q1"}, grader=self._grader
        )
        for _ in range(20):
            orch.tick()
            if orch.status()["stage"] == Stage.AWAITING_ANSWER:
                orch.submit_answer("回答")
            speaker.finish()
        texts = speaker.texts
        self.assertLess(texts.index("回答得很好。"), texts.index("第二段讲稿"),
                        "反馈必须先于后续教学段")
        self.assertLess(texts.index("第一题题干？"), texts.index("回答得很好。"))

    def test_two_questions_each_answered_once(self):
        orch, speaker, records, _ = make_orchestrator(
            points=[0, 2], assignments={0: "q1", 2: "q2"}, grader=self._grader
        )
        for _ in range(30):
            orch.tick()
            if orch.status()["stage"] == Stage.AWAITING_ANSWER:
                orch.submit_answer("回答")
            speaker.finish()
        self.assertEqual(orch.status()["stage"], Stage.FINISHED)
        self.assertEqual(len(records.items), 2, "两道题各记录一次")
        self.assertEqual([item["question_id"] for item in records.items], ["q1", "q2"])
        self.assertEqual(orch.status()["completed"], 2)
        self.assertEqual(orch.status()["total"], 2)


class StandaloneQuestionsTestCase(unittest.TestCase):
    """11.3 无讲稿时的纯提问：没有教学段，按顺序逐题提问，反馈后收尾。"""

    QUESTIONS = {
        "inline-1": {"id": "inline-1", "question": "第一题？", "reference_answer": "一",
                     "rubric": [], "slide_id": None},
        "inline-2": {"id": "inline-2", "question": "第二题？", "reference_answer": "二",
                     "rubric": [], "slide_id": 4},
    }

    def _make(self):
        speaker = FakeSpeaker()
        records = FakeRecords()
        orchestrator = QuizOrchestrator(
            sessionid="s1", segments=[], points=[0, 1],
            assignments={0: "inline-1", 1: "inline-2"},
            questions=self.QUESTIONS, speaker=speaker, records=records,
            grader=lambda **kwargs: {"verdict": "correct", "feedback": "很好。"},
            answer_timeout=30.0, clock=FakeClock(),
        )
        return orchestrator, speaker, records

    def test_build_steps_emits_ask_only_without_segments(self):
        self.assertEqual(build_steps([], [0, 1]), [("ask", 0), ("ask", 1)])

    def test_build_steps_keeps_teach_then_ask(self):
        self.assertEqual(build_steps([("a", 1), ("b", 2)], [0]),
                         [("teach", 0), ("ask", 0), ("teach", 1)])

    def test_quiz_only_asks_in_order_and_never_feeds_teaching_segments(self):
        orchestrator, speaker, _ = self._make()

        orchestrator.tick()
        self.assertEqual(orchestrator.stage, Stage.ASKING)
        self.assertEqual(speaker.texts, ["第一题？"], "纯提问不得投喂任何教学段")

        speaker.finish()
        orchestrator.tick()
        self.assertEqual(orchestrator.stage, Stage.AWAITING_ANSWER)

        orchestrator.submit_answer("回答一")
        self.assertEqual(orchestrator.stage, Stage.FEEDBACK)
        self.assertEqual(speaker.texts[-1], "很好。", "反馈仍必须朗读")

        speaker.finish()
        orchestrator.tick()
        self.assertEqual(speaker.texts[-1], "第二题？", "第二题必须随后抛出")
        self.assertEqual(orchestrator.stage, Stage.ASKING)

    def test_question_slide_is_used_when_there_is_no_teaching_segment(self):
        orchestrator, speaker, _ = self._make()
        orchestrator.tick()
        speaker.finish()
        orchestrator.tick()
        orchestrator.skip()
        speaker.finish()
        orchestrator.tick()

        text, datainfo = speaker.delivered[-1]
        self.assertEqual(text, "第二题？")
        self.assertEqual(datainfo.get("slide_id"), 4,
                         "问答对自带的幻灯片必须交给下游（切页发生在这段音频开始播放时）")

    def test_quiz_only_finishes_after_last_question(self):
        orchestrator, speaker, records = self._make()
        for _ in range(8):
            orchestrator.tick()
            if orchestrator.stage == Stage.AWAITING_ANSWER:
                orchestrator.submit_answer("回答")
            speaker.finish()
        self.assertEqual(orchestrator.stage, Stage.FINISHED)
        self.assertEqual([item["question_id"] for item in records.items],
                         ["inline-1", "inline-2"], "每题各记录一次，顺序与提问一致")


class RebindTestCase(unittest.TestCase):
    """页面刷新会换会话：编排器必须能接到新会话上，进度一点不丢。"""

    def _orchestrator(self, speaker):
        segments = [("第一段。", 1), ("第二段。", 2)]
        return QuizOrchestrator(
            sessionid="old", segments=segments, points=[0],
            assignments={0: "q1"},
            questions={"q1": {"id": "q1", "question": "题目一？"}},
            speaker=speaker, answer_timeout=0,
        )

    def test_rebind_swaps_speaker_and_sessionid(self):
        old_speaker = FakeSpeaker()
        orchestrator = self._orchestrator(old_speaker)
        orchestrator.tick()                      # 投喂第一段
        old_speaker.finish()

        new_speaker = FakeSpeaker()
        orchestrator.rebind("new", new_speaker)
        self.assertEqual(orchestrator.status()["sessionid"], "new")

        orchestrator.tick()                      # 在新会话上继续推进
        self.assertEqual(new_speaker.texts[0], "题目一？",
                         "迁移后的推进必须落到新会话的朗读通道上")
        self.assertEqual(old_speaker.texts, ["第一段。"], "旧会话不得再收到内容")
        self.assertEqual(orchestrator.stage, Stage.ASKING)

    def test_rebind_keeps_awaiting_question(self):
        speaker = FakeSpeaker()
        orchestrator = self._orchestrator(speaker)
        orchestrator.tick()
        speaker.finish()
        orchestrator.tick()                      # 进入 ASKING，念题
        speaker.finish()
        orchestrator.tick()                      # 进入等待作答
        self.assertEqual(orchestrator.stage, Stage.AWAITING_ANSWER)

        orchestrator.rebind("new", FakeSpeaker())
        status = orchestrator.status()
        self.assertEqual(status["stage"], Stage.AWAITING_ANSWER, "等待作答状态必须保留")
        self.assertEqual(status["question"]["question"], "题目一？",
                         "刷新后学员还能看到同一道题")


class FinishSummaryTestCase(unittest.TestCase):
    """课堂讲完必须给出总评分，并把它念出来（任务 8.2）。"""

    def _grader(self, sessionid, question, answer, **kwargs):
        return {"verdict": "correct", "feedback": "回答得很好。"}

    def _run_to_finish(self, points, assignments, answer="学员的回答"):
        orch, speaker, records, clock = make_orchestrator(
            points=points, assignments=assignments, grader=self._grader)
        for _ in range(30):
            orch.tick()
            if orch.stage == Stage.AWAITING_ANSWER:
                orch.submit_answer(answer)
            speaker.finish()
            if orch.stage == Stage.FINISHED:
                break
        return orch, speaker, records

    def test_summary_is_spoken_and_attached_to_status(self):
        orch, speaker, _ = self._run_to_finish([0], {0: "q1"})
        self.assertEqual(orch.stage, Stage.FINISHED)
        self.assertTrue(any("本次得分 100 分" in text for text in speaker.texts),
                        "结束评分必须被朗读")
        status = orch.status()
        self.assertIsNotNone(status["summary"])
        self.assertEqual(status["summary"]["score"], 100)
        self.assertEqual(status["summary"]["total"], 1)

    def test_no_records_means_no_summary(self):
        """一句都没问到的课堂不给分，也不念评分。"""
        speaker = FakeSpeaker()
        orch = QuizOrchestrator(
            sessionid="s1", segments=[("只有一段。", 1)], points=[],
            assignments={}, questions={}, speaker=speaker,
            records=FakeRecords(), answer_timeout=30.0, clock=FakeClock(),
        )
        for _ in range(5):
            orch.tick()
            speaker.finish()
        self.assertEqual(orch.stage, Stage.FINISHED)
        self.assertIsNone(orch.status()["summary"])

    def test_summary_is_not_spoken_twice(self):
        orch, speaker, _ = self._run_to_finish([0], {0: "q1"})
        before = len([t for t in speaker.texts if "本次得分" in t])
        orch.tick()
        orch.tick()
        after = len([t for t in speaker.texts if "本次得分" in t])
        self.assertEqual(before, 1)
        self.assertEqual(after, 1, "重复 tick 不得重复念评分")


class GradingStageTestCase(unittest.TestCase):
    """判分是阻塞调用（由调用方卸载到线程池）。

    判分进行中编排器必须站住：不投喂下一段、不触发作答超时、不接受重复提交。
    否则判分的那 1~3 秒里 tick 会一路讲下去，把判分反馈挤到很后面，
    学员听到的"反馈"就跟题目对不上了。
    """

    def setUp(self):
        self.gate = threading.Event()
        self.started = threading.Event()

        def slow_grader(sessionid, question, answer, **kwargs):
            self.started.set()
            self.gate.wait(5)
            return {"verdict": "correct", "feedback": "回答正确。"}

        self.orch, self.speaker, self.records, self.clock = make_orchestrator(
            points=[0], assignments={0: "q1"}, grader=slow_grader)
        self._drive_to_awaiting()

    def tearDown(self):
        self.gate.set()

    def _drive_to_awaiting(self, limit=20):
        for _ in range(limit):
            self.orch.tick()
            if self.orch.stage == Stage.AWAITING_ANSWER:
                return
            self.speaker.finish()
        raise AssertionError("未能进入等待作答状态")

    def _submit_in_thread(self, text="我的回答"):
        thread = threading.Thread(target=self.orch.submit_answer, args=(text,))
        thread.start()
        self.assertTrue(self.started.wait(3), "判分没有开始")
        return thread

    def test_stage_is_grading_while_grading(self):
        thread = self._submit_in_thread()
        self.assertEqual(self.orch.stage, Stage.GRADING)
        self.assertEqual(self.orch.status()["stage"], Stage.GRADING,
                         "status 必须能让前端看到 grading（显示「判分中」）")
        self.gate.set()
        thread.join(5)

    def test_tick_does_not_advance_while_grading(self):
        thread = self._submit_in_thread()
        delivered = len(self.speaker.delivered)
        for _ in range(20):
            self.orch.tick()
        self.assertEqual(len(self.speaker.delivered), delivered, "判分期间不得投喂下一段")
        self.assertEqual(self.orch.stage, Stage.GRADING)
        self.gate.set()
        thread.join(5)

    def test_answer_timeout_does_not_fire_while_grading(self):
        thread = self._submit_in_thread()
        self.clock.advance(10_000)     # 远超 answer_timeout=30s
        self.orch.tick()
        self.assertEqual(self.orch.stage, Stage.GRADING, "判分期间不得被超时推进")
        self.gate.set()
        thread.join(5)

    def test_second_submit_is_rejected_and_records_once(self):
        thread = self._submit_in_thread()
        self.assertFalse(self.orch.submit_answer("我又答一次"), "判分中重复提交必须被拒")
        self.gate.set()
        thread.join(5)
        self.assertEqual(len(self.records.items), 1, "该题只能产生一条记录")

    def test_grading_resolves_to_feedback(self):
        thread = self._submit_in_thread()
        self.gate.set()
        thread.join(5)
        self.assertEqual(self.orch.stage, Stage.FEEDBACK)
        self.assertIn("回答正确。", self.speaker.texts)


if __name__ == "__main__":
    unittest.main()
