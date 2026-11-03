# -*- coding: utf-8 -*-
"""头像生成任务的路径与回调加固测试。

对应 openspec/changes/harden-file-paths-and-cors/tasks.md 第 3 组。
沿用既有测试约定：直接调用处理函数并传入最小请求替身，不启动 aiohttp 服务。
"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import server.avatar_routes as avatar_routes


class FakePart(object):
    def __init__(self, name, filename=None, text='', chunks=()):
        self.name = name
        self.filename = filename
        self._text = text
        self._chunks = list(chunks)

    async def read_chunk(self):
        return self._chunks.pop(0) if self._chunks else b''

    async def text(self):
        return self._text


class FakeReader(object):
    def __init__(self, parts):
        self._parts = list(parts)

    async def next(self):
        return self._parts.pop(0) if self._parts else None


class FakeRequest(object):
    def __init__(self, body=None, parts=None, content_type='application/json'):
        self._body = body
        self._parts = parts
        self.content_type = content_type

    async def json(self):
        if self._body is None:
            raise ValueError('请求体不是合法 JSON')
        return self._body

    async def multipart(self):
        return FakeReader(self._parts or [])


class FakeTaskManager(object):
    def __init__(self):
        self.calls = []

    def add_task(self, model_type, avatar_id, params, task_id=None,
                 notify_url=None):
        self.calls.append({
            'model_type': model_type,
            'avatar_id': avatar_id,
            'params': params,
            'task_id': task_id,
            'notify_url': notify_url,
        })
        return task_id or 'auto-task-id'


def call(handler, request):
    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(handler(request))
    finally:
        loop.close()
    return json.loads(response.text), response


class AvatarGuardTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='avatar-guard-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tmp_dir = os.path.join(self.tmp, 'data', 'tmp')
        os.makedirs(self.tmp_dir)

        self.manager = FakeTaskManager()
        for name, value in (('TMP_DIR', self.tmp_dir),
                            ('task_manager', self.manager)):
            patcher = mock.patch.object(avatar_routes, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        # 允许根指向临时目录，避免依赖真实项目目录
        self.video_root = os.path.join(self.tmp, 'videos')
        os.makedirs(self.video_root)
        patcher = mock.patch.dict(os.environ,
                                  {'AVATAR_VIDEO_ROOTS': self.video_root})
        patcher.start()
        self.addCleanup(patcher.stop)

        # 回调允许列表默认清空：除非用例显式设置，否则一律拒绝
        patcher = mock.patch.dict(os.environ, {'AVATAR_NOTIFY_ALLOWED_HOSTS': ''})
        patcher.start()
        self.addCleanup(patcher.stop)

    def post_json(self, **body):
        body.setdefault('model', 'wav2lip')
        body.setdefault('avatar_id', 'Teacher')
        return call(avatar_routes.create_avatar_task, FakeRequest(body=body))

    def post_upload(self, filename, **extra):
        parts = [FakePart('video_file', filename=filename,
                          chunks=[b'fake-video-bytes'])]
        for key, value in extra.items():
            parts.append(FakePart(key, text=value))
        parts.append(FakePart('model', text='wav2lip'))
        parts.append(FakePart('avatar_id', text='Teacher'))
        return call(avatar_routes.create_avatar_task,
                    FakeRequest(parts=parts, content_type='multipart/form-data'))

    # ── 上传落盘 ──────────────────────────────────────────────────────────

    def test_upload_lands_inside_tmp_dir(self):
        body, _ = self.post_upload('lecture.mp4')
        self.assertEqual(body['code'], 0)
        written = os.listdir(self.tmp_dir)
        self.assertEqual(len(written), 1)
        self.assertTrue(written[0].endswith('_lecture.mp4'))

    def test_upload_filename_with_traversal_stays_inside_tmp_dir(self):
        for filename in ('..\\..\\evil.mp4', '../../evil.mp4',
                         '..\\evil.mp4', '/etc/evil.mp4',
                         'C:\\Windows\\evil.mp4'):
            body, _ = self.post_upload(filename)
            self.assertEqual(body['code'], 0, filename)
            written = os.listdir(self.tmp_dir)
            self.assertTrue(written, filename)
            for name in written:
                self.assertNotIn('..', name, filename)
                self.assertNotIn('/', name, filename)
                self.assertNotIn('\\', name, filename)
                self.assertFalse(os.path.isabs(name), filename)
            # 项目根之外不得出现 evil.mp4
            self.assertFalse(os.path.exists(os.path.join(self.tmp, 'evil.mp4')))
            for name in written:
                os.remove(os.path.join(self.tmp_dir, name))

    def test_unusable_filename_falls_back_to_default(self):
        body, _ = self.post_upload('..')
        self.assertEqual(body['code'], 0)
        written = os.listdir(self.tmp_dir)
        self.assertEqual(len(written), 1)
        self.assertTrue(written[0].endswith('_video.mp4'), written)
        os.remove(os.path.join(self.tmp_dir, written[0]))

    def test_upload_records_absolute_path_in_task_params(self):
        body, _ = self.post_upload('lecture.mp4')
        self.assertEqual(body['code'], 0)
        recorded = self.manager.calls[0]['params']['video_path']
        self.assertTrue(os.path.isabs(recorded))
        self.assertEqual(os.path.dirname(os.path.realpath(recorded)),
                         os.path.realpath(self.tmp_dir))

    # ── video_path ────────────────────────────────────────────────────────

    def test_absolute_video_path_inside_allowlist_is_accepted(self):
        inside = os.path.join(self.video_root, 'clip.mp4')
        with open(inside, 'wb') as handle:
            handle.write(b'x')
        body, _ = self.post_json(video_path=inside)
        self.assertEqual(body['code'], 0)
        self.assertEqual(self.manager.calls[0]['params']['video_path'], inside)

    def test_absolute_video_path_outside_allowlist_is_rejected(self):
        outside = os.path.join(self.tmp, 'outside.mp4')
        with open(outside, 'wb') as handle:
            handle.write(b'x')
        body, _ = self.post_json(video_path=outside)
        self.assertNotEqual(body['code'], 0)
        self.assertIn('AVATAR_VIDEO_ROOTS', body['msg'])
        self.assertEqual(self.manager.calls, [])

    def test_system_path_is_rejected(self):
        target = 'C:\\Windows\\win.ini' if os.name == 'nt' else '/etc/passwd'
        body, _ = self.post_json(video_path=target)
        self.assertNotEqual(body['code'], 0)
        self.assertEqual(self.manager.calls, [])

    def test_traversal_video_path_is_rejected(self):
        body, _ = self.post_json(video_path='../../../etc/passwd')
        self.assertNotEqual(body['code'], 0)
        self.assertEqual(self.manager.calls, [])

    def test_relative_video_path_keeps_original_semantics(self):
        """相对路径仍解析为 ./data/avatars/<name>，不被改写成绝对路径。"""
        body, _ = self.post_json(video_path='alice.mp4')
        self.assertEqual(body['code'], 0)
        self.assertEqual(self.manager.calls[0]['params']['video_path'],
                         os.path.join('./data/avatars', 'alice.mp4'))

    def test_allowlist_can_be_extended_by_env(self):
        extra = os.path.join(self.tmp, 'extra')
        os.makedirs(extra)
        clip = os.path.join(extra, 'clip.mp4')
        with open(clip, 'wb') as handle:
            handle.write(b'x')
        with mock.patch.dict(os.environ,
                             {'AVATAR_VIDEO_ROOTS':
                              '{0},{1}'.format(self.video_root, extra)}):
            body, _ = self.post_json(video_path=clip)
        self.assertEqual(body['code'], 0)

    def test_empty_allowlist_env_falls_back_to_default_roots(self):
        with mock.patch.dict(os.environ, {'AVATAR_VIDEO_ROOTS': ''}):
            roots = avatar_routes.allowed_video_roots()
        self.assertTrue(roots)
        for root in roots:
            self.assertTrue(os.path.isabs(root))

    # ── notifyurl ─────────────────────────────────────────────────────────

    def valid_body(self, **extra):
        clip = os.path.join(self.video_root, 'clip.mp4')
        if not os.path.exists(clip):
            with open(clip, 'wb') as handle:
                handle.write(b'x')
        body = {'model': 'wav2lip', 'avatar_id': 'Teacher', 'video_path': clip}
        body.update(extra)
        return body

    def test_notifyurl_rejected_when_allowlist_is_empty(self):
        body, _ = self.post_json(**self.valid_body(
            notifyurl='http://hook.example/task'))
        self.assertNotEqual(body['code'], 0)
        self.assertIn('AVATAR_NOTIFY_ALLOWED_HOSTS', body['msg'])
        self.assertEqual(self.manager.calls, [])

    def test_link_local_notifyurl_is_rejected(self):
        body, _ = self.post_json(**self.valid_body(
            notifyurl='http://169.254.169.254/latest/meta-data'))
        self.assertNotEqual(body['code'], 0)
        self.assertEqual(self.manager.calls, [])

    def test_registered_host_is_accepted(self):
        with mock.patch.dict(os.environ,
                             {'AVATAR_NOTIFY_ALLOWED_HOSTS': 'hook.example'}):
            body, _ = self.post_json(**self.valid_body(
                notifyurl='http://hook.example/task'))
        self.assertEqual(body['code'], 0)
        self.assertEqual(self.manager.calls[0]['notify_url'],
                         'http://hook.example/task')

    def test_unregistered_host_is_rejected_when_allowlist_is_set(self):
        with mock.patch.dict(os.environ,
                             {'AVATAR_NOTIFY_ALLOWED_HOSTS': 'hook.example'}):
            body, _ = self.post_json(**self.valid_body(
                notifyurl='http://evil.example/task'))
        self.assertNotEqual(body['code'], 0)
        self.assertEqual(self.manager.calls, [])

    def test_non_http_scheme_is_rejected(self):
        with mock.patch.dict(os.environ,
                             {'AVATAR_NOTIFY_ALLOWED_HOSTS': 'hook.example'}):
            body, _ = self.post_json(**self.valid_body(
                notifyurl='file:///etc/passwd'))
        self.assertNotEqual(body['code'], 0)
        self.assertEqual(self.manager.calls, [])

    def test_absent_notifyurl_is_still_allowed(self):
        body, _ = self.post_json(**self.valid_body())
        self.assertEqual(body['code'], 0)
        self.assertIsNone(self.manager.calls[0]['notify_url'])

    def test_wildcard_allowlist_fails_closed(self):
        with mock.patch.dict(os.environ,
                             {'AVATAR_NOTIFY_ALLOWED_HOSTS': '*'}):
            hosts = avatar_routes.allowed_notify_hosts()
        self.assertEqual(hosts, ())

    # ── 既有必填校验未被破坏 ──────────────────────────────────────────────

    def test_missing_video_source_still_reported(self):
        body, _ = self.post_json()
        self.assertNotEqual(body['code'], 0)
        self.assertIn('video_file 或 video_path 是必填的', body['msg'])


if __name__ == '__main__':
    unittest.main()
