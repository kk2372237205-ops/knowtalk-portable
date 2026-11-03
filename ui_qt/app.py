# -*- coding: utf-8 -*-
"""Qt 版主窗口：侧边导航 + 懒加载页面 + 主题热切换。

两个刻意的设计，都针对旧版的黑屏问题：

1. **页面懒加载**：点进某个页面才构造它。旧版在 `MainPage.__init__` 里一次建 13 个
   页面，其中 7 个构造函数里有同步 HTTP，主线程一堵就是几十秒。
2. **启动只画骨架**：主窗口先把导航和空内容区画出来，再让后台线程去拉数据，
   任何等待都不会挡住第一帧。
"""

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow,
                               QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from ui_qt import theme as ui_theme
from ui_qt import widgets as w
from ui_qt.pages import AppearancePage, BroadcastPage, IdlePage

NAV_SECTIONS = {
    "speech": [
        ("备课", [("script", "讲稿"), ("quiz", "提问"),
                  ("course", "课件"), ("kb", "课程知识库")]),
        ("上课", [("broadcast", "开播"), ("bg", "背景设置")]),
        ("资源", [("studio", "形象与设置")]),
    ],
    "expo": [
        ("全息舱", [("wake", "唤醒与打断"), ("menu", "菜单库"),
                    ("idle", "待机与迎宾"), ("qalog", "问答记录"),
                    ("bg", "背景设置")]),
        ("启动", [("broadcast", "开播")]),
    ],
}

# 已经搬过来的页面；其余的先给占位，等逐页迁移
_PAGE_FACTORY = {
    "broadcast": lambda win: BroadcastPage(),
    "idle": lambda win: IdlePage(),
}

_MISSING = "这一页还没搬过来——原型只实现了「开播」「待机与迎宾」「外观」。"


class PlaceholderPage(QWidget):
    def __init__(self, key, title, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(6)
        head = QLabel(title)
        head.setObjectName("pageTitle")
        layout.addWidget(head)
        desc = QLabel(_MISSING)
        desc.setObjectName("muted")
        layout.addWidget(desc)
        code = QLabel("page key: %s" % key)
        code.setObjectName("hint")
        layout.addWidget(code)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("知课 KnowTalking - Qt 版原型")
        self.resize(1360, 860)
        self.setMinimumSize(1080, 720)

        self._mode = "speech"
        self._pages = {}
        self._nav_buttons = {}
        self._current_key = None

        central = QWidget()
        central.setObjectName("root")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_side())
        root.addWidget(self._build_body(), 1)
        self.setCentralWidget(central)

        self.apply_theme(ui_theme.current_theme_id())
        self.switch_mode("speech")
        self.show_page("broadcast")

    # ── 侧边栏 ────────────────────────────────────────────────────
    def _build_side(self):
        side = QWidget()
        side.setObjectName("side")
        side.setFixedWidth(216)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(10)

        brand = QLabel("知课 KnowTalking")
        brand.setObjectName("brand")
        layout.addWidget(brand)

        self.mode_seg = w.Segmented([("speech", "演讲模式"), ("expo", "展览模式")], "speech")
        self.mode_seg.valueChanged.connect(self.switch_mode)
        layout.addWidget(self.mode_seg)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav_host = QWidget()
        self.nav_layout = QVBoxLayout(self.nav_host)
        self.nav_layout.setContentsMargins(0, 6, 0, 6)
        self.nav_layout.setSpacing(2)
        scroll.setWidget(self.nav_host)
        layout.addWidget(scroll, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.foot_label = QLabel("主服务 检测中")
        self.foot_label.setObjectName("muted")
        foot.addWidget(self.foot_label)
        foot.addStretch(1)
        settings = w.button("外观", lambda: self.show_page("appearance"), "ghost")
        foot.addWidget(settings)
        layout.addLayout(foot)
        return side

    def _build_body(self):
        self.stack = QStackedWidget()
        return self.stack

    # ── 导航渲染 ──────────────────────────────────────────────────
    def switch_mode(self, mode):
        if mode not in NAV_SECTIONS:
            return
        self._mode = mode
        while self.nav_layout.count():
            item = self.nav_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._nav_buttons.clear()
        for title, items in NAV_SECTIONS[mode]:
            self.nav_layout.addWidget(w.group_label(title))
            for key, text in items:
                btn = w.NavButton(text)
                btn.clicked.connect(lambda _=False, k=key: self.show_page(k))
                self.nav_layout.addWidget(btn)
                self._nav_buttons[key] = btn
        self.nav_layout.addStretch(1)
        self.show_page("broadcast")

    def show_page(self, key):
        self._current_key = key
        page = self._pages.get(key)
        if page is None:
            page = self._create_page(key)
            self._pages[key] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        for nav_key, btn in self._nav_buttons.items():
            btn.setChecked(nav_key == key)

    def _create_page(self, key):
        factory = _PAGE_FACTORY.get(key)
        if factory:
            return factory(self)
        if key == "appearance":
            return AppearancePage(on_pick=self.apply_theme)
        title = dict(
            (k, t) for _, items in NAV_SECTIONS[self._mode] for k, t in items
        ).get(key, key)
        return PlaceholderPage(key, title)

    def shutdown(self):
        """关窗时让每页自己收尾（断开 WebRTC、停音频线程）。"""
        for page in self._pages.values():
            hook = getattr(page, "shutdown", None)
            if callable(hook):
                try:
                    hook()
                except Exception:  # noqa: BLE001
                    pass

    # ── 主题 ──────────────────────────────────────────────────────
    def apply_theme(self, theme_id):
        app = QApplication.instance()
        app.setStyleSheet(ui_theme.qss(theme_id))
        for page in self._pages.values():
            stage = getattr(page, "stage", None)
            if stage is not None:
                stage.update()


def run():
    app = QApplication(sys.argv)
    # 固定用 Fusion：Windows 原生样式自己画一半控件，QSS 只能改到另一半，
    # 结果就是「边框改了、箭头没改」。Fusion 的整套控件都由样式表驱动。
    app.setStyle("Fusion")
    app.setApplicationName("KnowTalk Qt")
    icon = QPixmap(1, 1)
    app.setWindowIcon(QIcon(icon))
    window = MainWindow()
    window.show()
    app.aboutToQuit.connect(window.shutdown)
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
