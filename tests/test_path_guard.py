# -*- coding: utf-8 -*-
"""utils/path_guard.py 的单元测试。

对应 openspec/changes/harden-file-paths-and-cors/tasks.md 第 1.1 项。
用例覆盖 Windows 与 Linux 两套语义：两种分隔符、大小写、跨盘符、符号链接。
"""

import os
import shutil
import tempfile
import unittest

from utils.path_guard import (
    PathGuardError,
    is_within,
    resolve_within,
    sanitize_filename,
    validate_filename,
)


def _make_symlink(target, link):
    """创建符号链接；平台或权限不允许时返回 False 让用例跳过。"""
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError, AttributeError):
        return False
    return True


class PathGuardTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='path-guard-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, 'data', 'voices')
        os.makedirs(self.root)
        self.sibling = os.path.join(self.tmp, 'data', 'voices2')
        os.makedirs(self.sibling)
        self.outside = os.path.join(self.tmp, 'outside.txt')
        with open(self.outside, 'w', encoding='utf-8') as handle:
            handle.write('secret')
        self.inside = os.path.join(self.root, 'x.wav')
        with open(self.inside, 'w', encoding='utf-8') as handle:
            handle.write('audio')

    # ── is_within / resolve_within ────────────────────────────────────────

    def test_path_inside_is_accepted(self):
        self.assertTrue(is_within(self.root, self.inside))
        self.assertEqual(resolve_within(self.root, self.inside),
                         os.path.realpath(self.inside))

    def test_root_itself_is_within(self):
        self.assertTrue(is_within(self.root, self.root))

    def test_relative_path_inside_is_accepted(self):
        relative = os.path.join('data', 'voices', 'x.wav')
        self.assertTrue(is_within(self.root, os.path.join(self.tmp, relative)))

    def test_parent_traversal_is_rejected(self):
        for candidate in (
            os.path.join(self.root, '..', '..', 'outside.txt'),
            os.path.join(self.root, '..', 'voices2'),
            os.path.join(self.root, '..', '..', '..', '..', 'outside.txt'),
        ):
            self.assertFalse(is_within(self.root, candidate), candidate)
            with self.assertRaises(PathGuardError):
                resolve_within(self.root, candidate)

    def test_absolute_path_outside_is_rejected(self):
        self.assertFalse(is_within(self.root, self.outside))
        with self.assertRaises(PathGuardError):
            resolve_within(self.root, self.outside)

    def test_sibling_with_shared_prefix_is_rejected(self):
        """data/voices2 与 data/voices 前缀相同，但必须判为不在之内。"""
        self.assertFalse(is_within(self.root, self.sibling))
        self.assertFalse(is_within(self.root, os.path.join(self.sibling, 'y.wav')))

    def test_symlink_pointing_outside_is_rejected(self):
        link = os.path.join(self.root, 'escape')
        if not _make_symlink(self.outside, link):
            self.skipTest('本平台/权限不支持创建符号链接')
        self.assertFalse(is_within(self.root, link))
        with self.assertRaises(PathGuardError):
            resolve_within(self.root, link)

    def test_file_behind_symlinked_parent_outside_is_rejected(self):
        link = os.path.join(self.tmp, 'datalink')
        if not _make_symlink(os.path.join(self.tmp, 'data'), link):
            self.skipTest('本平台/权限不支持创建符号链接')
        candidate = os.path.join(link, 'voices', 'x.wav')
        # 经 realpath 后等于 root 下的同一个文件，应当放行
        self.assertTrue(is_within(self.root, candidate))

    def test_nonexistent_target_inside_is_accepted(self):
        """创建场景：目标还不存在时也要能判定，且父目录的符号链接要被解开。"""
        candidate = os.path.join(self.root, 'not-created-yet.wav')
        self.assertTrue(is_within(self.root, candidate))
        self.assertEqual(resolve_within(self.root, candidate),
                         os.path.realpath(candidate))

    def test_nonexistent_target_outside_is_rejected(self):
        candidate = os.path.join(self.tmp, 'data', 'not-created.wav')
        self.assertFalse(is_within(self.root, candidate))

    def test_case_insensitive_on_windows(self):
        upper = os.path.join(self.tmp, 'DATA', 'VOICES', 'x.wav')
        if os.name == 'nt':
            self.assertTrue(is_within(self.root, upper))
        else:
            self.assertFalse(is_within(self.root, upper))

    def test_cross_drive_is_rejected_on_windows(self):
        if os.name != 'nt':
            self.skipTest('跨盘符语义只在 Windows 上成立')
        current = os.path.splitdrive(os.path.abspath(self.root))[0].upper()
        other = next('{0}:'.format(letter) for letter in 'ZYXWVU'
                     if '{0}:'.format(letter) != current)
        self.assertFalse(is_within(self.root, os.path.join(other, 'x.wav')))

    def test_backslash_separator_is_understood(self):
        candidate = self.root.replace(os.sep, '\\') + '\\x.wav'
        self.assertTrue(is_within(self.root, candidate))

    def test_empty_and_none_inputs_are_rejected(self):
        for candidate in (None, '', '   '):
            self.assertFalse(is_within(self.root, candidate))
            with self.assertRaises(PathGuardError):
                resolve_within(self.root, candidate)

    def test_missing_root_is_rejected(self):
        with self.assertRaises(PathGuardError):
            resolve_within('', self.inside)
        self.assertFalse(is_within('', self.inside))

    # ── sanitize_filename ─────────────────────────────────────────────────

    def test_sanitize_strips_directory_components(self):
        cases = {
            '../../x': 'x',
            '..\\..\\x': 'x',
            'a/b': 'b',
            'a\\b': 'b',
            '/etc/passwd': 'passwd',
            'C:\\Windows\\win.ini': 'win.ini',
            'dir/': 'dir',
        }
        for raw, expected in cases.items():
            self.assertEqual(sanitize_filename(raw), expected, raw)

    def test_sanitize_removes_forbidden_characters(self):
        self.assertEqual(sanitize_filename('a<b>c:d"e|f?g*h'), 'abcdefgh')
        self.assertEqual(sanitize_filename('tab\there'), 'tabhere')

    def test_sanitize_falls_back_for_unusable_names(self):
        for raw in (None, '', '   ', '.', '..', './', '../', '...', '/', '\\',
                    '\x00', '\x00\x01'):
            self.assertEqual(sanitize_filename(raw, 'upload'), 'upload', repr(raw))

    def test_sanitize_strips_control_characters_but_keeps_the_rest(self):
        """净化是"尽力保留"，不是"整串作废"。"""
        self.assertEqual(sanitize_filename('a\x00b'), 'ab')

    def test_sanitize_trims_trailing_dot_and_space(self):
        """Windows 会静默丢弃结尾的点和空格，返回值必须与实际落盘名一致。"""
        self.assertEqual(sanitize_filename('name. '), 'name')
        self.assertEqual(sanitize_filename('name.'), 'name')

    def test_sanitize_keeps_unicode_names(self):
        self.assertEqual(sanitize_filename('讲师甲.wav'), '讲师甲.wav')

    # ── validate_filename ─────────────────────────────────────────────────

    def test_validate_accepts_plain_names(self):
        for raw in ('讲师甲', 'alice', 'a-b_c.1', '声音 01'):
            self.assertEqual(validate_filename(raw), raw.strip())

    def test_validate_rejects_separators_and_control_characters(self):
        for raw in ('../../x', 'a/b', 'a\\b', 'a\x00b', 'a\tb', 'a:b', 'a|b',
                    'a?b', 'a*b', 'a"b', 'a<b', 'a>b'):
            with self.assertRaises(PathGuardError, msg=repr(raw)):
                validate_filename(raw)

    def test_validate_rejects_dot_entries_and_empty(self):
        for raw in (None, '', '   ', '.', '..'):
            with self.assertRaises(PathGuardError, msg=repr(raw)):
                validate_filename(raw)


if __name__ == '__main__':
    unittest.main()
