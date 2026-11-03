# -*- coding: utf-8 -*-
"""声音接口的路径加固测试。

对应 openspec/changes/harden-file-paths-and-cors/tasks.md 第 2 组。
沿用既有测试约定：直接调用处理函数并传入最小请求替身，不启动 aiohttp 服务。
"""

import asyncio
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import server.routes as routes
from utils.path_guard import PathGuardError


class FakeUpload(object):
    """模拟 multipart 里的文件部件。"""

    def __init__(self, filename, content=b'RIFF....'):
        self.filename = filename
        self.file = io.BytesIO(content)


class FakeForm(dict):
    pass


class FakeRequest(object):
    def __init__(self, body=None, form=None):
        self._body = body
        self._form = form

    async def json(self):
        if self._body is None:
            raise ValueError('请求体不是合法 JSON')
        return self._body

    async def post(self):
        if self._form is None:
            raise ValueError('请求体不是 multipart')
        return self._form


def call(handler, request):
    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(handler(request))
    finally:
        loop.close()
    return json.loads(response.text)


class VoiceGuardTestCase(unittest.TestCase):
    def setUp(self):
        # 用临时目录隔离：绝不触碰真实的 data/voices
        self.tmp = tempfile.mkdtemp(prefix='voice-guard-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.voices = os.path.join(self.tmp, 'data', 'voices')
        os.makedirs(self.voices)
        self.sentinel = os.path.join(self.tmp, 'README.md')
        with open(self.sentinel, 'w', encoding='utf-8') as handle:
            handle.write('do not delete me')

        # VOICES_DIR 与 PROJECT_ROOT 都要指向临时目录：相对路径按 PROJECT_ROOT
        # 解析，只改其中一个会让"合法相对路径"被判成越界。
        for name, value in (('PROJECT_ROOT', self.tmp),
                            ('VOICES_DIR', self.voices)):
            patcher = mock.patch.object(routes, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def upload(self, filename, name):
        form = FakeForm()
        form['file'] = FakeUpload(filename)
        form['name'] = name
        return call(routes.voice_upload, FakeRequest(form=form))

    def delete(self, path):
        return call(routes.voice_delete, FakeRequest(body={'path': path}))

    def listing(self):
        return sorted(os.listdir(self.voices))

    # ── 上传 ──────────────────────────────────────────────────────────────

    def test_legit_upload_succeeds_inside_voices_dir(self):
        body = self.upload('sample.wav', '讲师甲')
        self.assertEqual(body['code'], 0)
        self.assertEqual(body['data']['name'], '讲师甲')
        self.assertEqual(body['data']['path'],
                         os.path.join('data', 'voices', '讲师甲.wav'))
        self.assertEqual(self.listing(), ['讲师甲.wav'])

    def test_name_with_path_separators_is_rejected(self):
        for bad in ('../../server/routes', '..\\..\\evil', 'a/b', 'a\\b',
                    '.', '..', 'a\x00b'):
            body = self.upload('sample.wav', bad)
            self.assertNotEqual(body['code'], 0, repr(bad))
            self.assertEqual(self.listing(), [], repr(bad))

    def test_blank_name_falls_back_to_default(self):
        """既有语义：名称为空/纯空白时按默认名处理，不是错误。"""
        for blank in (None, '', '   '):
            body = self.upload('sample.wav', blank)
            self.assertEqual(body['code'], 0, repr(blank))
            self.assertEqual(body['data']['name'], '未命名', repr(blank))

    def test_name_traversal_writes_nothing_outside_voices_dir(self):
        before = sorted(os.listdir(self.tmp))
        body = self.upload('sample.wav', '../../evil')
        self.assertNotEqual(body['code'], 0)
        self.assertEqual(sorted(os.listdir(self.tmp)), before)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'evil.wav')))

    def test_disallowed_extension_is_rejected(self):
        for filename in ('payload.py', 'x.exe', 'noextension'):
            body = self.upload(filename, '讲师甲')
            self.assertNotEqual(body['code'], 0, filename)
            self.assertEqual(self.listing(), [], filename)

    def test_allowed_extensions_are_accepted_case_insensitively(self):
        for ext in routes.ALLOWED_VOICE_EXTS:
            body = self.upload('sample' + ext.upper(), 'v' + ext.strip('.'))
            self.assertEqual(body['code'], 0, ext)

    def test_missing_file_is_reported(self):
        form = FakeForm()
        form['name'] = '讲师甲'
        body = call(routes.voice_upload, FakeRequest(form=form))
        self.assertNotEqual(body['code'], 0)
        self.assertIn('请上传音频文件', body['msg'])

    def test_default_name_is_used_when_absent(self):
        form = FakeForm()
        form['file'] = FakeUpload('sample.wav')
        body = call(routes.voice_upload, FakeRequest(form=form))
        self.assertEqual(body['code'], 0)
        self.assertEqual(body['data']['name'], '未命名')

    # ── 删除 ──────────────────────────────────────────────────────────────

    def test_delete_of_registered_voice_removes_the_file(self):
        target = os.path.join(self.voices, 'x.wav')
        with open(target, 'wb') as handle:
            handle.write(b'audio')
        body = self.delete(os.path.join('data', 'voices', 'x.wav'))
        self.assertEqual(body['code'], 0)
        self.assertFalse(os.path.exists(target))

    def test_delete_outside_voices_dir_is_rejected(self):
        body = self.delete('README.md')
        self.assertNotEqual(body['code'], 0)
        self.assertTrue(os.path.exists(self.sentinel))

    def test_delete_by_absolute_path_outside_is_rejected(self):
        body = self.delete(self.sentinel)
        self.assertNotEqual(body['code'], 0)
        self.assertTrue(os.path.exists(self.sentinel))

    def test_delete_by_traversal_is_rejected(self):
        for bad in ('../../README.md',
                    os.path.join('data', 'voices', '..', '..', 'README.md'),
                    'data/voices/../../README.md'):
            body = self.delete(bad)
            self.assertNotEqual(body['code'], 0, bad)
            self.assertTrue(os.path.exists(self.sentinel), bad)

    def test_delete_cannot_escape_into_sibling_directory(self):
        sibling = os.path.join(self.tmp, 'voices2')
        os.makedirs(sibling)
        victim = os.path.join(sibling, 'y.wav')
        with open(victim, 'wb') as handle:
            handle.write(b'audio')
        body = self.delete(os.path.join('..', 'voices2', 'y.wav'))
        self.assertNotEqual(body['code'], 0)
        self.assertTrue(os.path.exists(victim))

    def test_delete_missing_file_inside_reports_not_found(self):
        body = self.delete(os.path.join('data', 'voices', 'nope.wav'))
        self.assertNotEqual(body['code'], 0)
        self.assertIn('文件不存在', body['msg'])

    def test_delete_without_path_is_reported(self):
        body = call(routes.voice_delete, FakeRequest(body={}))
        self.assertNotEqual(body['code'], 0)
        self.assertIn('请指定文件路径', body['msg'])

    def test_delete_rejects_directory_outside_without_removing_it(self):
        """越界路径即便存在也不能被删掉。"""
        victim_dir = os.path.join(self.tmp, 'data')
        body = self.delete('data')
        self.assertNotEqual(body['code'], 0)
        self.assertTrue(os.path.isdir(victim_dir))


class VoiceListConsistencyTestCase(unittest.TestCase):
    def test_list_uses_the_same_extension_allowlist_as_upload(self):
        """列举与上传必须共用同一份后缀定义，否则会出现"传得上但列不出"。"""
        self.assertEqual(set(routes.ALLOWED_VOICE_EXTS),
                         {'.wav', '.mp3', '.ogg', '.m4a', '.flac'})

    def test_path_guard_error_is_a_value_error(self):
        self.assertTrue(issubclass(PathGuardError, ValueError))


if __name__ == '__main__':
    unittest.main()
