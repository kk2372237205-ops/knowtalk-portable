# -*- coding: utf-8 -*-
"""形象「可用」判定：wav2lip 与 MuseTalk 两种素材形态都必须被判为 ready。

对应 openspec/changes/migrate-lipsync-to-musetalk/specs/avatar-management/spec.md
的「形象库列举与缩略图」MODIFIED 要求：MuseTalk 形象没有 face_imgs/，
不能因此被标成「生成未完成」。
"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import server.avatar_routes as ar


def call_loop(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def touch(path, payload=b'x'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(payload)


class AvatarMaterialReadyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='avatar-ready-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, 'avatars')
        os.makedirs(self.root)
        patcher = mock.patch.object(ar, 'DATA_DIR', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ── 造素材 ────────────────────────────────────────────────────────
    def make_wav2lip(self, aid, *, coords=True, face=True, image=True):
        base = os.path.join(self.root, aid)
        os.makedirs(base, exist_ok=True)
        if image:
            touch(os.path.join(base, 'full_imgs', '00000000.png'))
        if coords:
            touch(os.path.join(base, 'coords.pkl'))
        if face:
            touch(os.path.join(base, 'face_imgs', '00000000.png'))
        return base

    def make_musetalk(self, aid, *, coords=True, mask=True, latents=True, mask_coords=True):
        base = os.path.join(self.root, aid)
        os.makedirs(base, exist_ok=True)
        touch(os.path.join(base, 'full_imgs', '00000000.png'))
        if coords:
            touch(os.path.join(base, 'coords.pkl'))
        if latents:
            touch(os.path.join(base, 'latents.pt'))
        if mask_coords:
            touch(os.path.join(base, 'mask_coords.pkl'))
        if mask:
            touch(os.path.join(base, 'mask', '00000000.png'))
        return base

    def list_avatars(self):
        resp = call_loop(ar.list_avatars(None))
        body = json.loads(resp.text)
        self.assertEqual(body.get('code'), 0, resp.text)
        return {item['id']: item for item in body['data']}

    # ── wav2lip 形态（既有行为不能变） ─────────────────────────────────
    def test_wav2lip_material_is_ready(self):
        self.make_wav2lip('ke01')
        self.assertTrue(self.list_avatars()['ke01']['ready'])
        self.assertEqual(self.list_avatars()['ke01']['status'], 'ready')

    def test_wav2lip_without_face_imgs_is_incomplete(self):
        self.make_wav2lip('half', face=False)
        item = self.list_avatars()['half']
        self.assertFalse(item['ready'])
        self.assertEqual(item['status'], 'incomplete')

    # ── MuseTalk 形态（本次新增） ──────────────────────────────────────
    def test_musetalk_material_is_ready_without_face_imgs(self):
        base = self.make_musetalk('musetalk_ke')
        self.assertFalse(os.path.isdir(os.path.join(base, 'face_imgs')))
        item = self.list_avatars()['musetalk_ke']
        self.assertTrue(item['ready'], item)
        self.assertEqual(item['status'], 'ready')

    def test_musetalk_material_without_mask_is_incomplete(self):
        self.make_musetalk('no_mask', mask=False)
        self.assertFalse(self.list_avatars()['no_mask']['ready'])

    def test_musetalk_material_without_latents_is_incomplete(self):
        self.make_musetalk('no_latents', latents=False)
        self.assertFalse(self.list_avatars()['no_latents']['ready'])

    # ── 共同约束 ──────────────────────────────────────────────────────
    def test_missing_coords_is_incomplete_for_both_forms(self):
        self.make_wav2lip('w_no_coords', coords=False)
        self.make_musetalk('m_no_coords', coords=False)
        listed = self.list_avatars()
        self.assertFalse(listed['w_no_coords']['ready'])
        self.assertFalse(listed['m_no_coords']['ready'])

    def test_directory_without_images_is_not_listed(self):
        base = os.path.join(self.root, 'empty')
        os.makedirs(base, exist_ok=True)
        touch(os.path.join(base, 'coords.pkl'))
        self.assertNotIn('empty', self.list_avatars())

    # ── 与当前运行模型匹配（前端只把 ready 的形象放进下拉框） ─────────────
    def set_model(self, model):
        patcher = mock.patch.object(ar, 'ACTIVE_MODEL', model)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_under_musetalk_wav2lip_assets_are_incomplete(self):
        self.set_model('musetalk')
        self.make_wav2lip('ke01')
        self.make_musetalk('musetalk_ke')
        listed = self.list_avatars()
        self.assertFalse(listed['ke01']['ready'], 'musetalk 下不能把 wav2lip 形象标成可用')
        self.assertTrue(listed['musetalk_ke']['ready'])

    def test_under_wav2lip_musetalk_assets_are_incomplete(self):
        self.set_model('wav2lip')
        self.make_wav2lip('ke01')
        self.make_musetalk('musetalk_ke')
        listed = self.list_avatars()
        self.assertTrue(listed['ke01']['ready'])
        self.assertFalse(listed['musetalk_ke']['ready'], 'wav2lip 下不能把 musetalk 形象标成可用')

    def test_unknown_model_accepts_either_form(self):
        self.set_model(None)
        self.make_wav2lip('ke01')
        self.make_musetalk('musetalk_ke')
        listed = self.list_avatars()
        self.assertTrue(listed['ke01']['ready'])
        self.assertTrue(listed['musetalk_ke']['ready'])


if __name__ == '__main__':
    unittest.main()
