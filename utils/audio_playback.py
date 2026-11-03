"""音频帧形状整理：把 WebRTC 收到的音频帧还原成 sounddevice 能播放的二维数组。

背景（真实的"桌面端无声"根因）
--------------------------------
桌面端从 WebRTC 音频轨收到的是 **Opus 解码后的帧**。aiortc 的
``OpusDecoder``（``aiortc/codecs/opus.py``）固定输出::

    format = "s16"    packed（非 planar）
    layout = "stereo"
    sample_rate = 48000
    每帧 960 samples/channel  ->  交织后共 1920 个 int16

av 的 ``AudioFrame.to_ndarray()`` 对 **packed** 格式返回 **交织** 的一维块，
形状是 ``(1, samples * channels)``，即上例为 ``(1, 1920)``——注意这与
*服务端* ``HumanPlayer.push_audio`` 推送的 ``(1, 320)``（单声道 16k）**完全
不是一回事**。

桌面端曾经直接拿 ``arr.shape[1]`` 当声道数，于是 ``channels=1920``，
``sd.OutputStream`` 抛 ``ValueError``；而该异常又被 ``except: pass`` 静默
吞掉，结果就是"画面在动、却一点声音都没有"，且日志里什么都看不到。

为什么不能靠形状猜
------------------
``(1, 320)``（单声道 packed）和 ``(1, 1920)``（立体声 packed）在形状上
**无法区分**——都是"1 行"。若用"行数 < 列数就转置"的启发式，
``(1, 1920)`` 会被转成 ``(1920, 1)``，把交织立体声当成单声道播放，
结果是双倍速的怪异噪声（有声但完全不对）。

因此这里 **必须以帧自带的元数据为准**：
- ``frame.layout.channels``  -> 真实声道数
- ``frame.format.is_planar`` -> planar（各声道分行）还是 packed（交织成一行）

本模块提供两个入口：
- :func:`reshape_for_sounddevice` —— 纯 numpy，只吃 ndarray，用于单测与兜底。
- :func:`prepare_audio_frame` —— 吃 ``av.AudioFrame``，按元数据正确还原。

幅度
----
float 格式（``fltp``/``flt``/``dbl``）的样本在 ``[-1.0, 1.0]``，而
``sounddevice`` 的 ``int16`` 流需要 ``[-32768, 32767]``。直接 ``astype(np.int16)``
会把 ``0.3`` 截断成 ``0``——**再次变成静音**。所以这里按峰值缩放到 int16，
并保留直流分量。
"""
import numpy as np

__all__ = [
    "reshape_for_sounddevice",
    "prepare_audio_frame",
    "frame_channels",
    "list_output_devices",
]


def list_output_devices():
    """列出可用的音频输出设备。

    返回 ``[(device_id, 名称), ...]``；第一项恒为 ``(None, "（系统默认）")``。
    真机上"没声音"的第二大原因就是系统默认输出指向了显示器/HDMI 而不是
    音箱，所以界面需要给出可选设备列表。

    sounddevice 不可用时返回仅含默认项的列表，不抛异常。
    """
    devices = [(None, "（系统默认）")]
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001 - 没装声卡库时只给默认项
        return devices
    try:
        default_out = sd.default.device[1]
    except Exception:  # noqa: BLE001
        default_out = None
    try:
        for idx, dev in enumerate(sd.query_devices()):
            # 只列输出通道的设备（max_output_channels > 0）。
            # query_devices() 返回的是 Device 对象（不是 dict），属性访问。
            if getattr(dev, "max_output_channels", 0) <= 0:
                continue
            name = str(getattr(dev, "name", "") or ("设备 %d" % idx))
            marker = "（默认）" if idx == default_out else ""
            devices.append((idx, "%s%s" % (name, marker)))
    except Exception:  # noqa: BLE001 - 查询失败不应让界面崩溃
        pass
    return devices


def frame_channels(frame):
    """从 ``av.AudioFrame`` 取真实声道数。

    ``layout.channels`` 在不同 av 版本里可能是 ``int`` 或 channel 元组，
    这里两种都兼容。
    """
    layout = getattr(frame, "layout", None)
    channels = getattr(layout, "channels", None)
    if channels is None:
        return 1
    if isinstance(channels, int):
        return max(1, channels)
    try:
        return max(1, len(channels))
    except TypeError:
        return 1


def _to_int16(arr):
    """把任意幅度表示的样本转成 int16。

    float 输入按峰值缩放（避免 ``0.3 -> 0`` 变成静音）；整数输入直接截断。
    """
    arr = np.asarray(arr)
    if arr.dtype == np.int16:
        return np.ascontiguousarray(arr)
    if np.issubdtype(arr.dtype, np.floating):
        arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0)
        peak = float(np.max(np.abs(arr))) if arr.size else 0.0
        if peak > 0.0:
            arr = arr * (32767.0 / peak)
        arr = np.clip(arr, -32768.0, 32767.0)
        return np.ascontiguousarray(arr, dtype=np.int16)
    return np.ascontiguousarray(arr, dtype=np.int16)


def prepare_audio_frame(frame):
    """把 ``av.AudioFrame`` 还原成 sounddevice 可播放的 ``(samples, channels)``。

    返回 ``(arr_2d, channels)``，其中 ``arr_2d`` 形状为 ``(samples, channels)``，
    dtype 为 int16。

    - **packed**（``is_planar=False``）：``to_ndarray()`` 给的是交织的一行
      ``(1, samples*channels)``，需要先 ``reshape(-1, channels)`` 拆出交织
      布局，再转成 sounddevice 需要的 ``(samples, channels)``。
    - **planar**（``is_planar=True``）：``to_ndarray()`` 给的是
      ``(channels, samples)``，直接转置即可。
    """
    channels = frame_channels(frame)
    arr = frame.to_ndarray()

    if arr.ndim == 1:
        # 一维：无法区分交织/分行，按"已是 (samples,)"的单声道处理
        arr = arr.reshape(-1, 1)
        channels = 1
    elif arr.ndim == 2:
        if bool(getattr(frame.format, "is_planar", False)):
            # planar：(channels, samples) -> (samples, channels)
            arr = arr.T
            channels = arr.shape[1]
        else:
            # packed：(1, samples*channels) 交织 -> (samples, channels)
            if arr.shape[0] != 1:
                # 非预期布局，退回"按形状推断"的兜底
                arr, channels = reshape_for_sounddevice(arr)
            else:
                total = arr.shape[1]
                samples = total // channels if channels else total
                usable = samples * channels
                flat = arr.reshape(-1)[:usable]
                arr = flat.reshape(samples, channels)
    else:
        arr = arr.reshape(arr.shape[0], -1)
        arr, channels = reshape_for_sounddevice(arr)

    return _to_int16(arr), arr.shape[1]


def reshape_for_sounddevice(arr):
    """兜底：把 ndarray 整理成 ``(samples, channels)``，返回 ``(arr, channels)``。

    仅在拿不到帧元数据时使用。**已知声道数时不要依赖它**——packed 交织数据
    的形状不携带声道信息，猜错就会变成噪声。
    """
    arr = np.asarray(arr)
    if arr.ndim == 0:
        arr = arr.reshape(1, 1)
    elif arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    elif arr.ndim == 2:
        if arr.shape[0] < arr.shape[1]:
            arr = arr.T
    else:
        arr = arr.reshape(arr.shape[0], -1)
        if arr.shape[0] < arr.shape[1]:
            arr = arr.T
    return _to_int16(arr), arr.shape[1]
