# -*- coding: utf-8 -*-
"""动作制作：预设、MiniMax 调用、入库归一化、接口。

所有网络调用都被替身挡掉——这些都是单元测试，不打真接口、不计费。
"""

import io
import json
import os
import pickle
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image

from server import gesture_make_routes
from utils import gesture_make, gesture_presets, minimax_video


class PresetTestCase(unittest.TestCase):
    def test_presets_have_unique_ids_and_labels(self):
        presets = gesture_presets.GESTURE_PRESETS
        # 只约束下限：预设是会继续加的（上一轮从 16 加到 18），
        # 断言"恰好 N 条"会让每次加动作都要改测试，反而没人愿意加。
        self.assertGreaterEqual(len(presets), 16)
        ids = [p['id'] for p in presets]
        self.assertEqual(len(set(ids)), len(ids))
        for preset in presets:
            self.assertTrue(preset['label'].strip())
            self.assertLessEqual(len(preset['label']), 40)
            self.assertGreater(len(preset['prompt']), 120)

    def test_idle_and_wave_presets_exist(self):
        """闲时与迎宾要靠这两套动作，缺了它们那两个下拉就是空的。"""
        by_id = {p['id']: p for p in gesture_presets.GESTURE_PRESETS}
        self.assertIn('idle_sway', by_id)
        self.assertIn('wave_one', by_id)

    def test_presets_carry_a_group(self):
        # 按「基础 / 强调与对比 / 互动与收尾 / 待机与问候」分组展示
        groups = ('基础', '强调与对比', '互动与收尾', '待机与问候')
        for preset in gesture_presets.GESTURE_PRESETS:
            self.assertIn(preset['group'], groups, preset['id'])

    def test_prompts_keep_the_three_hard_constraints(self):
        # 头部零位移 / 嘴闭合 / 首尾同姿态——少一条就会毁素材
        for preset in gesture_presets.GESTURE_PRESETS:
            text = preset['prompt']
            # 待机微摆允许躯干极轻微移动，头部约束写成「头部保持位置」，
            # 语义等价，不能只认一种措辞
            self.assertTrue('头部完全锁死' in text or '头部保持位置' in text, preset['id'])
            self.assertIn('嘴巴闭合', text, preset['id'])
            # 首尾同姿态便于循环：三种写法（回到体侧 / 末帧与首帧 / 末帧回到与首帧）
            self.assertTrue(any(k in text for k in ('末帧与首帧', '末帧回到与首帧', '回到体侧')),
                            preset['id'])

    def test_preset_lookup(self):
        self.assertEqual(gesture_presets.preset_by_id('chop')['label'], '切砍强调')
        self.assertIsNone(gesture_presets.preset_by_id('nope'))


class MinimaxCallTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(minimax_video, 'settings', return_value={
            'api_key': 'sk-test', 'base_url': 'https://api.example.com',
            'model': 'MiniMax-H3', 'resolution': '768P', 'duration': 5,
        })
        self.settings = patcher.start()
        self.addCleanup(patcher.stop)

    def test_create_task_payload(self):
        captured = {}

        class Resp:
            status_code = 200
            text = '{"task_id":"t-1"}'

            def json(self):
                return {'task_id': 't-1'}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update({'url': url, 'headers': headers, 'body': json})
            return Resp()

        with mock.patch('requests.post', fake_post):
            task_id = minimax_video.create_task('提示词', __file__)

        self.assertEqual(task_id, 't-1')
        self.assertTrue(captured['url'].endswith('/v2/video_generation'))
        self.assertEqual(captured['headers']['Authorization'], 'Bearer sk-test')
        body = captured['body']
        self.assertEqual(body['model'], 'MiniMax-H3')
        self.assertEqual(body['duration'], 5)
        self.assertEqual(body['resolution'], '768P')
        types = [item['type'] for item in body['content']]
        self.assertEqual(types, ['text', 'image_url'])
        self.assertEqual(body['content'][1]['role'], 'first_frame')
        self.assertTrue(body['content'][1]['image_url']['url'].startswith('data:image/'))

    def test_missing_key_raises_before_request(self):
        with mock.patch.object(minimax_video, 'settings',
                               return_value={'api_key': '', 'base_url': 'x',
                                             'model': 'm', 'resolution': '768P',
                                             'duration': 5}):
            with mock.patch('requests.post', side_effect=AssertionError('不该发请求')):
                with self.assertRaises(minimax_video.MinimaxNotConfigured):
                    minimax_video.create_task('p', __file__)

    def test_query_picks_task_by_id(self):
        payload = {'items': [{'id': 'other', 'status': 'running'},
                             {'id': 't-2', 'status': 'succeeded',
                              'content': {'url': 'https://v/x.mp4'}}]}

        class Resp:
            status_code = 200
            text = 'ok'

            def json(self):
                return payload

        with mock.patch('requests.get', lambda *a, **k: Resp()):
            info = minimax_video.query_task('t-2')
        self.assertEqual(info['status'], 'succeeded')
        self.assertEqual(info['url'], 'https://v/x.mp4')

    def test_wait_for_video_maps_states(self):
        calls = {'n': 0}

        def fake_query(task_id):
            calls['n'] += 1
            if calls['n'] == 1:
                return {'status': 'running', 'url': '', 'reason': ''}
            return {'status': 'succeeded', 'url': 'https://v/x.mp4', 'reason': ''}

        with mock.patch.object(minimax_video, 'query_task', fake_query), \
                mock.patch('time.sleep', lambda s: None):
            self.assertEqual(minimax_video.wait_for_video('t-3'), 'https://v/x.mp4')

        with mock.patch.object(minimax_video, 'query_task',
                               return_value={'status': 'failed', 'url': '',
                                             'reason': '余额不足'}):
            with self.assertRaises(minimax_video.MinimaxError) as ctx:
                minimax_video.wait_for_video('t-4')
            self.assertIn('余额不足', str(ctx.exception))


class NormalizeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.avatar = os.path.join(self.tmp, 'A')
        os.makedirs(os.path.join(self.avatar, 'full_imgs'))
        Image.new('RGB', (538, 1080), 'white').save(
            os.path.join(self.avatar, 'full_imgs', '00000000.png'))
        self.gesture = os.path.join(self.tmp, 'A', 'gestures', 'g1')
        os.makedirs(os.path.join(self.gesture, 'full_imgs'))
        for index in range(2):
            Image.new('RGB', (528, 1080), 'white').save(
                os.path.join(self.gesture, 'full_imgs', '%08d.png' % index))
        with open(os.path.join(self.gesture, 'coords.pkl'), 'wb') as handle:
            pickle.dump([(10, 200, 30, 90), (12, 202, 32, 92)], handle)

    def test_narrow_frames_are_padded_and_coords_shifted(self):
        old, new = gesture_make.normalize_size(self.avatar, self.gesture)
        self.assertEqual(old, (528, 1080))
        self.assertEqual(new, (538, 1080))
        with Image.open(os.path.join(self.gesture, 'full_imgs', '00000000.png')) as image:
            self.assertEqual(image.size, (538, 1080))
        with open(os.path.join(self.gesture, 'coords.pkl'), 'rb') as handle:
            coords = pickle.load(handle)
        # 528 → 538，两侧各补 5px，人脸框左边整体右移 5
        self.assertEqual(coords[0], (10, 200, 35, 95))

    def test_same_size_is_noop(self):
        with open(os.path.join(self.avatar, 'full_imgs', '00000000.png'), 'rb') as handle:
            before = handle.read()
        target = os.path.join(self.tmp, 'A', 'gestures', 'g2')
        shutil.copytree(self.gesture, target)
        for name in os.listdir(os.path.join(target, 'full_imgs')):
            Image.open(os.path.join(target, 'full_imgs', name)).resize((538, 1080)).save(
                os.path.join(target, 'full_imgs', name))
        old, new = gesture_make.normalize_size(self.avatar, target)
        self.assertEqual(old, new)
        with open(os.path.join(self.avatar, 'full_imgs', '00000000.png'), 'rb') as handle:
            self.assertEqual(handle.read(), before)


class JobGuardTestCase(unittest.TestCase):
    def test_free_gesture_id_avoids_collision(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(gesture_make.gesture_utils, 'avatars_root',
                               return_value=tmp):
            os.makedirs(os.path.join(tmp, 'A', 'gestures', 'open'))
            self.assertEqual(gesture_make._free_gesture_id('A', 'open'), 'open-2')
            self.assertEqual(gesture_make._free_gesture_id('A', 'chop'), 'chop')

    def test_unknown_avatar_rejected(self):
        with self.assertRaises(gesture_make.GestureMakeError):
            gesture_make.start('no-such-avatar', preset_id='open')

    def test_missing_prompt_and_name_rejected(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, 'A', 'full_imgs'))
        Image.new('RGB', (10, 10), 'white').save(os.path.join(tmp, 'A', 'full_imgs', '0.png'))
        with mock.patch.object(gesture_make.gesture_utils, 'avatars_root',
                               return_value=tmp):
            with self.assertRaises(gesture_make.GestureMakeError):
                gesture_make.start('A', preset_id=None, name='x', prompt='')
            with self.assertRaises(gesture_make.GestureMakeError):
                gesture_make.start('A', preset_id='open', name='名' * 41)

    def test_busy_avatar_rejected(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, 'A', 'full_imgs'))
        Image.new('RGB', (10, 10), 'white').save(os.path.join(tmp, 'A', 'full_imgs', '0.png'))
        fake_job = {'id': 'j1', 'avatar_id': 'A', 'stage_text': '正在生成动作视频'}
        with mock.patch.object(gesture_make.gesture_utils, 'avatars_root',
                               return_value=tmp), \
                mock.patch.object(gesture_make, '_active', {'A': fake_job}), \
                mock.patch.object(minimax_video, 'is_configured', return_value=True):
            with self.assertRaises(gesture_make.GestureMakeBusy):
                gesture_make.start('A', preset_id='open')


class RoutesTestCase(unittest.TestCase):
    """接口层只验参数校验与响应结构，不真的建作业。"""

    def test_presets_route_payload(self):
        import asyncio

        response = asyncio.run(gesture_make_routes.list_presets(None))
        body = json.loads(response.text)
        self.assertEqual(body['code'], 0)
        self.assertGreaterEqual(len(body['data']['presets']), 16)
        self.assertIn('configured', body['data'])

    def test_submit_rejects_empty_avatar(self):
        import asyncio

        request = mock.Mock()

        async def fake_json():
            return {}

        request.json = fake_json
        response = asyncio.run(gesture_make_routes.submit(request))
        self.assertIn('avatar_id', json.loads(response.text)['msg'])

    def test_submit_returns_409_when_busy(self):
        import asyncio

        request = mock.Mock()

        async def fake_json():
            return {'avatar_id': 'A', 'preset_id': 'open'}

        request.json = fake_json
        with mock.patch.object(gesture_make, 'start',
                               side_effect=gesture_make.GestureMakeBusy('已有作业')):
            response = asyncio.run(gesture_make_routes.submit(request))
        self.assertEqual(response.status, 409)
        self.assertIn('已有作业', json.loads(response.text)['msg'])


if __name__ == '__main__':
    unittest.main()
