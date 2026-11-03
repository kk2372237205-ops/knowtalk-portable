# -*- coding: utf-8 -*-
"""语音转写接口与语音作答接口的测试（TDD：这些断言先于实现存在）。"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from utils.speech_to_text import SpeechToTextError

import server.quiz_routes as quiz_routes
import server.stt_routes as stt_routes
from server.quiz_routes import QuizRegistry
from utils.quiz_session import Stage


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


class FakePart(object):
    """模拟 aiohttp 的 multipart 分片，支持流式读取。"""

    def __init__(self, name="audio", filename="a.wav", data=b"RIFF1234", chunk_size=8):
        self.name = name
        self.filename = filename
        self._data = data
        self._pos = 0
        self._chunk_size = chunk_size
        self.reads = 0

    async def read_chunk(self, size=8192):
        self.reads += 1
        size = min(size, self._chunk_size)
        if self._pos >= len(self._data):
            return b""
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk


class FakeMultipart(object):
    def __init__(self, parts):
        self._parts = list(parts)
        self._index = 0

    async def next(self):
        if self._index >= len(self._parts):
            return None
        part = self._parts[self._index]
        self._index += 1
        return part


class FakeRequest(object):
    def __init__(self, body=None, query=None, match=None, parts=None,
                 content_type="multipart/form-data"):
        self._body = body
        self.query = query or {}
        self.match_info = match or {}
        self._parts = parts
        # 真实 aiohttp 请求对象有 content_type；缺了它 _save_upload 会把请求判成
        # 非 multipart。默认值与真实调用方一致，个别用例可以显式传别的取值。
        self.content_type = content_type

    async def json(self):
        if self._body is None:
            raise ValueError("请求体不是合法 JSON")
        return self._body

    async def multipart(self):
        # aiohttp 3.14 的 BaseRequest.multipart 就是 async def，这里必须一致
        return FakeMultipart(self._parts or [])


def call(handler, request):
    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(handler(request))
        return json.loads(response.text)
    finally:
        loop.close()


SCRIPT = "[PPT:1] 第一页讲稿。[PPT:2] 第二页讲稿。"


class SttRoutesBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="stt-routes-")
        self.transcribed = "语音识别出来的文字"
        self.transcribe_error = None

        def fake_transcribe(path, **kwargs):
            if self.transcribe_error is not None:
                raise self.transcribe_error
            return self.transcribed

        self._patches = [
            mock.patch.object(stt_routes, "transcribe_audio", side_effect=fake_transcribe),
        ]
        for item in self._patches:
            item.start()

        self._saved_routes = (quiz_routes.registry, quiz_routes.get_bank,
                              quiz_routes.get_records)
        self.speakers = {"s1": FakeSpeaker()}
        self.registry = QuizRegistry(session_lookup=self.speakers.get)
        self.registry.answer_timeout = 60.0
        from utils.quiz_store import QuizBank, QuizRecords
        self.bank = QuizBank(os.path.join(self.tmpdir, "bank.json"))
        self.records = QuizRecords(os.path.join(self.tmpdir, "records.json"))
        self.registry.bank = self.bank
        self.registry.records = self.records
        self.graded = []

        def grader(sessionid, question, answer, **kwargs):
            self.graded.append((sessionid, answer))
            return {"verdict": "correct", "feedback": "回答正确。"}

        self.registry.grader = grader
        quiz_routes.registry = self.registry
        quiz_routes.get_bank = lambda: self.bank
        quiz_routes.get_records = lambda: self.records

    def tearDown(self):
        for item in self._patches:
            item.stop()
        quiz_routes.registry, quiz_routes.get_bank, quiz_routes.get_records = \
            self._saved_routes
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def transcribe(self, parts):
        return call(stt_routes.stt_transcribe, FakeRequest(parts=parts))

    def audio_answer(self, parts, sessionid="s1"):
        return call(stt_routes.quiz_answer_audio,
                    FakeRequest(query={"sessionid": sessionid}, parts=parts))


class TranscribeRouteTestCase(SttRoutesBase):
    """6.3 转写接口。"""

    def test_success_returns_text(self):
        result = self.transcribe([FakePart()])
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(result["data"]["text"], self.transcribed)

    def test_response_does_not_leak_paths(self):
        result = self.transcribe([FakePart(filename="a.wav")])
        body = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("Temp", body)
        self.assertNotIn(":\\", body)
        self.assertNotIn("/tmp", body)

    def test_unsupported_format_is_rejected_naming_supported(self):
        result = self.transcribe([FakePart(filename="a.txt")])
        self.assertNotEqual(result["code"], 0)
        self.assertIn("wav", result["msg"], "错误信息必须列出支持的格式")

    def test_missing_audio_part_is_rejected(self):
        result = self.transcribe([])
        self.assertNotEqual(result["code"], 0)

    def test_part_without_filename_is_rejected(self):
        result = self.transcribe([FakePart(filename="")])
        self.assertNotEqual(result["code"], 0)

    def test_missing_content_type_is_rejected_with_a_readable_error(self):
        """回归：缺少 Content-Type 时曾把 KeyError 原文当业务错误返回。"""
        request = FakeRequest(parts=[FakePart()], content_type="")
        result = call(stt_routes.stt_transcribe, request)
        self.assertNotEqual(result["code"], 0)
        self.assertIn("multipart/form-data", result["msg"])
        self.assertNotIn("KeyError", result["msg"])
        self.assertNotIn("'Content-Type'", result["msg"])

    def test_non_multipart_content_type_is_rejected(self):
        for content_type in ("application/json", "audio/wav", "text/plain"):
            request = FakeRequest(parts=[FakePart()], content_type=content_type)
            result = call(stt_routes.stt_transcribe, request)
            self.assertNotEqual(result["code"], 0, content_type)
            self.assertIn("multipart/form-data", result["msg"], content_type)

    def test_content_type_with_boundary_is_accepted(self):
        request = FakeRequest(parts=[FakePart()],
                              content_type="multipart/form-data; boundary=xyz")
        result = call(stt_routes.stt_transcribe, request)
        self.assertEqual(result["code"], 0, result)

    def test_oversize_is_rejected_without_reading_everything(self):
        big = b"x" * (40 * 1024 * 1024)
        part = FakePart(data=big, chunk_size=1024 * 1024)
        with mock.patch.dict(os.environ, {"STT_MAX_MB": "1"}):
            result = self.transcribe([part])
        self.assertNotEqual(result["code"], 0)
        self.assertIn("上限", result["msg"])
        self.assertLess(part._pos, len(big), "超限后不得继续读取剩余内容")

    def test_busy_is_reported_distinctly(self):
        self.transcribe_error = SpeechToTextError("正在处理另一段语音，请稍后重试", busy=True)
        result = self.transcribe([FakePart()])
        self.assertNotEqual(result["code"], 0)
        self.assertIn("正在", result["msg"])

    def test_transcribe_failure_includes_hint(self):
        self.transcribe_error = SpeechToTextError("未识别到语音内容", hint="检查麦克风")
        result = self.transcribe([FakePart()])
        self.assertNotEqual(result["code"], 0)
        self.assertIn("未识别到语音", result["msg"])
        self.assertIn("检查麦克风", result["msg"], "排查建议必须一并返回给调用方")

    def test_temporary_file_removed_after_success(self):
        with mock.patch.object(stt_routes, "UPLOAD_DIR", self.tmpdir):
            self.transcribe([FakePart()])
            leftover = [n for n in os.listdir(self.tmpdir) if n.startswith("stt-")]
        self.assertEqual(leftover, [], "临时文件必须被清理")

    def test_temporary_file_removed_after_failure(self):
        self.transcribe_error = SpeechToTextError("识别失败")
        with mock.patch.object(stt_routes, "UPLOAD_DIR", self.tmpdir):
            self.transcribe([FakePart()])
            leftover = [n for n in os.listdir(self.tmpdir) if n.startswith("stt-")]
        self.assertEqual(leftover, [], "失败路径也必须清理临时文件")


class AudioAnswerRouteTestCase(SttRoutesBase):
    """6.7 语音作答：转写结果必须与文字作答走同一条判分路径。"""

    def _start_quiz(self):
        for index in range(3):
            self.bank.add_question(question="第%d题" % index, reference_answer="答案")
        return call(quiz_routes.quiz_lecture, FakeRequest(body={
            "sessionid": "s1", "text": SCRIPT, "count": 1, "seed": 1,
        }))

    def _advance_until_awaiting(self):
        for _ in range(30):
            self.registry.tick_all()
            status = call(quiz_routes.quiz_status,
                          FakeRequest(query={"sessionid": "s1"}))["data"]
            if status.get("stage") == Stage.AWAITING_ANSWER.value:
                return status
            self.speakers["s1"].finish()
        raise AssertionError("未能进入等待作答状态")

    def test_audio_answer_goes_through_the_same_grading_path(self):
        self._start_quiz()
        self._advance_until_awaiting()
        result = self.audio_answer([FakePart()])
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(self.graded[-1][1], self.transcribed,
                         "转写文本必须作为作答传给判分器")
        self.assertEqual(result["data"].get("text"), self.transcribed)

    def test_audio_answer_is_recorded(self):
        self._start_quiz()
        self._advance_until_awaiting()
        self.audio_answer([FakePart()])
        records = self.records.list_records("s1")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["answer"], self.transcribed)
        self.assertEqual(records[0]["verdict"], "correct")

    def test_transcribe_failure_keeps_awaiting_and_does_not_grade(self):
        self._start_quiz()
        self._advance_until_awaiting()
        self.transcribe_error = SpeechToTextError("未识别到语音内容", hint="重录一次")
        result = self.audio_answer([FakePart()])
        self.assertNotEqual(result["code"], 0)
        self.assertEqual(self.graded, [], "转写失败不得触发判分")
        self.assertEqual(self.records.list_records("s1"), [], "转写失败不得写入作答记录")
        status = call(quiz_routes.quiz_status,
                      FakeRequest(query={"sessionid": "s1"}))["data"]
        self.assertEqual(status["stage"], Stage.AWAITING_ANSWER.value,
                         "转写失败后必须允许重新作答")

    def test_answer_when_not_awaiting_is_rejected(self):
        self._start_quiz()
        result = self.audio_answer([FakePart()])
        self.assertNotEqual(result["code"], 0)

    def test_unknown_session_is_rejected(self):
        result = self.audio_answer([FakePart()], sessionid="nope")
        self.assertNotEqual(result["code"], 0)

    def test_unsupported_format_is_rejected_before_transcribing(self):
        self._start_quiz()
        self._advance_until_awaiting()
        result = self.audio_answer([FakePart(filename="a.txt")])
        self.assertNotEqual(result["code"], 0)
        self.assertEqual(self.graded, [], "格式不合法不得进入判分")


if __name__ == "__main__":
    unittest.main()
