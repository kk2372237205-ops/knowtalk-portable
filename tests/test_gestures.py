# -*- coding: utf-8 -*-
"""手势素材发现、标识校验与加载归一化的测试（TDD：这些断言先于实现存在）。

对应 openspec/changes/add-gesture-module/tasks.md 第 1 组。
"""

import json
import os
import pickle
import shutil
import tempfile
import unittest

import cv2
import numpy as np

from utils.gestures import (
    GestureError,
    avatars_root,
    is_valid_gesture_id,
    list_gestures,
    load_gesture,
)


def write_png(path, width, height):
    """写一张纯色图，尺寸可控——用于断言归一化前后的大小。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, 1] = 180
    cv2.imwrite(path, image)


def make_gesture(root, avatar_id, gesture_id, frames=3, size=(64, 96),
                 face_size=(48, 48), coords=None, with_coords=True,
                 full_imgs=True, face_imgs=True, label=None):
    """在临时 data 目录下造一套手势素材。"""
    base = os.path.join(root, avatar_id, 'gestures', gesture_id)
    os.makedirs(base, exist_ok=True)
    if full_imgs:
        for index in range(frames):
            write_png(os.path.join(base, 'full_imgs', '%08d.png' % index), *size)
    if face_imgs:
        for index in range(frames):
            write_png(os.path.join(base, 'face_imgs', '%08d.png' % index), *face_size)
    if with_coords:
        boxes = coords or [(10, 40, 10 + i, 50 + i) for i in range(frames)]
        with open(os.path.join(base, 'coords.pkl'), 'wb') as handle:
            pickle.dump(boxes, handle)
    if label is not None:
        with open(os.path.join(base, 'gesture.json'), 'w', encoding='utf-8') as handle:
            json.dump({'label': label}, handle, ensure_ascii=False)
    return base


class TempDataCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='gesture-test-')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class ListGesturesTestCase(TempDataCase):
    """1.1 只保留三件套齐备的手势，显示名可被 gesture.json 覆盖。"""

    def test_complete_gesture_is_listed(self):
        make_gesture(self.root, 'Teacher', 'explain')
        self.assertEqual(
            list_gestures('Teacher', data_dir=self.root),
            [{'id': 'explain', 'label': 'explain', 'frames': 3, 'width': 64, 'height': 96,
             'muse_ready': False}],
        )

    def test_missing_coords_is_excluded(self):
        make_gesture(self.root, 'Teacher', 'explain')
        make_gesture(self.root, 'Teacher', 'broken', with_coords=False)
        self.assertEqual([g['id'] for g in list_gestures('Teacher', data_dir=self.root)], ['explain'])

    def test_empty_face_imgs_is_excluded(self):
        make_gesture(self.root, 'Teacher', 'explain')
        make_gesture(self.root, 'Teacher', 'empty_face', face_imgs=False)
        self.assertEqual([g['id'] for g in list_gestures('Teacher', data_dir=self.root)], ['explain'])

    def test_missing_gestures_dir_returns_empty(self):
        os.makedirs(os.path.join(self.root, 'Teacher', 'full_imgs'), exist_ok=True)
        self.assertEqual(list_gestures('Teacher', data_dir=self.root), [])

    def test_directory_name_outside_charset_is_skipped(self):
        make_gesture(self.root, 'Teacher', 'explain')
        for bad_name in ('bad name', '中文手势', 'a..b'):
            make_gesture(self.root, 'Teacher', bad_name)
        self.assertEqual([g['id'] for g in list_gestures('Teacher', data_dir=self.root)], ['explain'])

    def test_plain_file_in_gestures_dir_is_skipped(self):
        make_gesture(self.root, 'Teacher', 'explain')
        with open(os.path.join(self.root, 'Teacher', 'gestures', 'README.md'), 'w', encoding='utf-8') as handle:
            handle.write('not a gesture')
        self.assertEqual([g['id'] for g in list_gestures('Teacher', data_dir=self.root)], ['explain'])

    def test_label_from_gesture_json(self):
        make_gesture(self.root, 'Teacher', 'wave', label='挥手')
        self.assertEqual(
            list_gestures('Teacher', data_dir=self.root),
            [{'id': 'wave', 'label': '挥手', 'frames': 3, 'width': 64, 'height': 96,
             'muse_ready': False}],
        )

    def test_blank_label_falls_back_to_id(self):
        make_gesture(self.root, 'Teacher', 'wave', label='   ')
        self.assertEqual([g['label'] for g in list_gestures('Teacher', data_dir=self.root)], ['wave'])

    def test_broken_gesture_json_falls_back_to_id(self):
        base = make_gesture(self.root, 'Teacher', 'wave', label='挥手')
        with open(os.path.join(base, 'gesture.json'), 'w', encoding='utf-8') as handle:
            handle.write('{ 这不是 json')
        self.assertEqual([g['label'] for g in list_gestures('Teacher', data_dir=self.root)], ['wave'])

    def test_order_is_stable_and_sorted_by_id(self):
        for gesture_id in ('zeta', 'alpha', 'mid'):
            make_gesture(self.root, 'Teacher', gesture_id)
        first = [g['id'] for g in list_gestures('Teacher', data_dir=self.root)]
        second = [g['id'] for g in list_gestures('Teacher', data_dir=self.root)]
        self.assertEqual(first, ['alpha', 'mid', 'zeta'])
        self.assertEqual(first, second)


class GestureIdTestCase(unittest.TestCase):
    """1.2 标识只接受 A-Za-z0-9_.- 且拒绝路径穿越。"""

    def test_accepts_declared_charset(self):
        for gesture_id in ('explain', 'wave_2', 'wave-2', 'a.b', 'ABC123'):
            self.assertTrue(is_valid_gesture_id(gesture_id), gesture_id)

    def test_rejects_separators_and_spaces(self):
        for gesture_id in ('a/b', 'a\\b', 'a b', 'a\tb'):
            self.assertFalse(is_valid_gesture_id(gesture_id), gesture_id)

    def test_rejects_traversal_and_empty(self):
        for gesture_id in ('..', 'a..b', '', '.', None, 5):
            self.assertFalse(is_valid_gesture_id(gesture_id), repr(gesture_id))

    def test_rejects_unicode_and_punctuation(self):
        for gesture_id in ('挥手', 'wave!', 'wave@2'):
            self.assertFalse(is_valid_gesture_id(gesture_id), gesture_id)


class LoadGestureTestCase(TempDataCase):
    """1.3 加载三件套；尺寸与默认素材不一致时归一化并告警。"""

    def test_loads_pieces_in_numeric_frame_order(self):
        coords = [(1, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12)]
        make_gesture(self.root, 'Teacher', 'explain', frames=3, coords=coords)
        frames, faces, loaded_coords = load_gesture(
            'Teacher', 'explain', data_dir=self.root, ref_size=(64, 96), ref_face_size=(48, 48))
        self.assertEqual(len(frames), 3)
        self.assertEqual(len(faces), 3)
        self.assertEqual(loaded_coords, coords)
        self.assertEqual((frames[0].shape[1], frames[0].shape[0]), (64, 96))
        self.assertEqual((faces[0].shape[1], faces[0].shape[0]), (48, 48))

    def test_normalizes_size_and_scales_coords(self):
        # 手势素材 32×48，默认素材 64×96：宽高各放大一倍
        make_gesture(self.root, 'Teacher', 'explain', frames=2, size=(32, 48),
                     face_size=(24, 24), coords=[(10, 20, 4, 12), (0, 0, 0, 0)])
        with self.assertLogs('utils.logger', level='WARNING') as captured:
            frames, faces, coords = load_gesture(
                'Teacher', 'explain', data_dir=self.root,
                ref_size=(64, 96), ref_face_size=(48, 48))
        self.assertEqual((frames[0].shape[1], frames[0].shape[0]), (64, 96))
        self.assertEqual((faces[0].shape[1], faces[0].shape[0]), (48, 48))
        # 人脸框是像素坐标，缩放后必须是整数——浮点会让下游 cv2.resize 逐帧失败。
        self.assertEqual(coords, [(20, 40, 8, 24), (0, 0, 0, 0)])
        for box in coords:
            for value in box:
                self.assertIsInstance(value, int, box)
        self.assertTrue(any('归一化' in message for message in captured.output), captured.output)

    def test_normalized_coords_are_usable_as_resize_target(self):
        """回归：归一化后的框必须能直接当 cv2.resize 的 dsize。

        曾因缩放产生浮点而逐帧失败，表现成"手势期间嘴不动"，只留一条 warning。
        这条用例直接消费真实归一化结果去调用 cv2.resize，把那种静默失败钉住。
        """
        import cv2
        import numpy as np

        make_gesture(self.root, 'Teacher', 'explain', frames=2, size=(536, 1080),
                     face_size=(24, 24), coords=[(82, 252, 203, 330), (0, 0, 0, 0)])
        _frames, faces, coords = load_gesture(
            'Teacher', 'explain', data_dir=self.root,
            ref_size=(538, 1080), ref_face_size=(96, 96))
        y1, y2, x1, x2 = coords[0]
        w, h = x2 - x1, y2 - y1
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)
        # 不抛 cv2.error 即为通过
        resized = cv2.resize(faces[0].astype(np.uint8), (w, h),
                             interpolation=cv2.INTER_LANCZOS4)
        self.assertEqual(resized.shape[:2], (h, w))

    def test_matching_size_does_not_warn(self):
        make_gesture(self.root, 'Teacher', 'explain', frames=2, size=(64, 96),
                     face_size=(48, 48))
        records = []
        import logging

        class Collect(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Collect()
        logging.getLogger('utils.logger').addHandler(handler)
        try:
            load_gesture('Teacher', 'explain', data_dir=self.root,
                         ref_size=(64, 96), ref_face_size=(48, 48))
        finally:
            logging.getLogger('utils.logger').removeHandler(handler)
        self.assertFalse([r for r in records if '归一化' in r.getMessage()])

    def test_without_reference_size_keeps_original(self):
        make_gesture(self.root, 'Teacher', 'explain', frames=2, size=(32, 48))
        frames, _faces, _coords = load_gesture('Teacher', 'explain', data_dir=self.root)
        self.assertEqual((frames[0].shape[1], frames[0].shape[0]), (32, 48))

    def test_invalid_id_raises(self):
        with self.assertRaises(GestureError):
            load_gesture('Teacher', '../etc', data_dir=self.root)

    def test_incomplete_gesture_raises(self):
        make_gesture(self.root, 'Teacher', 'broken', with_coords=False)
        with self.assertRaises(GestureError):
            load_gesture('Teacher', 'broken', data_dir=self.root)


class RootPathTestCase(unittest.TestCase):
    def test_avatars_root_points_at_data_avatars(self):
        self.assertTrue(avatars_root().endswith(os.path.join('data', 'avatars')))


if __name__ == '__main__':
    unittest.main()
