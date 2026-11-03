# -*- coding: utf-8 -*-
"""运行期设置与进程控制接口。

- ``/api/settings*``      设置读写、LLM 连通性测试
- ``/api/control/rag/*``  RAG sidecar 的状态查询与重启

这些路径不再需要访问口令（口令机制已退役），与课堂运行面接口一致。
"""

import asyncio
import time

from aiohttp import web

from server.routes import json_error, json_ok
from utils.logger import logger
from utils.settings_store import (
    EFFECT_IMMEDIATE,
    SettingsError,
    get_store,
    is_masked_or_empty,
    mask_secret,
)

# 供应商预设：只用于在界面上填充 base_url 与候选模型名。
# 后端不因供应商名改变请求方式——一律按 OpenAI 兼容协议发起。
# 候选模型名是常见取值，实际可用范围以账号为准。
LLM_PRESETS = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-v4-pro", "deepseek-flash"],
        "note": "官方接口，两个模型都是推理模型，开口前会有思考延迟",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini"],
        "note": "需要能访问 api.openai.com",
    },
    {
        "id": "anthropic",
        "name": "Anthropic（Claude）",
        "base_url": "https://api.anthropic.com/v1",
        "models": ["claude-sonnet-4-5", "claude-opus-4-1"],
        "note": "需要该账号支持 OpenAI 兼容协议；若测试不通，请改用提供 OpenAI 兼容接口的中转网关，并把地址填在下方",
    },
    {
        "id": "moonshot",
        "name": "Moonshot（Kimi）",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2-0905-preview", "moonshot-v1-8k"],
        "note": "",
    },
    {
        "id": "dashscope",
        "name": "通义千问（兼容模式）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo"],
        "note": "",
    },
    {
        "id": "custom",
        "name": "自定义 / 中转网关",
        "base_url": "",
        "models": [],
        "note": "任何 OpenAI 兼容端点都可以，把 /v1 地址填在下方",
    },
]

LLM_TEST_TIMEOUT = 20.0
LLM_TEST_MAX_TOKENS = 16


def _store(request):
    store = request.app.get("settings_store")
    return store if store is not None else get_store()


def _sanitize(message, api_key):
    """错误摘要中不得出现完整密钥。"""
    text = str(message)
    if api_key:
        text = text.replace(api_key, mask_secret(api_key))
    if len(text) > 300:
        text = text[:300] + "..."
    return text


# ─── 设置读写 ──────────────────────────────────────────────────

async def get_settings(request):
    store = _store(request)
    opt = request.app.get("opt")
    return json_ok(data=store.read_all(opt=opt))


async def put_settings(request):
    store = _store(request)
    try:
        payload = await request.json()
    except Exception:
        return json_error("请求体必须是合法 JSON")
    try:
        changed = store.update(payload)
    except SettingsError as exc:
        return json_error("字段 {0} 不合法：{1}".format(exc.field, exc.message))
    return json_ok(data={"changed": sorted(changed.keys())})


# ─── LLM 配置 ──────────────────────────────────────────────────

def _llm_view(store):
    group = store.read_all().get("llm", {})
    return {
        "presets": LLM_PRESETS,
        "settings": group,
    }


async def get_llm(request):
    return json_ok(data=_llm_view(_store(request)))


async def put_llm(request):
    store = _store(request)
    try:
        payload = await request.json()
    except Exception:
        return json_error("请求体必须是合法 JSON")
    if not isinstance(payload, dict):
        return json_error("请求体必须是 JSON 对象")
    try:
        changed = store.update({"llm": payload})
    except SettingsError as exc:
        return json_error("字段 {0} 不合法：{1}".format(exc.field, exc.message))
    return json_ok(data={"changed": sorted(changed.keys())})


def _probe_llm(api_key, base_url, model):
    """发一次非流式最小请求。返回 (ok, 详情)。"""
    from openai import OpenAI

    begin = time.perf_counter()
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=LLM_TEST_TIMEOUT)
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "回复两个字：正常"}],
            max_tokens=LLM_TEST_MAX_TOKENS,
            stream=False,
        )
        elapsed = round((time.perf_counter() - begin) * 1000.0, 1)
        reply = ""
        if completion.choices:
            reply = (completion.choices[0].message.content or "").strip()
        return True, {
            "latency_ms": elapsed,
            "model": getattr(completion, "model", model) or model,
            "reply_preview": reply[:80],
        }
    except Exception as exc:  # noqa: BLE001 - 任何上游错误都作为测试结果返回
        elapsed = round((time.perf_counter() - begin) * 1000.0, 1)
        return False, {
            "latency_ms": elapsed,
            "error": "{0}: {1}".format(type(exc).__name__, _sanitize(exc, api_key)),
        }


async def test_llm(request):
    store = _store(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    stored_key = store.get_str("llm.api_key")
    submitted_key = payload.get("api_key")
    if is_masked_or_empty(submitted_key, stored_key):
        api_key = stored_key
    else:
        api_key = str(submitted_key)

    base_url = payload.get("base_url") or store.get_str("llm.base_url")
    model = payload.get("model") or store.get_str("llm.model")

    if not api_key:
        return json_error("尚未配置 API Key，无法测试")
    if not base_url:
        return json_error("尚未配置接口地址，无法测试")
    if not model:
        return json_error("尚未配置模型名，无法测试")

    loop = asyncio.get_event_loop()
    ok, detail = await loop.run_in_executor(None, _probe_llm, api_key, base_url, model)
    detail["ok"] = ok
    detail["base_url"] = base_url
    detail["model"] = detail.get("model") or model
    return json_ok(data=detail)


# ─── RAG 参数 ──────────────────────────────────────────────────

async def get_rag(request):
    store = _store(request)
    data = store.read_all().get("rag", {})
    # 界面从未写过的 rag 字段，settings_store 会回落内置默认值（如 qwen3:8b），
    # 与实际生效的 .env.rag 配置不符，会让界面显示误导性模型名。
    # 这里用 .env.rag 的真实值填充展示（只影响显示，不改写入路径）。
    if store.rag_source() == "env":
        actual = store.rag_env_file_values()
        for key, item in data.items():
            if key in actual and isinstance(item, dict):
                item["value"] = actual[key]
                item["source"] = "env.rag"
    return json_ok(data={
        "settings": data,
        "source": store.rag_source(),
        "effect": "restart",
    })


async def put_rag(request):
    store = _store(request)
    try:
        payload = await request.json()
    except Exception:
        return json_error("请求体必须是合法 JSON")
    if not isinstance(payload, dict):
        return json_error("请求体必须是 JSON 对象")
    try:
        changed = store.update({"rag": payload})
    except SettingsError as exc:
        return json_error("字段 {0} 不合法：{1}".format(exc.field, exc.message))
    return json_ok(data={
        "changed": sorted(changed.keys()),
        "effect": "restart",
        "hint": "参数已保存，需重启 RAG 服务后才生效",
    })


# ─── RAG 进程控制 ──────────────────────────────────────────────

async def rag_status(request):
    provider = request.app.get("rag_status_provider")
    if provider is None:
        return json_error("RAG 控制不可用")
    return json_ok(data=provider())


async def rag_restart(request):
    restarter = request.app.get("rag_restarter")
    if restarter is None:
        return json_error("RAG 控制不可用")
    result = await restarter()
    if not result.get("ok"):
        return json_error(result.get("error") or "RAG 重启失败")
    return json_ok(data=result)


def setup_settings_routes(app):
    """注册设置与控制路由。

    必须在 ``server/routes.py`` 的 ``add_static('/')`` 之前调用，
    否则可能被静态资源规则吞掉。
    """
    app.router.add_get("/api/settings", get_settings)
    app.router.add_put("/api/settings", put_settings)

    app.router.add_get("/api/settings/llm", get_llm)
    app.router.add_put("/api/settings/llm", put_llm)
    app.router.add_post("/api/settings/llm/test", test_llm)

    app.router.add_get("/api/settings/rag", get_rag)
    app.router.add_put("/api/settings/rag", put_rag)

    app.router.add_get("/api/control/rag/status", rag_status)
    app.router.add_post("/api/control/rag/restart", rag_restart)
