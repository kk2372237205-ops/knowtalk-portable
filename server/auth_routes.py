# -*- coding: utf-8 -*-
"""本地账号登录接口。

路径全在 ``/api/auth/*`` 下，中间件对这段前缀一律放行（否则登录页拉不到会话）。
响应体沿用项目的 ``{code, msg, data}`` 约定；失败时同时给非 2xx 状态码，
方便前端 ``fetch`` 直接判断，也方便 curl 排障。
"""

import json

from aiohttp import web

from utils.auth_store import (
    SESSION_COOKIE,
    SESSION_TTL,
    AuthError,
    AuthStore,
    get_store,
    issue_token,
    read_token,
)
from utils.logger import logger

# 用 AppKey 而不是字符串键：aiohttp 对字符串键有弃用警告，且拼错不会报错
AUTH_STORE = web.AppKey("auth_store", AuthStore)


def _store(request):
    store = request.app.get(AUTH_STORE)
    return store if store is not None else get_store()


def _ok(data=None):
    body = {"code": 0, "msg": "ok"}
    if data is not None:
        body["data"] = data
    return web.Response(content_type="application/json", text=json.dumps(body))


def _fail(msg, status=400, code=-1):
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps({"code": code, "msg": str(msg)}, ensure_ascii=False),
    )


async def _payload(request):
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _session_info(request, store, username=None):
    return {
        "authenticated": bool(username),
        "username": username or "",
        "display_name": store.display_name(username) if username else "",
        "require_login": store.require_login,
        "has_users": store.has_users(),
    }


def current_user(request):
    """从 Cookie 解出当前登录的账号名（未登录返回 ``None``）。

    不把身份塞进 ``request['auth_user']``：aiohttp 的请求存储在新版本里已被标记为
    不推荐，多读一次 Cookie 更省事，也少一处状态。
    """
    store = _store(request)
    token = request.cookies.get(SESSION_COOKIE) or ""
    if not token:
        return store, None
    username = read_token(token, store.secret())
    if username and not store.has_user(username):
        username = None
    return store, username


def _attach_session(response, store, username):
    response.set_cookie(
        SESSION_COOKIE,
        issue_token(username, store.secret()),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


def _clear_session(response):
    response.del_cookie(SESSION_COOKIE, path="/")
    return response


# ── 处理函数 ──────────────────────────────────────────────────────────────

async def session_info(request):
    store, username = current_user(request)
    return _ok(_session_info(request, store, username))


async def setup(request):
    """创建首位管理员。仅在系统里还没有任何账号时可用。"""
    store = _store(request)
    if store.has_users():
        return _fail("账号已存在，请直接登录", status=400)

    payload = await _payload(request)
    try:
        store.create_user(
            payload.get("username"),
            payload.get("password"),
            display_name=payload.get("display_name"),
            require_login=payload.get("require_login", True),
        )
    except AuthError as exc:
        return _fail(str(exc), status=400)

    username = str(payload.get("username")).strip().lower()
    logger.info("已创建本地账号：%s（需要登录=%s）", username, store.require_login)
    return _attach_session(_ok(_session_info(request, store, username)), store, username)


async def login(request):
    store = _store(request)
    if not store.has_users():
        return _fail("系统还没有账号，请先创建管理员", status=400)

    payload = await _payload(request)
    username = payload.get("username")
    password = payload.get("password")
    if not store.verify(username, password):
        logger.warning("登录失败：%s", username)
        return _fail("用户名或密码不正确", status=401)

    key = str(username).strip().lower()
    logger.info("登录成功：%s", key)
    return _attach_session(_ok(_session_info(request, store, key)), store, key)


async def logout(request):
    return _clear_session(_ok({"authenticated": False}))


async def change_password(request):
    store, username = current_user(request)
    if not username:
        return _fail("请先登录", status=401)

    payload = await _payload(request)
    try:
        store.change_password(
            username,
            payload.get("old_password"),
            payload.get("new_password"),
        )
    except AuthError as exc:
        return _fail(str(exc), status=400)

    # 密钥已轮换：当前浏览器补发一张新票，别把改密的人自己踢下线。
    logger.info("账号 %s 修改了密码，旧会话全部失效", username)
    return _attach_session(_ok(_session_info(request, store, username)), store, username)


def setup_auth_routes(app, store=None):
    if store is not None:
        app[AUTH_STORE] = store
    app.router.add_get("/api/auth/session", session_info)
    app.router.add_post("/api/auth/setup", setup)
    app.router.add_post("/api/auth/login", login)
    app.router.add_post("/api/auth/logout", logout)
    app.router.add_post("/api/auth/password", change_password)
