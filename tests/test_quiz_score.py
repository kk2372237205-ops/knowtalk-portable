# -*- coding: utf-8 -*-
"""课堂总评分的口径（变更 improve-quiz-mode-ux 任务 8.1）。

口径写在规格里：正确 100 / 部分正确 60 / 未覆盖或未作答 0，取算术平均。
这些数字一旦漂移，学员看到的分数就会变，必须有测试钉住。
"""

import unittest

from utils.quiz_score import format_summary, summarize


def rec(verdict=None, unanswered=False, degraded=False):
    return {"sessionid": "s1", "question_id": "q", "verdict": verdict,
            "unanswered": unanswered, "degraded": degraded}


class SummarizeTestCase(unittest.TestCase):
    def test_all_correct_is_full_score(self):
        summary = summarize([rec("correct"), rec("correct")])
        self.assertEqual(summary["score"], 100)
        self.assertEqual(summary["correct"], 2)
        self.assertEqual(summary["total"], 2)

    def test_correct_plus_partial_is_eighty(self):
        summary = summarize([rec("correct"), rec("partial")])
        self.assertEqual(summary["score"], 80, "（100+60）/2 = 80")

    def test_partial_only_is_sixty(self):
        self.assertEqual(summarize([rec("partial")])["score"], 60)

    def test_missing_is_zero(self):
        summary = summarize([rec("missing"), rec("missing")])
        self.assertEqual(summary["score"], 0)
        self.assertEqual(summary["missing"], 2)

    def test_unanswered_counts_zero_and_is_counted_separately(self):
        summary = summarize([rec("correct"), rec(unanswered=True)])
        self.assertEqual(summary["score"], 50, "（100+0）/2 = 50")
        self.assertEqual(summary["unanswered"], 1)
        self.assertEqual(summary["answered"], 1)

    def test_degraded_without_verdict_counts_zero_but_is_reported(self):
        summary = summarize([rec("correct"), rec(None, degraded=True)])
        self.assertEqual(summary["score"], 50)
        self.assertEqual(summary["degraded"], 1)
        self.assertEqual(summary["missing"], 1, "没有结论的题按未覆盖计")

    def test_rounding(self):
        # 100 + 60 + 60 = 220 / 3 = 73.33 → 73
        summary = summarize([rec("correct"), rec("partial"), rec("partial")])
        self.assertEqual(summary["score"], 73)
        # 100 + 100 + 60 = 260 / 3 = 86.67 → 87
        self.assertEqual(summarize([rec("correct"), rec("correct"), rec("partial")])["score"], 87)

    def test_empty_records_yield_no_score(self):
        self.assertIsNone(summarize([]), "没有作答就不评分，不能给 0 分")
        self.assertIsNone(summarize(None))

    def test_ignores_non_dict_items(self):
        summary = summarize([rec("correct"), "脏数据", None])
        self.assertEqual(summary["total"], 1)


class FormatSummaryTestCase(unittest.TestCase):
    def test_sentence_contains_score_and_counts(self):
        text = format_summary(summarize([rec("correct"), rec(unanswered=True)]))
        self.assertIn("共 2 题", text)
        self.assertIn("答对 1 题", text)
        self.assertIn("未作答 1 题", text)
        self.assertIn("得分 50 分", text)

    def test_skips_zero_count_parts(self):
        text = format_summary(summarize([rec("correct")]))
        self.assertNotIn("部分正确", text)
        self.assertNotIn("未作答", text)

    def test_mentions_degraded_grading(self):
        text = format_summary(summarize([rec(None, degraded=True)]))
        self.assertIn("判分服务不可用", text)

    def test_empty_summary_is_empty_text(self):
        self.assertEqual(format_summary(None), "")


if __name__ == "__main__":
    unittest.main()
