# -*- coding: utf-8 -*-
"""讲稿分段解析（[PPT:N] / [slide:N] / [GESTURE:<id>]）的测试。

对应 openspec/changes/add-gesture-module/tasks.md 第 2 组。
这些断言先于实现存在。
"""

import unittest

from server.routes import _parse_ppt_segments, _parse_segments


class MixedTagTestCase(unittest.TestCase):
    """2.1 两类标签在同一层解析，都作为段落边界且都不进入朗读文本。"""

    def test_gesture_and_ppt_mixed(self):
        segments = _parse_segments("[GESTURE:explain] 我们来分析这个问题。[PPT:3] 先看第三页。")
        self.assertEqual(segments, [
            ("我们来分析这个问题。", None, "explain"),
            ("先看第三页。", 3, None),
        ])

    def test_only_ppt_tag_matches_legacy_behavior(self):
        segments = _parse_segments("[PPT:1] 欢迎。[PPT:2] 今天介绍新产品。")
        self.assertEqual(segments, [
            ("欢迎。", 1, None),
            ("今天介绍新产品。", 2, None),
        ])

    def test_text_before_first_tag_has_no_slide_and_no_gesture(self):
        segments = _parse_segments("开场白。[PPT:2] 切换到第二页。")
        self.assertEqual(segments, [
            ("开场白。", None, None),
            ("切换到第二页。", 2, None),
        ])

    def test_only_gesture_tag(self):
        segments = _parse_segments("[GESTURE:wave] 大家好。")
        self.assertEqual(segments, [("大家好。", None, "wave")])

    def test_no_tags_returns_single_segment(self):
        self.assertEqual(_parse_segments("没有标签的一段话。"),
                         [("没有标签的一段话。", None, None)])

    def test_clean_text_contains_no_tags(self):
        segments = _parse_segments("[gesture:explain] 甲。[slide:2] 乙。[GESTURE:wave] 丙。")
        for text, _slide, _gesture in segments:
            self.assertNotIn('[', text)
            self.assertNotIn(']', text)

    def test_slide_tag_is_case_insensitive(self):
        self.assertEqual(_parse_segments("[slide:4] 内容"),
                         _parse_segments("[SLIDE:4] 内容"))

    def test_gesture_tag_name_is_case_insensitive(self):
        self.assertEqual(_parse_segments("[gesture:explain] 内容"),
                         _parse_segments("[GESTURE:explain] 内容"))

    def test_gesture_id_keeps_its_case(self):
        segments = _parse_segments("[GESTURE:Explain] 内容")
        self.assertEqual(segments[0][2], 'Explain')

    def test_gesture_tag_before_ppt_tag_attaches_to_following_text(self):
        segments = _parse_segments("[GESTURE:wave][PPT:2] 正文")
        self.assertEqual(segments, [("正文", 2, "wave")])


class MultipleGesturesTestCase(unittest.TestCase):
    """2.2 段内取第一个手势标签，其余一并移除；手势不跨段继承。"""

    def test_first_gesture_wins(self):
        segments = _parse_segments("[GESTURE:wave] 第一段。[GESTURE:point] 第二段。")
        self.assertEqual(segments, [
            ("第一段。", None, "wave"),
            ("第二段。", None, "point"),
        ])

    def test_second_gesture_in_same_segment_is_dropped(self):
        segments = _parse_segments("[GESTURE:wave][GESTURE:point] 内容")
        self.assertEqual(segments, [("内容", None, "wave")])

    def test_gesture_does_not_carry_to_next_segment(self):
        segments = _parse_segments("[GESTURE:explain] 第一段。[PPT:2] 第二段没有标签。")
        self.assertEqual(segments, [
            ("第一段。", None, "explain"),
            ("第二段没有标签。", 2, None),
        ])

    def test_trailing_gesture_without_text_is_dropped(self):
        segments = _parse_segments("第一段。[GESTURE:wave]")
        self.assertEqual(segments, [("第一段。", None, None)])


class EquivalenceTestCase(unittest.TestCase):
    """2.3 不含手势标签时分段结果与改动前逐项相同。"""

    def test_ppt_wrapper_returns_two_tuples(self):
        self.assertEqual(_parse_ppt_segments("[PPT:1] 甲。[GESTURE:wave] 乙。[PPT:2] 丙。"),
                         [("甲。", 1), ("乙。", 1), ("丙。", 2)])

    def test_gesture_tag_before_ppt_tag_keeps_slide_segmentation_identical(self):
        with_gesture = _parse_segments("[PPT:1] 甲。[GESTURE:wave][PPT:2] 乙。")
        without = _parse_segments("[PPT:1] 甲。[PPT:2] 乙。")
        self.assertEqual([(s[0], s[1]) for s in with_gesture], [(s[0], s[1]) for s in without])
        self.assertEqual([s[2] for s in with_gesture], [None, "wave"])

    def test_slide_of_each_segment_is_the_last_seen_ppt_tag(self):
        segments = _parse_segments("[PPT:1] 甲。[GESTURE:wave] 乙。[PPT:2] 丙。丁。")
        self.assertEqual([s[1] for s in segments], [1, 1, 2])
        self.assertEqual([s[0] for s in segments], ["甲。", "乙。", "丙。丁。"])

    def test_text_without_gesture_tags_carries_no_gesture(self):
        segments = _parse_segments("[PPT:1] 甲。[PPT:2] 乙。")
        self.assertEqual([s[2] for s in segments], [None, None])

    def test_untagged_script_equivalence(self):
        self.assertEqual(_parse_ppt_segments("一整段话。"), [("一整段话。", None)])
        self.assertEqual(_parse_segments("一整段话。"), [("一整段话。", None, None)])

    def test_duplicate_script_truncation_still_applies(self):
        doubled = "[PPT:1] 甲。[PPT:2] 乙。[PPT:1] 甲。[PPT:2] 乙。"
        self.assertEqual([s[1] for s in _parse_segments(doubled)], [1, 2])

    def test_duplicate_truncation_keeps_gesture_of_kept_segments(self):
        doubled = "[PPT:1] 甲。[PPT:2] 乙。[PPT:1] 甲。[GESTURE:point][PPT:2] 乙。"
        self.assertEqual(_parse_segments(doubled),
                         [("甲。", 1, None), ("乙。", 2, "point")])


if __name__ == '__main__':
    unittest.main()
