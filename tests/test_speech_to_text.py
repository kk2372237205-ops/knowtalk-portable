# -*- coding: utf-8 -*-
"""语音转写的测试（TDD：这些断言先于实现存在）。

不依赖真实模型与真实音频：模型加载与推理全部打桩。
"""

import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from utils.speech_to_text import (
    SIMPLIFIED_PROMPT,
    SpeechToTextError,
    clear_model_cache,
    get_config,
    is_busy,
    transcribe_audio,
    validate_audio_name,
    validate_size,
)

# 并发额度是模块级的，故意让后台工作跑完，避免测试之间互相污染
BUSY_SETTLE_TIMEOUT = 8.0


def wait_until_idle():
    deadline = time.time() + BUSY_SETTLE_TIMEOUT
    while is_busy() and time.time() < deadline:
        time.sleep(0.05)
    return not is_busy()


class FakeSegment(object):
    def __init__(self, text):
        self.text = text


class FakeModel(object):
    """可控的假模型：记录调用参数，返回预设文本。"""

    def __init__(self, text="同学们好。", delay=0.0, raise_exc=None):
        self.text = text
        self.delay = delay
        self.raise_exc = raise_exc
        self.calls = []
        self.load_count = 0

    def transcribe(self, path, language=None, beam_size=None, initial_prompt=None, **kwargs):
        self.calls.append({
            "path": path, "language": language, "beam_size": beam_size,
            "initial_prompt": initial_prompt,
        })
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.delay:
            time.sleep(self.delay)
        return iter([FakeSegment(self.text)]), {"language": language}


class SpeechToTextBase(unittest.TestCase):
    def setUp(self):
        wait_until_idle()
        clear_model_cache()
        self.tmpdir = tempfile.mkdtemp(prefix="stt-test-")
        self.audio = os.path.join(self.tmpdir, "answer.wav")
        with open(self.audio, "wb") as handle:
            handle.write(b"RIFF" + b"\x00" * 200)

    def tearDown(self):
        wait_until_idle()
        clear_model_cache()
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class ConfigTestCase(unittest.TestCase):
    """6.2 模型规格可配置且支持本地模型目录，缺失时回退默认。"""

    def test_defaults_when_nothing_configured(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in ("STT_MODEL", "STT_DEVICE", "STT_COMPUTE_TYPE", "STT_TIMEOUT",
                        "STT_MAX_MB", "STT_LANGUAGE"):
                os.environ.pop(key, None)
            config = get_config()
        self.assertEqual(config["model"], "small")
        self.assertEqual(config["device"], "cpu")
        self.assertEqual(config["compute_type"], "int8")
        self.assertEqual(config["language"], "zh")
        self.assertGreater(config["timeout"], 0)
        self.assertGreater(config["max_bytes"], 0)

    def test_local_directory_is_used_as_model(self):
        with mock.patch.dict(os.environ, {"STT_MODEL": r"D:\models\faster-whisper-small"}):
            self.assertEqual(get_config()["model"], r"D:\models\faster-whisper-small")

    def test_cuda_device_picks_float16_by_default(self):
        with mock.patch.dict(os.environ, {"STT_DEVICE": "cuda"}, clear=False):
            os.environ.pop("STT_COMPUTE_TYPE", None)
            self.assertEqual(get_config()["compute_type"], "float16")

    def test_invalid_timeout_falls_back(self):
        with mock.patch.dict(os.environ, {"STT_TIMEOUT": "abc"}):
            self.assertGreater(get_config()["timeout"], 0)


class ValidationTestCase(unittest.TestCase):
    """6.3 格式与体积上限。"""

    def test_supported_suffixes_accepted(self):
        for name in ("a.wav", "a.mp3", "a.M4A", "a.flac", "a.ogg", "a.webm"):
            self.assertTrue(validate_audio_name(name))

    def test_unsupported_suffix_rejected_naming_supported(self):
        with self.assertRaises(SpeechToTextError) as ctx:
            validate_audio_name("a.txt")
        self.assertIn("txt", str(ctx.exception))
        self.assertIn("wav", str(ctx.exception), "错误信息应列出支持的格式")

    def test_size_over_limit_rejected(self):
        with self.assertRaises(SpeechToTextError) as ctx:
            validate_size(30 * 1024 * 1024, 25 * 1024 * 1024)
        self.assertIn("25", str(ctx.exception), "错误信息应说明上限")

    def test_size_at_limit_accepted(self):
        validate_size(25 * 1024 * 1024, 25 * 1024 * 1024)


class TranscribeTestCase(SpeechToTextBase):
    def test_returns_recognized_text(self):
        with mock.patch("utils.speech_to_text._load_model", return_value=FakeModel("你好世界")):
            self.assertEqual(transcribe_audio(self.audio), "你好世界")

    def test_simplified_prompt_is_always_passed(self):
        """实测：不传简体 initial_prompt 时模型会输出繁体，必须始终传。"""
        model = FakeModel("同学们好")
        with mock.patch("utils.speech_to_text._load_model", return_value=model):
            transcribe_audio(self.audio)
        self.assertEqual(model.calls[0]["initial_prompt"], SIMPLIFIED_PROMPT)

    def test_language_defaults_to_chinese(self):
        model = FakeModel()
        with mock.patch("utils.speech_to_text._load_model", return_value=model):
            transcribe_audio(self.audio)
        self.assertEqual(model.calls[0]["language"], "zh")

    def test_language_can_be_overridden(self):
        model = FakeModel()
        with mock.patch("utils.speech_to_text._load_model", return_value=model):
            transcribe_audio(self.audio, language="en")
        self.assertEqual(model.calls[0]["language"], "en")

    def test_empty_result_is_reported_as_no_speech(self):
        with mock.patch("utils.speech_to_text._load_model", return_value=FakeModel("   ")):
            with self.assertRaises(SpeechToTextError) as ctx:
                transcribe_audio(self.audio)
        self.assertIn("未识别到语音", str(ctx.exception))
        self.assertTrue(ctx.exception.hint)

    def test_whitespace_only_result_is_rejected(self):
        with mock.patch("utils.speech_to_text._load_model", return_value=FakeModel("\n\t")):
            with self.assertRaises(SpeechToTextError):
                transcribe_audio(self.audio)

    def test_missing_file_is_reported(self):
        with self.assertRaises(SpeechToTextError) as ctx:
            transcribe_audio(os.path.join(self.tmpdir, "nope.wav"))
        self.assertIn("不存在", str(ctx.exception))

    def test_missing_dependency_reports_clearly(self):
        with mock.patch("utils.speech_to_text._load_model",
                        side_effect=SpeechToTextError("缺少 faster-whisper 依赖", hint="pip install")):
            with self.assertRaises(SpeechToTextError) as ctx:
                transcribe_audio(self.audio)
        self.assertIn("faster-whisper", str(ctx.exception))
        self.assertTrue(ctx.exception.hint)


class ModelCacheTestCase(SpeechToTextBase):
    """6.1 模型按需加载、进程内复用、同一规格不重复加载。"""

    def test_model_is_not_loaded_before_first_use(self):
        loader = mock.Mock(return_value=FakeModel())
        with mock.patch("utils.speech_to_text._load_model", loader):
            self.assertEqual(loader.call_count, 0)
            transcribe_audio(self.audio)
            self.assertEqual(loader.call_count, 1)

    def test_same_spec_loads_model_once(self):
        loader = mock.Mock(return_value=FakeModel())
        with mock.patch("utils.speech_to_text._load_model", loader):
            transcribe_audio(self.audio)
            transcribe_audio(self.audio)
            transcribe_audio(self.audio)
        self.assertEqual(loader.call_count, 1, "同一规格不得重复加载模型")

    def test_different_spec_loads_separately(self):
        loader = mock.Mock(side_effect=lambda *a, **k: FakeModel())
        with mock.patch("utils.speech_to_text._load_model", loader):
            transcribe_audio(self.audio, model_ref="small")
            transcribe_audio(self.audio, model_ref="base")
        self.assertEqual(loader.call_count, 2)

    def test_error_from_loader_is_propagated(self):
        with mock.patch("utils.speech_to_text._load_model",
                        side_effect=SpeechToTextError("模型目录不可用", hint="检查路径")):
            with self.assertRaises(SpeechToTextError) as ctx:
                transcribe_audio(self.audio, model_ref="D:/nope")
        self.assertIn("模型目录不可用", str(ctx.exception))


class ConcurrencyTestCase(SpeechToTextBase):
    """6.4 并发上限 1：超出立即拒绝，不排队到超时。"""

    def test_second_call_is_rejected_immediately_while_busy(self):
        gate = threading.Event()
        model = FakeModel()

        def slow_transcribe(*args, **kwargs):
            gate.wait(5)
            return iter([FakeSegment("慢的结果")]), {}

        model.transcribe = slow_transcribe
        results = {}

        def first():
            with mock.patch("utils.speech_to_text._load_model", return_value=model):
                results["first"] = transcribe_audio(self.audio, timeout=10)

        thread = threading.Thread(target=first)
        thread.start()
        time.sleep(0.3)   # 让第一个请求进入转写

        started = time.time()
        with mock.patch("utils.speech_to_text._load_model", return_value=model):
            with self.assertRaises(SpeechToTextError) as ctx:
                transcribe_audio(self.audio, timeout=10)
        elapsed = time.time() - started
        self.assertLess(elapsed, 1.0, "繁忙时必须立即拒绝，而不是排队等待")
        self.assertIn("正在", str(ctx.exception))
        self.assertTrue(getattr(ctx.exception, "busy", False), "必须标记为「正在忙」以便上层区分")

        gate.set()
        thread.join(5)
        self.assertEqual(results.get("first"), "慢的结果")

    def test_lock_is_released_after_completion(self):
        with mock.patch("utils.speech_to_text._load_model", return_value=FakeModel("好")):
            transcribe_audio(self.audio)
            transcribe_audio(self.audio)   # 第二次应能正常进行


class TimeoutTestCase(SpeechToTextBase):
    """6.6 超时必须返回业务错误而不是无限等待。"""

    def test_timeout_returns_error(self):
        model = FakeModel("结果")
        model.transcribe = lambda *a, **k: (time.sleep(5) or (iter([]), {}))
        with mock.patch("utils.speech_to_text._load_model", return_value=model):
            started = time.time()
            with self.assertRaises(SpeechToTextError) as ctx:
                transcribe_audio(self.audio, timeout=0.4)
            elapsed = time.time() - started
        self.assertIn("超时", str(ctx.exception))
        self.assertLess(elapsed, 3.0, "不得等到实际转写结束才返回")

    def test_busy_flag_stays_set_until_work_really_finishes(self):
        """超时只是"不再等"，实际工作仍占用并发额度，避免真并发。"""
        model = FakeModel()
        model.transcribe = lambda *a, **k: (time.sleep(0.8) or (iter([FakeSegment("x")]), {}))
        loader = mock.Mock(return_value=model)
        with mock.patch("utils.speech_to_text._load_model", loader):
            with self.assertRaises(SpeechToTextError):
                transcribe_audio(self.audio, timeout=0.2)
            # 工作线程仍在跑，此时再次请求应被拒绝而不是并发执行
            with self.assertRaises(SpeechToTextError) as ctx:
                transcribe_audio(self.audio, timeout=5)
            self.assertTrue(getattr(ctx.exception, "busy", False))
            time.sleep(1.2)
            # 并发额度已释放；模型仍被缓存复用，所以拿到的还是同一个模型的结果
            self.assertEqual(transcribe_audio(self.audio), "x")
            self.assertEqual(loader.call_count, 1, "模型必须被缓存复用而不是重新加载")


if __name__ == "__main__":
    unittest.main()
