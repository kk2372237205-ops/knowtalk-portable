# -*- coding: utf-8 -*-
"""把 Tk 版那套 WebRTC 客户端接到 Qt 上。

复用而不是重写：`AvatarClient` / `AudioPlayer` 里的代际校验、音轨元数据还原、
播放设备选择都踩过坑，重写一遍只会把这些坑再踩一次。等 Tk 版退役时再把这两个类
搬到 `utils/` 下。

**线程模型**（这是接 Qt 最容易出事的地方）：

- aiortc 在自己的事件循环线程里回调 `frame_cb` / `audio_cb`；
- Qt 的控件只能在主线程碰，跨线程直接 `setText` 会崩或静默不刷新；
- 视频帧如果每个都用信号 emit 过去，UI 处理不过来时队列会越堆越长，越跑越慢。

所以：状态/会话号走 Qt 信号（低频，靠自动排队回到主线程），**视频帧只存"最新一帧"**，
由主线程的定时器按显示节奏去取。丢帧不积压，这是播放器的常规做法。
"""

import numpy as np
from PySide6.QtCore import QObject, Signal

from utils.audio_playback import list_output_devices


class RtcBridge(QObject):
    """连接状态与会话号用信号；视频帧只保留最新一张。"""

    statusChanged = Signal(str)
    sessionChanged = Signal(str)

    def __init__(self, base, output_device=None, parent=None):
        super().__init__(parent)
        # 延迟到真正要用时才 import：desktop_app 会把 tkinter 一起拉进来，
        # 而没接数字人的页面不该为它付启动成本。
        from desktop_app import AudioPlayer, AvatarClient

        self._latest_rgb = None
        self._latest_seq = 0
        self._seen_seq = 0

        self.player = AudioPlayer(device=output_device)
        self.player.start()
        # trust_env=False：别让本机代理把 127.0.0.1 的 /offer 也转发出去
        import requests
        sess = requests.Session()
        sess.trust_env = False
        self.client = AvatarClient(
            base,
            sess=sess,
            session_cb=lambda sid: self.sessionChanged.emit(sid or ""),
            status_cb=lambda text: self.statusChanged.emit(text or ""),
            frame_cb=self._on_frame,
            audio_cb=self._on_audio,
        )

    # ── 来自 aiortc 线程 ─────────────────────────────────────────
    def _on_frame(self, frame):
        # AvatarClient 给的是 rgb24，直接可用
        self._latest_rgb = frame
        self._latest_seq += 1

    def _on_audio(self, arr, samplerate, channels):
        self.player.play(arr, samplerate, channels)

    # ── 主线程 ───────────────────────────────────────────────────
    def take_frame(self):
        """取走自上次调用以来最新的一帧；没有新帧返回 None。"""
        if self._latest_seq == self._seen_seq:
            return None
        self._seen_seq = self._latest_seq
        return self._latest_rgb

    # 名字不叫 connect/disconnect：QObject 自带这两个方法（信号连接用的），
    # 子类覆盖掉之后 `signal.connect(slot)` 会直接抛 TypeError。
    def open(self):
        self.client.connect()

    def close(self):
        self.client.disconnect()

    def stop(self):
        try:
            self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.player.stop()
        except Exception:  # noqa: BLE001
            pass


def output_devices():
    """可选输出设备 [(索引, 名字)]，真机多屏时用得上。"""
    return list_output_devices()
