# -*- coding: utf-8 -*-
"""答案正文质量：提示词约束 + 兜底句噪声清理。

对应 openspec/changes/harden-rag-failure-reporting 的
「生成的答案 MUST 是面向学员的答案正文」。

背景：本机只能用 3B 级小模型（8B 与数字人抢显存必然 OOM）。小模型有两个毛病：
复述规则与片段编号、给实质答案后又照抄一句「课程资料中未涉及该内容。」。
前者靠提示词约束，后者靠服务端清理。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rag_service import generator


def make_chunks(text="内容", page=3, source="教材.pdf"):
    return [{
        "text": text,
        "metadata": {"source_file": source, "page": page, "section": "第一章"},
    }]


class PromptTestCase(unittest.TestCase):
    def test_prompt_forbids_fragment_markers(self):
        prompt = generator.RAG_PROMPT
        self.assertIn("片段", prompt, "必须明确禁止输出片段编号/标记")
        self.assertIn("不得推断", prompt, "必须明确禁止推断资料以外的内容")

    def test_prompt_forbids_preamble_and_analysis(self):
        prompt = generator.RAG_PROMPT
        self.assertIn("分析过程", prompt, "必须禁止输出分析过程")
        self.assertIn("开场白", prompt, "必须禁止客套开场白")

    def test_prompt_keeps_fixed_fallback_rule(self):
        """第 2 条那句固定回答必须保留——资料为空时靠它作答。"""
        self.assertIn("课程资料中未涉及该内容。", generator.RAG_PROMPT)


class AnswerCleanupTestCase(unittest.TestCase):
    def test_trailing_fallback_sentence_is_removed(self):
        with mock.patch.object(generator, "chat", return_value=(
                "变量是用于存储数据的标识符。\n\n课程资料中未涉及该内容。")):
            out = generator.generate_answer("什么是变量？", make_chunks())
        self.assertEqual(out["answer"], "变量是用于存储数据的标识符。")
        self.assertNotIn("未涉及该内容", out["answer"])

    def test_answer_that_is_only_the_fallback_is_kept(self):
        with mock.patch.object(generator, "chat", return_value="课程资料中未涉及该内容。"):
            out = generator.generate_answer("什么是变量？", make_chunks())
        self.assertEqual(out["answer"], "课程资料中未涉及该内容。")
        self.assertEqual(out["speak_text"], "课程资料中未涉及该内容。")

    def test_empty_model_reply_falls_back(self):
        with mock.patch.object(generator, "chat", return_value="   "):
            out = generator.generate_answer("什么是变量？", make_chunks())
        self.assertEqual(out["answer"], "课程资料中未涉及该内容。")

    def test_no_chunks_short_circuits_without_calling_model(self):
        with mock.patch.object(generator, "chat") as chat_spy:
            out = generator.generate_answer("什么是变量？", [])
        chat_spy.assert_not_called()
        self.assertEqual(out["answer"], "课程资料中未涉及该内容。")
        self.assertEqual(out["citations"], [])


if __name__ == "__main__":
    unittest.main()
