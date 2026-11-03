# -*- coding: utf-8 -*-
"""提问点选取、随机复现与绑定校验的测试（TDD：这些断言先于实现存在）。"""

import unittest

from utils.quiz_plan import (
    QuizConfigError,
    assign_inline_questions,
    assign_questions,
    candidate_positions,
    normalize_inline_questions,
    select_points,
    validate_bindings,
)


def seg(text, slide=None):
    return (text, slide)


class CandidatePositionsTestCase(unittest.TestCase):
    """3.1 候选位置推导：默认落在幻灯片边界；无标签时退化为段落边界。"""

    def test_slide_boundaries_use_last_segment_of_each_slide(self):
        segments = [seg("第一页开头", 1), seg("第一页结尾", 1),
                    seg("第二页内容", 2), seg("第三页内容", 3)]
        self.assertEqual(candidate_positions(segments), [1, 2, 3])

    def test_untagged_script_falls_back_to_segment_boundaries(self):
        segments = [seg("一"), seg("二"), seg("三")]
        self.assertEqual(candidate_positions(segments), [0, 1, 2])

    def test_leading_untagged_segment_is_its_own_candidate(self):
        segments = [seg("开场白"), seg("第一页", 1), seg("第一页续", 1)]
        # 开场白（无标签）自成一个候选点；第一页的边界在其最后一段
        self.assertEqual(candidate_positions(segments), [0, 2])

    def test_repeated_slide_only_yields_one_candidate(self):
        segments = [seg("A", 1), seg("B", 2), seg("C", 1)]
        # 幻灯片 1 在末尾再次出现，其边界是索引 2；幻灯片 2 的边界是索引 1
        self.assertEqual(candidate_positions(segments), [1, 2])

    def test_empty_segments_yield_no_candidates(self):
        self.assertEqual(candidate_positions([]), [])


class SelectPointsTestCase(unittest.TestCase):
    """3.2 三种选取方式与默认值。"""

    CANDIDATES = [0, 1, 2, 3, 4, 5]

    def test_random_is_default_mode(self):
        points = select_points(self.CANDIDATES, count=2, seed=1234)
        self.assertEqual(len(points), 2)
        self.assertTrue(set(points).issubset(set(self.CANDIDATES)))

    def test_interval_takes_every_nth_candidate(self):
        # 间隔 2 => 第 2、4、6 个候选位置（1-based）=> 索引 1、3、5
        points = select_points(self.CANDIDATES, mode="interval", interval=2)
        self.assertEqual(points, [1, 3, 5])

    def test_interval_with_count_caps_result(self):
        points = select_points(self.CANDIDATES, mode="interval", interval=2, count=2)
        self.assertEqual(points, [1, 3])

    def test_explicit_positions_are_returned_as_given(self):
        points = select_points(self.CANDIDATES, mode="explicit", explicit=[4, 1])
        self.assertEqual(points, [1, 4], "结果应排序且不多不少")

    def test_explicit_outside_candidates_is_rejected(self):
        with self.assertRaises(QuizConfigError) as ctx:
            select_points(self.CANDIDATES, mode="explicit", explicit=[99])
        self.assertIn("99", str(ctx.exception))

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(QuizConfigError):
            select_points(self.CANDIDATES, mode="nonsense", count=1)

    def test_interval_must_be_positive(self):
        with self.assertRaises(QuizConfigError):
            select_points(self.CANDIDATES, mode="interval", interval=0)


class ReproducibleRandomTestCase(unittest.TestCase):
    """3.3 随机必须可复现：不得依赖全局随机状态。"""

    CANDIDATES = list(range(20))

    def test_same_seed_gives_same_points(self):
        first = select_points(self.CANDIDATES, count=5, seed=42)
        second = select_points(self.CANDIDATES, count=5, seed=42)
        self.assertEqual(first, second)

    def test_different_seed_generally_gives_different_points(self):
        first = select_points(self.CANDIDATES, count=5, seed=1)
        second = select_points(self.CANDIDATES, count=5, seed=2)
        self.assertNotEqual(first, second)

    def test_does_not_consume_global_random_state(self):
        import random
        random.seed(7)
        expected_next = random.random()

        random.seed(7)
        select_points(self.CANDIDATES, count=5, seed=42)
        self.assertEqual(random.random(), expected_next,
                         "选取提问点不得消耗全局随机状态")

    def test_missing_seed_still_works(self):
        points = select_points(self.CANDIDATES, count=3)
        self.assertEqual(len(points), 3)


class CountAndRangeTestCase(unittest.TestCase):
    """3.4 幻灯片范围限定与数量不足的拒绝。"""

    CANDIDATES = [0, 1, 2, 3, 4, 5]

    def test_count_larger_than_candidates_is_rejected_with_available_count(self):
        with self.assertRaises(QuizConfigError) as ctx:
            select_points(self.CANDIDATES, count=10, seed=1)
        self.assertIn("6", str(ctx.exception), "错误信息必须给出可选数量")

    def test_zero_or_negative_count_is_rejected(self):
        with self.assertRaises(QuizConfigError):
            select_points(self.CANDIDATES, count=0, seed=1)

    def test_slide_range_limits_candidates(self):
        segments = [seg("一", 1), seg("二", 2), seg("三", 3), seg("四", 4), seg("五", 5)]
        points = select_points(segments, count=2, seed=3, slide_range=(3, 5))
        # 幻灯片 3..5 对应索引 2..4；返回的仍是候选位置
        self.assertTrue(set(points).issubset({2, 3, 4}))
        self.assertEqual(len(points), 2)

    def test_slide_range_with_no_candidates_is_rejected(self):
        segments = [seg("一", 1), seg("二", 2)]
        with self.assertRaises(QuizConfigError):
            select_points(segments, count=1, seed=1, slide_range=(7, 9))


class ValidateBindingsTestCase(unittest.TestCase):
    """3.5 绑定表必须校验且冲突必须被指明。"""

    CANDIDATES = [0, 1, 2, 3]

    def test_valid_binding_passes(self):
        validate_bindings({1: "q1", 2: "q2"}, {"q1", "q2"}, self.CANDIDATES, {})

    def test_unknown_question_is_rejected_and_named(self):
        with self.assertRaises(QuizConfigError) as ctx:
            validate_bindings({1: "missing"}, {"q1"}, self.CANDIDATES, {})
        self.assertIn("missing", str(ctx.exception))

    def test_slide_outside_candidates_is_rejected_and_named(self):
        with self.assertRaises(QuizConfigError) as ctx:
            validate_bindings({9: "q1"}, {"q1"}, self.CANDIDATES, {})
        self.assertIn("9", str(ctx.exception))

    def test_same_question_bound_twice_is_rejected(self):
        with self.assertRaises(QuizConfigError) as ctx:
            validate_bindings({1: "q1", 2: "q1"}, {"q1"}, self.CANDIDATES, {})
        self.assertIn("q1", str(ctx.exception))

    def test_question_slide_does_not_constrain_binding(self):
        # 题目自带 slide_id=1，但被绑到幻灯片 3 —— 必须被接受
        bank = {"q1": {"id": "q1", "slide_id": 1}}
        validate_bindings({3: "q1"}, {"q1"}, self.CANDIDATES, bank)


class AssignQuestionsTestCase(unittest.TestCase):
    """3.6 绑定优先：被绑定的提问点使用绑定题目，其余按选取方式取。

    绑定表以 **幻灯片编号** 为键；它作用于“幻灯片等于该编号”的那个候选提问点。
    """

    def test_binding_wins_over_selection(self):
        points = [0, 1, 2, 3]
        slide_of = {0: 1, 1: 2, 2: 3, 3: 4}
        bindings = {1: "qA", 3: "qB"}      # 幻灯片 1 -> qA，幻灯片 3 -> qB
        result = assign_questions(points, bindings, ["qA", "qB", "qC", "qD"],
                                  slide_of=slide_of, seed=5)
        self.assertEqual(result[0], "qA", "幻灯片 1 的候选点是索引 0")
        self.assertEqual(result[2], "qB", "幻灯片 3 的候选点是索引 2")
        self.assertNotIn(result[1], {"qA", "qB"}, "未绑定的点不得复用绑定题目")
        self.assertNotIn(result[3], {"qA", "qB"})

    def test_unbound_points_never_reuse_bound_questions(self):
        points = [0, 1, 2]
        bindings = {1: "qA"}
        available = ["qA", "qB", "qC"]
        result = assign_questions(points, bindings, available,
                                  slide_of={0: 1, 1: 2, 2: 3}, seed=9)
        values = [result[p] for p in points]
        self.assertEqual(len(set(values)), 3, "一次讲课内题目不得重复")
        self.assertEqual(result[0], "qA")

    def test_insufficient_questions_is_rejected_with_count(self):
        with self.assertRaises(QuizConfigError) as ctx:
            assign_questions([0, 1, 2], {}, ["qA"],
                             slide_of={0: 1, 1: 2, 2: 3}, seed=1)
        self.assertIn("1", str(ctx.exception))

    def test_binding_to_unknown_question_is_rejected(self):
        with self.assertRaises(QuizConfigError) as ctx:
            assign_questions([0, 1], {1: "missing"}, ["qA", "qB"],
                             slide_of={0: 1, 1: 2}, seed=1)
        self.assertIn("missing", str(ctx.exception))

    def test_reproducible_assignment(self):
        args = ([0, 1, 2, 3], {}, ["qA", "qB", "qC", "qD"])
        kwargs = dict(slide_of={0: 1, 1: 2, 2: 3, 3: 4}, seed=77)
        self.assertEqual(assign_questions(*args, **kwargs),
                         assign_questions(*args, **kwargs))


class NormalizeInlineQuestionsTestCase(unittest.TestCase):
    """11.1 内联问答对的规范化与校验。"""

    def test_valid_pairs_are_normalized(self):
        items = normalize_inline_questions([
            {"question": "第一题？", "reference_answer": "答案一", "rubric": "要点A"},
            {"question": "第二题？", "reference_answer": "答案二", "slide_id": "3"},
        ])
        self.assertEqual([item["id"] for item in items], ["inline-1", "inline-2"])
        self.assertEqual(items[0]["rubric"], ["要点A"], "评判要点是字符串时应归一为数组")
        self.assertIsNone(items[0]["slide_id"])
        self.assertEqual(items[1]["slide_id"], 3, "页码是字符串时必须转成整数")
        self.assertEqual(items[0]["source"], "inline", "内联题目不得被当成题库条目")

    def test_missing_question_names_the_index(self):
        with self.assertRaises(QuizConfigError) as ctx:
            normalize_inline_questions([
                {"question": "第一题？", "reference_answer": "答案一"},
                {"question": "   ", "reference_answer": "答案二"},
            ])
        self.assertIn("2", str(ctx.exception), "必须指明是第几个问答对缺题干")

    def test_missing_reference_answer_names_the_index(self):
        with self.assertRaises(QuizConfigError) as ctx:
            normalize_inline_questions([
                {"question": "第一题？", "reference_answer": "答案一"},
                {"question": "第二题？"},
            ])
        self.assertIn("2", str(ctx.exception), "必须指明是第几个问答对缺参考答案")

    def test_empty_list_is_rejected(self):
        with self.assertRaises(QuizConfigError):
            normalize_inline_questions([])

    def test_non_list_is_rejected(self):
        with self.assertRaises(QuizConfigError):
            normalize_inline_questions("不是数组")

    def test_non_object_entry_is_rejected(self):
        with self.assertRaises(QuizConfigError):
            normalize_inline_questions(["字符串"])


class AssignInlineQuestionsTestCase(unittest.TestCase):
    """11.2 内联问答对决定提问数量与顺序，可指定幻灯片。"""

    SEGMENTS = [seg("第一页", 1), seg("第二页", 2), seg("第三页", 3), seg("第四页", 4)]

    @staticmethod
    def pairs(count, slides=None):
        items = []
        for index in range(count):
            items.append({
                "id": "inline-%d" % (index + 1),
                "question": "第%d题？" % (index + 1),
                "reference_answer": "答案%d" % (index + 1),
                "rubric": [],
                "slide_id": (slides or {}).get(index + 1),
                "source": "inline",
            })
        return items

    def test_quiz_only_mode_asks_in_given_order(self):
        points, assignments = assign_inline_questions([], self.pairs(3))
        self.assertEqual(points, [0, 1, 2], "没有讲稿时每个问答对自成一个提问点")
        self.assertEqual([assignments[point] for point in points],
                         ["inline-1", "inline-2", "inline-3"])

    def test_point_count_equals_pair_count(self):
        points, assignments = assign_inline_questions(self.SEGMENTS, self.pairs(2), seed=3)
        self.assertEqual(len(points), 2, "提问数量由问答对数量决定")
        self.assertEqual(len(set(assignments.values())), 2)

    def test_order_is_preserved_for_pairs_without_slide(self):
        points, assignments = assign_inline_questions(self.SEGMENTS, self.pairs(3), seed=5)
        self.assertEqual([assignments[point] for point in sorted(points)],
                         ["inline-1", "inline-2", "inline-3"])

    def test_slide_binding_is_honored(self):
        points, assignments = assign_inline_questions(
            self.SEGMENTS, self.pairs(3, slides={1: 3}), seed=5)
        self.assertIn(2, points, "幻灯片 3 对应索引 2")
        self.assertEqual(assignments[2], "inline-1", "指定幻灯片的那一对必须问在该页")

    def test_two_pairs_on_same_slide_is_rejected(self):
        with self.assertRaises(QuizConfigError) as ctx:
            assign_inline_questions(self.SEGMENTS,
                                    self.pairs(2, slides={1: 2, 2: 2}), seed=1)
        self.assertIn("幻灯片 2", str(ctx.exception), "必须指明冲突的幻灯片编号")

    def test_slide_outside_candidates_is_rejected(self):
        with self.assertRaises(QuizConfigError) as ctx:
            assign_inline_questions(self.SEGMENTS, self.pairs(1, slides={1: 9}), seed=1)
        self.assertIn("9", str(ctx.exception), "必须指明越界的幻灯片编号")

    def test_too_many_pairs_for_script_is_rejected_with_available_count(self):
        with self.assertRaises(QuizConfigError) as ctx:
            assign_inline_questions(self.SEGMENTS, self.pairs(9), seed=1)
        message = str(ctx.exception)
        self.assertIn("4", message, "必须说明讲稿里可用的提问点数量")
        self.assertIn("未指定幻灯片", message,
                      "必须说清是问答对太多，而不是抛一句泛泛的「抽取失败」")

    def test_reproducible_points(self):
        first = assign_inline_questions(self.SEGMENTS, self.pairs(2), seed=11)
        second = assign_inline_questions(self.SEGMENTS, self.pairs(2), seed=11)
        self.assertEqual(first, second)

    def test_explicit_mode_is_rejected(self):
        with self.assertRaises(QuizConfigError) as ctx:
            assign_inline_questions(self.SEGMENTS, self.pairs(2), mode="explicit")
        self.assertIn("不支持", str(ctx.exception),
                      "必须明确告诉调用方内联问答对不支持显式位置，而不是抛无关提示")

    def test_empty_pairs_is_rejected(self):
        with self.assertRaises(QuizConfigError):
            assign_inline_questions(self.SEGMENTS, [])

    def test_slide_range_limits_free_points(self):
        points, assignments = assign_inline_questions(
            self.SEGMENTS, self.pairs(2), seed=4, slide_range=(2, 3))
        self.assertTrue(set(points).issubset({1, 2}), "问答对不得落在限定范围之外")
        self.assertEqual(len(points), 2)


if __name__ == "__main__":
    unittest.main()
