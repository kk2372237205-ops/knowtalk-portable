# -*- coding: utf-8 -*-
"""avatar_id 校验的加固测试。

对应 openspec/changes/harden-avatar-id-validation/tasks.md。
关键用例：`DELETE /api/avatars/...` 曾在沙箱里删光整个 avatars 目录，
这里断言它现在被拒且什么都不删——DATA_DIR 指向临时目录，即使修复失效也不会碰真实素材。
"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import server.avatar_routes as ar


def call_loop(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class ResolveAvatarDirTestCase(unittest.TestCase):
    """判定函数本身：纯点段与越界必须被拦，正常标识必须放行。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='avatar-id-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.avatars = os.path.join(self.tmp, 'data', 'avatars')
        os.makedirs(os.path.join(self.avatars, 'Mr.Chen'))
        patcher = mock.patch.object(ar, 'DATA_DIR', self.avatars)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_valid_ids_are_accepted(self):
        for good in ('Mr.Chen', 'Teacher', 'a.b', 'v1.2', 'x_y-z', 'A1'):
            resolved = ar.resolve_avatar_dir(good)
            self.assertEqual(os.path.realpath(str(resolved)),
                             os.path.realpath(os.path.join(self.avatars, good)))

    def test_dots_only_ids_are_rejected(self):
        for bad in ('.', '..', '...', '....', '................'):
            with self.assertRaises(ar.AvatarIdError, msg=repr(bad)):
                ar.resolve_avatar_dir(bad)

    def test_separator_and_illegal_ids_are_rejected(self):
        for bad in ('a/b', 'a\\b', '../x', '..\\x', '', None, 'a b', 'a:b', 'a*b'):
            with self.assertRaises(ar.AvatarIdError, msg=repr(bad)):
                ar.resolve_avatar_dir(bad)

    def test_resolved_path_never_equals_or_escapes_root(self):
        """"含点即拒"是错的写法；这里确认正常含点标识仍然放行，而纯点段拿不到根目录。"""
        root = os.path.realpath(self.avatars)
        for good in ('a.b', 'Mr.Chen'):
            self.assertNotEqual(os.path.realpath(str(ar.resolve_avatar_dir(good))), root)

    def test_symlink_pointing_outside_root_is_rejected(self):
        """包含性兜底的真正用途：标识通过字符校验，但解析后落在根目录之外。

        纯点段由"整段只由点组成"那条拦下；这一条覆盖的是前者拦不住的形态
        （符号链接），没有它，兜底代码就是没人执行过的死代码。
        """
        outside = os.path.join(self.tmp, 'outside-target')
        os.makedirs(outside)
        link = os.path.join(self.avatars, 'escape')
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest('本平台/权限不支持创建符号链接')
        with self.assertRaises(ar.AvatarIdError):
            ar.resolve_avatar_dir('escape')


class AvatarRouteGuardTestCase(unittest.TestCase):
    """接口层：四个以 {avatar_id} 寻址的接口都必须走同一判定。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='avatar-route-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.avatars = os.path.join(self.tmp, 'data', 'avatars')
        os.makedirs(os.path.join(self.avatars, 'Mr.Chen', 'full_imgs'))
        os.makedirs(os.path.join(self.avatars, 'Mr.Chen', 'face_imgs'))
        with open(os.path.join(self.avatars, 'Mr.Chen', 'coords.pkl'), 'wb') as f:
            f.write(b'x')
        with open(os.path.join(self.avatars, 'Mr.Chen', 'full_imgs', '0.png'), 'wb') as f:
            f.write(b'\x89PNG')
        # 同级目录代表真实的 data/voices、data/settings
        for extra in ('voices', 'settings'):
            os.makedirs(os.path.join(self.tmp, 'data', extra))

        for name, value in (('DATA_DIR', self.avatars),):
            patcher = mock.patch.object(ar, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.app = web.Application()
        ar.setup_avatar_routes(self.app)
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)

        # aiohttp 3.14 的 TestClient 在构造时就要拿到当前运行中的事件循环，
        # 因此客户端必须在协程里创建，不能直接写在 setUp 中。
        async def boot():
            client = TestClient(TestServer(self.app))
            await client.start_server()
            return client

        self.client = self.loop.run_until_complete(boot())
        self.addCleanup(lambda: self.loop.run_until_complete(self.client.close()))

    def request(self, method, path, payload=None, raw=False):
        async def go():
            kwargs = {}
            if payload is not None:
                kwargs['json'] = payload
            resp = await self.client.request(method, path, **kwargs)
            body = await resp.read() if raw else await resp.text()
            return resp.status, body
        return self.loop.run_until_complete(go())

    def body_of(self, text):
        try:
            return json.loads(text)
        except Exception:
            return {}

    # ── P0：纯点段绝不能清空形象库 ───────────────────────────────────────

    def test_dots_only_delete_is_rejected_and_deletes_nothing(self):
        for payload in ('%2e%2e%2e', '...', '....'):
            status, text = self.request('DELETE', '/api/avatars/' + payload)
            body = self.body_of(text)
            self.assertEqual(body.get('code'), 400, (payload, text))
            self.assertTrue(os.path.isdir(self.avatars), payload)
            self.assertTrue(os.path.isdir(os.path.join(self.avatars, 'Mr.Chen')), payload)

    def test_root_siblings_survive(self):
        self.request('DELETE', '/api/avatars/%2e%2e%2e')
        for extra in ('voices', 'settings'):
            self.assertTrue(os.path.isdir(os.path.join(self.tmp, 'data', extra)))

    def test_traversal_delete_is_rejected(self):
        status, text = self.request('DELETE', '/api/avatars/%2e%2e%2f%2e%2e')
        self.assertEqual(self.body_of(text).get('code'), 400, text)
        self.assertTrue(os.path.isdir(self.avatars))

    def test_symlinked_avatar_cannot_be_deleted_through(self):
        """越界符号链接必须被包含性兜底拦下，且不得删掉链接指向的目录。"""
        outside = os.path.join(self.tmp, 'outside-target')
        os.makedirs(outside)
        with open(os.path.join(outside, 'keep.txt'), 'w', encoding='utf-8') as handle:
            handle.write('must survive')
        try:
            os.symlink(outside, os.path.join(self.avatars, 'escape'))
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest('本平台/权限不支持创建符号链接')

        status, text = self.request('DELETE', '/api/avatars/escape')
        self.assertEqual(self.body_of(text).get('code'), 400, text)
        self.assertTrue(os.path.isfile(os.path.join(outside, 'keep.txt')))

    # ── 重命名 ─────────────────────────────────────────────────────────────

    def test_dots_only_rename_is_rejected(self):
        status, text = self.request('PUT', '/api/avatars/%2e%2e%2e',
                                    payload={'new_id': 'renamed'})
        self.assertEqual(self.body_of(text).get('code'), 400, text)
        self.assertTrue(os.path.isdir(os.path.join(self.avatars, 'Mr.Chen')))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'data', 'renamed')))

    def test_illegal_new_id_is_rejected(self):
        for bad in ('../x', 'a/b', '...', ''):
            status, text = self.request('PUT', '/api/avatars/Mr.Chen',
                                        payload={'new_id': bad})
            self.assertEqual(self.body_of(text).get('code'), 400, (bad, text))
            self.assertTrue(os.path.isdir(os.path.join(self.avatars, 'Mr.Chen')), bad)

    # ── 只读接口 ───────────────────────────────────────────────────────────

    def test_dots_only_thumbnail_and_gestures_are_rejected(self):
        for path in ('/api/avatars/%2e%2e%2e/thumbnail',
                     '/api/avatars/%2e%2e%2e/gestures'):
            status, text = self.request('GET', path)
            self.assertEqual(self.body_of(text).get('code'), 400, (path, text))

    # ── 合法标识不受影响 ──────────────────────────────────────────────────

    def test_legal_avatar_still_works(self):
        status, body = self.request('GET', '/api/avatars/Mr.Chen/thumbnail', raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(body, b'\x89PNG')

        status, text = self.request('GET', '/api/avatars/Mr.Chen/gestures')
        body = self.body_of(text)
        self.assertEqual(body.get('code'), 0, text)

        status, text = self.request('GET', '/api/avatars')
        body = self.body_of(text)
        self.assertEqual(body.get('code'), 0, text)
        self.assertEqual([a['id'] for a in body.get('data', [])], ['Mr.Chen'])

    def test_missing_but_legal_avatar_still_reports_not_found(self):
        status, text = self.request('DELETE', '/api/avatars/NoSuchAvatar')
        self.assertEqual(self.body_of(text).get('code'), 404, text)

    def test_legal_nested_dot_id_still_resolves(self):
        os.makedirs(os.path.join(self.avatars, 'v1.2'))
        status, text = self.request('GET', '/api/avatars/v1.2/gestures')
        self.assertEqual(self.body_of(text).get('code'), 0, text)


if __name__ == '__main__':
    unittest.main()
