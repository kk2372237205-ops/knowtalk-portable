# -*- coding: utf-8 -*-
"""页码门槛：题目必须在指定页才抛出。

现场问题：问答对上填了页码，但一开课就把题目念了出来（日志是
`启动提问式讲课: 0 段` + 立刻 `在提问点 0 抛出题目`）。根因有两个：

1. 纯提问模式（无讲稿）把 `slide_id` 整个丢掉了，按填写顺序直接开问；
2. 编排器没有"等这一页"的阶段，提问点一到就投喂题目。

本文件的断言对应 `openspec/changes/fix-quiz-slide-gating/specs/quiz-mode/spec.md`。
"""

import unittest
from unittest import mock

from utils.material_manager import slide_reached
from utils.quiz_plan import assign_inline_questions
from utils.quiz_session import QuizOrchestrator, Stage

SEGMENTS = [("第一段讲稿", 1), ("第二段讲稿", 2), ("第三段讲稿", 3)]
QUESTIONS = {
    "q1": {"id": "q1", "question": "第一题？", "reference_answer": "答一", "slide_id": 1},
    "q2": {"id": "q2", "question": "第二题？", "reference_answer": "答二", "slide_id": 2},
    "q3": {"id": "q3", "question": "第三题？", "reference_answer": "答三", "slide_id": 3},
}


class FakeSpeaker(object):
    def __init__(self):
        self.delivered = []
        self.speaking = False

    def put_msg_txt(self, text, datainfo=None):
        self.delivered.append((text, dict(datainfo or {})))
        self.speaking = True

    def is_speaking(self):
        return self.speaking

    def finish(self):
        self.speaking = False

    @property
    def texts(self):
        return [text for text, _ in self.delivered]


class FlushSpeaker(FakeSpeaker):
    """带 `flush_talk` 的朗读替身：记录清空发生在第几次投递之后。"""

    def __init__(self):
        FakeSpeaker.__init__(self)
        self.flush_calls = 0
        self.flush_at = None

    def flush_talk(self):
        self.flush_calls += 1
        self.flush_at = len(self.delivered)
        self.speaking = False


class NoFlushSpeaker(FakeSpeaker):
    """没有 `flush_talk` 的朗读替身（真实实现里也可能缺这个方法）。"""


class FakeClock(object):
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ProbeStub(object):
    """可拨动的页码判定：`reached` 为真时视为已到达。"""

    def __init__(self, reached=False):
        self.reached = reached
        self.calls = []

    def __call__(self, sessionid, slide_id):
        self.calls.append((sessionid, slide_id))
        return self.reached


def build(points, assignments, segments=None, probe=None, questions=None):
    speaker = FakeSpeaker()
    clock = FakeClock()
    orchestrator = QuizOrchestrator(
        sessionid="s1",
        segments=SEGMENTS if segments is None else segments,
        points=points,
        assignments=assignments,
        questions=questions or QUESTIONS,
        speaker=speaker,
        records=None,
        grader=None,
        answer_timeout=30.0,
        clock=clock,
        slide_probe=probe,
    )
    return orchestrator, speaker, clock


def run_to_ask(orch, speaker, limit=12):
    """推进到提问点：每到一段就先"播完"再继续，直到出现等待/提问阶段。

    steps 的形态是 [teach0, teach1, ask1, teach2]——提问点 1 的题排在第 2 段
    讲完之后，因此必须先把前面的教学段都播完。
    """
    for _ in range(limit):
        orch.tick()
        stage = orch.status()["stage"]
        if stage in (Stage.WAITING_SLIDE, Stage.ASKING, Stage.FINISHED):
            return stage
        speaker.finish()
    return orch.status()["stage"]


class WaitForSlideTestCase(unittest.TestCase):
    """到提问点但页码未到时必须站住，不得提前念题。"""

    def test_does_not_ask_before_slide_arrives(self):
        probe = ProbeStub(reached=False)
        orch, speaker, _ = build(points=[1], assignments={1: "q2"}, probe=probe)

        run_to_ask(orch, speaker)   # 讲完第 1、2 段后到提问点，但第 2 页还没到

        self.assertEqual(orch.status()["stage"], Stage.WAITING_SLIDE)
        self.assertEqual(speaker.texts, ["第一段讲稿", "第二段讲稿"],
                         "页码未到就投喂了题目：%s" % speaker.texts)
        self.assertEqual(orch.status()["waiting_slide"], 2)

    def test_asks_once_slide_arrives(self):
        probe = ProbeStub(reached=False)
        orch, speaker, _ = build(points=[1], assignments={1: "q2"}, probe=probe)
        run_to_ask(orch, speaker)
        self.assertEqual(orch.status()["stage"], Stage.WAITING_SLIDE)

        probe.reached = True
        orch.tick()

        self.assertEqual(orch.status()["stage"], Stage.ASKING)
        self.assertIn("第二题？", speaker.texts)
        self.assertIsNone(orch.status()["waiting_slide"])

    def test_probe_receives_target_slide(self):
        probe = ProbeStub(reached=False)
        orch, speaker, _ = build(points=[1], assignments={1: "q2"}, probe=probe)
        run_to_ask(orch, speaker)
        self.assertEqual(probe.calls[-1], ("s1", 2))

    def test_waits_indefinitely(self):
        """页码是触发条件而非时限条件：等多久都不许自己把题目念出来。"""
        probe = ProbeStub(reached=False)
        orch, speaker, clock = build(points=[1], assignments={1: "q2"}, probe=probe)
        run_to_ask(orch, speaker)

        for _ in range(120):        # 每次前进 10 秒，累计 20 分钟
            clock.advance(10.0)
            orch.tick()

        self.assertEqual(orch.status()["stage"], Stage.WAITING_SLIDE)
        self.assertNotIn("第二题？", speaker.texts,
                         "等不到页码也把题目念了，等于没有门槛：%s" % speaker.texts)
        self.assertEqual(orch.status()["waiting_slide"], 2)
        self.assertGreaterEqual(orch.status()["waiting_seconds"], 1200)

    def test_waiting_keeps_logging(self):
        """等不到页码时要有"仍在等待"的周期日志，否则现场分不清卡死还是等翻页。"""
        probe = ProbeStub(reached=False)
        orch, speaker, clock = build(points=[1], assignments={1: "q2"}, probe=probe)
        run_to_ask(orch, speaker)

        clock.advance(20.0)
        with self.assertLogs("utils.logger", level="INFO") as logs:
            orch.tick()

        self.assertTrue(any("仍在等待第 2 页" in line for line in logs.output),
                        logs.output)
        self.assertEqual(orch.status()["stage"], Stage.WAITING_SLIDE)

    def test_manual_skip_asks_without_waiting(self):
        """学员手动跳过等待：立即出题，且不能记成"未作答"。"""
        probe = ProbeStub(reached=False)
        orch, speaker, _ = build(points=[1], assignments={1: "q2"}, probe=probe)
        run_to_ask(orch, speaker)
        self.assertEqual(orch.status()["stage"], Stage.WAITING_SLIDE)

        self.assertTrue(orch.skip())

        self.assertEqual(orch.status()["stage"], Stage.ASKING)
        self.assertIn("第二题？", speaker.texts)
        self.assertEqual(orch.status()["completed"], 0, "还没作答就计成完成了一题")
        self.assertIsNone(orch.status()["waiting_slide"])

    def test_no_slide_target_means_no_gate(self):
        """段落和题目都没有页码时不设门槛，判定器不应被调用。"""
        probe = ProbeStub(reached=False)
        questions = {"q0": {"id": "q0", "question": "无页码题？", "reference_answer": "答"}}
        orch, speaker, _ = build(
            points=[1], assignments={1: "q0"}, questions=questions,
            segments=[("第一段讲稿", None), ("第二段讲稿", None)],
            probe=probe,
        )
        run_to_ask(orch, speaker)

        self.assertEqual(orch.status()["stage"], Stage.ASKING)
        self.assertEqual(probe.calls, [])

    def test_question_slide_gates_when_segment_untagged(self):
        """讲稿段没标签、但题目自带页码时，仍然以题目页码为准。"""
        probe = ProbeStub(reached=False)
        orch, speaker, _ = build(
            points=[1], assignments={1: "q2"},
            segments=[("第一段讲稿", None), ("第二段讲稿", None)],
            probe=probe,
        )
        run_to_ask(orch, speaker)

        self.assertEqual(orch.status()["stage"], Stage.WAITING_SLIDE)
        self.assertEqual(orch.status()["waiting_slide"], 2)

    def test_pure_quiz_gates_on_question_slide(self):
        """纯提问模式没有教学段，页码只能来自题目本身。"""
        probe = ProbeStub(reached=False)
        orch, speaker, _ = build(
            points=[0], assignments={0: "q3"}, segments=[], probe=probe
        )
        orch.tick()
        self.assertEqual(orch.status()["stage"], Stage.WAITING_SLIDE)
        self.assertEqual(speaker.texts, [])
        self.assertEqual(orch.status()["waiting_slide"], 3)

        probe.reached = True
        orch.tick()
        self.assertEqual(orch.status()["stage"], Stage.ASKING)
        self.assertIn("第三题？", speaker.texts)


class NoWaitDeadlineTestCase(unittest.TestCase):
    """页码是触发条件，不是"最多等一会儿"的软条件：实现里不许留任何时限。"""

    def test_no_wait_timeout_parameter(self):
        import inspect

        params = inspect.signature(QuizOrchestrator.__init__).parameters
        offenders = [name for name in params
                     if "timeout" in name and name != "answer_timeout"]
        self.assertEqual(offenders, [],
                         "等待页码不许有时限参数（只为作答保留 answer_timeout）：%s"
                         % offenders)

    def test_no_wait_deadline_constant(self):
        import inspect
        import utils.quiz_session as module

        source = inspect.getsource(module)
        self.assertNotIn("SLIDE_WAIT_TIMEOUT", source)
        self.assertNotIn("slide_wait_timeout", source)
        self.assertNotIn("超过 %.0f 秒仍未到", source,
                         "「等不到就先念题」的兜底不允许回归")


class StatusShapeTestCase(unittest.TestCase):
    def test_has_lecture_true_when_segments_present(self):
        orch, _, _ = build(points=[], assignments={})
        self.assertTrue(orch.status()["has_lecture"])

    def test_has_lecture_false_for_pure_quiz(self):
        orch, _, _ = build(points=[0], assignments={0: "q1"}, segments=[])
        self.assertFalse(orch.status()["has_lecture"])

    def test_waiting_slide_is_none_outside_waiting(self):
        orch, _, _ = build(points=[], assignments={})
        self.assertIsNone(orch.status()["waiting_slide"])
        self.assertIsNone(orch.status()["waiting_seconds"])


class SlideReachedTestCase(unittest.TestCase):
    """页码比较必须按页序，而不是 slide_id 的数值大小。"""

    def _patch_slides(self, ids):
        return mock.patch(
            "utils.material_manager.list_slides",
            return_value=[{"id": slide_id} for slide_id in ids],
        )

    def test_compares_by_page_order(self):
        with self._patch_slides([10, 20, 30]):
            self.assertTrue(slide_reached(30, 20), "第 3 页已越过第 2 页")
            self.assertFalse(slide_reached(10, 30))
            self.assertTrue(slide_reached(20, 20))

    def test_jump_forward_counts_as_reached(self):
        with self._patch_slides([1, 2, 3, 4, 5]):
            self.assertTrue(slide_reached(5, 4), "从第 2 页跳到第 5 页，应视为越过第 4 页")

    def test_unknown_slide_falls_back_to_numeric(self):
        with self._patch_slides([1, 2, 3]):
            self.assertTrue(slide_reached(5, 4))
            self.assertFalse(slide_reached(2, 4))

    def test_none_current_is_not_reached(self):
        with self._patch_slides([1, 2, 3]):
            self.assertFalse(slide_reached(None, 2))

    def test_none_target_is_always_reached(self):
        with self._patch_slides([1, 2, 3]):
            self.assertTrue(slide_reached(1, None))


class PureQuizOrderingTestCase(unittest.TestCase):
    """无讲稿时按页码升序提问，未指定页码的排最后并保持填写顺序。"""

    def _pairs(self):
        return [
            {"id": "inline-1", "question": "三？", "reference_answer": "答", "slide_id": 3},
            {"id": "inline-2", "question": "一？", "reference_answer": "答", "slide_id": 1},
            {"id": "inline-3", "question": "二？", "reference_answer": "答", "slide_id": 2},
            {"id": "inline-4", "question": "无页码 A？", "reference_answer": "答", "slide_id": None},
            {"id": "inline-5", "question": "无页码 B？", "reference_answer": "答", "slide_id": None},
        ]

    def test_sorted_by_slide_id(self):
        points, assignments = assign_inline_questions([], self._pairs())
        self.assertEqual(points, [0, 1, 2, 3, 4])
        self.assertEqual(
            [assignments[position] for position in points],
            ["inline-2", "inline-3", "inline-1", "inline-4", "inline-5"],
        )

    def test_all_questions_assigned_once(self):
        points, assignments = assign_inline_questions([], self._pairs())
        self.assertEqual(len(assignments), len(self._pairs()))
        self.assertEqual(sorted(assignments.values()),
                         sorted(["inline-1", "inline-2", "inline-3",
                                 "inline-4", "inline-5"]))

    def test_steps_follow_slide_order(self):
        """编排器按 point 升序取题，所以 points 的排序就是实际提问顺序。"""
        from utils.quiz_session import build_steps
        points, assignments = assign_inline_questions([], self._pairs())
        steps = build_steps([], points)
        asked = [assignments[index] for kind, index in steps if kind == "ask"]
        self.assertEqual(asked, ["inline-2", "inline-3", "inline-1",
                                 "inline-4", "inline-5"])


class AnswerWhileAskingTestCase(unittest.TestCase):
    """题目一呈现就能作答。

    展示页在题目呈现的那一刻就静音了，若作答入口要等"念完"才开，学员只能对着
    静音画面干等一个看不见进度的过程。所以 `asking` 阶段的提交必须被接受，
    且要把没念完的题目清掉，让判分反馈紧随其后。
    """

    def setUp(self):
        self.speaker = FlushSpeaker()
        self.clock = FakeClock()
        self.orch = QuizOrchestrator(
            sessionid="s1",
            segments=SEGMENTS,
            points=[1],
            assignments={1: "q2"},
            questions=QUESTIONS,
            speaker=self.speaker,
            records=None,
            grader=None,
            answer_timeout=30.0,
            clock=self.clock,
            slide_probe=None,
        )

    def _to_asking(self, limit=12):
        for _ in range(limit):
            self.orch.tick()
            if self.orch.stage == Stage.ASKING:
                return
            self.speaker.finish()
        raise AssertionError("未能进入念题阶段")

    def test_submit_during_asking_is_accepted(self):
        self._to_asking()
        self.assertEqual(self.orch.stage, Stage.ASKING)
        self.assertTrue(self.orch.submit_answer("答二"),
                        "题目都已显示在屏幕上，念没念完不影响作答有效")

    def test_pending_speech_is_flushed_before_feedback(self):
        """念题没念完就收到作答：先清掉念题，否则反馈排在念题后面。"""
        self._to_asking()
        before = len(self.speaker.delivered)
        self.orch.submit_answer("答二")

        self.assertEqual(self.speaker.flush_calls, 1, "必须清掉未播完的念题")
        # 清空发生在反馈入队之前：那一刻队列里只剩题目本身
        self.assertEqual(self.speaker.flush_at, before,
                         "清空必须在反馈入队之前，否则反馈还是排在念题后面")
        self.assertGreater(len(self.speaker.delivered), before,
                           "清空之后要有反馈，不能光清不播")

    def test_feedback_is_still_delivered(self):
        self._to_asking()
        self.orch.submit_answer("答二")
        self.assertTrue(len(self.speaker.delivered) >= 2,
                        "判完分仍要有反馈，学员才知道答得怎么样")

    def test_submit_before_question_is_rejected(self):
        """题目还没呈现（仍在讲授）时的提交不算作答。"""
        self.orch.tick()
        self.assertEqual(self.orch.stage, Stage.TEACHING)
        self.assertFalse(self.orch.submit_answer("抢答"))
        self.assertEqual(self.speaker.flush_calls, 0, "没题目时不该去清队列")

    def test_speaker_without_flush_is_tolerated(self):
        """朗读通道没有 flush_talk（精简实现/替身）时不能把作答搞崩。"""
        self._to_asking()
        self.orch._speaker = NoFlushSpeaker()
        self.assertTrue(self.orch.submit_answer("答二"),
                        "flush 只是让反馈更及时，缺它不能让提交失败")


if __name__ == "__main__":
    unittest.main()
