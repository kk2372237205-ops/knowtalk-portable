# -*- coding: utf-8 -*-
"""系统音色枚举：为「声音克隆 → 更多系统音色」提供数据源。

两个来源：
- **EdgeTTS 在线神经语音**（``edge_tts.list_voices()``）：默认 TTS 引擎
  ``edgetts`` 直接使用微软语音名（如 ``zh-CN-XiaoxiaoNeural``）合成，音质好。
  列表来自微软在线服务，有网络往返，因此做 12 小时缓存；失败时冷却重试，
  不阻塞页面。
- **Windows 本机 SAPI 语音**（``comtypes`` 枚举 ``SAPI.SpVoice``）：完全离线，
  音质机械，数量通常很少（取决于系统安装了哪些语音包）。

枚举失败一律返回空列表——音色列表只是锦上添花，不应该让页面崩掉。
"""

import asyncio
import threading
import time

from utils.logger import logger
from utils.platform_support import IS_WINDOWS

# ── EdgeTTS 音色 ────────────────────────────────────────────────────────────

# 英语常用音色的少量补充：除了全部中文（zh-*）音色外，再附带这些常见英文音色，
# 避免列表过长又保持"更多音色可选"的实用性。
_EN_FALLBACK_IDS = {
    "en-US-AriaNeural", "en-US-JennyNeural", "en-US-GuyNeural",
    "en-US-ChristopherNeural", "en-US-EricNeural", "en-US-AnaNeural",
    "en-GB-SoniaNeural", "en-GB-RyanNeural", "en-GB-LibbyNeural",
}

_EDGE_VOICES = None
_EDGE_FETCHED_AT = 0.0
_EDGE_TTL = 12 * 3600          # 12 小时缓存
_LOCK = threading.Lock()


def edge_voices():
    """返回 EdgeTTS 可用音色（中文全部 + 少量英文）。线程安全 + 缓存。"""
    global _EDGE_VOICES, _EDGE_FETCHED_AT
    now = time.time()
    if _EDGE_VOICES is not None and now - _EDGE_FETCHED_AT < _EDGE_TTL:
        return _EDGE_VOICES
    with _LOCK:
        if _EDGE_VOICES is not None and time.time() - _EDGE_FETCHED_AT < _EDGE_TTL:
            return _EDGE_VOICES
        try:
            import edge_tts
            voices = asyncio.run(edge_tts.list_voices())
            rows = []
            for voice in voices:
                short = (voice.get("ShortName") or "").strip()
                locale = voice.get("Locale") or ""
                if not short:
                    continue
                if not (locale.startswith("zh") or short in _EN_FALLBACK_IDS):
                    continue
                rows.append({
                    "id": short,
                    "name": voice.get("FriendlyName") or short,
                    "gender": voice.get("Gender", ""),
                    "locale": locale,
                    "engine": "edgetts",
                })
            rows.sort(key=lambda row: (row["locale"], row["id"]))
            _EDGE_VOICES = rows
            _EDGE_FETCHED_AT = time.time()
        except Exception:  # noqa: BLE001 - 枚举失败不致命，冷却后重试
            logger.warning("[系统音色] EdgeTTS 音色列表获取失败（%s 分钟后重试）",
                           _EDGE_TTL // 60)
            _EDGE_VOICES = _EDGE_VOICES or []
            _EDGE_FETCHED_AT = time.time()
        return _EDGE_VOICES


# ── Windows SAPI 本机语音 ───────────────────────────────────────────────────

# 常见 LCID（SAPI 返回不带 0x 的十六进制串，如 '804'）→ 语言标签。
_LCID_TO_LOCALE = {
    "0x804": "zh-CN", "0x1004": "zh-CN", "0x404": "zh-TW", "0xc04": "zh-HK",
    "0x1404": "zh-MO", "0x409": "en-US", "0x809": "en-GB", "0xc09": "en-AU",
    "0x411": "ja-JP", "0x412": "ko-KR", "0x407": "de-DE", "0x40c": "fr-FR",
    "0x410": "it-IT", "0x40a": "es-ES", "0x416": "pt-BR", "0x419": "ru-RU",
}


def _lcid_to_locale(lcid_text):
    """SAPI Language 属性（'804' 形式）→ 简短语言标签；未知返回原文。"""
    if not lcid_text:
        return ""
    key = lcid_text.strip().lower()
    if key in _LCID_TO_LOCALE:
        return _LCID_TO_LOCALE[key]
    # '804' 之类不带 0x 前缀的写法也认
    try:
        int_key = int(key, 16)
    except ValueError:
        return key
    return _LCID_TO_LOCALE.get("0x{:x}".format(int_key), key)


def _ensure_com_initialized():
    """确保当前线程的 COM 已初始化（SAPI 是每线程 COM 模型）。

    comtypes 只在「导入它的那个线程」上自动初始化 COM（模块级
    ``CoInitializeEx()``）。真实服务进程里 comtypes 通常早已由主线程导入，
    之后线程池线程再 ``import comtypes.client`` 并不会触发初始化，直接创建
    SAPI 对象会报 CO_E_NOTINITIALIZED（-2147221008）。显式初始化是幂等的：
    已初始化线程返回 S_FALSE；线程模型冲突（RPC_E_CHANGED_MODE）说明 COM
    已可用，忽略即可。
    """
    try:
        import comtypes  # noqa: PLC0415 - 仅在使用时导入
    except Exception:  # noqa: BLE001 - 后续枚举失败会返回空列表
        return
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
    except Exception:  # noqa: BLE001 - 线程模型冲突 = COM 已初始化，可直接使用
        pass


def sapi_voices():
    """返回 Windows 本机 SAPI 语音；非 Windows 或枚举失败返回空列表。"""
    if not IS_WINDOWS:
        return []
    try:
        import comtypes.client  # noqa: PLC0415 - 仅在使用时导入
        _ensure_com_initialized()
        voice = comtypes.client.CreateObject("SAPI.SpVoice")
        voices = voice.GetVoices()
        count = voices.Count if voices is not None else 0
        rows = []
        for index in range(count):
            item = voices.Item(index)
            try:
                name = item.GetAttribute("Name") or ""
            except Exception:  # noqa: BLE001
                name = ""
            try:
                description = item.GetDescription() or name
            except Exception:  # noqa: BLE001
                description = name
            try:
                language = item.GetAttribute("Language") or ""
            except Exception:  # noqa: BLE001
                language = ""
            rows.append({
                "id": name or description,
                "name": description or name,
                "gender": "",
                "locale": _lcid_to_locale(language),
                "engine": "sapi",
            })
        return rows
    except Exception:  # noqa: BLE001
        logger.warning("[系统音色] SAPI 语音枚举失败（返回空列表）")
        return []


def all_system_voices():
    """便捷入口：一次取回全部系统音色（Edge + SAPI）。"""
    return {"edge": edge_voices(), "sapi": sapi_voices()}
