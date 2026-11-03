# -*- coding: utf-8 -*-
"""TTS 引擎的失败可见性、回退编排与离线引擎契约测试。

不依赖真实网络、不依赖真实语音设备：涉及 SAPI 与 edge 的部分全部打桩。
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

import comtypes
import comtypes.client

from tts.base_tts import BaseTTS, State, TTSSynthesisError, PRIMARY_FAILURE_THRESHOLD
from tts.sapi import SapiTTS, percent_to_sapi_rate, _pick_chinese_voice, synthesize_to_wav, _ensure_com_initialized, _create_com_object
import tts.sapi as sapi_mod
from utils.platform_support import DEFAULT_TTS_FALLBACK, IS_WINDOWS, default_tts_fallback


class FakeOpt(object):
    def __init__(self, tts="edgetts", fallback="", fps=25, rate="+0%"):
        self.tts = tts
        self.TTS_FALLBACK = fallback
        self.TTS_RATE = rate
        self.fps = fps
        self.REF_FILE = "zh-CN-YunjianNeural"
        self.REF_TEXT = None


class FakeParent(object):
    """记录 put_audio_frame 的调用，用于断言帧契约。"""

    def __init__(self):
        self.frames = []

    def put_audio_frame(self, frame, eventpoint=None):
        self.frames.append((np.asarray(frame), dict(eventpoint or {})))


class FakeEngine(object):
    """可控的假引擎：按预设行为抛错或产出。"""

    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.calls = 0

    def txt_to_audio(self, msg):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TTSSynthesisError("假引擎失败", hint="假提示")


class TtsErrorTestCase(unittest.TestCase):
    def test_error_carries_message_and_hint(self):
        exc = TTSSynthesisError("合成失败", hint="改用离线引擎")
        self.assertEqual(exc.message, "合成失败")
        self.assertEqual(exc.hint, "改用离线引擎")
        self.assertEqual(str(exc), "合成失败")

    def test_hint_is_optional(self):
        exc = TTSSynthesisError("只有消息")
        self.assertEqual(exc.hint, "")


class FallbackOrchestrationTestCase(unittest.TestCase):
    """回退必须至多一次、不得成环、失败必须可见。"""

    def make_tts(self, tts="edgetts", fallback="sapi"):
        parent = FakeParent()
        base = BaseTTS(FakeOpt(tts=tts, fallback=fallback), parent)
        return base, parent

    def test_main_success_does_not_touch_fallback(self):
        base, _ = self.make_tts()
        base.txt_to_audio = lambda msg: None
        self.assertTrue(base._synthesize_with_fallback(("你好", {})))
        self.assertIsNone(base.last_error)
        self.assertEqual(base.fallback_count, 0)

    def test_main_failure_triggers_fallback_once(self):
        base, _ = self.make_tts()
        base.txt_to_audio = mock.Mock(side_effect=TTSSynthesisError("主引擎失败", hint="主提示"))
        fallback = FakeEngine()
        base._ensure_fallback = lambda: fallback

        self.assertTrue(base._synthesize_with_fallback(("你好", {})))
        self.assertEqual(fallback.calls, 1, "备用引擎必须被调用恰好一次")
        self.assertEqual(base.fallback_count, 1)
        self.assertEqual(base.last_error[0], "主引擎失败")

    def test_fallback_failure_does_not_loop(self):
        base, _ = self.make_tts()
        base.txt_to_audio = mock.Mock(side_effect=TTSSynthesisError("主引擎失败"))
        fallback = FakeEngine(fail_times=99)
        base._ensure_fallback = lambda: fallback

        self.assertFalse(base._synthesize_with_fallback(("你好", {})))
        self.assertEqual(fallback.calls, 1, "备用引擎只应尝试一次，不得成环")
        self.assertEqual(base.fallback_count, 0)

    def test_no_fallback_configured_reports_failure(self):
        base, _ = self.make_tts(fallback="")
        base.txt_to_audio = mock.Mock(side_effect=TTSSynthesisError("主引擎失败", hint="主提示"))
        self.assertFalse(base._synthesize_with_fallback(("你好", {})))
        self.assertEqual(base.last_error, ("主引擎失败", "主提示"))

    def test_unexpected_error_is_also_reported(self):
        base, _ = self.make_tts(fallback="")
        base.txt_to_audio = mock.Mock(side_effect=ValueError("未预期错误"))
        self.assertFalse(base._synthesize_with_fallback(("你好", {})))
        self.assertIn("未预期错误", base.last_error[0])

    def test_fallback_same_as_main_is_invalid(self):
        base, _ = self.make_tts(tts="sapi", fallback="sapi")
        self.assertIsNone(base._ensure_fallback(), "同名备用引擎必须被判为无效")
        self.assertEqual(base._fallback_name, "", "无效配置必须被清除以免反复尝试")

    def test_unknown_fallback_is_rejected(self):
        base, _ = self.make_tts(tts="edgetts", fallback="not-a-real-engine")
        self.assertIsNone(base._ensure_fallback())
        self.assertEqual(base._fallback_name, "")

    def test_failure_is_confined_to_single_request(self):
        base, _ = self.make_tts(fallback="")
        base.txt_to_audio = mock.Mock(side_effect=TTSSynthesisError("第一次失败"))
        self.assertFalse(base._synthesize_with_fallback(("一", {})))
        # 第二次请求恢复正常，必须成功
        base.txt_to_audio = mock.Mock(return_value=None)
        self.assertTrue(base._synthesize_with_fallback(("二", {})))
        self.assertIsNone(base.last_error, "成功一次后应清除上一次的错误记录")


class DefaultFallbackTestCase(unittest.TestCase):
    """开箱默认兜底：不做任何配置时，在线引擎不可用也必须有路可走。

    背景：`edgetts` 默认引擎已被服务端持续拒绝，而备用引擎默认值是空，
    于是"默认配置"恰好等于"必然失败 + 无兜底"，整堂课无声。
    """

    def test_platform_judgement_is_explicit(self):
        """离线兜底只在提供系统语音能力的平台默认启用。"""
        self.assertEqual(default_tts_fallback("win32"), "sapi")
        self.assertEqual(default_tts_fallback("linux"), "",
                         "非 Windows 指向 sapi 只会制造一个必然失败的配置项")
        self.assertEqual(default_tts_fallback("darwin"), "")

    def test_constant_follows_current_platform(self):
        expected = "sapi" if IS_WINDOWS else ""
        self.assertEqual(DEFAULT_TTS_FALLBACK, expected)

    def test_cli_default_matches_platform_default(self):
        import config
        with mock.patch.object(sys, "argv", ["app.py"]):
            opt = config.parse_args()
        self.assertEqual(opt.TTS_FALLBACK, DEFAULT_TTS_FALLBACK,
                         "不带参数启动时，生效的备用引擎必须是平台默认兜底")

    def test_cli_argument_still_wins(self):
        import config
        with mock.patch.object(sys, "argv", ["app.py", "--TTS_FALLBACK", "cosyvoice"]):
            opt = config.parse_args()
        self.assertEqual(opt.TTS_FALLBACK, "cosyvoice")

    def test_settings_default_uses_same_judgement(self):
        """两处默认值必须同源，否则会出现"页面显示已启用、实际不回退"。"""
        from utils.settings_store import FIELD_SPECS
        self.assertEqual(FIELD_SPECS["tts.fallback"]["default"], DEFAULT_TTS_FALLBACK)


class ShortCircuitTestCase(unittest.TestCase):
    """主引擎被反复证实失败后，本会话不再为每段文本重复等待同一个失败。"""

    def make(self, tts="edgetts", fallback="sapi"):
        return BaseTTS(FakeOpt(tts=tts, fallback=fallback), FakeParent())

    def test_single_failure_does_not_short_circuit(self):
        base = self.make()
        primary = mock.Mock(side_effect=[TTSSynthesisError("第一次失败"), None])
        base.txt_to_audio = primary
        fallback = FakeEngine()
        base._ensure_fallback = lambda: fallback

        self.assertTrue(base._synthesize_with_fallback(("一", {})))
        base.txt_to_audio = mock.Mock(return_value=None)
        self.assertTrue(base._synthesize_with_fallback(("二", {})))
        self.assertTrue(base._synthesize_with_fallback(("三", {})))

        self.assertEqual(fallback.calls, 1)
        self.assertEqual(base.txt_to_audio.call_count, 2,
                         "单次失败可能只是网络抖动，之后必须继续优先使用主引擎")

    def test_success_resets_failure_streak(self):
        """失败计数是"连续"的：中间成功过一次就必须清零。"""
        base = self.make()
        base.txt_to_audio = mock.Mock(side_effect=[TTSSynthesisError("失败"), None])
        base._ensure_fallback = lambda: FakeEngine()
        self.assertTrue(base._synthesize_with_fallback(("一", {})))   # 失败 1 次
        self.assertTrue(base._synthesize_with_fallback(("二", {})))   # 成功，必须清零

        base.txt_to_audio = mock.Mock(side_effect=[TTSSynthesisError("失败"), None])
        self.assertTrue(base._synthesize_with_fallback(("三", {})))
        self.assertFalse(base._skip_primary,
                         "中间成功过就必须清零，不得凭累计次数短路")

        base.txt_to_audio = mock.Mock(return_value=None)
        self.assertTrue(base._synthesize_with_fallback(("四", {})))
        self.assertEqual(base.txt_to_audio.call_count, 1, "仍应继续优先使用主引擎")

    def test_repeated_failures_short_circuit(self):
        base = self.make()
        primary = mock.Mock(side_effect=TTSSynthesisError("主引擎持续失败"))
        base.txt_to_audio = primary
        fallback = FakeEngine()
        base._ensure_fallback = lambda: fallback

        for i in range(4):
            self.assertTrue(base._synthesize_with_fallback(("%d" % i, {})),
                            "短路后文本仍必须被合成，不得被拒绝、跳过或丢弃")

        self.assertEqual(primary.call_count, PRIMARY_FAILURE_THRESHOLD,
                         "达到阈值后不得再调用已被证实失败的主引擎")
        self.assertEqual(fallback.calls, 4, "每一段文本都必须由备用引擎合成")

    def test_no_short_circuit_without_fallback(self):
        base = self.make(fallback="")
        primary = mock.Mock(side_effect=TTSSynthesisError("失败"))
        base.txt_to_audio = primary

        for _ in range(3):
            self.assertFalse(base._synthesize_with_fallback(("x", {})))

        self.assertFalse(base._skip_primary, "没有可接管的备用引擎时不得短路")
        self.assertEqual(primary.call_count, 3, "仍应继续尝试主引擎")

    def test_missing_fallback_exits_short_circuit(self):
        base = self.make()
        base.txt_to_audio = mock.Mock(side_effect=TTSSynthesisError("主引擎失败"))
        base._ensure_fallback = lambda: FakeEngine()
        for _ in range(PRIMARY_FAILURE_THRESHOLD):
            base._synthesize_with_fallback(("x", {}))
        self.assertTrue(base._skip_primary)

        base._ensure_fallback = lambda: None          # 备用引擎不再可用
        base.txt_to_audio = mock.Mock(return_value=None)
        self.assertTrue(base._synthesize_with_fallback(("y", {})))
        self.assertFalse(base._skip_primary, "备用引擎不可用时短路的前提不成立")
        self.assertEqual(base.txt_to_audio.call_count, 1, "解除后必须重新尝试主引擎")

    def test_fallback_failure_exits_short_circuit(self):
        base = self.make()
        primary = mock.Mock(side_effect=TTSSynthesisError("主引擎失败"))
        base.txt_to_audio = primary
        fallback = FakeEngine()
        base._ensure_fallback = lambda: fallback
        for _ in range(PRIMARY_FAILURE_THRESHOLD):
            self.assertTrue(base._synthesize_with_fallback(("x", {})))
        self.assertTrue(base._skip_primary)

        fallback.fail_times = 99                      # 备用引擎也开始失败
        self.assertFalse(base._synthesize_with_fallback(("y", {})))
        self.assertFalse(base._skip_primary, "备用引擎失效必须解除短路")

        base.txt_to_audio = mock.Mock(return_value=None)
        self.assertTrue(base._synthesize_with_fallback(("z", {})))
        self.assertEqual(base.txt_to_audio.call_count, 1, "解除后必须重新尝试主引擎")

    def test_short_circuit_is_per_session(self):
        opt = FakeOpt()
        first = BaseTTS(opt, FakeParent())
        first.txt_to_audio = mock.Mock(side_effect=TTSSynthesisError("失败"))
        first._ensure_fallback = lambda: FakeEngine()
        for _ in range(PRIMARY_FAILURE_THRESHOLD):
            first._synthesize_with_fallback(("x", {}))
        self.assertTrue(first._skip_primary)

        second = BaseTTS(opt, FakeParent())
        primary = mock.Mock(return_value=None)
        second.txt_to_audio = primary
        self.assertTrue(second._synthesize_with_fallback(("y", {})))
        self.assertEqual(primary.call_count, 1,
                         "新会话必须重新尝试主引擎，使外部服务恢复后能自动回到好音质")

    def test_transitions_are_logged(self):
        base = self.make()
        base.txt_to_audio = mock.Mock(side_effect=TTSSynthesisError("主引擎失败"))
        fallback = FakeEngine()
        base._ensure_fallback = lambda: fallback
        with self.assertLogs("utils.logger", level="WARNING") as captured:
            for _ in range(PRIMARY_FAILURE_THRESHOLD):
                base._synthesize_with_fallback(("x", {}))
            fallback.fail_times = 99
            base._synthesize_with_fallback(("y", {}))
        text = "\n".join(captured.output)
        self.assertIn("接管", text, "进入短路必须留下日志（说明由备用引擎接管）")
        self.assertIn("解除", text, "解除短路必须留下日志")


class SapiRateMappingTestCase(unittest.TestCase):
    def test_percent_to_rate(self):
        self.assertEqual(percent_to_sapi_rate("+0%"), 0)
        self.assertEqual(percent_to_sapi_rate("+20%"), 2)
        self.assertEqual(percent_to_sapi_rate("-30%"), -3)
        self.assertEqual(percent_to_sapi_rate("10%"), 1)

    def test_rate_is_clamped(self):
        self.assertEqual(percent_to_sapi_rate("+500%"), 10)
        self.assertEqual(percent_to_sapi_rate("-500%"), -10)

    def test_invalid_text_falls_back_to_default(self):
        self.assertEqual(percent_to_sapi_rate("快一点", default=3), 3)
        self.assertEqual(percent_to_sapi_rate(None, default=1), 1)


class FakeComVoice(object):
    def __init__(self, descriptions):
        self._items = [FakeComVoiceItem(d) for d in descriptions]

    def GetVoices(self):
        return FakeComVoiceList(self._items)


class FakeComVoiceItem(object):
    def __init__(self, description, language=""):
        self._description = description
        self._language = language

    def GetDescription(self):
        return self._description

    def GetAttribute(self, name):
        if name != "Language":
            return ""
        return self._language


class FakeComVoiceList(object):
    def __init__(self, items):
        self._items = items
        self.Count = len(items)

    def Item(self, index):
        return self._items[index]


class SapiVoiceSelectionTestCase(unittest.TestCase):
    """语音识别以 Language（LCID）为主信号，描述串仅作兜底。

    实测本机：Huihui = '804'（zh-CN 简体），Zira = '409'（en-US）。
    """

    def test_prefers_chinese_voice(self):
        voice = FakeComVoice([])
        voice._items = [
            FakeComVoiceItem("Microsoft Zira Desktop - English (United States)", "409"),
            FakeComVoiceItem("Microsoft Huihui Desktop - Chinese (Simplified)", "804"),
        ]
        voice.GetVoices = lambda: FakeComVoiceList(voice._items)
        picked = _pick_chinese_voice(voice)
        self.assertEqual(picked.GetDescription(), "Microsoft Huihui Desktop - Chinese (Simplified)")

    def test_uses_language_attribute(self):
        voice = FakeComVoice([])
        voice._items = [
            FakeComVoiceItem("Some Voice", "409"),
            FakeComVoiceItem("另一个", "804"),
        ]
        voice.GetVoices = lambda: FakeComVoiceList(voice._items)
        picked = _pick_chinese_voice(voice)
        self.assertEqual(picked.GetDescription(), "另一个")

    def test_description_keyword_is_fallback(self):
        voice = FakeComVoice([])
        voice._items = [FakeComVoiceItem("Microsoft Huihui Desktop", "")]
        voice.GetVoices = lambda: FakeComVoiceList(voice._items)
        picked = _pick_chinese_voice(voice)
        self.assertEqual(picked.GetDescription(), "Microsoft Huihui Desktop")

    def test_traditional_chinese_accepted(self):
        voice = FakeComVoice([])
        voice._items = [FakeComVoiceItem("Microsoft Hanhan Desktop", "404")]
        voice.GetVoices = lambda: FakeComVoiceList(voice._items)
        picked = _pick_chinese_voice(voice)
        self.assertEqual(picked.GetDescription(), "Microsoft Hanhan Desktop")

    def test_simplified_preferred_over_traditional(self):
        voice = FakeComVoice([])
        voice._items = [
            FakeComVoiceItem("Traditional Voice", "404"),
            FakeComVoiceItem("Simplified Voice", "804"),
        ]
        voice.GetVoices = lambda: FakeComVoiceList(voice._items)
        picked = _pick_chinese_voice(voice)
        self.assertEqual(picked.GetDescription(), "Simplified Voice")

    def test_preferred_voice_is_used(self):
        """界面上选定本机 SAPI 音色后，必须精确选用它（按描述串匹配）。"""
        voice = FakeComVoice([])
        voice._items = [
            FakeComVoiceItem("Microsoft Zira Desktop - English (United States)", "409"),
            FakeComVoiceItem("Microsoft Huihui Desktop - Chinese (Simplified)", "804"),
        ]
        voice.GetVoices = lambda: FakeComVoiceList(voice._items)
        picked = _pick_chinese_voice(voice, preferred="Microsoft Huihui Desktop")
        self.assertEqual(picked.GetDescription(),
                         "Microsoft Huihui Desktop - Chinese (Simplified)")

    def test_preferred_edge_name_falls_back_to_chinese_pick(self):
        """默认 REF_FILE 是 Edge 语音名（zh-CN-*），SAPI 里没有对应项：
        必须退回自动挑选中文语音，而不是报错。"""
        voice = FakeComVoice([])
        voice._items = [
            FakeComVoiceItem("Microsoft Zira Desktop - English (United States)", "409"),
            FakeComVoiceItem("Microsoft Huihui Desktop - Chinese (Simplified)", "804"),
        ]
        voice.GetVoices = lambda: FakeComVoiceList(voice._items)
        picked = _pick_chinese_voice(voice, preferred="zh-CN-YunjianNeural")
        self.assertEqual(picked.GetDescription(),
                         "Microsoft Huihui Desktop - Chinese (Simplified)")

    def test_no_voices_reports_clearly(self):
        with self.assertRaises(TTSSynthesisError) as ctx:
            _pick_chinese_voice(FakeComVoice([]))
        self.assertIn("未安装任何可用", ctx.exception.message)
        self.assertTrue(ctx.exception.hint)

    def test_no_chinese_voice_reports_clearly(self):
        voice = FakeComVoice([])
        voice._items = [FakeComVoiceItem("Microsoft Zira Desktop - English (United States)", "409")]
        voice.GetVoices = lambda: FakeComVoiceList(voice._items)
        with self.assertRaises(TTSSynthesisError) as ctx:
            _pick_chinese_voice(voice)
        self.assertIn("没有中文语音", ctx.exception.message)
        self.assertIn("Zira", ctx.exception.message, "错误信息应列出已安装语音以便排查")


class SapiComInitTestCase(unittest.TestCase):
    """SAPI 是每线程 COM 模型：工作线程使用前必须确保当前线程 COM 已初始化。

    真实服务进程里 comtypes 通常已由主线程导入，工作线程直接创建 SAPI 对象
    会报 CO_E_NOTINITIALIZED（-2147221008）；guard 必须幂等地补上初始化。
    """

    def test_guard_tolerates_changed_mode(self):
        # RPC_E_CHANGED_MODE：线程已用其它模型初始化过 COM，直接可用，不应抛
        with mock.patch.object(comtypes, "CoInitializeEx",
                               side_effect=OSError(22, "changed mode", None, -2147417850, None)):
            _ensure_com_initialized()  # 不抛异常

    def test_guard_tolerates_missing_comtypes(self):
        with mock.patch.dict("sys.modules", {"comtypes": None, "comtypes.client": None}):
            _ensure_com_initialized()  # 不抛异常；后续 CreateObject 会给出明确报错

    def test_create_object_inits_com_before_creating(self):
        with mock.patch.object(sapi_mod, "_ensure_com_initialized") as guard, \
                mock.patch.object(comtypes.client, "CreateObject",
                                  return_value=object()) as create:
            obj = _create_com_object("SAPI.SpVoice")
            guard.assert_called_once()
            create.assert_called_once_with("SAPI.SpVoice")
            self.assertIsNotNone(obj)


class SapiPlatformTestCase(unittest.TestCase):
    def test_non_windows_reports_clearly(self):
        with mock.patch("tts.sapi.IS_WINDOWS", False):
            with self.assertRaises(TTSSynthesisError) as ctx:
                synthesize_to_wav("测试")
        self.assertIn("仅在 Windows 上可用", ctx.exception.message)
        self.assertTrue(ctx.exception.hint)

    def test_missing_comtypes_reports_clearly(self):
        with mock.patch("tts.sapi.IS_WINDOWS", True):
            with mock.patch.dict("sys.modules", {"comtypes": None, "comtypes.client": None}):
                with self.assertRaises(TTSSynthesisError) as ctx:
                    synthesize_to_wav("测试")
        self.assertIn("comtypes", ctx.exception.message)


class SapiFrameContractTestCase(unittest.TestCase):
    """换引擎不得破坏下游：16 kHz 单声道、既有帧长、事件点约定一致。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="sapi-test-")
        self.parent = FakeParent()
        self.tts = SapiTTS(FakeOpt(tts="sapi"), self.parent)
        self.expected_chunk = 16000 // (25 * 2)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_chunk_size_matches_existing_engines(self):
        self.assertEqual(self.tts.chunk, self.expected_chunk)
        self.assertEqual(self.tts.sample_rate, 16000)

    def test_frames_and_eventpoints(self):
        # 1 秒 24 kHz 单声道音频，应被重采样为 16 kHz 并切成 50 帧
        samples = np.zeros(24000, dtype=np.float32)
        path = os.path.join(self.tmpdir, "fake.wav")
        import soundfile as sf
        sf.write(path, samples, 24000)

        stream = self.tts._read_as_stream(path)
        self.assertEqual(stream.ndim, 1, "必须是单声道")
        self.assertAlmostEqual(stream.shape[0], 16000, delta=50, msg="必须重采样到 16 kHz")

        self.tts._emit_frames(stream, "测试文本", {"slide_id": 2})
        self.assertGreater(len(self.parent.frames), 0)
        for frame, _ in self.parent.frames:
            self.assertEqual(frame.shape[0], self.expected_chunk, "每帧必须是固定帧长")
        first = self.parent.frames[0][1]
        last = self.parent.frames[-1][1]
        self.assertEqual(first.get("status"), "start")
        self.assertEqual(last.get("status"), "end")
        self.assertEqual(first.get("slide_id"), 2, "textevent 必须被合并进事件点")

    def test_multichannel_is_downmixed(self):
        samples = np.zeros((16000, 2), dtype=np.float32)
        path = os.path.join(self.tmpdir, "stereo.wav")
        import soundfile as sf
        sf.write(path, samples, 16000)
        stream = self.tts._read_as_stream(path)
        self.assertEqual(stream.ndim, 1, "多声道必须只取第一声道")

    def test_undecodable_audio_reports_expected_error(self):
        """损坏音频必须变成 TTSSynthesisError，不得泄漏原始库异常。"""
        path = os.path.join(self.tmpdir, "broken.wav")
        with open(path, "wb") as fh:
            fh.write(b"this is not audio")
        with self.assertRaises(TTSSynthesisError) as ctx:
            self.tts._read_as_stream(path)
        self.assertIn("无法解码", ctx.exception.message)
        self.assertTrue(ctx.exception.hint)

    def test_paused_state_stops_emitting(self):
        self.tts.state = State.PAUSE
        stream = np.zeros(16000, dtype=np.float32)
        self.tts._emit_frames(stream, "文本", {})
        self.assertEqual(self.parent.frames, [], "暂停状态不得继续推送帧")


class SapiTempFileTestCase(unittest.TestCase):
    def test_temp_file_removed_on_success(self):
        parent = FakeParent()
        tts = SapiTTS(FakeOpt(tts="sapi"), parent)
        created = {}

        def fake_synth(text, rate, preferred=None):
            import soundfile as sf
            fd, path = tempfile.mkstemp(prefix="sapi-t-", suffix=".wav")
            os.close(fd)
            sf.write(path, np.zeros(1600, dtype=np.float32), 16000)
            created["path"] = path
            return path

        with mock.patch("tts.sapi.synthesize_to_wav", side_effect=fake_synth):
            tts.txt_to_audio(("你好", {}))
        self.assertFalse(os.path.exists(created["path"]), "成功路径必须删除临时文件")

    def test_temp_file_removed_on_failure(self):
        parent = FakeParent()
        tts = SapiTTS(FakeOpt(tts="sapi"), parent)
        created = {}

        def fake_synth(text, rate, preferred=None):
            fd, path = tempfile.mkstemp(prefix="sapi-t-", suffix=".wav")
            os.close(fd)
            with open(path, "wb") as fh:
                fh.write(b"not-a-real-wav")
            created["path"] = path
            return path

        with mock.patch("tts.sapi.synthesize_to_wav", side_effect=fake_synth):
            with self.assertRaises(TTSSynthesisError):
                tts.txt_to_audio(("你好", {}))
        self.assertFalse(os.path.exists(created["path"]), "失败路径也必须删除临时文件")


if __name__ == "__main__":
    unittest.main()
