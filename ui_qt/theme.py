# -*- coding: utf-8 -*-
"""色板 → QSS。

色板复用 `utils/theme.py`（四套：蓝白 / 科技 / 商务 / 米白），这里只做一件事：
把语义色填进样式表模板。换主题 = 重新 `app.setStyleSheet(...)`，即时生效。

QSS 的坑：它只认有限的选择器，`box-shadow`、`transition` 都不支持；圆角、边框、
渐变、`:hover/:pressed/:checked` 是支持的。下面刻意只用支持的那部分。
"""

import re

from utils import theme as palette_source

FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
_TEMPLATE = """
QWidget {
    background: transparent;
    color: @ink;
    font-family: @font;
    font-size: 14px;
}
QMainWindow, QWidget#root { background: @bg; }

/* ── 侧边栏 ─────────────────────────────────── */
QWidget#side { background: @card; border-right: 1px solid @line_soft; }
QLabel#brand { font-size: 16px; font-weight: 600; color: @ink; }
QLabel#groupTitle { color: @muted; font-size: 11px; font-weight: 600;
                    letter-spacing: 1px; padding: 10px 12px 4px 12px; }
QPushButton#nav {
    background: transparent; border: none; border-radius: 8px;
    padding: 7px 12px; text-align: left; color: @muted2; font-size: 13px;
}
QPushButton#nav:hover { background: @hover; color: @ink; }
QPushButton#nav:checked { background: @nav_on; color: @accent; font-weight: 600; }

/* ── 卡片 ───────────────────────────────────── */
QFrame#card {
    background: @card; border: 1px solid @line; border-radius: 12px;
}
QLabel#cardTitle { font-size: 15px; font-weight: 600; color: @ink; }
QLabel#muted { color: @muted; font-size: 12px; }
QLabel#hint { color: @muted2; font-size: 12px; }
QLabel#pageTitle { font-size: 20px; font-weight: 600; color: @ink; }

/* ── 按钮 ───────────────────────────────────── */
QPushButton {
    background: @card; border: 1px solid @line; border-radius: 8px;
    padding: 7px 16px; color: @ink; font-size: 14px;
}
QPushButton:hover { background: @btn_hov; }
QPushButton:pressed { background: @btn_down; }
QPushButton:disabled { color: @dot_off; border-color: @line_soft; background: @card_alt; }
QPushButton[primary="true"]:disabled { background: @line; border-color: @line;
                                      color: @muted; }
QPushButton[primary="true"] {
    background: @accent; border: 1px solid @accent; color: @on_accent; font-weight: 600;
}
QPushButton[primary="true"]:hover { background: @ink_hov; border-color: @ink_hov; }
QPushButton[primary="true"]:pressed { background: @ink; }
QPushButton[variant="ghost"] { background: transparent; border: 1px solid @line_soft; }
QPushButton[variant="ghost"]:hover { background: @hover; }
QPushButton[variant="danger"] { background: transparent; border: 1px solid @line;
                                color: @danger; }
QPushButton[variant="danger"]:hover { background: @danger; color: @on_accent; }

/* 分段选择器：未选中的那半是透明的，选中的半是强调色 */
QPushButton#seg {
    background: transparent; border: none; border-radius: 7px;
    padding: 5px 14px; color: @muted2; font-size: 13px;
}
QPushButton#seg:hover { color: @ink; }
QPushButton#seg:checked { background: @card; color: @accent; font-weight: 600; }
QFrame#segTrack { background: @card_alt; border-radius: 9px; }

/* ── 输入 ───────────────────────────────────── */
QLineEdit, QComboBox, QSpinBox {
    background: @card; border: 1px solid @line; border-radius: 8px;
    padding: 6px 10px; color: @ink; selection-background-color: @accent;
    selection-color: @on_accent;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: @accent; }
QLineEdit:disabled, QComboBox:disabled { color: @dot_off; background: @card_alt; }
/* 不自己画箭头：QSS 的 border 三角形在 Qt 里不生效，写了反而把系统箭头吃掉 */
/* SpinBox 的上下按钮默认按原生像素画，套上圆角边框后会溢出框外，
   必须显式指定 subcontrol 位置与尺寸，否则就是截图里那根竖条 */
QSpinBox::up-button {
    subcontrol-origin: border; subcontrol-position: top right;
    width: 18px; height: 12px; border: none; background: transparent;
    margin: 3px 3px 0 0;
}
QSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; height: 12px; border: none; background: transparent;
    margin: 0 3px 3px 0;
}
QComboBox QAbstractItemView {
    background: @card; border: 1px solid @line; border-radius: 8px;
    padding: 4px; outline: none;
    selection-background-color: @nav_on; selection-color: @accent;
}

/* ── 其它 ───────────────────────────────────── */
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: @dot_off; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: @muted; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QSlider::groove:horizontal { height: 5px; background: @line; border-radius: 3px; }
QSlider::sub-page:horizontal { background: @accent; border-radius: 3px; }
QSlider::handle:horizontal {
    background: @card; border: 2px solid @accent; width: 14px; height: 14px;
    margin: -6px 0; border-radius: 9px;
}
QToolTip { background: @ink; color: @card; border: none; padding: 6px 10px;
           border-radius: 6px; }
"""


def qss(theme_id=None):
    """按当前主题生成样式表。

    替换必须用正则一次性做完，不能按 key 逐个 `str.replace`：色板里 `card` 和
    `card_alt`、`line` / `line_soft` / `line_str` 互为前缀，先替换短的会把长的
    改成 `@FFFFFF_alt` 这种无效值——控件上表现为「背景没上色、边框消失」。
    """
    palette = palette_source.load_palette(theme_id)
    text = _TEMPLATE.replace("@font", FONT_FAMILY)
    return _PLACEHOLDER.sub(lambda m: palette.get(m.group(1), m.group(0)), text)


_PLACEHOLDER = re.compile(r"@([a-z_]+)")


def palette(theme_id=None):
    return palette_source.load_palette(theme_id)


def current_theme_id():
    return palette_source.load_theme_id()


def theme_choices():
    """[(id, 名称, 说明)]，设置页用。"""
    return [(tid, meta["label"], meta["hint"])
            for tid, meta in palette_source.THEMES.items()]
