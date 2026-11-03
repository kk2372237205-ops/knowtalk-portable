# -*- coding: utf-8 -*-
"""MuseTalk 手势资产：就绪判定、读取、幂等补齐与"缺资产降级"的模型感知。

对应 openspec/changes/support-gesture-in-musetalk/。

关键语义：动作只贡献身体与姿态，**人脸参考资产恒为形象自己的那一份**（长度 1）。
动作素材是生成出来的视频，脸型会漂移（实测更胖），逐帧用手势帧的脸编码 latent
会让动作期间"换了张脸"。
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

import server.avatar_routes as avatar_routes
import server.routes as routes
from avatars.base_avatar import _indexed
from utils import gestures as g


def write_png(path, width=64, height=96, value=180):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, 1] = value
    cv2.imwrite(path, image)


def make_avatar(root, avatar_id='Mus', frames=1, latent_value=7.0):
    """造形象的 MuseTalk 基础资产：latents.pt / mask/ / mask_coords.pkl / coords.pkl。"""
    base = os.path.join(root, avatar_id)
    for index in range(frames):
        write_png(os.path.join(base, 'full_imgs', '%08d.png' % index), value=200)
        write_png(os.path.join(base, 'mask', '%08d.png' % index), 32, 32, value=255)
    with open(os.path.join(base, 'coords.pkl'), 'wb') as handle:
        pickle.dump([(20, 10, 50, 60)] * frames, handle)
    with open(os.path.join(base, 'mask_coords.pkl'), 'wb') as handle:
        pickle.dump([(18, 8, 52, 62)] * frames, handle)
    torch.save([torch.full((1, 8, 32, 32), latent_value)] * frames,
               os.path.join(base, 'latents.pt'))
    return base


def make_gesture(root, avatar_id='Mus', gesture_id='wave', frames=3):
    base = os.path.join(root, avatar_id, 'gestures', gesture_id)
    for index in range(frames):
        write_png(os.path.join(base, 'full_imgs', '%08d.png' % index))
        write_png(os.path.join(base, 'face_imgs', '%08d.png' % index), 48, 48)
    with open(os.path.join(base, 'coords.pkl'), 'wb') as handle:
        pickle.dump([(10, 60, 20, 50)] * frames, handle)
    return base


def add_muse_assets(base, frames=1, latent_value=3.0):
    """造一套「已就绪」的共享人脸资产（不跑模型）。"""
    muse = os.path.join(base, 'musetalk')
    os.makedirs(os.path.join(muse, 'mask'), exist_ok=True)
    for index in range(frames):
        write_png(os.path.join(muse, 'mask', '%08d.png' % index), 32, 32)
    with open(os.path.join(muse, 'coords.pkl'), 'wb') as handle:
        pickle.dump([(20, 10, 50, 60)] * frames, handle)
    with open(os.path.join(muse, 'mask_coords.pkl'), 'wb') as handle:
        pickle.dump([(18, 8, 52, 62)] * frames, handle)
    torch.save([torch.full((1, 8, 32, 32), latent_value)] * frames,
               os.path.join(muse, 'latents.pt'))
    with open(os.path.join(muse, 'asset.json'), 'w', encoding='utf-8') as handle:
        json.dump({'mode': 'shared'}, handle)
    return muse


def add_legacy_assets(base, frames=3):
    """造旧版逐帧资产（没有 asset.json）。"""
    muse = add_muse_assets(base, frames=frames)
    os.remove(os.path.join(muse, 'asset.json'))
    return muse


class MuseGestureAssetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='muse-gesture-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.object(g, 'avatars_root', lambda data_dir=None: self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ── 就绪判定与列举 ────────────────────────────────────────────────
    def test_not_ready_without_muse_dir(self):
        make_gesture(self.tmp)
        self.assertFalse(g.has_muse_assets('Mus', 'wave', data_dir=self.tmp))
        items = g.list_gestures('Mus', data_dir=self.tmp)
        self.assertFalse(items[0]['muse_ready'])

    def test_ready_after_assets_added(self):
        base = make_gesture(self.tmp)
        add_muse_assets(base)
        self.assertTrue(g.has_muse_assets('Mus', 'wave', data_dir=self.tmp))
        items = g.list_gestures('Mus', data_dir=self.tmp)
        self.assertTrue(items[0]['muse_ready'])

    def test_legacy_per_frame_assets_are_not_ready(self):
        """旧版逐帧资产没有 asset.json，必须判为未就绪以便自动迁移。"""
        base = make_gesture(self.tmp)
        add_legacy_assets(base, frames=3)
        self.assertFalse(g.has_muse_assets('Mus', 'wave', data_dir=self.tmp))

    def test_corrupt_meta_is_not_ready(self):
        base = make_gesture(self.tmp)
        muse = add_muse_assets(base)
        with open(os.path.join(muse, 'asset.json'), 'w', encoding='utf-8') as handle:
            handle.write('{not json')
        self.assertFalse(g.has_muse_assets('Mus', 'wave', data_dir=self.tmp))

    def test_load_muse_assets_returns_none_when_missing(self):
        make_gesture(self.tmp)
        self.assertIsNone(g.load_muse_assets('Mus', 'wave', data_dir=self.tmp))

    def test_load_muse_assets_returns_payload(self):
        base = make_gesture(self.tmp)
        add_muse_assets(base)
        payload = g.load_muse_assets('Mus', 'wave', data_dir=self.tmp)
        self.assertIsNotNone(payload)
        for key in ('latents', 'masks', 'mask_coords', 'coords'):
            self.assertEqual(len(payload[key]), 1, key)

    # ── 补齐：幂等且不动 wav2lip 素材 ─────────────────────────────────
    def test_build_is_idempotent(self):
        base = make_gesture(self.tmp)
        add_muse_assets(base)
        before = open(os.path.join(base, 'coords.pkl'), 'rb').read()
        result = g.build_muse_assets('Mus', 'wave', data_dir=self.tmp)
        self.assertTrue(result['skipped'], result)
        self.assertEqual(open(os.path.join(base, 'coords.pkl'), 'rb').read(), before)
        self.assertEqual(len(os.listdir(os.path.join(base, 'full_imgs'))), 3)

    def test_build_rejects_incomplete_gesture(self):
        base = make_gesture(self.tmp)
        shutil.rmtree(os.path.join(base, 'face_imgs'))
        with self.assertRaises(g.GestureError):
            g.build_muse_assets('Mus', 'wave', data_dir=self.tmp)

    # ── 受理阶段：musetalk 下缺资产按不可用处理 ───────────────────────
    def test_partition_requires_muse_assets_under_musetalk(self):
        make_gesture(self.tmp, gesture_id='wave')
        make_gesture(self.tmp, gesture_id='open')
        add_muse_assets(os.path.join(self.tmp, 'Mus', 'gestures', 'open'))

        patcher = mock.patch.object(avatar_routes, 'ACTIVE_MODEL', 'musetalk')
        patcher.start()
        self.addCleanup(patcher.stop)

        usable, missing, available = routes.partition_gestures('Mus', ['wave', 'open'])
        self.assertEqual(usable, ['open'])
        self.assertEqual(missing, ['wave'])
        self.assertEqual(available, ['open'])

    def test_partition_ignores_muse_requirement_under_wav2lip(self):
        make_gesture(self.tmp, gesture_id='wave')

        patcher = mock.patch.object(avatar_routes, 'ACTIVE_MODEL', 'wav2lip')
        patcher.start()
        self.addCleanup(patcher.stop)

        usable, missing, _ = routes.partition_gestures('Mus', ['wave'])
        self.assertEqual(usable, ['wave'])
        self.assertEqual(missing, [])


class SharedFaceAssetsTest(unittest.TestCase):
    """动作的人脸参考资产必须来自形象本身，且与动作帧数无关。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='muse-shared-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.object(g, 'avatars_root', lambda data_dir=None: self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _build(self, gesture_frames=3, avatar_frames=1, latent_value=7.0):
        make_avatar(self.tmp, frames=avatar_frames, latent_value=latent_value)
        base = make_gesture(self.tmp, frames=gesture_frames)
        result = g.build_muse_assets('Mus', 'wave', data_dir=self.tmp)
        return base, result

    def test_latent_copied_from_avatar(self):
        base, result = self._build(latent_value=7.0)
        self.assertFalse(result['skipped'], result)
        payload = g.load_muse_assets('Mus', 'wave', data_dir=self.tmp)
        self.assertEqual(len(payload['latents']), 1)
        self.assertTrue(torch.allclose(payload['latents'][0], torch.full((1, 8, 32, 32), 7.0)))

    def test_asset_count_is_one_regardless_of_gesture_frames(self):
        """动作 89 帧也只用 1 份人脸资产，否则脸型会随帧漂移。"""
        base, _ = self._build(gesture_frames=5)
        muse = os.path.join(base, 'musetalk')
        payload = torch.load(os.path.join(muse, 'latents.pt'))
        self.assertEqual(len(payload), 1)
        self.assertEqual(len(os.listdir(os.path.join(muse, 'mask'))), 1)
        for name in ('coords.pkl', 'mask_coords.pkl'):
            with open(os.path.join(muse, name), 'rb') as handle:
                self.assertEqual(len(pickle.load(handle)), 1, name)

    def test_mask_and_box_copied_from_avatar(self):
        base, _ = self._build()
        muse = os.path.join(base, 'musetalk')
        with open(os.path.join(muse, 'coords.pkl'), 'rb') as handle:
            self.assertEqual(pickle.load(handle), [(20, 10, 50, 60)])
        with open(os.path.join(muse, 'mask_coords.pkl'), 'rb') as handle:
            self.assertEqual(pickle.load(handle), [(18, 8, 52, 62)])

    def test_legacy_assets_are_migrated_and_masks_cleared(self):
        """旧版 3 帧遮罩必须被清掉，不能混进共享资产。"""
        make_avatar(self.tmp)
        base = make_gesture(self.tmp, frames=3)
        add_legacy_assets(base, frames=3)
        self.assertFalse(g.has_muse_assets('Mus', 'wave', data_dir=self.tmp))

        result = g.build_muse_assets('Mus', 'wave', data_dir=self.tmp)
        self.assertFalse(result['skipped'], result)
        self.assertTrue(g.has_muse_assets('Mus', 'wave', data_dir=self.tmp))
        self.assertEqual(len(os.listdir(os.path.join(base, 'musetalk', 'mask'))), 1)
        self.assertEqual(len(torch.load(os.path.join(base, 'musetalk', 'latents.pt'))), 1)

    def test_build_keeps_gesture_pieces_untouched(self):
        base, _ = self._build(gesture_frames=4)
        self.assertEqual(len(os.listdir(os.path.join(base, 'full_imgs'))), 4)
        self.assertEqual(len(os.listdir(os.path.join(base, 'face_imgs'))), 4)
        with open(os.path.join(base, 'coords.pkl'), 'rb') as handle:
            self.assertEqual(len(pickle.load(handle)), 4)

    def test_meta_records_shared_mode_and_face_box(self):
        base, _ = self._build()
        with open(os.path.join(base, 'musetalk', 'asset.json'), encoding='utf-8') as handle:
            meta = json.load(handle)
        self.assertEqual(meta['mode'], g.MUSE_ASSET_MODE_SHARED)
        self.assertEqual(meta['avatar_id'], 'Mus')
        self.assertEqual(meta['face_box'], [20, 10, 50, 60])

    def test_missing_avatar_assets_raise(self):
        make_gesture(self.tmp)
        with self.assertRaises(g.GestureError):
            g.build_muse_assets('Mus', 'wave', data_dir=self.tmp)

    def test_latent_is_index_invariant(self):
        """任意帧索引都必须取到同一份 latent —— 这是脸型恒定的前提。"""
        self._build(gesture_frames=4)
        payload = g.load_muse_assets('Mus', 'wave', data_dir=self.tmp)
        reference = payload['latents'][0]
        for idx in (0, 1, 37, 88):
            self.assertIs(_indexed(payload['latents'], idx), reference)
            self.assertIs(_indexed(payload['masks'], idx), payload['masks'][0])
            self.assertEqual(_indexed(payload['coords'], idx), payload['coords'][0])


if __name__ == '__main__':
    unittest.main()


class AutoBuildAfterGenerateTestCase(unittest.TestCase):
    """动作生成完必须顺手补齐 MuseTalk 资产。

    缺这一步，动作在 MuseTalk 形象上**不会播**：加载期抛错后静默降级成默认素材，
    界面一切正常、现场就是"配了动作却没反应"。这个坑很难从现象反推回原因，
    所以把"生成流程里必须调用补齐"钉成测试。
    """

    def test_build_assets_calls_muse_builder(self):
        from utils import gesture_make

        job = {'avatar_id': 'Mus', 'gesture_id': 'wave_one'}
        with mock.patch.object(gesture_make.gesture_utils, 'build_muse_assets',
                               return_value={'ready': True}) as builder:
            assert gesture_make._build_muse_assets(job) is True
        builder.assert_called_once_with('Mus', 'wave_one')

    def test_build_assets_failure_does_not_raise(self):
        from utils import gesture_make

        job = {'avatar_id': 'Mus', 'gesture_id': 'wave_one'}
        with mock.patch.object(gesture_make.gesture_utils, 'build_muse_assets',
                               side_effect=g.GestureError('缺资产')):
            assert gesture_make._build_muse_assets(job) is False

    def test_generate_pipeline_invokes_muse_builder(self):
        """拆帧 → 归一化 → 写元数据 → 补 MuseTalk 资产，顺序不能漏。"""
        import inspect

        from utils import gesture_make

        source = inspect.getsource(gesture_make._build_assets)
        assert '_build_muse_assets(job)' in source
