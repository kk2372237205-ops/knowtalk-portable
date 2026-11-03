# -*- coding: utf-8 -*-
"""POST /api/playback 的契约测试：暂停态切换、404/400、幂等。

对应 openspec/changes/restore-default-frame-on-pause/。
"""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import server.routes as routes


class FakeSession:
    def __init__(self):
        self.playback_paused = False


class PlaybackControlTest(unittest.TestCase):
    def setUp(self):
        self.sessions = {'sid-1': FakeSession()}
        fake_manager = SimpleNamespace(get_session=lambda sid: self.sessions.get(sid))
        patcher = mock.patch.object(routes, 'session_manager', fake_manager)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.app = web.Application()
        self.app.router.add_post('/api/playback', routes.playback_control)
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)

        async def boot():
            client = TestClient(TestServer(self.app))
            await client.start_server()
            return client

        self.client = self.loop.run_until_complete(boot())
        self.addCleanup(lambda: self.loop.run_until_complete(self.client.close()))

    def post(self, payload, raw_body=None):
        async def go():
            if raw_body is not None:
                resp = await self.client.post('/api/playback', data=raw_body)
            else:
                resp = await self.client.post('/api/playback', json=payload)
            return json.loads(await resp.text())
        return self.loop.run_until_complete(go())

    def test_pause_sets_flag(self):
        body = self.post({'sessionid': 'sid-1', 'paused': True})
        self.assertEqual(body['code'], 0, body)
        self.assertTrue(self.sessions['sid-1'].playback_paused)
        self.assertTrue(body['data']['paused'])

    def test_resume_clears_flag(self):
        self.sessions['sid-1'].playback_paused = True
        body = self.post({'sessionid': 'sid-1', 'paused': False})
        self.assertEqual(body['code'], 0, body)
        self.assertFalse(self.sessions['sid-1'].playback_paused)

    def test_idempotent(self):
        self.post({'sessionid': 'sid-1', 'paused': True})
        body = self.post({'sessionid': 'sid-1', 'paused': True})
        self.assertEqual(body['code'], 0, body)
        self.assertTrue(self.sessions['sid-1'].playback_paused)

    def test_unknown_session_is_404(self):
        body = self.post({'sessionid': 'nope', 'paused': True})
        self.assertEqual(body['code'], 404, body)

    def test_missing_paused_is_400(self):
        body = self.post({'sessionid': 'sid-1'})
        self.assertEqual(body['code'], 400, body)
        self.assertFalse(self.sessions['sid-1'].playback_paused)

    def test_non_boolean_paused_is_400_and_keeps_state(self):
        self.sessions['sid-1'].playback_paused = True
        body = self.post({'sessionid': 'sid-1', 'paused': 'yes'})
        self.assertEqual(body['code'], 400, body)
        self.assertTrue(self.sessions['sid-1'].playback_paused, '非法参数不得改变暂停态')

    def test_invalid_json_is_400(self):
        body = self.post(None, raw_body='not-json')
        self.assertEqual(body['code'], 400, body)


if __name__ == '__main__':
    unittest.main()
