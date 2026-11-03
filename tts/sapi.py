# -*- coding: utf-8 -*-
"""离线 TTS 引擎：使用 Windows 系统自带的语音合成（SAPI）。

用途：当在线 TTS 服务不可用时（例如 edgetts 被服务端以 403 拒绝）仍能让课堂发声。
本项目默认的 edgetts 依赖微软在线服务，实测已不可用，且 edge-tts 7.2.8 已是
最新版、无可升级路径，因此需要这个离线兜底。

音质取舍（必须如实告知使用者）：SAPI 是拼接式合成，音质明显不如在线神经语音。
"""

import os
import re
import sys
import tempfile

import numpy as np
import resampy
import soundfile as sf

from utils.logger import logger
from utils.platform_support import IS_WINDOWS
from .base_tts import BaseTTS, State, TTSSynthesisError
from registry import register

# SAPI 的 Rate 取值 -10..10（0 为正常）。与形如 +20% 的百分比只能近似换算。
_PERCENT_PER_RATE_STEP = 10
_RATE_MIN, _RATE_MAX = -10, 10

# 中文相关 LCID。首选简体，其次其它中文变体。
# 实测本机 Microsoft Huihui Desktop 的 Language 属性为 '804'（= 0x804 = 2052，zh-CN 简体），
# 英文语音为 '409'（= 0x409 = 1041，en-US）。
_ZH_SIMPLIFIED_LCIDS = {0x0804, 0x1004}
_ZH_OTHER_LCIDS = {0x0404, 0x0C04, 0x1404}
_ZH_DESC_KEYWORDS = ("chinese", "中文", "普通话", "huihui", "yaoyao", "kangkang")


def _lcid_of(item):
    """读取语音的 Language 属性（SAPI 返回不带 0x 的十六进制串，如 '804'）。"""
    try:
        raw = str(item.GetAttribute("Language") or "").strip()
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        return int(raw, 16)
    except ValueError:
        return None


def _looks_chinese(item, description):
    """LCID 是可靠信号；描述串只作为兜底，因为其措辞随系统语言变化。"""
    lcid = _lcid_of(item)
    if lcid is not None and lcid in (_ZH_SIMPLIFIED_LCIDS | _ZH_OTHER_LCIDS):
        return True
    lowered = description.lower()
    return any(keyword in lowered for keyword in _ZH_DESC_KEYWORDS)


def _pick_chinese_voice(voice, preferred=None):
    """从系统已安装语音中挑一个中文语音，优先简体。找不到就明确报错。

    ``preferred`` 为用户在界面上选定的音色名（SAPI 语音的 Name 或描述串）。
    能精确匹配就用它；匹配不上（例如默认 REF_FILE 是 Edge 语音名）就退回
    原有的"自动挑第一个中文语音"逻辑，保证默认行为不变。
    """
    voices = voice.GetVoices()
    count = voices.Count if voices is not None else 0
    if count == 0:
        raise TTSSynthesisError(
            "系统未安装任何可用的语音合成语音",
            hint="在 Windows「设置 → 时间和语言 → 语音」中添加中文语音后重试",
        )

    descriptions = []
    simplified, other = [], []

    if preferred:
        preferred_text = str(preferred).strip().lower()
        for index in range(count):
            item = voices.Item(index)
            try:
                name = str(item.GetAttribute("Name") or "").strip().lower()
                description = item.GetDescription()
            except Exception:  # noqa: BLE001
                name, description = "", "<无法读取>"
            if preferred_text and preferred_text in (name, description.strip().lower()):
                logger.info("[TTS] SAPI 选用用户指定语音: %s", description)
                return item

    for index in range(count):
        item = voices.Item(index)
        try:
            description = item.GetDescription()
        except Exception:  # noqa: BLE001
            description = "<无法读取>"
        descriptions.append(description)
        if not _looks_chinese(item, description):
            continue
        lcid = _lcid_of(item)
        (simplified if lcid in _ZH_SIMPLIFIED_LCIDS else other).append((item, description, lcid))

    if simplified:
        item, description, lcid = simplified[0]
        logger.info("[TTS] SAPI 选用语音: %s (Language=%s)", description, lcid)
        return item
    if other:
        item, description, lcid = other[0]
        logger.warning(
            "[TTS] 系统没有简体中文语音，改用其它中文语音: %s (Language=%s)", description, lcid
        )
        return item

    raise TTSSynthesisError(
        "系统没有中文语音，SAPI 无法合成中文（已安装: %s）" % "; ".join(descriptions),
        hint="在 Windows「设置 → 时间和语言 → 语音」中添加中文语音后重试",
    )


def percent_to_sapi_rate(rate_text, default=0):
    """把 `--TTS_RATE` 的 `+20%` / `-30%` 形式换算为 SAPI 的 Rate 整数。

    换算规则：每 10% 对应 1 级，越界裁剪到 [-10, 10]。
    这只是近似——SAPI 的级距与百分比并非线性对应，因此取整不可避免。
    """
    if rate_text is None:
        return default
    match = re.match(r"^\s*([+-]?)\s*(\d+)\s*%\s*$", str(rate_text))
    if not match:
        return default
    sign = -1 if match.group(1) == "-" else 1
    percent = sign * int(match.group(2))
    steps = int(round(percent / float(_PERCENT_PER_RATE_STEP)))
    return max(_RATE_MIN, min(_RATE_MAX, steps))


def _require_windows():
    if not IS_WINDOWS:
        raise TTSSynthesisError(
            "SAPI 引擎仅在 Windows 上可用（当前平台 %s）" % sys.platform,
            hint="请改用其它 TTS 引擎，例如 --tts edgetts；或在本机安装系统语音后重试",
        )


def _ensure_com_initialized():
    """确保当前线程的 COM 已初始化（SAPI 是每线程 COM 模型）。

    comtypes 只在「导入它的那个线程」上自动初始化 COM（模块级
    ``CoInitializeEx()``）。真实服务进程里 comtypes 通常早已由主线程导入，
    之后 TTS 工作线程 / 线程池线程再 ``import comtypes.client`` 并不会触发
    初始化，直接创建 SAPI 对象会报 CO_E_NOTINITIALIZED（-2147221008）
    「尚未调用 CoInitialize」。因此在每次使用 COM 前显式初始化：

    - 首次调用 → S_OK；
    - 线程已用同模型初始化 → S_FALSE，无副作用；
    - 线程已用其它模型初始化（RPC_E_CHANGED_MODE）→ COM 已可用，忽略异常。
    """
    try:
        import comtypes  # noqa: PLC0415 - 仅在使用时导入
    except Exception:  # noqa: BLE001 - 后续 CreateObject 会给出更明确报错
        return
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
    except Exception:  # noqa: BLE001 - 线程模型冲突 = COM 已初始化，可直接使用
        pass


def _create_com_object(prog_id):
    """延迟导入 comtypes 并创建 COM 对象。

    非 Windows 平台或缺少 comtypes 时给出带说明的失败，而不是泄漏原始 ImportError。
    """
    _require_windows()
    try:
        import comtypes.client  # noqa: PLC0415 - 仅在真正合成时导入
    except Exception as exc:  # noqa: BLE001
        raise TTSSynthesisError(
            "无法加载 comtypes，SAPI 引擎不可用: %s" % exc,
            hint="执行 pip install comtypes 后重试",
        )
    _ensure_com_initialized()
    return comtypes.client.CreateObject(prog_id)


def _pick_chinese_voice_removed():
    pass


def synthesize_to_wav(text, rate_text=None, preferred=None):
    """把文本合成为 wav 文件，返回文件路径。调用方负责删除。

    ``preferred`` 是用户指定的 SAPI 语音名；None 时沿用自动挑选中文语音。
    """
    voice = _create_com_object("SAPI.SpVoice")
    stream = _create_com_object("SAPI.SpFileStream")

    fd, path = tempfile.mkstemp(prefix="sapi-", suffix=".wav")
    os.close(fd)

    try:
        selected = _pick_chinese_voice(voice, preferred=preferred)
        voice.Voice = selected
        voice.Rate = percent_to_sapi_rate(rate_text)
        stream.Open(path, 3)  # 3 = SSFMCreateForWrite
        voice.AudioOutputStream = stream
        voice.Speak(text)
        stream.Close()
    except TTSSynthesisError:
        try:
            stream.Close()
        except Exception:  # noqa: BLE001
            pass
        if os.path.exists(path):
            os.remove(path)
        raise
    except Exception as exc:  # noqa: BLE001
        try:
            stream.Close()
        except Exception:  # noqa: BLE001
            pass
        if os.path.exists(path):
            os.remove(path)
        raise TTSSynthesisError(
            "SAPI 合成失败: %s" % exc,
            hint="检查系统语音是否完整；必要时改用其它 TTS 引擎",
        )

    if not os.path.exists(path) or os.path.getsize(path) <= 0:
        if os.path.exists(path):
            os.remove(path)
        raise TTSSynthesisError(
            "SAPI 合成没有产出音频",
            hint="检查系统语音是否完整；必要时改用其它 TTS 引擎",
        )
    return path


@register("tts", "sapi")
class SapiTTS(BaseTTS):
    """Windows 系统语音（SAPI）离线引擎。"""

    def __init__(self, opt, parent):
        super().__init__(opt, parent)
        self.rate_text = getattr(opt, "TTS_RATE", None)

    def txt_to_audio(self, msg: tuple[str, dict]):
        text, textevent = msg
        if not text or not text.strip():
            return

        slide_id = textevent.get("slide_id", "无")
        logger.info(
            "[TTS] SAPI 开始合成: rate=%s, slide_id=%s, text=%s...",
            self.rate_text, slide_id, text[:80],
        )

        path = None
        try:
            preferred = getattr(self.opt, 'REF_FILE', None)
            path = synthesize_to_wav(text, self.rate_text, preferred=preferred)
            stream = self._read_as_stream(path)
        finally:
            # 成功与失败路径都必须清理临时文件
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    logger.warning("[TTS] 临时文件删除失败: %s", path)

        if stream is None or stream.shape[0] == 0:
            raise TTSSynthesisError(
                "SAPI 合成结果为空",
                hint="检查系统语音是否完整；必要时改用其它 TTS 引擎",
            )

        self._emit_frames(stream, text, textevent)

    def _read_as_stream(self, path):
        """读入音频并统一成 16 kHz 单声道 float32，与既有引擎一致。

        解码失败必须转为 TTSSynthesisError：否则损坏的音频会让原始库异常
        冒到 TTS 工作线程之外，违背"失败必须可预期"。
        """
        try:
            stream, sample_rate = sf.read(path)
        except Exception as exc:  # noqa: BLE001
            raise TTSSynthesisError(
                "SAPI 产出的音频无法解码: %s" % exc,
                hint="检查系统语音是否完整；必要时改用其它 TTS 引擎",
            )
        stream = stream.astype(np.float32)
        if stream.ndim > 1:
            logger.info("[TTS] SAPI 音频为 %d 声道，只取第一声道", stream.shape[1])
            stream = stream[:, 0]
        if sample_rate != self.sample_rate and stream.shape[0] > 0:
            logger.info(
                "[TTS] SAPI 音频采样率 %s，重采样为 %s", sample_rate, self.sample_rate
            )
            stream = resampy.resample(
                x=stream, sr_orig=sample_rate, sr_new=self.sample_rate
            )
        return stream

    def _emit_frames(self, stream, text, textevent):
        """按 20 ms 帧长切分并推送，事件点约定与既有引擎一致。"""
        streamlen = stream.shape[0]
        idx = 0
        while streamlen > 0 and self.state == State.RUNNING:
            eventpoint = {}
            frame = stream[idx:idx + self.chunk]
            actual_len = frame.shape[0]
            if actual_len < self.chunk:
                frame = np.pad(frame, (0, self.chunk - actual_len))
            streamlen -= actual_len
            if idx == 0:
                eventpoint = {"status": "start", "text": text}
            if streamlen <= 0:
                eventpoint = {"status": "end", "text": text}
            eventpoint.update(**textevent)
            self.parent.put_audio_frame(frame, eventpoint)
            idx += self.chunk
