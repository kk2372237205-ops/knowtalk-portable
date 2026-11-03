# -*- coding: utf-8 -*-
"""跨域策略的接口级测试。

对应 openspec/changes/harden-file-paths-and-cors/tasks.md 第 4.1 项。
这里启动真实的最小 aiohttp 应用，验证响应头而不是只看配置对象——
`aiohttp_cors` 的来源匹配是**精确字符串比对**，必须实测。
"""

import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from server.cors_policy import allowed_origins, install_cors

ALLOWED = 'https://ok.example'
ALSO_ALLOWED = 'http://127.0.0.1:3000'
DISALLOWED = 'https://evil.example'

ACAO = 'Access-Control-Allow-Origin'
ACAC = 'Access-Control-Allow-Credentials'


def build_app(origins):
    app = web.Application()

    async def handler(request):
        return web.json_response({'code': 0, 'msg': 'ok'})

    app.router.add_get('/api/admin/config', handler)
    install_cors(app, origins=origins)
    return app


class CorsPolicyTestCase(unittest.IsolatedAsyncioTestCase):
    async def client_for(self, origins):
        client = TestClient(TestServer(build_app(origins)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        return client

    # ── 已登记来源 ────────────────────────────────────────────────────────

    async def test_registered_origin_gets_allow_headers(self):
        client = await self.client_for((ALLOWED,))
        response = await client.get('/api/admin/config',
                                    headers={'Origin': ALLOWED})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get(ACAO), ALLOWED)
        self.assertEqual(response.headers.get(ACAC), 'true')
        # aiohttp_cors 把 expose_headers="*" 展开成响应自身的头部列表（不发字面量 *）
        exposed = response.headers.get('Access-Control-Expose-Headers', '')
        self.assertIn('Content-Length', exposed)

    async def test_preflight_allows_custom_request_headers(self):
        """预检必须放行任意自定义请求头（管理接口可能带自己的头）。"""
        client = await self.client_for((ALLOWED,))
        response = await client.options(
            '/api/admin/config',
            headers={'Origin': ALLOWED,
                     'Access-Control-Request-Method': 'GET',
                     'Access-Control-Request-Headers': 'X-Custom-Header'})
        self.assertEqual(response.status, 200)
        allowed = response.headers.get('Access-Control-Allow-Headers', '')
        # aiohttp_cors 会把请求头名大写回显；HTTP 头名大小写不敏感，按不敏感比对。
        self.assertIn('x-custom-header', allowed.lower())

    async def test_second_registered_origin_is_also_accepted(self):
        client = await self.client_for((ALLOWED, ALSO_ALLOWED))
        response = await client.get('/api/admin/config',
                                    headers={'Origin': ALSO_ALLOWED})
        self.assertEqual(response.headers.get(ACAO), ALSO_ALLOWED)

    async def test_preflight_from_registered_origin_succeeds(self):
        client = await self.client_for((ALLOWED,))
        response = await client.options(
            '/api/admin/config',
            headers={'Origin': ALLOWED,
                     'Access-Control-Request-Method': 'GET'})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get(ACAO), ALLOWED)
        self.assertEqual(response.headers.get(ACAC), 'true')

    # ── 未登记来源 ────────────────────────────────────────────────────────

    async def test_unregistered_origin_gets_no_allow_headers(self):
        client = await self.client_for((ALLOWED,))
        response = await client.get('/api/admin/config',
                                    headers={'Origin': DISALLOWED})
        self.assertNotEqual(response.headers.get(ACAO), DISALLOWED)
        self.assertIsNone(response.headers.get(ACAO))
        self.assertIsNone(response.headers.get(ACAC))

    async def test_unregistered_origin_does_not_qualify_for_wildcard(self):
        """配置里没有通配来源，未登记来源绝不能被 '*' 兜底放行。"""
        client = await self.client_for((ALLOWED,))
        response = await client.get('/api/admin/config',
                                    headers={'Origin': DISALLOWED})
        self.assertNotEqual(response.headers.get(ACAO), '*')

    async def test_preflight_from_unregistered_origin_is_denied(self):
        client = await self.client_for((ALLOWED,))
        response = await client.options(
            '/api/admin/config',
            headers={'Origin': DISALLOWED,
                     'Access-Control-Request-Method': 'GET'})
        self.assertNotEqual(response.status, 200)
        self.assertNotEqual(response.headers.get(ACAO), DISALLOWED)
        self.assertIsNone(response.headers.get(ACAC))

    # ── 未配置来源 ────────────────────────────────────────────────────────

    async def test_no_origins_configured_installs_nothing(self):
        app = web.Application()

        async def handler(request):
            return web.json_response({'code': 0, 'msg': 'ok'})

        app.router.add_get('/api/admin/config', handler)
        self.assertIsNone(install_cors(app, origins=()))

    async def test_same_origin_still_works_without_any_configuration(self):
        client = await self.client_for(())
        response = await client.get('/api/admin/config')
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(),
                         {'code': 0, 'msg': 'ok'})
        self.assertIsNone(response.headers.get(ACAO))

    async def test_cross_origin_gets_no_headers_when_nothing_configured(self):
        client = await self.client_for(())
        response = await client.get('/api/admin/config',
                                    headers={'Origin': DISALLOWED})
        self.assertEqual(response.status, 200)
        self.assertIsNone(response.headers.get(ACAO))
        self.assertIsNone(response.headers.get(ACAC))


class AllowedOriginsParsingTestCase(unittest.TestCase):
    def test_parses_comma_separated_origins(self):
        self.assertEqual(
            allowed_origins(' {0} , {1} '.format(ALLOWED, ALSO_ALLOWED)),
            (ALLOWED, ALSO_ALLOWED))

    def test_empty_configuration_yields_empty_tuple(self):
        for value in ('', '   ', None):
            self.assertEqual(allowed_origins(value), ())

    def test_wildcard_configuration_fails_closed(self):
        self.assertEqual(allowed_origins('*'), ())
        self.assertEqual(allowed_origins('{0},*'.format(ALLOWED)), ())

    def test_invalid_port_fails_closed(self):
        self.assertEqual(allowed_origins('https://a.example:99999'), ())

    def test_reads_environment_variable(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {'CORS_ALLOWED_ORIGINS': ALLOWED}):
            self.assertEqual(allowed_origins(), (ALLOWED,))
        with mock.patch.dict(os.environ, {'CORS_ALLOWED_ORIGINS': ''}):
            self.assertEqual(allowed_origins(), ())


if __name__ == '__main__':
    unittest.main()
