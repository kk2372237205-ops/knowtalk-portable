# -*- coding: utf-8 -*-
"""LLM 判分的测试（TDD：这些断言先于实现存在）。

判分器通过 `grader(sessionid, question, answer)` 注入编排器；
失败时**抛出异常**，由编排器统一降级（降级只写在一处）。
"""

import unittest
from unittest import mock

import utils.quiz_grade as quiz_grade
from utils.quiz_grade import (
    LLM_MAX_RETRIES,
    LLM_TIMEOUT,
    VERDICT_CORRECT,
    VERDICT_MISSING,
    VERDICT_PARTIAL,
    QuizGradeError,
    build_grading_prompt,
    grade_answer,
    parse_verdict,
)

QUESTION = {
    "id": "q1",
    "question": "铁路客流量统计的作用是什么？",
    "reference_answer": "用于评估运力与调度安排。",
    "rubric": ["提到运力评估", "提到调度安排"],
}


class ParseVerdictTestCase(unittest.TestCase):
    def test_plain_json(self):
        verdict, feedback = parse_verdict(
            '{"verdict": "correct", "feedback": "回答正确，两个要点都答到了。"}'
        )
        self.assertEqual(verdict, VERDICT_CORRECT)
        self.assertIn("回答正确", feedback)

    def test_code_fenced_json(self):
        raw = '```json\n{"verdict": "partial", "feedback": "答到了一点。"}\n```'
        self.assertEqual(parse_verdict(raw)[0], VERDICT_PARTIAL)

    def test_json_embedded_in_prose(self):
        raw = '判定结果：{"verdict": "missing", "feedback": "没有覆盖要点。"} 完毕'
        self.assertEqual(parse_verdict(raw)[0], VERDICT_MISSING)

    def test_chinese_verdict_is_normalized(self):
        self.assertEqual(parse_verdict('{"verdict": "正确", "feedback": "好"}')[0], VERDICT_CORRECT)
        self.assertEqual(parse_verdict('{"verdict": "部分正确", "feedback": "一般"}')[0], VERDICT_PARTIAL)
        self.assertEqual(parse_verdict('{"verdict": "未覆盖", "feedback": "不对"}')[0], VERDICT_MISSING)

    def test_wrong_is_treated_as_missing(self):
        self.assertEqual(parse_verdict('{"verdict": "wrong", "feedback": "错"}')[0], VERDICT_MISSING)

    def test_chinese_wrong_is_treated_as_missing(self):
        for alias in ("错误", "不正确"):
            self.assertEqual(
                parse_verdict('{"verdict": "%s", "feedback": "错"}' % alias)[0],
                VERDICT_MISSING, alias,
            )

    def test_verdict_is_case_insensitive(self):
        for raw in ("CORRECT", "Correct", "PARTIAL", "Missing", "  correct  "):
            verdict, _ = parse_verdict('{"verdict": "%s", "feedback": "x"}' % raw)
            self.assertIn(verdict, (VERDICT_CORRECT, VERDICT_PARTIAL, VERDICT_MISSING),
                          "大小写不得影响归一: %s" % raw)
        self.assertEqual(parse_verdict('{"verdict": "CORRECT", "feedback": "x"}')[0], VERDICT_CORRECT)
        self.assertEqual(parse_verdict('{"verdict": "Partial", "feedback": "x"}')[0], VERDICT_PARTIAL)
        self.assertEqual(parse_verdict('{"verdict": "Missing", "feedback": "x"}')[0], VERDICT_MISSING)

    def test_every_alias_maps_to_its_declared_verdict(self):
        """逐个钉住全部别名，避免某个别名映射错了却无人察觉。"""
        from utils.quiz_grade import _VERDICT_ALIASES
        expected = {
            VERDICT_CORRECT: {"correct", "right", "true", "正确", "完全正确"},
            VERDICT_PARTIAL: {"partial", "partially", "part", "部分正确", "部分"},
            VERDICT_MISSING: {"missing", "wrong", "incorrect", "false", "未覆盖", "错误", "不正确"},
        }
        declared = set()
        for verdict, aliases in expected.items():
            for alias in aliases:
                declared.add(alias)
                self.assertEqual(
                    parse_verdict('{"verdict": "%s", "feedback": "x"}' % alias)[0],
                    verdict, "别名 %s 应归一到 %s" % (alias, verdict),
                )
        self.assertEqual(declared, set(_VERDICT_ALIASES),
                         "测试必须覆盖声明过的每一个别名")

    def test_unknown_verdict_is_rejected(self):
        with self.assertRaises(QuizGradeError):
            parse_verdict('{"verdict": "maybe", "feedback": "说不清"}')

    def test_missing_verdict_field_is_rejected(self):
        with self.assertRaises(QuizGradeError):
            parse_verdict('{"feedback": "只有反馈"}')

    def test_non_json_is_rejected(self):
        with self.assertRaises(QuizGradeError):
            parse_verdict("模型直接说了人话，没有 JSON")

    def test_empty_output_is_rejected(self):
        with self.assertRaises(QuizGradeError):
            parse_verdict("")

    def test_missing_feedback_gets_a_default_line(self):
        verdict, feedback = parse_verdict('{"verdict": "correct"}')
        self.assertEqual(verdict, VERDICT_CORRECT)
        self.assertTrue(feedback.strip(), "反馈语不得为空，否则学员什么都听不到")

    def test_feedback_is_trimmed(self):
        _, feedback = parse_verdict('{"verdict": "correct", "feedback": "  有内容  "}')
        self.assertEqual(feedback, "有内容")


class BuildPromptTestCase(unittest.TestCase):
    def test_prompt_contains_question_answer_rubric_and_reply(self):
        prompt = build_grading_prompt(QUESTION, "我答的是运力")
        self.assertIn(QUESTION["question"], prompt)
        self.assertIn(QUESTION["reference_answer"], prompt)
        self.assertIn("提到运力评估", prompt)
        self.assertIn("我答的是运力", prompt)

    def test_prompt_survives_missing_rubric(self):
        prompt = build_grading_prompt({"question": "Q", "reference_answer": "A"}, "回答")
        self.assertIn("Q", prompt)


class GradeAnswerTestCase(unittest.TestCase):
    def setUp(self):
        # 判分客户端按配置缓存在模块级：测试各用例都要拿自己的 mock 客户端，
        # 不清缓存就会互相串（第二个用例拿到第一个用例的 mock）。
        quiz_grade.reset_client_cache()

    def _client(self, content):
        completion = type("C", (), {})()
        completion.choices = [type("Ch", (), {
            "message": type("M", (), {"content": content})()
        })()]
        client = mock.MagicMock()
        client.chat.completions.create.return_value = completion
        return client

    def _store(self, **values):
        defaults = {"llm.api_key": "sk-x", "llm.base_url": "https://x/v1", "llm.model": "grader-model"}
        defaults.update(values)
        store = mock.MagicMock()
        store.get_str.side_effect = lambda key: defaults.get(key, "")
        return store

    def test_rejects_blank_answer(self):
        """空作答必须在**调用模型之前**就被拒绝，且错误信息要指明原因。"""
        client = self._client('{"verdict": "correct", "feedback": "不该走到这里"}')
        with mock.patch("utils.quiz_grade.get_store", return_value=self._store()):
            with mock.patch("openai.OpenAI", return_value=client) as ctor:
                for blank in ("", "   ", "\n\t "):
                    with self.assertRaises(QuizGradeError) as ctx:
                        grade_answer(sessionid="s1", question=QUESTION, answer=blank)
                    self.assertIn("为空", str(ctx.exception),
                                  "必须是「作答为空」这一类错误，而不是别的原因导致的失败")
                self.assertEqual(ctor.call_count, 0, "空作答不得触发任何模型调用")

    def test_missing_api_key_is_reported(self):
        with mock.patch("utils.quiz_grade.get_store", return_value=self._store(**{"llm.api_key": ""})):
            with self.assertRaises(QuizGradeError) as ctx:
                grade_answer(sessionid="s1", question=QUESTION, answer="回答")
        self.assertIn("API Key", str(ctx.exception))

    def test_successful_grading_uses_settings_and_is_not_streamed(self):
        client = self._client('{"verdict": "correct", "feedback": "很好"}')
        with mock.patch("utils.quiz_grade.get_store", return_value=self._store()):
            with mock.patch("openai.OpenAI", return_value=client):
                result = grade_answer(sessionid="s1", question=QUESTION, answer="回答")
        self.assertEqual(result["verdict"], VERDICT_CORRECT)
        self.assertEqual(result["feedback"], "很好")
        self.assertFalse(result["degraded"])
        kwargs = client.chat.completions.create.call_args[1]
        self.assertEqual(kwargs["model"], "grader-model", "模型名必须来自运行期设置")
        self.assertFalse(kwargs["stream"], "判分必须是非流式，才能拿到完整结论")
        self.assertIn("回答", kwargs["messages"][1]["content"])

    def test_client_gets_a_timeout(self):
        client = self._client('{"verdict": "correct", "feedback": "好"}')
        with mock.patch("utils.quiz_grade.get_store", return_value=self._store()):
            with mock.patch("openai.OpenAI", return_value=client) as ctor:
                grade_answer(sessionid="s1", question=QUESTION, answer="回答")
        self.assertIsNotNone(ctor.call_args[1].get("timeout"), "判分必须带超时")

    def test_llm_failure_is_raised_for_caller_to_degrade(self):
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("上游 500")
        with mock.patch("utils.quiz_grade.get_store", return_value=self._store()):
            with mock.patch("openai.OpenAI", return_value=client):
                with self.assertRaises(QuizGradeError):
                    grade_answer(sessionid="s1", question=QUESTION, answer="回答")

    def test_unparseable_output_is_raised(self):
        client = self._client("模型没说 JSON")
        with mock.patch("utils.quiz_grade.get_store", return_value=self._store()):
            with mock.patch("openai.OpenAI", return_value=client):
                with self.assertRaises(QuizGradeError):
                    grade_answer(sessionid="s1", question=QUESTION, answer="回答")

    def test_empty_choices_is_raised(self):
        client = mock.MagicMock()
        client.chat.completions.create.return_value = type("C", (), {"choices": []})()
        with mock.patch("utils.quiz_grade.get_store", return_value=self._store()):
            with mock.patch("openai.OpenAI", return_value=client):
                with self.assertRaises(QuizGradeError):
                    grade_answer(sessionid="s1", question=QUESTION, answer="回答")


class ClientReuseTestCase(unittest.TestCase):
    """现场"判分慢"的一部分是每次重新建连（TLS 握手实测 +2s）。"""

    def setUp(self):
        quiz_grade.reset_client_cache()

    def _store(self):
        defaults = {"llm.api_key": "sk-x", "llm.base_url": "https://x/v1", "llm.model": "m"}
        store = mock.MagicMock()
        store.get_str.side_effect = lambda key: defaults.get(key, "")
        return store

    def _client(self):
        completion = type("C", (), {})()
        completion.choices = [type("Ch", (), {
            "message": type("M", (), {"content": '{"verdict": "correct", "feedback": "好"}'})()
        })()]
        client = mock.MagicMock()
        client.chat.completions.create.return_value = completion
        return client

    def test_client_is_reused_across_calls(self):
        client = self._client()
        with mock.patch("utils.quiz_grade.get_store", return_value=self._store()):
            with mock.patch("openai.OpenAI", return_value=client) as ctor:
                grade_answer(sessionid="s1", question=QUESTION, answer="回答")
                grade_answer(sessionid="s1", question=QUESTION, answer="再回答")
        self.assertEqual(ctor.call_count, 1, "配置未变化时必须复用同一个客户端")

    def test_client_is_rebuilt_when_config_changes(self):
        with mock.patch("utils.quiz_grade.get_store", return_value=self._store()):
            with mock.patch("openai.OpenAI", return_value=self._client()) as ctor:
                grade_answer(sessionid="s1", question=QUESTION, answer="回答")
                quiz_grade.reset_client_cache()
                grade_answer(sessionid="s1", question=QUESTION, answer="回答")
        self.assertEqual(ctor.call_count, 2)

    def test_client_has_capped_timeout_and_retries(self):
        client = self._client()
        with mock.patch("utils.quiz_grade.get_store", return_value=self._store()):
            with mock.patch("openai.OpenAI", return_value=client) as ctor:
                grade_answer(sessionid="s1", question=QUESTION, answer="回答")
        kwargs = ctor.call_args.kwargs
        self.assertEqual(kwargs.get("timeout"), LLM_TIMEOUT)
        self.assertEqual(kwargs.get("max_retries"), LLM_MAX_RETRIES)
        # 上界必须是一个"课堂等得起"的数：单次 ≤ 20s、总等待 ≤ 40s
        self.assertLessEqual(LLM_TIMEOUT, 20)
        self.assertLessEqual(LLM_TIMEOUT * (LLM_MAX_RETRIES + 1), 40)


class GraderWorksWithOrchestratorTestCase(unittest.TestCase):
    """判分器的签名必须与编排器注入约定一致。"""

    def setUp(self):
        quiz_grade.reset_client_cache()

    def test_signature_matches_orchestrator_call(self):
        client = mock.MagicMock()
        completion = type("C", (), {})()
        completion.choices = [type("Ch", (), {
            "message": type("M", (), {
                "content": '{"verdict": "partial", "feedback": "部分正确"}'
            })()
        })()]
        client.chat.completions.create.return_value = completion
        store = mock.MagicMock()
        store.get_str.side_effect = lambda key: {
            "llm.api_key": "sk-x", "llm.base_url": "https://x/v1", "llm.model": "m",
        }.get(key, "")

        with mock.patch("utils.quiz_grade.get_store", return_value=store):
            with mock.patch("openai.OpenAI", return_value=client):
                # 编排器就是这样调用的
                result = grade_answer(sessionid="s1", question=QUESTION, answer="回答")

        self.assertEqual(set(["verdict", "feedback", "degraded"]).issubset(result), True)


if __name__ == "__main__":
    unittest.main()
