# -*- coding: utf-8 -*-
"""Qt 版通用控件。

和 Tk 版 `make_btn / make_entry / SegmentedControl / ToggleSwitch` 一一对应，
但少了 `S(px)` 换算——Qt 按显示器 DPR 渲染，写逻辑像素即可。
"""

import numpy as np
from PySide6.QtCore import (Property, QEasingCurve, QPropertyAnimation, QRectF, QSize,
                            Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from ui_qt import theme as ui_theme


# ─── 卡片 ────────────────────────────────────────────────────────────

class Card(QFrame):
    """圆角卡片：标题 + 一行说明 + 内容区（`body` 布局直接往里塞）。"""

    def __init__(self, title="", desc="", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)
        if title:
            label = QLabel(title)
            label.setObjectName("cardTitle")
            outer.addWidget(label)
            self.title_label = label
        else:
            self.title_label = None
        if desc:
            hint = QLabel(desc)
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            outer.addWidget(hint)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body)


# ─── 按钮 ────────────────────────────────────────────────────────────

def button(text, on_click=None, kind="normal", parent=None):
    """kind: normal / primary / ghost / danger。"""
    btn = QPushButton(text, parent)
    if kind == "primary":
        btn.setProperty("primary", "true")
    elif kind != "normal":
        btn.setProperty("variant", kind)
    btn.setCursor(Qt.PointingHandCursor)
    if on_click:
        btn.clicked.connect(on_click)
    return btn


def line_edit(text="", width=None, parent=None):
    """单行输入框。

    `setText` 之后光标停在末尾，QLineEdit 会滚到光标处——长文本（迎宾语那种整句）
    打开时只能看见最后半截。填完把光标拨回开头。
    """
    box = QLineEdit(text, parent)
    box.setCursorPosition(0)
    # 默认 sizeHint 只有 12 个字符宽，迎宾语那种二十多字的整句会被截掉一半
    box.setMinimumWidth(width or 300)
    return box


# ─── 分段选择器 ──────────────────────────────────────────────────────

class Segmented(QFrame):
    """两三个选项的胶囊分段器。`valueChanged(值)` 给出被选中的 value。

    继承 QFrame 而不是 QWidget：QSS 的 `background` 对裸 QWidget 不生效
    （除非开 `WA_StyledBackground`），QFrame 是天生带背景绘制的。
    """

    valueChanged = Signal(str)

    def __init__(self, options, value=None, parent=None):
        """options: [(value, 显示文字), ...]"""
        super().__init__(parent)
        self.setObjectName("segTrack")
        self._values = [v for v, _ in options]
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        for index, (val, text) in enumerate(options):
            btn = QPushButton(text)
            btn.setObjectName("seg")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFont(QFont("Microsoft YaHei UI", 9))
            btn.setChecked(index == 0)
            self._group.addButton(btn, index)
            layout.addWidget(btn)
        self._group.idToggled.connect(self._on_toggled)
        if value is not None:
            self.set_value(value)

    def _on_toggled(self, index, checked):
        if checked:
            self.valueChanged.emit(self._values[index])

    def value(self):
        index = self._group.checkedId()
        return self._values[index] if 0 <= index < len(self._values) else ""

    def set_value(self, value):
        if value in self._values:
            self._group.button(self._values.index(value)).setChecked(True)


# ─── 开关 ────────────────────────────────────────────────────────────

class Switch(QAbstractButton):
    """自绘开关：圆角轨道 + 滑块，选中色跟随主题强调色。"""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(42, 24)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_knob(self):
        return self._pos

    def set_knob(self, value):
        self._pos = value
        self.update()

    knob = Property(float, get_knob, set_knob)

    def paintEvent(self, event):  # noqa: N802 - Qt 命名
        palette = ui_theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track_on = QColor(palette["accent"])
        track_off = QColor(palette["dot_off"])
        track = QColor(
            int(track_off.red() + (track_on.red() - track_off.red()) * self._pos),
            int(track_off.green() + (track_on.green() - track_off.green()) * self._pos),
            int(track_off.blue() + (track_on.blue() - track_off.blue()) * self._pos),
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, 42, 24), 12, 12)
        painter.setBrush(QColor(palette["card"]))
        x = 3 + (42 - 24) * self._pos
        painter.drawEllipse(QRectF(x, 3, 18, 18))
        painter.end()


# ─── 表单行 ──────────────────────────────────────────────────────────

class FormRow(QWidget):
    """一行「标签 + 控件」。标签宽度固定，控件吃满剩余空间并左对齐。"""

    def __init__(self, label, widget, label_width=88, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        text = QLabel(label)
        text.setObjectName("hint")
        text.setFixedWidth(label_width)
        layout.addWidget(text, 0, Qt.AlignVCenter)
        layout.addWidget(widget)
        layout.addStretch(1)


class SwitchRow(QWidget):
    """一行「开关 + 标题 + 说明」，说明贴在文字右侧。"""

    def __init__(self, title, switch, hint="", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(switch, 0, Qt.AlignTop)
        block = QVBoxLayout()
        block.setSpacing(2)
        name = QLabel(title)
        block.addWidget(name)
        if hint:
            sub = QLabel(hint)
            sub.setObjectName("muted")
            block.addWidget(sub)
        layout.addLayout(block)
        layout.addStretch(1)


# ─── 视频舞台 ────────────────────────────────────────────────────────

class VideoStage(QWidget):
    """数字人画面。

    Tk 版走 `PIL.ImageTk.PhotoImage` 每帧重建位图贴到 Canvas，1080p 下每秒
    几十兆的拷贝；这里直接把 numpy 缓冲区包成 QImage 交给 QPainter，
    只在 paintEvent 里做一次缩放绘制，且由 Qt 的合成器上屏。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer = None          # 必须持有引用，QImage 不拷贝数据
        self._image = None
        self._fps = 0.0
        self._frames = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self.setMinimumSize(360, 480)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._placeholder = "等待连接数字人"

    def set_frame(self, frame, rgb=True):
        """送入一帧。

        `rgb=True` 表示 frame 已经是 RGB（aiortc 的 `to_ndarray(format="rgb24")`
        就是这个顺序）；传 BGR（OpenCV 默认）时把 rgb 置 False。
        摄像头和数字人两路的顺序不一样，弄反了人脸会发蓝紫。
        """
        if frame is None:
            return
        data = frame if rgb else frame[:, :, ::-1]
        rgb_buffer = np.ascontiguousarray(data)
        height, width = rgb_buffer.shape[:2]
        self._buffer = rgb_buffer      # QImage 不拷贝数据，引用必须留在 Python 侧
        self._image = QImage(self._buffer.data, width, height, 3 * width,
                             QImage.Format_RGB888)
        self._frames += 1
        self.update()

    def clear(self):
        self._image = None
        self.update()

    def _tick(self):
        self._fps = self._frames
        self._frames = 0

    @staticmethod
    def _fit(rect, image_size):
        """按比例缩放并居中，返回目标矩形。

        以前是 `QImage.scaled()` 生成一张新图再画——每帧多一次全图重采样，
        1080p 下直接把帧率从 30 拉到 20 出头。改成直接把源图绘到目标矩形，
        缩放交给绘制引擎做。
        """
        width, height = image_size.width(), image_size.height()
        if width <= 0 or height <= 0:
            return QRectF(rect)
        scale = min(rect.width() / width, rect.height() / height)
        draw_w = width * scale
        draw_h = height * scale
        return QRectF((rect.width() - draw_w) / 2.0,
                      (rect.height() - draw_h) / 2.0, draw_w, draw_h)

    def paintEvent(self, event):  # noqa: N802
        palette = ui_theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 14, 14)
        painter.setClipPath(path)
        painter.fillRect(rect, QColor(palette["stage"]))
        if self._image is not None and not self._image.isNull():
            target = self._fit(rect, self._image.size())
            painter.drawImage(target, self._image)
        else:
            painter.setPen(QPen(QColor(palette["muted"])))
            painter.drawText(rect, Qt.AlignCenter, self._placeholder)
        if self._fps:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(rect.adjusted(0, 0, -12, -8),
                             Qt.AlignRight | Qt.AlignBottom, "%d fps" % self._fps)
        painter.end()


# ─── 导航 ────────────────────────────────────────────────────────────

class NavButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(34)


def group_label(text):
    label = QLabel(text)
    label.setObjectName("groupTitle")
    return label
