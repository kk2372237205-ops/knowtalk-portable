# -*- coding: utf-8 -*-
"""提问式讲课里的手势标签：`[GESTURE:<id>]` 必须随教学段生效。

对应 `openspec/changes/fix-quiz-gesture-tags/`。
这些断言针对一个真实回归：提问链路用 `_parse_ppt_segments()` 解析讲稿，
把 `gesture` 字段整个扔掉了，于是同一份讲稿在 echo 模式有动作、在提问模式没有。
"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import server.quiz_routes as quiz_routes
import utils.gestures as gesture_utils
from server.quiz_routes import QuizRegistry
from utils import material_manager as mat_mgr
from utils.quiz_session import QuizOrchestrator
from utils.quiz_store import QuizBank, QuizRecords


class FakeRequest(object):
    def __init__(self, body=None, query=None):
        self._body = body
        self.query = query or {}
        self.match_info = {}

    async def json(self):
        if self._body is None:
            raise ValueError("请求体不是合法 JSON")
        return self._body


def call(handler, request):
    loop = asyncio.new_event_loop()
    try:
        return json.loads(loop.run_until_complete(handler(request)).text)
    finally:
        loop.close()


class Speaker(object):
    """最小朗读通道：记录每次投喂的 (text, datainfo)。"""

    def __init__(self, avatar_id="Teacher"):
        self.opt = SimpleNamespace(avatar_id=avatar_id)
        self.delivered = []
        self.speaking = False
        self.preheat_calls = []

    def put_msg_txt(self, text, datainfo=None):
        self.delivered.append((text, dict(datainfo or {})))
        self.speaking = True

    def is_speaking(self):
        return self.speaking

    def flush_talk(self):
        self.speaking = False

    def finish(self):
        self.speaking = False

    def preheat_gestures(self, names):
        self.preheat_calls.append(list(names))

    @property
    def texts(self):
        return [text for text, _ in self.delivered]


def _identity_slide(value):
    return value


class SegmentParsingTestCase(unittest.TestCase):
    """1.1-1.3：分段阶段必须保留 gesture。"""

    def setUp(self):
        patcher = mock.patch.object(
            quiz_routes, "_resolve_slide_id", side_effect=_identity_slide)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_text_keeps_gesture_and_resolves_slide(self):
        segments = quiz_routes._build_segments({
            "text": "[PPT:1] 甲。[GESTURE:wave] 乙。[PPT:2] 丙。",
        })
        self.assertEqual(segments, [
            ("甲。", 1, None),
            ("乙。", 1, "wave"),
            ("丙。", 2, None),
        ])

    def test_gesture_tag_is_removed_from_read_text(self):
        segments = quiz_routes._build_segments({"text": "[GESTURE:wave] 大家好。"})
        self.assertEqual(segments, [("大家好。", None, "wave")])

    def test_two_element_segments_are_still_accepted(self):
        segments = quiz_routes._build_segments({
            "segments": [["第一段", 1], ["第二段", 2]],
        })
        self.assertEqual(segments, [("第一段", 1, None), ("第二段", 2, None)])

    def test_three_element_segments_keep_gesture(self):
        segments = quiz_routes._build_segments({
            "segments": [["第一段", 1, "wave"], ["第二段", 2]],
        })
        self.assertEqual(segments, [("第一段", 1, "wave"), ("第二段", 2, None)])

    def test_stored_lecture_keeps_gesture(self):
        mat_mgr.remember_lecture("sg", "[PPT:1] 甲。[GESTURE:wave] 乙。")
        self.addCleanup(mat_mgr.forget_lecture, "sg")
        segments = quiz_routes._segments_from_stored_lecture("sg")
        self.assertEqual(segments[1][2], "wave")


class OrchestratorDeliveryTestCase(unittest.TestCase):
    """1.4：gesture 必须随该段 datainfo 下发，且不继承。"""

    def make(self, segments):
        speaker = Speaker()
        orchestrator = QuizOrchestrator(
            sessionid="s", segments=segments, points=[], assignments={},
            questions={}, speaker=speaker,
        )
        return speaker, orchestrator

    def test_gesture_is_delivered_at_segment_start(self):
        speaker, orchestrator = self.make([
            ("第一段。", 1, "wave"),
            ("第二段。", 1, None),
        ])
        orchestrator.tick()
        self.assertEqual(speaker.delivered[0][1], {"slide_id": 1, "gesture": "wave"})

    def test_gesture_is_not_inherited(self):
        speaker, orchestrator = self.make([
            ("第一段。", 1, "wave"),
            ("第二段。", 1, None),
        ])
        orchestrator.tick()
        speaker.finish()
        orchestrator.tick()
        self.assertEqual(speaker.delivered[1][0], "第二段。")
        self.assertNotIn("gesture", speaker.delivered[1][1])

    def test_two_tuple_segments_still_work(self):
        speaker, orchestrator = self.make([
            ("第一段。", 1),
            ("第二段。", 2),
        ])
        orchestrator.tick()
        self.assertEqual(speaker.delivered[0][1], {"slide_id": 1})
        speaker.finish()
        orchestrator.tick()
        self.assertEqual(speaker.delivered[1][1], {"slide_id": 2})

    def test_question_and_feedback_carry_no_gesture(self):
        speaker = Speaker()
        orchestrator = QuizOrchestrator(
            sessionid="s",
            segments=[("第一段。", 1, "wave")],
            points=[0],
            assignments={0: "q1"},
            questions={"q1": {"id": "q1", "question": "题目？", "slide_id": 1}},
            speaker=speaker,
            grader=lambda **kwargs: {"verdict": "correct", "feedback": "答得好。"},
        )
        orchestrator.tick()          # 投喂教学段（带手势）
        speaker.finish()
        orchestrator.tick()          # 抛题
        speaker.finish()
        orchestrator.tick()          # 进入等待作答
        orchestrator.submit_answer("我的回答")
        for _text, datainfo in speaker.delivered[1:]:
            self.assertNotIn("gesture", datainfo,
                             "题干与判分反馈都不应触发教学段的手势")


class GesturePolicyTestCase(unittest.TestCase):
    """2.1-2.4 + 路由层：校验、降级提示、预热。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="quiz-gesture-")
        self.speaker = Speaker()
        self.bank = QuizBank(os.path.join(self.tmpdir, "bank.json"))
        self.records = QuizRecords(os.path.join(self.tmpdir, "records.json"))
        self.registry = QuizRegistry(
            session_lookup=lambda sid: self.speaker if sid == "s1" else None)
        self.registry.bank = self.bank
        self.registry.records = self.records
        self.registry.grader = lambda **kwargs: {"verdict": "correct", "feedback": "好。"}
        self.registry.answer_timeout = 60.0

        self._saved = (quiz_routes.registry, quiz_routes.get_bank, quiz_routes.get_records)
        quiz_routes.registry = self.registry
        quiz_routes.get_bank = lambda: self.bank
        quiz_routes.get_records = lambda: self.records
        mat_mgr.forget_lecture("s1")

        patcher = mock.patch.object(
            quiz_routes, "_resolve_slide_id", side_effect=_identity_slide)
        self.addCleanup(patcher.stop)
        patcher.start()

    def tearDown(self):
        quiz_routes.registry, quiz_routes.get_bank, quiz_routes.get_records = self._saved
        mat_mgr.forget_lecture("s1")
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def start(self, body):
        payload = {"sessionid": "s1"}
        payload.update(body)
        return call(quiz_routes.quiz_lecture, FakeRequest(body=payload))

    def test_available_gesture_is_delivered_and_preheated(self):
        with mock.patch.object(gesture_utils, "list_gestures",
                               return_value=[{"id": "wave", "muse_ready": True}]):
            result = self.start({
                "text": "[GESTURE:wave] 第一段。[PPT:2] 第二段。",
                "questions": [
                    {"question": "第二页的问题？", "reference_answer": "答案", "slide_id": 2},
                ],
            })
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(self.speaker.delivered[0][0], "第一段。")
        self.assertEqual(self.speaker.delivered[0][1]["gesture"], "wave")
        self.assertEqual(self.speaker.preheat_calls, [["wave"]])

    def test_unavailable_gesture_is_stripped_but_text_still_reads(self):
        with mock.patch.object(gesture_utils, "list_gestures", return_value=[]):
            result = self.start({
                "text": "[GESTURE:wave] 第一段。[PPT:2] 第二段。",
                "questions": [
                    {"question": "第二页的问题？", "reference_answer": "答案", "slide_id": 2},
                ],
            })
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(self.speaker.texts[0], "第一段。")
        self.assertNotIn("gesture", self.speaker.delivered[0][1])
        warnings = (result["data"].get("warnings") or [])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "GESTURE_UNAVAILABLE")
        self.assertIn("wave", warnings[0]["message"])
        self.assertEqual(self.speaker.preheat_calls, [])

    def test_no_gesture_tags_means_no_warnings(self):
        self.bank.add_question(question="题目？", reference_answer="答案", rubric=["要点"])
        result = self.start({"text": "[PPT:1] 第一页。[PPT:2] 第二页。", "count": 1, "seed": 1})
        self.assertEqual(result["code"], 0, result)
        self.assertNotIn("warnings", result["data"])

    def test_stored_lecture_gesture_reaches_renderer(self):
        mat_mgr.remember_lecture("s1", "[GESTURE:wave] 第一段。")
        with mock.patch.object(gesture_utils, "list_gestures",
                               return_value=[{"id": "wave", "muse_ready": True}]):
            result = self.start({
                "questions": [{"question": "问题？", "reference_answer": "答案"}],
            })
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(self.speaker.delivered[0][0], "第一段。")
        self.assertEqual(self.speaker.delivered[0][1]["gesture"], "wave")


if __name__ == "__main__":
    unittest.main()
