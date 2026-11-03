# -*- coding: utf-8 -*-
"""自动出题的解析与 LLM 调用封装测试。

说明：`utils/quiz_generate.py` 的实现先于本文件存在（一次 TDD 偏离），
因此这些测试额外做了一轮变异检查——把实现故意改坏，确认测试确实会失败，
以证明断言不是空转。
"""

import json
import unittest
from unittest import mock

from utils.quiz_generate import (
    QuizGenerateError,
    _build_prompt,
    _strip_code_fence,
    generate_questions,
    parse_questions,
)


class ParseQuestionsTestCase(unittest.TestCase):
    def test_plain_json_array(self):
        result = parse_questions('[{"question": "Q", "reference_answer": "A", "rubric": ["r"]}]')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["question"], "Q")
        self.assertEqual(result[0]["rubric"], ["r"])

    def test_code_fenced_json(self):
        raw = '```json\n[{"question": "Q", "reference_answer": "A"}]\n```'
        self.assertEqual(len(parse_questions(raw)), 1)

    def test_json_embedded_in_prose(self):
        raw = '好的，结果如下：[{"question": "Q", "reference_answer": "A"}] 完毕'
        self.assertEqual(len(parse_questions(raw)), 1)

    def test_object_wrapper_with_questions_key(self):
        raw = '{"questions": [{"question": "Q", "reference_answer": "A"}]}'
        self.assertEqual(len(parse_questions(raw)), 1)

    def test_string_rubric_becomes_list(self):
        raw = '[{"question": "Q", "reference_answer": "A", "rubric": "单条要点"}]'
        self.assertEqual(parse_questions(raw)[0]["rubric"], ["单条要点"])

    def test_items_missing_required_fields_are_dropped(self):
        raw = ('[{"question": "Q1", "reference_answer": "A1"},'
               ' {"question": "只有题干"}, {"reference_answer": "只有答案"}]')
        result = parse_questions(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["question"], "Q1")

    def test_all_items_invalid_raises(self):
        with self.assertRaises(QuizGenerateError) as ctx:
            parse_questions('[{"question": "只有题干"}]')
        self.assertIn("没有可用", ctx.exception.message if hasattr(ctx.exception, "message") else str(ctx.exception))

    def test_empty_output_raises(self):
        with self.assertRaises(QuizGenerateError):
            parse_questions("")

    def test_non_json_raises(self):
        with self.assertRaises(QuizGenerateError):
            parse_questions("模型胡说八道，没有 JSON")

    def test_json_object_without_array_raises(self):
        with self.assertRaises(QuizGenerateError):
            parse_questions('{"foo": "bar"}')

    def test_count_truncates(self):
        raw = json.dumps([{"question": "Q%d" % i, "reference_answer": "A"} for i in range(5)])
        self.assertEqual(len(parse_questions(raw, count=2)), 2)

    def test_whitespace_trimmed(self):
        raw = '[{"question": "  Q  ", "reference_answer": "  A  "}]'
        question = parse_questions(raw)[0]
        self.assertEqual(question["question"], "Q")
        self.assertEqual(question["reference_answer"], "A")


class StripCodeFenceTestCase(unittest.TestCase):
    def test_removes_json_fence(self):
        self.assertEqual(_strip_code_fence("```json\n[X]\n```"), "[X]")

    def test_removes_bare_fence(self):
        self.assertEqual(_strip_code_fence("```\n[X]\n```"), "[X]")

    def test_leaves_plain_text(self):
        self.assertEqual(_strip_code_fence("[X]"), "[X]")


class BuildPromptTestCase(unittest.TestCase):
    def test_includes_slide_text_and_count(self):
        prompt = _build_prompt("幻灯片内容", 3)
        self.assertIn("幻灯片内容", prompt)
        self.assertIn("3", prompt)

    def test_context_block_is_optional(self):
        without = _build_prompt("内容", 1)
        with_context = _build_prompt("内容", 1, context="知识库片段")
        self.assertNotIn("知识库片段", without)
        self.assertIn("知识库片段", with_context)


class GenerateQuestionsTestCase(unittest.TestCase):
    def _client(self, content):
        completion = type("C", (), {})()
        completion.choices = [type("Ch", (), {
            "message": type("M", (), {"content": content})()
        })()]
        client = mock.MagicMock()
        client.chat.completions.create.return_value = completion
        return client

    def test_rejects_blank_slide_text(self):
        with self.assertRaises(QuizGenerateError):
            generate_questions("   ", 1)

    def test_rejects_out_of_range_count(self):
        with self.assertRaises(QuizGenerateError):
            generate_questions("内容", 0)
        with self.assertRaises(QuizGenerateError):
            generate_questions("内容", 999)

    def test_missing_api_key_reports_clearly(self):
        with mock.patch("utils.quiz_generate.get_store") as store:
            store.return_value.get_str.return_value = ""
            with self.assertRaises(QuizGenerateError) as ctx:
                generate_questions("内容", 1)
        self.assertIn("API Key", str(ctx.exception))

    def test_successful_generation_uses_settings(self):
        client = self._client('[{"question": "Q", "reference_answer": "A"}]')
        with mock.patch("utils.quiz_generate.get_store") as store:
            store.return_value.get_str.side_effect = (
                lambda key: {"llm.api_key": "sk-x", "llm.base_url": "https://x/v1",
                             "llm.model": "m"}[key]
            )
            with mock.patch("openai.OpenAI", return_value=client):
                result = generate_questions("幻灯片文字", 1)
        self.assertEqual(len(result), 1)
        kwargs = client.chat.completions.create.call_args[1]
        self.assertEqual(kwargs["model"], "m", "模型名必须来自运行期设置")
        self.assertFalse(kwargs["stream"])
        self.assertIn("幻灯片文字", kwargs["messages"][1]["content"])

    def test_llm_failure_is_reported(self):
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("上游 500")
        with mock.patch("utils.quiz_generate.get_store") as store:
            store.return_value.get_str.return_value = "sk-x"
            with mock.patch("openai.OpenAI", return_value=client):
                with self.assertRaises(QuizGenerateError) as ctx:
                    generate_questions("内容", 1)
        self.assertIn("调用模型失败", str(ctx.exception))

    def test_invalid_model_output_is_reported(self):
        client = self._client("模型没有输出 JSON")
        with mock.patch("utils.quiz_generate.get_store") as store:
            store.return_value.get_str.return_value = "sk-x"
            with mock.patch("openai.OpenAI", return_value=client):
                with self.assertRaises(QuizGenerateError):
                    generate_questions("内容", 1)


if __name__ == "__main__":
    unittest.main()
