# -*- coding: utf-8 -*-
"""Qt 桌面端的主服务启动桥接。

主服务的内存预检、端口复用和优雅退出逻辑已经在旧桌面端经过真机验证；Qt
界面先复用这套非视觉逻辑，避免另写一份进程管理代码。回调全部转成 Qt 信号，
因此后台线程不会直接触碰 Qt 控件。
"""

import threading

from PySide6.QtCore import QObject, Signal


class BackendController(QObject):
    """在后台拉起或复用本机主服务，并向 Qt 界面报告状态。"""

    ready = Signal()
    progress = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._launcher = None

    def start(self):
        if self._launcher is not None:
            return
        # 这里只导入启动器，不会创建 Tk 根窗口；Qt 仍是唯一的可视界面。
        from desktop_app import ServiceLauncher, load_tts_choice

        self._launcher = ServiceLauncher(
            load_tts_choice(),
            ready_cb=self.ready.emit,
            fail_cb=self.failed.emit,
            progress_cb=self.progress.emit,
        )
        self._launcher.start()

    def stop(self):
        """只停止由本窗口创建的服务；复用的外部服务不会被误杀。"""
        launcher = self._launcher
        if launcher is None:
            return
        threading.Thread(target=launcher.stop, name="qt-backend-stop", daemon=True).start()
