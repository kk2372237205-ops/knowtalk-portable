# -*- coding: utf-8 -*-
"""网页端运行期配置。

这里不托管业务状态；只将浏览器建立 WebRTC 所需的 ICE 配置从服务器 `.env`
安全地按登录会话下发。TURN 凭据本来就必须提供给浏览器使用，因此只能放在
受 HTTPS 与登录保护的响应里，绝不能写入版本库或静态 JavaScript 文件。
"""

import json
import os

from server.routes import json_ok
from utils.logger import logger

DEFAULT_ICE_SERVERS = [{"urls": ["stun:stun.freeswitch.org:3478"]}]
_ALLOWED_SCHEMES = ("stun:", "stuns:", "turn:", "turns:")


def _configured_ice_servers():
    """读取并收敛 ``WEBRTC_ICE_SERVERS``。

    环境变量格式是浏览器 ``RTCPeerConnection`` 的 iceServers 数组，例如：
    ``[{"urls":["turns:turn.example.edu:5349"],"username":"...","credential":"..."}]``。
    无效项不能让开播页崩掉，整份配置不合法时保留项目当前可用的 STUN 默认值。
    """
    raw = (os.getenv("WEBRTC_ICE_SERVERS") or "").strip()
    if not raw:
        return DEFAULT_ICE_SERVERS
    try:
        source = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("[网页端] WEBRTC_ICE_SERVERS 不是合法 JSON，已使用默认 STUN")
        return DEFAULT_ICE_SERVERS
    if not isinstance(source, list):
        logger.error("[网页端] WEBRTC_ICE_SERVERS 必须是数组，已使用默认 STUN")
        return DEFAULT_ICE_SERVERS

    result = []
    for item in source:
        if not isinstance(item, dict):
            continue
        urls = item.get("urls")
        urls = [urls] if isinstance(urls, str) else urls
        if not isinstance(urls, list):
            continue
        safe_urls = [url.strip() for url in urls
                     if isinstance(url, str)
                     and url.strip().lower().startswith(_ALLOWED_SCHEMES)]
        if not safe_urls:
            continue
        entry = {"urls": safe_urls}
        # TURN 的 username / credential 对浏览器是必需字段；其它陌生字段
        # （例如 vendor 自定义项）一律不下发，避免不小心暴露环境信息。
        for key in ("username", "credential", "credentialType"):
            value = item.get(key)
            if isinstance(value, str) and value:
                entry[key] = value
        result.append(entry)
    if result:
        return result
    logger.error("[网页端] WEBRTC_ICE_SERVERS 没有有效地址，已使用默认 STUN")
    return DEFAULT_ICE_SERVERS


async def rtc_config(_request):
    """给已登录的网页端提供 ICE/TURN 配置。"""
    return json_ok(data={"ice_servers": _configured_ice_servers()})


def setup_web_routes(app):
    app.router.add_get("/api/web/rtc-config", rtc_config)
