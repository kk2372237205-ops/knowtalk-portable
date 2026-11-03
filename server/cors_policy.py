# -*- coding: utf-8 -*-
"""跨域策略：只对**显式登记**的可信来源返回跨域响应头。

改动前的配置是 `defaults={"*": ResourceOptions(allow_credentials=True, ...)}`，
即"任何来源 + 允许凭据"。配合当时尚未落地的口令鉴权，任意站点都能带凭据读取管理
数据（审计第 3.5 节）。

现在来源取自环境变量 `CORS_ALLOWED_ORIGINS`（逗号分隔的完整来源）。未配置时
**不安装跨域中间件**——服务对任何来源都不返回跨域响应头，同源请求不受影响
（浏览器对同源请求本就不需要这些头）。

配置只影响启动期，改完必须重启主服务，与 `utils/settings_store.py` 里
`EFFECT_RESTART` 的语义一致。
"""

import os

import aiohttp_cors

from utils.logger import logger
from utils.net_guard import NetGuardError, parse_allowed_origins

CORS_ORIGINS_ENV = 'CORS_ALLOWED_ORIGINS'


def allowed_origins(env_value=None):
    """解析 ``CORS_ALLOWED_ORIGINS``；非法配置一律退化为"不信任任何来源"。"""
    if env_value is None:
        env_value = os.environ.get(CORS_ORIGINS_ENV, '')
    try:
        return parse_allowed_origins(env_value)
    except NetGuardError as exc:
        logger.error("[跨域] %s；本次按不信任任何来源处理", exc)
        return ()


def install_cors(app, origins=None):
    """给 ``app`` 的全部已注册路由安装跨域策略。

    必须在所有路由注册**之后**调用，否则后注册的路由不会被覆盖。
    未登记任何来源时返回 ``None`` 且不安装任何中间件。
    """
    if origins is None:
        origins = allowed_origins()
    if not origins:
        logger.info("[跨域] 未配置 %s，仅允许同源访问", CORS_ORIGINS_ENV)
        return None

    defaults = {
        origin: aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
        )
        for origin in origins
    }
    cors = aiohttp_cors.setup(app, defaults=defaults)
    for route in list(app.router.routes()):
        cors.add(route)
    logger.info("[跨域] 已登记来源: %s", ", ".join(origins))
    return cors
