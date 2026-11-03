# -*- coding: utf-8 -*-
"""讲稿留底与回退：`/human` 投喂过的整篇讲稿要能被提问模式复用。

现场问题是 `启动提问式讲课: 0 段, 1 个提问点`：展示页「连接后播放预设」把整篇
讲稿一次性投给 `/human`（排在 TTS 队列里），启动提问时 `flush_talk()` 清空队列，
讲稿就此蒸发。没有讲稿就没有"第几页"，页码门槛和"答完继续讲"都无从谈起。
"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest

import server.quiz_routes as quiz_routes
import server.routes as routes
from server.quiz_routes import QuizRegistry
from utils import material_manager as mat_mgr
from utils.quiz_store import QuizBank, QuizRecords


class FakeSpeaker(object):
    def __init__(self):
        self.delivered = []
        self.speaking = False
        self.flush_calls = 0

    def put_msg_txt(self, text, datainfo=None):
        self.delivered.append((text, dict(datainfo or {})))
        self.speaking = True

    def is_speaking(self):
        return self.speaking

    def flush_talk(self):
        self.flush_calls += 1
        self.speaking = False

    def finish(self):
        self.speaking = False

    @property
    def texts(self):
        return [text for text, _ in self.delivered]


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


SCRIPT = "[PPT:1] 第一页讲稿。[PPT:2] 第二页讲稿。[PPT:3] 第三页讲稿。"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="quiz-recover-")
        self.speaker = FakeSpeaker()
        self.bank = QuizBank(os.path.join(self.tmpdir, "bank.json"))
        self.records = QuizRecords(os.path.join(self.tmpdir, "records.json"))
        self.registry = QuizRegistry(session_lookup=lambda sid: self.speaker)
        self.registry.bank = self.bank
        self.registry.records = self.records
        self.registry.grader = lambda **kwargs: {"verdict": "correct", "feedback": "好。"}
        self.registry.answer_timeout = 60.0

        self._saved = (quiz_routes.registry, quiz_routes.get_bank, quiz_routes.get_records)
        quiz_routes.registry = self.registry
        quiz_routes.get_bank = lambda: self.bank
        quiz_routes.get_records = lambda: self.records
        mat_mgr.forget_lecture("s1")

    def tearDown(self):
        quiz_routes.registry, quiz_routes.get_bank, quiz_routes.get_records = self._saved
        mat_mgr.forget_lecture("s1")
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def start(self, body):
        payload = {"sessionid": "s1"}
        payload.update(body)
        return call(quiz_routes.quiz_lecture, FakeRequest(body=payload))

    def status(self):
        return call(quiz_routes.quiz_status, FakeRequest(query={"sessionid": "s1"}))["data"]


class StoredLectureFallbackTestCase(Base):
    def test_uses_stored_lecture_when_request_has_none(self):
        mat_mgr.remember_lecture("s1", SCRIPT)
        result = self.start({"questions": [
            {"question": "第二页的问题？", "reference_answer": "答案", "slide_id": 2},
        ]})
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(len(self.registry.get("s1").segments), 3,
                         "应当用留底讲稿分成 3 段，而不是 0 段")
        self.assertTrue(self.status()["has_lecture"])

    def test_without_lecture_falls_back_to_pure_quiz(self):
        result = self.start({"questions": [
            {"question": "问题？", "reference_answer": "答案", "slide_id": 2},
        ]})
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(self.registry.get("s1").segments, [])
        self.assertFalse(self.status()["has_lecture"])

    def test_explicit_text_wins_over_stored_lecture(self):
        mat_mgr.remember_lecture("s1", "[PPT:9] 旧讲稿。")
        result = self.start({"text": SCRIPT, "questions": [
            {"question": "问题？", "reference_answer": "答案", "slide_id": 2},
        ]})
        self.assertEqual(result["code"], 0, result)
        segments = self.registry.get("s1").segments
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0][0], "第一页讲稿。")

    def test_bank_mode_also_recovers_lecture(self):
        mat_mgr.remember_lecture("s1", SCRIPT)
        self.bank.add_question(question="题库题？", reference_answer="答案", rubric=["要点"])
        result = self.start({"count": 1, "seed": 1})
        self.assertEqual(result["code"], 0, result)
        self.assertTrue(self.status()["has_lecture"])

    def test_pair_slide_is_normalized_to_slide_id(self):
        """教师填的是页码，讲稿分段里是真实 slide_id，两边必须归一化。"""
        from unittest import mock
        self.registry.slide_probe = lambda sid, slide: False   # 页码永远"没到"，好观察目标值
        with mock.patch.object(quiz_routes, "_resolve_slide_id", side_effect=lambda v: v * 100):
            result = self.start({"questions": [
                {"question": "第二页的问题？", "reference_answer": "答案", "slide_id": 2},
            ]})
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(self.status()["waiting_slide"], 200,
                         "问答对的页码没有走 _resolve_slide_id，与讲稿分段对不上")

    def test_bank_mode_without_any_lecture_is_rejected(self):
        result = self.start({"count": 1})
        self.assertNotEqual(result["code"], 0, "题库模式没有讲稿时必须报错，不能静默开空课堂")


class HumanRemembersLectureTestCase(unittest.TestCase):
    """`/human` 的 echo 分支负责留底，但只认带 [PPT:N] 的整篇讲稿。"""

    def setUp(self):
        self.speaker = FakeSpeaker()
        self.speaker.speech_request_id = 0
        self._saved = routes.get_session
        routes.get_session = lambda request, sessionid: self.speaker
        mat_mgr.forget_lecture("s1")

    def tearDown(self):
        routes.get_session = self._saved
        mat_mgr.forget_lecture("s1")

    def send(self, text):
        return call(routes.human, FakeRequest(body={
            "sessionid": "s1", "type": "echo", "text": text,
        }))

    def test_remembers_tagged_lecture(self):
        self.send(SCRIPT)
        self.assertEqual(mat_mgr.get_lecture("s1"), SCRIPT)

    def test_ignores_text_without_ppt_tags(self):
        self.send("你好，请介绍一下你自己。")
        self.assertEqual(mat_mgr.get_lecture("s1"), "",
                         "不带 [PPT:N] 的长文本可能是闲聊，不能当讲稿")

    def test_remembers_latest_lecture(self):
        self.send(SCRIPT)
        self.send("[PPT:1] 新的一页。")
        self.assertEqual(mat_mgr.get_lecture("s1"), "[PPT:1] 新的一页。")

    def test_forget_clears(self):
        self.send(SCRIPT)
        mat_mgr.forget_lecture("s1")
        self.assertEqual(mat_mgr.get_lecture("s1"), "")


if __name__ == "__main__":
    unittest.main()
