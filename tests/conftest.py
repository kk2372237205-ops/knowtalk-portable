"""pytest 全局配置。

开发机常常没有声卡（``sounddevice`` 未安装），而 ``desktop_app`` 在导入时会做
依赖自检并**重投解释器**——在 pytest 里这会直接把进程重启掉，测试根本跑不起来。

这里在 ``desktop_app`` 被导入前塞一个 ``sounddevice`` 桩：它只提供
``OutputStream`` / ``query_devices`` / ``default`` 三个接口，够测试用；
真机上若已装 sounddevice，则保留真实实现（桩只在缺失时生效）。
"""
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _install_sounddevice_stub():
    """仅当 sounddevice 不可用时安装桩。"""
    try:
        import sounddevice  # noqa: F401
        return
    except Exception:  # noqa: BLE001
        pass

    class _Device:
        def __init__(self, name, max_out, index):
            self.name = name
            self.max_output_channels = max_out
            self.max_input_channels = 2 if max_out == 0 else 0
            self.index = index

    class _Default:
        device = (2, 0)

    stub = types.ModuleType("sounddevice")
    stub._DEVICES = [
        _Device("Speakers (Stub)", 2, 0),
        _Device("HDMI Output (Stub)", 2, 1),
        _Device("Microphone Array (Stub)", 0, 2),
    ]
    stub.query_devices = lambda: list(stub._DEVICES)
    stub.default = lambda: _Default()

    class OutputStream:
        def __init__(self, **kw):
            self.samplerate = kw.get("samplerate")
            self.channels = kw.get("channels")
            self.device = kw.get("device")
            self.dtype = kw.get("dtype")
            self.written = []
            self.closed = False

        def start(self):
            pass

        def write(self, arr):
            self.written.append(arr)

        def stop(self):
            pass

        def close(self):
            self.closed = True

    stub.OutputStream = OutputStream
    # desktop_app 的依赖自检用 importlib.util.find_spec 探测模块，
    # find_spec 对 sys.modules 里已存在的模块仍要求 __spec__ 可用，
    # 缺了会被判成"没装 sounddevice"而直接退出进程。
    import importlib.machinery

    stub.__spec__ = importlib.machinery.ModuleSpec("sounddevice", loader=None)
    stub.__version__ = "0.0.0-stub"
    sys.modules["sounddevice"] = stub


_install_sounddevice_stub()
