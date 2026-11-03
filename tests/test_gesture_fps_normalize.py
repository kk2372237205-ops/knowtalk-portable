# -*- coding: utf-8 -*-
"""动作素材按输出帧率重采样：保证动作按原始速度播放，不被输出帧率拖慢。

素材由外部视频导入（MiniMax 出片 24fps），播放端是"每输出一帧前进一帧"，两者不等
就会变速：24fps 的 4 秒动作在 20fps 输出下会播成 5.3 秒。对应
openspec/changes/support-gesture-in-musetalk/ 的「动作素材按原始速度播放」。
"""

import json
import os
import pickle
import shutil
import tempfile
import unittest
from unittest import mock

import cv2
import numpy as np
import torch

from utils import gestures as g


def write_png(path, width=32, height=48, value=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image = np.full((height, width, 3), value, dtype=np.uint8)
    cv2.imwrite(path, image)


def make_gesture(root, avatar_id='Mus', gesture_id='wave', frames=107, meta=None):
    base = os.path.join(root, avatar_id, 'gestures', gesture_id)
    for index in range(frames):
        # 每帧像素值不同，便于断言重采样取到的是哪一帧
        write_png(os.path.join(base, 'full_imgs', '%08d.png' % index), value=index % 251)
        write_png(os.path.join(base, 'face_imgs', '%08d.png' % index), 16, 16)
    with open(os.path.join(base, 'coords.pkl'), 'wb') as handle:
        pickle.dump([(index, index + 10, index, index + 10) for index in range(frames)], handle)
    if meta is not None:
        with open(os.path.join(base, 'gesture.json'), 'w', encoding='utf-8') as handle:
            json.dump(meta, handle, ensure_ascii=False)
    return base


def add_muse_assets(base, frames=1):
    """造一套共享人脸资产——长度恒为 1，与画面帧数无关。"""
    muse = os.path.join(base, 'musetalk')
    os.makedirs(os.path.join(muse, 'mask'), exist_ok=True)
    for index in range(frames):
        write_png(os.path.join(muse, 'mask', '%08d.png' % index), 8, 8)
    with open(os.path.join(muse, 'coords.pkl'), 'wb') as handle:
        pickle.dump([(index, index, index + 10, index + 10) for index in range(frames)], handle)
    with open(os.path.join(muse, 'mask_coords.pkl'), 'wb') as handle:
        pickle.dump([(index, index, index + 12, index + 12) for index in range(frames)], handle)
    torch.save([torch.full((1, 8, 4, 4), float(index)) for index in range(frames)],
               os.path.join(muse, 'latents.pt'))
    with open(os.path.join(muse, 'asset.json'), 'w', encoding='utf-8') as handle:
        json.dump({'mode': 'shared'}, handle)
    return muse


class PlaybackIndicesTest(unittest.TestCase):
    def test_downsample_keeps_duration(self):
        # 107 帧 @24fps ≈ 4.46 秒；20fps 输出应得 89 帧（而不是原样 107 帧）
        indices = g.playback_indices(107, 24.0, 20.0)
        self.assertEqual(len(indices), 89)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 106)

    def test_downsample_is_monotonic(self):
        indices = g.playback_indices(107, 24.0, 20.0)
        self.assertEqual(indices, sorted(indices))
        self.assertTrue(all(0 <= index <= 106 for index in indices))

    def test_upsample_duplicates_frames(self):
        indices = g.playback_indices(100, 20.0, 25.0)
        self.assertEqual(len(indices), 125)
        self.assertEqual(indices[-1], 99)

    def test_same_fps_is_identity(self):
        self.assertEqual(g.playback_indices(30, 25.0, 25.0), list(range(30)))

    def test_unknown_fps_is_identity(self):
        self.assertEqual(g.playback_indices(10, 0, 20.0), list(range(10)))
        self.assertEqual(g.playback_indices(10, 24.0, None), list(range(10)))

    def test_empty_sequence(self):
        self.assertEqual(g.playback_indices(0, 24.0, 20.0), [])


class SourceFpsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='gesture-fps-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.object(g, 'avatars_root', lambda data_dir=None: self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_meta_fps_wins(self):
        make_gesture(self.tmp, frames=3, meta={'label': 'x', 'fps': 30})
        self.assertEqual(g.gesture_source_fps('Mus', 'wave', data_dir=self.tmp), 30.0)

    def test_default_when_meta_missing(self):
        make_gesture(self.tmp, frames=3)
        self.assertEqual(g.gesture_source_fps('Mus', 'wave', data_dir=self.tmp),
                         g.DEFAULT_SOURCE_FPS)

    def test_default_when_meta_fps_invalid(self):
        make_gesture(self.tmp, frames=3, meta={'label': 'x', 'fps': 'abc'})
        self.assertEqual(g.gesture_source_fps('Mus', 'wave', data_dir=self.tmp),
                         g.DEFAULT_SOURCE_FPS)


class LoadGestureResampleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='gesture-fps-load-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.object(g, 'avatars_root', lambda data_dir=None: self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_frames_faces_coords_stay_aligned(self):
        make_gesture(self.tmp, frames=107)
        frames, faces, coords = g.load_gesture('Mus', 'wave', data_dir=self.tmp, target_fps=20)
        self.assertEqual(len(frames), 89)
        self.assertEqual(len(faces), 89)
        self.assertEqual(len(coords), 89)
        # 首帧/末帧必须保留：动作起手与"回到体侧"的收尾都不能丢
        self.assertEqual(coords[0][0], 0)
        self.assertEqual(coords[-1][0], 106)

    def test_no_target_fps_keeps_raw_frames(self):
        make_gesture(self.tmp, frames=107)
        frames, _faces, coords = g.load_gesture('Mus', 'wave', data_dir=self.tmp)
        self.assertEqual(len(frames), 107)
        self.assertEqual(len(coords), 107)

    def test_same_fps_keeps_raw_frames(self):
        make_gesture(self.tmp, frames=107, meta={'label': 'x', 'fps': 20})
        frames, _faces, _coords = g.load_gesture('Mus', 'wave', data_dir=self.tmp, target_fps=20)
        self.assertEqual(len(frames), 107)


class LoadMuseAssetsResampleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='gesture-fps-muse-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.object(g, 'avatars_root', lambda data_dir=None: self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_face_assets_stay_single_after_resampling(self):
        """画面帧重采样到 89 帧，人脸资产仍只有 1 份——脸型不随动作帧变化。"""
        base = make_gesture(self.tmp, frames=107, meta={'fps': 24})
        add_muse_assets(base, frames=1)
        payload = g.load_muse_assets('Mus', 'wave', data_dir=self.tmp, target_fps=20)
        for key in ('latents', 'masks', 'mask_coords', 'coords'):
            self.assertEqual(len(payload[key]), 1, key)

    def test_no_target_fps_keeps_single_asset(self):
        base = make_gesture(self.tmp, frames=107)
        add_muse_assets(base, frames=1)
        payload = g.load_muse_assets('Mus', 'wave', data_dir=self.tmp)
        self.assertEqual(len(payload['latents']), 1)
        self.assertEqual(float(payload['latents'][0].flatten()[0]), 0.0)


if __name__ == '__main__':
    unittest.main()
