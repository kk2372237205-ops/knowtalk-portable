# -*- coding: utf-8 -*-
"""登录保护中间件。

三条放行规则，缺一条就会把人锁在门外：

1. **没有任何账号时全放行** —— 否则连创建首个管理员都做不到。
2. **登录保护开关关掉时全放行** —— 登录只用来表明身份，不拦人。
3. **登录页、登录接口、静态资源放行** —— 否则登录页自己会 302 到自己。

第四条是机器对机器的入口（`LOCAL_PROCESS_PATHS`）：本机检测器进程上报的
`POST /api/kiosk/proximity/event` 必须放行，否则人走近了不会有任何反应。

拦截时的形状分两种：``/api/`` 前缀与非 GET 请求返回 401 JSON（脚本与 fetch 能直接判断），
页面 GET 返回 302 到 ``/login.html?next=<原路径>``，登录后由前端跳回去。
"""

from urllib.parse import quote

from aiohttp import web

from server.auth_routes import AUTH_STORE, current_user, setup_auth_routes
from utils.auth_store import get_store
from utils.logger import logger

LOGIN_PAGE = "/login.html"

# 登录接口自身必须放行，否则谁能登录
PUBLIC_PREFIXES = ("/api/auth/",)

# 只由**本机进程**上报的入口：摄像头检测器是独立进程（scripts/proximity_detect.py），
# 它没有登录态，也拿不到账号——被中间件拦成 401 时，现场表现就是"人走近了数字人
# 既不欢迎也不做动作"，而服务日志里只有一行 debug 的"未登录访问被拦截"。
#
# 放行条件同时要求「路径命中」+「来源是回环地址」，外部机器伪造不了来源 IP；
# 其它接口依旧要登录。kiosk 机器上服务不挂反向代理，remote 就是真实来源；
# 若将来挂了代理，这里要改成共享令牌（见 docs 里的说明）。
LOCAL_PROCESS_PATHS = (
    "/api/kiosk/proximity/event",     # 检测器进程上报
    "/api/system/shutdown",           # 桌面版关窗时请求主服务优雅退出
)

_LOOPBACK = ("127.0.0.1", "::1", "localhost")

# 静态资源放行：登录页要能加载样式，否则是个白板
STATIC_SUFFIXES = (
    ".css", ".js", ".mjs", ".map", ".json",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".wav", ".mp4", ".webm",
)


def _from_local(request) -> bool:
    """请求是否来自本机回环地址（检测器等本机进程）。"""
    peer = (request.remote or "").strip()
    if peer.startswith("::ffff:"):          # 双栈下 IPv4 会带这个前缀
        peer = peer[7:]
    return peer in _LOOPBACK


def _is_public(request) -> bool:
    path = request.path
    if request.method == "OPTIONS":
        # 跨域预检不带 Cookie，拦下来等于把 CORS 打死
        return True
    if path in (LOGIN_PAGE, "/login", "/favicon.ico"):
        return True
    if path.startswith(PUBLIC_PREFIXES):
        return True
    if path in LOCAL_PROCESS_PATHS and _from_local(request):
        return True
    if path.startswith("/assets/"):
        return True
    return path.lower().endswith(STATIC_SUFFIXES)


def _wants_json(request) -> bool:
    if request.path.startswith("/api/"):
        return True
    return request.method not in ("GET", "HEAD")


def _login_location(request) -> str:
    target = request.path_qs or "/"
    return f"{LOGIN_PAGE}?next={quote(target, safe='/?=&')}"


@web.middleware
async def auth_middleware(request, handler):
    store = request.app.get(AUTH_STORE)
    if store is None:
        store = get_store()

    if _is_public(request) or not store.has_users() or not store.require_login:
        return await handler(request)

    _, username = current_user(request)
    if username:
        return await handler(request)

    if _wants_json(request):
        logger.debug("未登录访问被拦截：%s %s", request.method, request.path)
        return web.json_response(
            {"code": 401, "msg": "请先登录", "data": {"login": LOGIN_PAGE}},
            status=401,
        )

    raise web.HTTPFound(_login_location(request))


def install_auth(app, store=None):
    """装中间件与登录接口。**必须早于 ``add_static('/')`` 调用**，否则静态挂载吃掉根路径。"""
    if store is not None:
        app[AUTH_STORE] = store
    setup_auth_routes(app, store=store)
    app.middlewares.append(auth_middleware)
    return app
