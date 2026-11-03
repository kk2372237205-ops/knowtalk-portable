"""音频帧整理的回归测试。

背景：桌面端曾因把 ``av`` 的 ``(channels, samples)`` / packed 交织形状误判，
导致 ``sd.OutputStream(channels=1920)`` 抛错且被静默吞掉 —— 表现为
"画面在动、完全没有声音"。这里锁定正确行为。

运行：``python -m pytest tests/test_audio_playback.py -q``
（不需要声卡/显示器，可在开发机与舱体 PC 上都跑）
"""
import numpy as np
import pytest

from utils.audio_playback import (
    prepare_audio_frame,
    reshape_for_sounddevice,
    frame_channels,
)

av = pytest.importorskip("av")


def _install_fake_sounddevice(monkeypatch, fake):
    """把 sounddevice 换成假模块。

    两处都要改：
    1. ``sys.modules['sounddevice']`` —— 供**之后**才 import 的代码用；
       必须带 ``__spec__``，否则 ``find_spec()`` 抛 ValueError，
       ``desktop_app`` 的依赖自检会误判成"没装 sounddevice"并 ``sys.exit(1)``。
    2. ``desktop_app.sd`` —— 模块顶层已 ``import sounddevice as sd``，
       持有的是原模块引用；只换 sys.modules 对已导入的代码**无效**。
    """
    import importlib.machinery
    import sys

    fake.__spec__ = importlib.machinery.ModuleSpec("sounddevice", loader=None)
    monkeypatch.setitem(sys.modules, "sounddevice", fake)

    # desktop_app 若还没被导入就主动导入（此时 sys.modules 里已是假模块，
    # 它顶层 `import sounddevice as sd` 拿到的就是假的），再替换模块属性。
    import desktop_app
    monkeypatch.setattr(desktop_app, "sd", fake, raising=False)
    return fake


def _make_packed(arr_1d, channels, sample_rate=48000):
    """构造一个 packed 格式的 AudioFrame（模拟 Opus 解码输出）。"""
    from av import AudioFrame
    frame = AudioFrame.from_ndarray(
        np.ascontiguousarray(arr_1d, dtype=np.int16).reshape(1, -1),
        format="s16",
        layout="stereo" if channels == 2 else "mono",
    )
    frame.sample_rate = sample_rate
    return frame


class TestPrepareAudioFrame:
    def test_packed_stereo_opus_frame_gives_two_channels(self):
        """客户端实际收到的帧：s16/stereo/48k packed，960 samples/channel。

        to_ndarray() -> (1, 1920)。必须还原成 (960, 2)，声道数=2。
        旧代码取 shape[1]=1920 当声道数 -> sd 抛错 -> 静音。
        """
        frame = _make_packed(np.arange(1920, dtype=np.int16), channels=2)
        raw = frame.to_ndarray()
        assert raw.shape == (1, 1920), "前提变了：av 的 packed 行为已改"

        arr, channels = prepare_audio_frame(frame)
        assert channels == 2
        assert arr.shape == (960, 2)
        assert arr.dtype == np.int16

    def test_packed_stereo_preserves_interleaving(self):
        """交织顺序不能错位：第 n 个样本的左声道在 [:, 0]。"""
        raw = np.arange(1920, dtype=np.int16)
        frame = _make_packed(raw, channels=2)
        arr, channels = prepare_audio_frame(frame)
        assert channels == 2
        # 交织：偶数下标是左声道，奇数下标是右声道
        assert arr[0, 0] == 0
        assert arr[0, 1] == 1
        assert arr[1, 0] == 2
        assert arr[1, 1] == 3
        assert arr[-1, 1] == 1919

    def test_packed_mono_frame_gives_one_channel(self):
        """服务端 push_audio 的形状 (1, 320) 单声道 packed。"""
        frame = _make_packed(np.arange(320, dtype=np.int16), channels=1)
        arr, channels = prepare_audio_frame(frame)
        assert channels == 1
        assert arr.shape == (320, 1)

    def test_planar_frame_is_transposed(self):
        """planar（fltp）帧：to_ndarray() 给 (channels, samples)，需转置。"""
        from av import AudioFrame
        data = np.arange(2 * 960, dtype=np.float32).reshape(2, 960)
        frame = AudioFrame.from_ndarray(data, format="fltp", layout="stereo")
        frame.sample_rate = 48000
        raw = frame.to_ndarray()
        assert raw.shape == (2, 960)

        arr, channels = prepare_audio_frame(frame)
        assert channels == 2
        assert arr.shape == (960, 2)

    def test_float_amplitude_is_scaled_not_truncated(self):
        """float 样本 0.3 若直接 astype(int16) 会变 0 -> 静音。

        必须按峰值缩放到 int16 范围。
        """
        from av import AudioFrame
        data = np.full((1, 960), 0.3, dtype=np.float32)
        frame = AudioFrame.from_ndarray(data, format="fltp", layout="mono")
        frame.sample_rate = 48000

        arr, channels = prepare_audio_frame(frame)
        assert channels == 1
        assert np.max(np.abs(arr)) > 1000, "float 幅度被截断成静音了"

    def test_1d_frame_treated_as_mono(self):
        from av import AudioFrame
        frame = AudioFrame.from_ndarray(
            np.zeros((1, 320), dtype=np.int16), format="s16", layout="mono"
        )
        frame.sample_rate = 16000
        arr, channels = prepare_audio_frame(frame)
        assert channels == 1
        assert arr.shape[0] == 320

    def test_frame_channels_handles_tuple_and_int(self):
        """av 不同版本 layout.channels 可能是 tuple 或 int。"""
        from av import AudioFrame
        frame = _make_packed(np.zeros(1920, dtype=np.int16), channels=2)
        assert frame_channels(frame) == 2


class TestReshapeFallback:
    def test_1d_becomes_column(self):
        arr, channels = reshape_for_sounddevice(np.zeros(320, dtype=np.int16))
        assert channels == 1
        assert arr.shape == (320, 1)

    def test_2d_transposed_when_rows_less_than_cols(self):
        arr, channels = reshape_for_sounddevice(
            np.zeros((1, 320), dtype=np.int16)
        )
        assert arr.shape == (320, 1)
        assert channels == 1

    def test_output_is_int16(self):
        arr, _ = reshape_for_sounddevice(np.zeros((1, 320), dtype=np.float32))
        assert arr.dtype == np.int16

    def test_float_not_truncated_to_zero(self):
        arr, _ = reshape_for_sounddevice(np.full((1, 320), 0.5, dtype=np.float32))
        assert np.max(np.abs(arr)) > 1000


class TestListOutputDevices:
    def test_lists_only_output_capable_devices(self, monkeypatch):
        """麦克风等输入设备不该出现在输出列表里。"""
        import importlib.machinery
        import sys
        import types

        class _Device:
            def __init__(self, name, max_out):
                self.name = name
                self.max_output_channels = max_out

        class _Default:
            device = (2, 0)

        fake = types.SimpleNamespace(
            query_devices=lambda: [
                _Device("Speakers", 2),
                _Device("HDMI", 2),
                _Device("Mic", 0),
            ],
            default=lambda: _Default(),
        )
        _install_fake_sounddevice(monkeypatch, fake)

        from utils.audio_playback import list_output_devices
        devices = list_output_devices()

        ids = [d for d, _ in devices]
        names = [n for _, n in devices]
        assert ids[0] is None, "第一项必须是系统默认"
        assert "（系统默认）" in names[0]
        assert 0 in ids and 1 in ids, "两个输出设备都应列出（设备 id 为其索引）"
        assert not any("Mic" in n for n in names), "输入设备不该出现"

    def test_returns_default_only_when_sounddevice_missing(self, monkeypatch):
        import builtins
        import sys

        real_import = builtins.__import__

        def _blocked(name, *args, **kwargs):
            if name == "sounddevice":
                raise ImportError("no sounddevice")
            return real_import(name, *args, **kwargs)

        monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
        monkeypatch.setattr(builtins, "__import__", _blocked)

        from utils.audio_playback import list_output_devices
        devices = list_output_devices()
        assert devices == [(None, "（系统默认）")]


class TestAudioPlayerWrites:
    """验证 AudioPlayer 真的按 (samples, channels) 写出，且不会静默吞错。"""

    def _player(self, tmp_path, device=None, sd_stub=None):
        import importlib
        import sys
        import types

        if sd_stub is not None:
            sys.modules["sounddevice"] = sd_stub
        import desktop_app
        return desktop_app.AudioPlayer(device=device)

    def test_writes_correct_shape_to_stream(self, monkeypatch):
        import sys
        import types
        import time

        written = []

        class FakeStream:
            def __init__(self, **kw):
                self.samplerate = kw.get("samplerate")
                self.channels = kw.get("channels")
                self.device = kw.get("device")
            def start(self): pass
            def write(self, arr): written.append(np.asarray(arr))
            def stop(self): pass
            def close(self): pass

        fake = types.SimpleNamespace(OutputStream=FakeStream)
        _install_fake_sounddevice(monkeypatch, fake)

        import desktop_app
        player = desktop_app.AudioPlayer(device=3)
        player.start()
        try:
            arr = np.zeros((960, 2), dtype=np.int16)
            player.play(arr, 48000, 2)
            deadline = time.time() + 3
            while time.time() < deadline and not written:
                time.sleep(0.05)
        finally:
            player.stop()

        assert written, "没有向输出流写入任何数据"
        assert written[0].shape == (960, 2), "写入形状必须是 (samples, channels)"
        # 设备 id 必须传下去
        assert player._device == 3

    def test_test_tone_generates_non_silent_audio(self, monkeypatch):
        import sys
        import types

        written = []

        class FakeStream:
            def __init__(self, **kw):
                self.samplerate = kw.get("samplerate")
                self.channels = kw.get("channels")
            def start(self): pass
            def write(self, arr): written.append(np.asarray(arr))
            def stop(self): pass
            def close(self): pass

        fake = types.SimpleNamespace(OutputStream=FakeStream)
        _install_fake_sounddevice(monkeypatch, fake)

        import desktop_app
        player = desktop_app.AudioPlayer()
        player.start()
        try:
            assert player.play_test_tone() is True
            import time
            deadline = time.time() + 3
            while time.time() < deadline and not written:
                time.sleep(0.05)
        finally:
            player.stop()

        assert written, "测试提示音没有写进输出流"
        assert np.max(np.abs(written[0])) > 1000, "测试音是静音的"
