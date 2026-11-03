# -*- coding: utf-8 -*-
"""检测器上报必须能进得来：登录保护不能把本机进程一起挡掉。

现场故障：摄像头检测器（`scripts/proximity_detect.py`）是独立进程，没有登录态，
它 POST `/api/kiosk/proximity/event` 被中间件拦成 401。服务端日志只有一行 debug
的"未登录访问被拦截"，界面上一切正常——人走近了，数字人既不欢迎也不做动作。

这里用真实的最小 aiohttp 应用（TestClient 从 127.0.0.1 发起）验证：
- 本机上报放行；
- 其它 kiosk 接口照样要登录（别为了修这个问题把鉴权整个打开）。
"""

import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from server.auth_mw import _from_local, install_auth
from server.auth_routes import AUTH_STORE


class FakeAuthStore:
    """只提供中间件真正会读的几样东西，不落盘、不碰真实账号文件。"""

    def has_users(self):
        return True

    @property
    def require_login(self):
        return True

    def secret(self):
        return b"0" * 32

    def has_user(self, username):
        return False


def build_app():
    app = web.Application()

    async def ok(request):
        return web.json_response({"code": 0, "data": {}})

    app.router.add_post("/api/kiosk/proximity/event", ok)
    app.router.add_get("/api/kiosk/state", ok)
    app.router.add_put("/api/kiosk/idle", ok)
    app.router.add_post("/api/system/shutdown", ok)
    install_auth(app, store=FakeAuthStore())
    return app


class KioskIngestAuthTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = TestClient(TestServer(build_app()))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_detector_report_is_allowed_without_login(self):
        response = await self.client.post("/api/kiosk/proximity/event",
                                          json={"present": True})
        self.assertEqual(response.status, 200,
                         "本机检测器上报被拦了，人走近不会有任何反应")

    async def test_state_still_requires_login(self):
        response = await self.client.get("/api/kiosk/state")
        self.assertEqual(response.status, 401)

    async def test_config_write_still_requires_login(self):
        response = await self.client.put("/api/kiosk/idle", json={"tip": "x"})
        self.assertEqual(response.status, 401)

    async def test_local_shutdown_request_is_allowed(self):
        """桌面版关窗时请求主服务优雅退出——不带 Cookie，靠回环来源放行。"""
        response = await self.client.post("/api/system/shutdown")
        self.assertEqual(response.status, 200,
                         "桌面版关不掉主服务，RAG sidecar 会变成孤儿进程")


class FromLocalTestCase(unittest.TestCase):
    """来源判断：只看回环地址，IPv6 双栈前缀要能认出来。"""

    class _Req:
        def __init__(self, remote):
            self.remote = remote

    def test_loopback_variants(self):
        for remote in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"):
            self.assertTrue(_from_local(self._Req(remote)), remote)

    def test_lan_address_is_not_local(self):
        for remote in ("192.168.1.20", "10.0.0.5", "", None):
            self.assertFalse(_from_local(self._Req(remote)), remote)
