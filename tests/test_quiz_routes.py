# -*- coding: utf-8 -*-
"""提问模式接口的测试（TDD：这些断言先于实现存在）。

直接调用处理函数并传入最小请求替身，不启动 aiohttp 服务，因此不依赖网络与
真实数字人会话。
"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from utils.quiz_store import QuizBank, QuizRecords
from utils.quiz_session import Stage

import server.quiz_routes as quiz_routes
from server.quiz_routes import QuizRegistry


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
        # 模拟 BaseAvatar.flush_talk：清空待播内容并记一次调用
        self.flush_calls += 1
        self.speaking = False

    def finish(self):
        self.speaking = False

    @property
    def texts(self):
        return [text for text, _ in self.delivered]


class FakeRequest(object):
    def __init__(self, body=None, query=None, match=None, app=None):
        self._body = body
        self.query = query or {}
        self.match_info = match or {}
        self.app = app or {}

    async def json(self):
        if self._body is None:
            raise ValueError("请求体不是合法 JSON")
        return self._body


def call(handler, request):
    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(handler(request))
        return json.loads(response.text)
    finally:
        loop.close()


SCRIPT = "[PPT:1] 第一页讲稿。[PPT:2] 第二页讲稿。[PPT:3] 第三页讲稿。"


class QuizRoutesBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="quiz-routes-")
        self.speakers = {"s1": FakeSpeaker(), "s2": FakeSpeaker()}
        self.bank = QuizBank(os.path.join(self.tmpdir, "bank.json"))
        self.records = QuizRecords(os.path.join(self.tmpdir, "records.json"))
        self.graded = []

        def grader(sessionid, question, answer, **kwargs):
            self.graded.append((sessionid, question.get("id"), answer))
            return {"verdict": "correct", "feedback": "回答正确。"}

        self.registry = QuizRegistry(session_lookup=self.speakers.get)
        self.registry.bank = self.bank
        self.registry.records = self.records
        self.registry.grader = grader
        self.registry.answer_timeout = 60.0

        self._saved = (quiz_routes.registry, quiz_routes.get_bank, quiz_routes.get_records)
        quiz_routes.registry = self.registry
        quiz_routes.get_bank = lambda: self.bank
        quiz_routes.get_records = lambda: self.records

    def tearDown(self):
        quiz_routes.registry, quiz_routes.get_bank, quiz_routes.get_records = self._saved
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def add_questions(self, count):
        ids = []
        for index in range(count):
            entry = self.bank.add_question(
                question="第%d题题干？" % (index + 1),
                reference_answer="答案%d" % (index + 1),
                rubric=["要点"],
            )
            ids.append(entry["id"])
        return ids

    def start(self, body):
        return call(quiz_routes.quiz_lecture, FakeRequest(body=body))

    def start_ok(self, **body):
        payload = {"sessionid": "s1", "text": SCRIPT, "count": 1, "seed": 1}
        payload.update(body)
        result = self.start(payload)
        self.assertEqual(result["code"], 0, result)
        return result["data"]

    def advance_until_awaiting(self, sessionid="s1", limit=30):
        for _ in range(limit):
            self.registry.tick_all()
            status = self.status(sessionid)["data"]
            if status.get("stage") == Stage.AWAITING_ANSWER.value:
                return status
            self.speakers[sessionid].finish()
        raise AssertionError("未能进入等待作答状态")

    def status(self, sessionid="s1"):
        return call(quiz_routes.quiz_status, FakeRequest(query={"sessionid": sessionid}))


class StartLectureTestCase(QuizRoutesBase):
    """5.1 启动一次提问式讲课。"""

    def test_start_returns_selected_points(self):
        self.add_questions(5)
        data = self.start_ok(count=2)
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["points"]), 2)
        self.assertTrue(data["question_ids"])

    def test_start_delivers_first_segment_only(self):
        self.add_questions(3)
        self.start_ok(count=1)
        speaker = self.speakers["s1"]
        self.assertEqual(len(speaker.delivered), 1, "启动后只应投喂第一段")
        self.assertIn("第一页讲稿", speaker.texts[0])

    def test_start_flushes_pending_queue_before_delivering(self):
        # 提问模式独占朗读：启动前必须清空展示页可能已投喂的预设音频，
        # 否则编排器再投喂一遍会出现"两段演讲叠在一起"。
        self.add_questions(3)
        self.start_ok(count=1)
        speaker = self.speakers["s1"]
        self.assertEqual(speaker.flush_calls, 1, "启动时必须先清空待播队列")
        self.assertEqual(len(speaker.delivered), 1, "清空后仍应投喂第一段")

    def test_missing_sessionid_is_rejected(self):
        self.add_questions(3)
        result = self.start({"text": SCRIPT, "count": 1})
        self.assertNotEqual(result["code"], 0)

    def test_unknown_session_is_rejected(self):
        self.add_questions(3)
        result = self.start({"sessionid": "nope", "text": SCRIPT, "count": 1})
        self.assertNotEqual(result["code"], 0)
        self.assertIn("nope", result["msg"])

    def test_missing_script_is_rejected(self):
        self.add_questions(3)
        result = self.start({"sessionid": "s1", "count": 1})
        self.assertNotEqual(result["code"], 0)

    def test_count_beyond_candidates_is_rejected_with_available_count(self):
        self.add_questions(9)
        result = self.start({"sessionid": "s1", "text": SCRIPT, "count": 99, "seed": 1})
        self.assertNotEqual(result["code"], 0)
        self.assertIn("3", result["msg"], "错误信息必须给出可选数量")

    def test_insufficient_questions_is_rejected_with_count(self):
        self.add_questions(1)
        result = self.start({"sessionid": "s1", "text": SCRIPT, "count": 3, "seed": 1})
        self.assertNotEqual(result["code"], 0)
        self.assertIn("1", result["msg"])

    def test_slide_range_limits_points(self):
        self.add_questions(5)
        data = self.start_ok(count=1, slide_range=[2, 3])
        slides = {self.registry.get("s1").segments[p][1] for p in data["points"]}
        self.assertTrue(slides.issubset({2, 3}), slides)

    def test_two_sessions_are_independent(self):
        self.add_questions(5)
        self.start_ok(count=1)
        self.registry.start(
            sessionid="s2", segments=[("另一会话的讲稿", 1)], points=[], assignments={},
            questions={}, records=self.records, grader=self.registry.grader,
        )
        self.assertEqual(len(self.speakers["s1"].delivered), 1)
        self.assertEqual(len(self.speakers["s2"].delivered), 1)
        self.assertEqual(self.status("s1")["data"]["total"], 1)
        self.assertEqual(self.status("s2")["data"]["total"], 0)


class StopQuizTestCase(QuizRoutesBase):
    """退出提问模式必须真正停止编排并清空待播。"""

    def test_stop_removes_orchestrator_and_flushes(self):
        self.add_questions(3)
        self.start_ok(count=1)
        speaker = self.speakers["s1"]
        before = speaker.flush_calls
        result = call(quiz_routes.quiz_stop, FakeRequest(body={"sessionid": "s1"}))
        self.assertEqual(result["code"], 0, result)
        self.assertIsNone(self.registry.get("s1"), "编排器必须被移除")
        self.assertFalse(self.status("s1")["data"]["in_quiz"])
        self.assertEqual(speaker.flush_calls, before + 1, "退出时必须清空待播")

    def test_stop_only_affects_named_session(self):
        self.add_questions(3)
        self.start_ok(count=1)
        self.registry.start(
            sessionid="s2", segments=[("另一会话", 1)], points=[], assignments={},
            questions={}, records=self.records, grader=self.registry.grader,
        )
        call(quiz_routes.quiz_stop, FakeRequest(body={"sessionid": "s1"}))
        self.assertIsNone(self.registry.get("s1"))
        self.assertIsNotNone(self.registry.get("s2"), "其它会话不受影响")

    def test_stop_missing_sessionid_is_rejected(self):
        result = call(quiz_routes.quiz_stop, FakeRequest(body={}))
        self.assertNotEqual(result["code"], 0)


class BindingViaApiTestCase(QuizRoutesBase):
    """5.8 绑定表校验的接口行为。"""

    def test_valid_binding_is_honored(self):
        ids = self.add_questions(5)
        # 绑定幻灯片 2 → 第三道题；同时让选取固定落在幻灯片 2 上
        data = self.start_ok(count=1, mode="explicit", explicit=[1], bindings={"2": ids[2]})
        self.assertEqual(list(data["assignments"].values()), [ids[2]])

    def test_unknown_question_id_is_rejected_and_named(self):
        self.add_questions(3)
        result = self.start({"sessionid": "s1", "text": SCRIPT, "count": 1, "seed": 1,
                             "bindings": {"2": "no-such-question"}})
        self.assertNotEqual(result["code"], 0)
        self.assertIn("no-such-question", result["msg"])

    def test_slide_outside_candidates_is_rejected_and_named(self):
        ids = self.add_questions(3)
        result = self.start({"sessionid": "s1", "text": SCRIPT, "count": 1, "seed": 1,
                             "bindings": {"9": ids[0]}})
        self.assertNotEqual(result["code"], 0)
        self.assertIn("9", result["msg"])

    def test_same_question_bound_twice_is_rejected(self):
        ids = self.add_questions(3)
        result = self.start({"sessionid": "s1", "text": SCRIPT, "count": 2, "seed": 1,
                             "bindings": {"1": ids[0], "2": ids[0]}})
        self.assertNotEqual(result["code"], 0)
        self.assertIn(ids[0], result["msg"])

    def test_bound_slide_not_selected_is_reported_clearly(self):
        ids = self.add_questions(5)
        # 显式只选幻灯片 1 的位置，却把题绑到幻灯片 3
        result = self.start({"sessionid": "s1", "text": SCRIPT, "count": 1,
                             "mode": "explicit", "explicit": [0],
                             "bindings": {"3": ids[0]}})
        self.assertNotEqual(result["code"], 0)
        self.assertIn("3", result["msg"])


class StatusAndAnswerTestCase(QuizRoutesBase):
    """5.2 状态查询、5.3 文字作答、5.4 跳过。"""

    def test_status_when_not_in_quiz_mode(self):
        result = self.status("s1")
        self.assertEqual(result["code"], 0, "未启用提问模式时也必须返回成功")
        self.assertFalse(result["data"]["in_quiz"])

    def test_status_exposes_question_and_progress(self):
        self.add_questions(3)
        self.start_ok(count=1)
        status = self.advance_until_awaiting()
        self.assertTrue(status["in_quiz"])
        # 跨 HTTP 边界后 stage 是字符串值（Enum 不能直接进 JSON）
        self.assertEqual(status["stage"], Stage.AWAITING_ANSWER.value)
        self.assertTrue(status["question"]["id"])
        self.assertTrue(status["question"]["question"])
        self.assertEqual(status["total"], 1)
        self.assertEqual(status["completed"], 0)

    def test_answer_is_accepted_and_graded(self):
        self.add_questions(3)
        self.start_ok(count=1)
        self.advance_until_awaiting()
        result = call(quiz_routes.quiz_answer,
                      FakeRequest(body={"sessionid": "s1", "answer": "我的回答"}))
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(self.graded[-1][2], "我的回答")
        self.assertEqual(len(self.records.list_records("s1")), 1)

    def test_blank_answer_is_rejected_and_stays_awaiting(self):
        self.add_questions(3)
        self.start_ok(count=1)
        self.advance_until_awaiting()
        result = call(quiz_routes.quiz_answer,
                      FakeRequest(body={"sessionid": "s1", "answer": "   "}))
        self.assertNotEqual(result["code"], 0)
        self.assertEqual(self.status()["data"]["stage"], Stage.AWAITING_ANSWER.value)

    def test_answer_when_not_awaiting_is_rejected(self):
        self.add_questions(3)
        self.start_ok(count=1)
        result = call(quiz_routes.quiz_answer,
                      FakeRequest(body={"sessionid": "s1", "answer": "太早"}))
        self.assertNotEqual(result["code"], 0)

    def test_skip_records_unanswered_and_resumes(self):
        self.add_questions(3)
        self.start_ok(count=1)
        self.advance_until_awaiting()
        result = call(quiz_routes.quiz_skip, FakeRequest(body={"sessionid": "s1"}))
        self.assertEqual(result["code"], 0, result)
        record = self.records.list_records("s1")[0]
        self.assertTrue(record["unanswered"])

    def test_answer_only_affects_the_named_session(self):
        """9.3 学员交互接口只作用于请求指定的会话，不提供跨会话批量操作。"""
        self.add_questions(3)
        self.start_ok(count=1)                      # 只有 s1 进入提问模式
        result = call(quiz_routes.quiz_answer,
                      FakeRequest(body={"sessionid": "s2", "answer": "x"}))
        self.assertNotEqual(result["code"], 0, "未启用提问模式的会话不接受作答")
        status = self.status("s1")["data"]
        self.assertTrue(status["in_quiz"], "s1 的提问状态不得受 s2 的请求影响")

    def test_skip_only_affects_the_named_session(self):
        self.add_questions(3)
        self.start_ok(count=1)
        result = call(quiz_routes.quiz_skip, FakeRequest(body={"sessionid": "s2"}))
        self.assertNotEqual(result["code"], 0)
        self.assertTrue(self.status("s1")["data"]["in_quiz"])


class BankRoutesTestCase(QuizRoutesBase):
    """5.5 题库接口。"""

    def test_list_empty(self):
        result = call(quiz_routes.quiz_bank_list, FakeRequest())
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"]["questions"], [])

    def test_add_then_list(self):
        result = call(quiz_routes.quiz_bank_add, FakeRequest(body={
            "question": "题干", "reference_answer": "答案", "rubric": ["要点"], "slide_id": 2,
        }))
        self.assertEqual(result["code"], 0, result)
        listed = call(quiz_routes.quiz_bank_list, FakeRequest())
        self.assertEqual(len(listed["data"]["questions"]), 1)
        self.assertEqual(listed["data"]["questions"][0]["source"], "manual")

    def test_add_without_reference_answer_is_rejected(self):
        result = call(quiz_routes.quiz_bank_add,
                      FakeRequest(body={"question": "题干"}))
        self.assertNotEqual(result["code"], 0)

    def test_update_changes_field(self):
        entry = self.bank.add_question(question="旧题干", reference_answer="答案")
        result = call(quiz_routes.quiz_bank_update, FakeRequest(
            body={"question": "新题干"}, match={"question_id": entry["id"]},
        ))
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(self.bank.get_question(entry["id"])["question"], "新题干")

    def test_update_unknown_id_is_rejected(self):
        result = call(quiz_routes.quiz_bank_update,
                      FakeRequest(body={"question": "x"}, match={"question_id": "nope"}))
        self.assertNotEqual(result["code"], 0)

    def test_delete_removes_entry(self):
        entry = self.bank.add_question(question="题干", reference_answer="答案")
        result = call(quiz_routes.quiz_bank_delete,
                      FakeRequest(match={"question_id": entry["id"]}))
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(self.bank.list_questions(), [])

    def test_delete_unknown_id_is_rejected(self):
        self.bank.add_question(question="题干", reference_answer="答案")
        result = call(quiz_routes.quiz_bank_delete,
                      FakeRequest(match={"question_id": "nope"}))
        self.assertNotEqual(result["code"], 0)
        self.assertEqual(len(self.bank.list_questions()), 1, "其它题目不受影响")


class GenerateRoutesTestCase(QuizRoutesBase):
    """5.6 AI 出题。"""

    def test_generate_returns_questions_without_persisting_by_default(self):
        def fake_generator(slide_text, count, context=None):
            return [{"question": "生成题干", "reference_answer": "生成答案",
                     "rubric": ["要点"]}]
        self.registry.generator = fake_generator

        result = call(quiz_routes.quiz_bank_generate, FakeRequest(body={
            "slide_text": "第一页的幻灯片文字", "count": 1,
        }))
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(len(result["data"]["questions"]), 1)
        self.assertEqual(self.bank.list_questions(), [],
                         "未显式落库时不得出现在题库里")

    def test_generate_with_persist_adds_to_bank(self):
        def fake_generator(slide_text, count, context=None):
            return [{"question": "生成题干", "reference_answer": "生成答案", "rubric": ["要点"]}]
        self.registry.generator = fake_generator

        result = call(quiz_routes.quiz_bank_generate, FakeRequest(body={
            "slide_text": "文字", "count": 1, "persist": True,
        }))
        self.assertEqual(result["code"], 0, result)
        stored = self.bank.list_questions()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["source"], "generated", "来源必须标为自动生成")

    def test_generate_without_input_is_rejected(self):
        result = call(quiz_routes.quiz_bank_generate, FakeRequest(body={"count": 1}))
        self.assertNotEqual(result["code"], 0)

    def test_generate_failure_returns_error_not_crash(self):
        def broken(slide_text, count, context=None):
            raise RuntimeError("LLM 不可用")
        self.registry.generator = broken
        result = call(quiz_routes.quiz_bank_generate,
                      FakeRequest(body={"slide_text": "文字", "count": 1}))
        self.assertNotEqual(result["code"], 0)


class PairsGenerateTestCase(QuizRoutesBase):
    """由预设文本生成内联问答对（课堂面、免口令、不落库、不需要会话）。"""

    def _install_generator(self, calls):
        def generator(segment_text, count, context=None):
            calls.append((segment_text, count))
            return [{
                "question": "关于「%s」的问题？" % segment_text[:6],
                "reference_answer": "参考答案",
                "rubric": ["要点"],
            }]
        self.registry.generator = generator

    def test_generates_pairs_per_segment_with_slide_id(self):
        calls = []
        self._install_generator(calls)
        with mock.patch.object(quiz_routes, "_resolve_slide_id", lambda value: value):
            result = call(quiz_routes.quiz_pairs_generate, FakeRequest(body={
                "text": "[PPT:1] 第一段讲稿。[PPT:2] 第二段讲稿。", "count": 1,
            }))
        self.assertEqual(result["code"], 0, result)
        pairs = result["data"]["pairs"]
        self.assertEqual(len(pairs), 2)
        self.assertEqual([pair["slide_id"] for pair in pairs], [1, 2])
        self.assertEqual(len(calls), 2, "每个非空段各调用一次生成器")
        self.assertEqual(self.bank.list_questions(), [], "不得写入题库")

    def test_without_tags_still_works(self):
        calls = []
        self._install_generator(calls)
        result = call(quiz_routes.quiz_pairs_generate, FakeRequest(body={
            "text": "没有标签的一整段讲稿。",
        }))
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(len(result["data"]["pairs"]), 1)
        self.assertNotIn("slide_id", result["data"]["pairs"][0])

    def test_missing_text_is_rejected(self):
        result = call(quiz_routes.quiz_pairs_generate, FakeRequest(body={"count": 1}))
        self.assertNotEqual(result["code"], 0)

    def test_generator_failure_returns_error_not_crash(self):
        def broken(segment_text, count, context=None):
            raise RuntimeError("LLM 不可用")
        self.registry.generator = broken
        result = call(quiz_routes.quiz_pairs_generate, FakeRequest(body={"text": "文字"}))
        self.assertNotEqual(result["code"], 0)
        self.assertIn("LLM", result["msg"])

    def test_registered_route_is_unprotected(self):
        # 课堂面接口必须免口令。口令机制已整体退役（server/auth.py 删除），
        # 因此这里断言的是"没有可拦截的口令层"，任何重新引入都会被这条钉住。
        import importlib.util
        self.assertIsNone(
            importlib.util.find_spec("server.auth"),
            "口令校验已退役，课堂面接口不应再被口令层拦截",
        )


class RecordsRouteTestCase(QuizRoutesBase):
    """5.7 问答记录查询。"""

    def test_list_records_for_session(self):
        self.add_questions(3)
        self.start_ok(count=1)
        self.advance_until_awaiting()
        call(quiz_routes.quiz_answer, FakeRequest(body={"sessionid": "s1", "answer": "回答"}))
        result = call(quiz_routes.quiz_records, FakeRequest(query={"sessionid": "s1"}))
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(len(result["data"]["records"]), 1)
        self.assertEqual(result["data"]["records"][0]["question_id"],
                         self.records.list_records("s1")[0]["question_id"])

    def test_list_records_without_session_returns_all(self):
        self.records.add_record(sessionid="s1", question_id="q1", answer="a")
        self.records.add_record(sessionid="s2", question_id="q2", answer="b")
        result = call(quiz_routes.quiz_records, FakeRequest(query={}))
        self.assertEqual(result["code"], 0)
        self.assertEqual(len(result["data"]["records"]), 2)


class InlineQuestionsViaApiTestCase(QuizRoutesBase):
    """11.4 发起时可直接给出内联问答对（不依赖题库，也不依赖讲稿）。"""

    PAIRS = [
        {"question": "第一题？", "reference_answer": "答案一"},
        {"question": "第二题？", "reference_answer": "答案二", "rubric": ["要点"]},
    ]

    def test_quiz_only_lecture_starts_with_empty_bank(self):
        self.assertEqual(self.bank.list_questions(), [], "前提：题库为空")
        data = self.start_ok(sessionid="s1", text=None, questions=self.PAIRS, count=9)

        self.assertEqual(data["total"], 2, "提问数量必须由问答对数量决定，忽略 count")
        self.assertEqual(data["points"], [0, 1], "没有讲稿时每个问答对自成一个提问点")
        self.assertEqual(data["question_ids"], ["inline-1", "inline-2"])
        self.assertTrue(self.status("s1")["data"]["in_quiz"])
        self.assertEqual(self.speakers["s1"].texts[0], "第一题？",
                         "纯提问必须直接念题干，不得投喂教学段")

    def test_inline_with_script_asks_in_given_order(self):
        data = self.start_ok(sessionid="s1", text=SCRIPT, questions=self.PAIRS, seed=1)
        self.assertEqual(data["total"], 2)
        ordered = [data["assignments"][str(point)] for point in data["points"]]
        self.assertEqual(ordered, ["inline-1", "inline-2"], "必须保持问答对的给出顺序")

    def test_pair_can_target_a_slide(self):
        data = self.start_ok(sessionid="s1", text=SCRIPT, seed=1, questions=[
            {"question": "第三页的题？", "reference_answer": "答案", "slide_id": 3},
        ])
        self.assertEqual(data["points"], [2], "幻灯片 3 对应候选位置 2")
        self.assertEqual(data["question_ids"], ["inline-1"])

    def test_missing_reference_answer_is_rejected(self):
        result = self.start({"sessionid": "s1", "questions": [{"question": "只有题干"}]})
        self.assertNotEqual(result["code"], 0)
        self.assertIn("1", result["msg"], "必须指明是第几个问答对")

    def test_inline_with_bindings_is_rejected(self):
        ids = self.add_questions(1)
        result = self.start({
            "sessionid": "s1", "questions": self.PAIRS, "bindings": {"1": ids[0]},
        })
        self.assertNotEqual(result["code"], 0)
        self.assertIn("绑定", result["msg"])

    def test_too_many_pairs_for_script_is_rejected_with_available_count(self):
        pairs = [{"question": "第%d题？" % i, "reference_answer": "答案"}
                 for i in range(1, 10)]
        result = self.start({"sessionid": "s1", "text": SCRIPT, "questions": pairs})
        self.assertNotEqual(result["code"], 0)
        self.assertIn("3", result["msg"], "讲稿只有 3 个候选提问点，必须说明可用数量")

    def test_no_script_and_no_questions_is_rejected(self):
        result = self.start({"sessionid": "s1"})
        self.assertNotEqual(result["code"], 0)


class SessionMigrationTestCase(QuizRoutesBase):
    """页面刷新会新建 WebRTC 会话：提问编排必须跟过去，否则刷新后课堂就断了。"""

    def test_migrate_moves_orchestrator_and_keeps_progress(self):
        self.add_questions(3)
        self.start_ok(count=1)
        before = self.advance_until_awaiting("s1")

        # 刷新：/offer 新建了 s3
        self.speakers["s3"] = FakeSpeaker()
        self.assertTrue(self.registry.migrate("s1", "s3", self.speakers["s3"]),
                        "旧会话上的编排必须迁到新会话")
        self.assertIsNone(self.registry.get("s1"), "旧会话不得留下编排器")

        status = self.status("s3")["data"]
        self.assertTrue(status["in_quiz"], "新会话必须查到同一堂课")
        self.assertEqual(status["stage"], Stage.AWAITING_ANSWER.value,
                         "当前等待作答状态必须保留，学员刷新后还能作答")
        self.assertEqual(status["question"], before["question"],
                         "迁移后当前题目必须还是同一道")
        self.assertTrue(status["question"]["question"], "题目文本不能为空")

        # 迁移后仍能作答，且反馈投到新会话（说明 speaker 也换过去了）
        result = call(quiz_routes.quiz_answer, FakeRequest(
            body={"sessionid": "s3", "answer": "答案1"}))
        self.assertEqual(result["code"], 0, result)
        self.assertIn("回答正确。", self.speakers["s3"].texts,
                      "迁移后判分反馈必须念在新会话上")

    def test_migrate_without_orchestrator_is_noop(self):
        self.assertFalse(self.registry.migrate("nope", "s3", FakeSpeaker()))

    def test_migrate_keeps_old_when_new_session_unavailable(self):
        self.add_questions(1)
        self.start_ok(count=1)
        self.assertFalse(self.registry.migrate("s1", "s9", None),
                         "拿不到新会话时不得迁移")
        self.assertIsNotNone(self.registry.get("s1"), "迁移失败必须把编排留在原处")

    def test_tick_all_pauses_when_session_disappeared(self):
        self.add_questions(1)
        self.start_ok(count=1)
        delivered = len(self.speakers["s1"].delivered)

        # 会话被销毁、但还没迁移：停止推进，别把后面的段落念给没人接收的通道
        self.registry.session_alive = lambda sid: False
        self.registry.tick_all()
        self.assertEqual(len(self.speakers["s1"].delivered), delivered)
        self.assertIsNotNone(self.registry.get("s1"), "会话消失时编排器必须保留等待迁移")

class LectureThenContinueTestCase(QuizRoutesBase):
    """带讲稿提问：先讲 → 到点提问 → 判分 → 继续讲剩余段落。

    现场故障：控制台没把讲稿一起提交，服务端拿到的是 `0 段`，
    学员答完题课堂立刻结束（日志 `提问式讲课结束（作答 1/1）`），
    看起来就是"判分完就没声了"。
    """

    def test_first_delivery_is_teaching_not_question(self):
        self.add_questions(3)
        self.start_ok(sessionid="s1", text=SCRIPT, count=2, seed=1)
        speaker = self.speakers["s1"]
        self.assertEqual(speaker.texts[0], "第一页讲稿。",
                         "带讲稿时第一个开口的必须是教学段，而不是题干")

    def test_after_judging_the_remaining_segments_are_delivered(self):
        self.add_questions(3)
        data = self.start_ok(sessionid="s1", text=SCRIPT, count=1, seed=1)
        self.assertGreater(len(data["points"]), 0)
        speaker = self.speakers["s1"]
        status = self.advance_until_awaiting("s1")
        self.assertEqual(status["stage"], Stage.AWAITING_ANSWER.value)

        result = call(quiz_routes.quiz_answer, FakeRequest(
            body={"sessionid": "s1", "answer": "我的回答"}))
        self.assertEqual(result["code"], 0, result)

        # 判分反馈先念，然后继续把后面的教学段讲完
        speaker.finish()
        delivered = list(speaker.texts)
        for _ in range(10):
            self.registry.tick_all()
            speaker.finish()
        self.assertIn("回答正确。", delivered, "判分反馈必须被朗读")
        self.assertIn("第二页讲稿。", speaker.texts,
                      "判分后必须继续讲授剩余段落，而不是整堂课结束")

class ReportRouteTestCase(QuizRoutesBase):
    """课堂结束的总评分：编排器销毁后也必须能按记录算出同样结果。"""

    def report(self, sessionid="s1"):
        return call(quiz_routes.quiz_report, FakeRequest(query={"sessionid": sessionid}))

    def test_no_records_means_no_score(self):
        data = self.report("s1")["data"]
        self.assertIsNone(data["summary"], "没作答就不评分")
        self.assertEqual(data["text"], "")

    def test_score_from_finished_lecture(self):
        self.add_questions(2)
        data = self.start_ok(sessionid="s1", text=SCRIPT, count=2, seed=1)
        self.assertEqual(data["total"], 2)
        self.advance_until_awaiting("s1")
        call(quiz_routes.quiz_answer, FakeRequest(
            body={"sessionid": "s1", "answer": "第一个回答"}))
        self.speakers["s1"].finish()
        for _ in range(10):
            self.registry.tick_all()
            self.speakers["s1"].finish()
        # 第二题不答：走"跳过"，记为未作答
        self.assertEqual(self.status("s1")["data"]["stage"], Stage.AWAITING_ANSWER.value)
        call(quiz_routes.quiz_skip, FakeRequest(body={"sessionid": "s1"}))
        for _ in range(10):
            self.registry.tick_all()
            self.speakers["s1"].finish()

        report = self.report("s1")["data"]
        self.assertIsNotNone(report["summary"])
        self.assertEqual(report["summary"]["total"], 2)
        self.assertEqual(report["summary"]["correct"], 1)
        self.assertGreaterEqual(report["summary"]["unanswered"], 1)
        self.assertEqual(report["summary"]["score"], 50)
        self.assertIn("得分 50 分", report["text"])

    def test_report_survives_orchestrator_removal(self):
        self.records.add_record(sessionid="s9", question_id="q1", verdict="correct",
                                feedback="很准确。")
        self.records.add_record(sessionid="s9", question_id="q2", answer="答了",
                                verdict="partial", feedback="还差一点。")
        self.registry.remove("s9")
        report = self.report("s9")["data"]
        self.assertEqual(report["summary"]["score"], 80)
        self.assertTrue(report["finished"], "没有编排器即视为课堂已结束")

    def test_missing_sessionid_is_rejected(self):
        result = call(quiz_routes.quiz_report, FakeRequest(query={}))
        self.assertNotEqual(result["code"], 0)


class ReleaseRouteTestCase(unittest.TestCase):
    """`POST /api/quiz/release`：刷新/关页时页面主动声明释放会话。

    没有这个入口时只能看 PeerConnection 状态，而刷新后的旧 PC 在服务端
    能长时间保持 connected —— 提问编排永远迁不到新会话（刷新后不再提问）。
    """

    def setUp(self):
        from server import rtc_manager as rtc_module
        self.rtc = rtc_module
        self.rtc._RELEASED_SESSIONS.clear()

    def tearDown(self):
        self.rtc._RELEASED_SESSIONS.clear()

    def release(self, body):
        return call(quiz_routes.quiz_release, FakeRequest(body=body))

    def test_release_marks_session_released(self):
        result = self.release({"sessionid": "s9"})
        self.assertEqual(result["code"], 0, result)
        self.assertTrue(self.rtc.is_session_released("s9"))

    def test_release_requires_sessionid(self):
        result = self.release({})
        self.assertNotEqual(result["code"], 0, "缺 sessionid 必须报错")

    def test_release_survives_empty_body(self):
        """sendBeacon 偶尔会带空体：不得抛异常，返回业务错误即可。"""
        result = call(quiz_routes.quiz_release, FakeRequest(body=None))
        self.assertNotEqual(result["code"], 0)


if __name__ == "__main__":
    unittest.main()
