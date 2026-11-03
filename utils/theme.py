# -*- coding: utf-8 -*-
"""桌面端主题色板。

Tkinter 的控件颜色在创建时就固化在控件上，改模块级常量不会重绘已有控件，
所以主题是**启动时读取**：`data/settings/theme.json` 里记着当前主题 id，
`desktop_app` 在定义 C_* 常量时向本模块取色。切换主题后需要重建界面才生效。

色板只描述语义（背景/卡片/描边/正文/强调…），不描述具体色相，
这样新增主题时不用回头改业务代码。
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEME_FILE = os.path.join(ROOT, "data", "settings", "theme.json")
DEFAULT_THEME = "blue"

# 语义键 → 界面里的 C_* 常量
KEYS = (
    "bg", "card", "card_alt", "line", "line_soft", "line_str",
    "ink", "ink_hov", "accent", "muted", "muted2", "danger",
    "stage", "nav_on", "hover", "sel", "dot_off", "btn_hov", "btn_down",
    "warn", "on_accent",
)

THEMES = {
    "blue": {
        "label": "蓝白风",
        "hint": "白底 + 蓝色强调，干净克制",
        "colors": {
            "bg": "#F6F9FD", "card": "#FFFFFF", "card_alt": "#EDF3FA",
            "line": "#D3DFEE", "line_soft": "#E5EDF7", "line_str": "#BFD0E4",
            "ink": "#16283C", "ink_hov": "#21364D", "accent": "#1D63C8",
            "muted": "#7E8DA0", "muted2": "#5A6B7D", "danger": "#B3261E",
            "stage": "#0E1A2B", "nav_on": "#E7F0FC", "hover": "#F0F5FC",
            "sel": "#E7F0FC", "dot_off": "#B7C3D2", "btn_hov": "#EDF3FA",
            "btn_down": "#DCE7F5", "warn": "#B0741A", "on_accent": "#FFFFFF",
        },
    },
    "tech": {
        "label": "科技风",
        "hint": "深色底 + 青色荧光，偏发布会气质",
        "colors": {
            "bg": "#0B1220", "card": "#111B2C", "card_alt": "#16223A",
            "line": "#23314B", "line_soft": "#1A2740", "line_str": "#2E4160",
            "ink": "#E6F0FF", "ink_hov": "#FFFFFF", "accent": "#22D3EE",
            "muted": "#6C8199", "muted2": "#94A7BD", "danger": "#F87171",
            "stage": "#060C16", "nav_on": "#15263D", "hover": "#131F33",
            "sel": "#15263D", "dot_off": "#3D5271", "btn_hov": "#182741",
            "btn_down": "#1F3153", "warn": "#F0B429", "on_accent": "#06202A",
        },
    },
    "business": {
        "label": "商务风",
        "hint": "浅灰白 + 深蓝强调，正式稳重",
        "colors": {
            "bg": "#F4F6F8", "card": "#FFFFFF", "card_alt": "#EAEEF2",
            "line": "#D2D8E0", "line_soft": "#E3E7EC", "line_str": "#BFC7D2",
            "ink": "#1A2A3A", "ink_hov": "#24374A", "accent": "#1E3A5F",
            "muted": "#7B8794", "muted2": "#5F6B78", "danger": "#9B2C2C",
            "stage": "#16212E", "nav_on": "#E5EAF0", "hover": "#EFF2F5",
            "sel": "#E5EAF0", "dot_off": "#B4BDC7", "btn_hov": "#ECEFF3",
            "btn_down": "#DDE3EA", "warn": "#9A6B12", "on_accent": "#FFFFFF",
        },
    },
    "cream": {
        "label": "米白",
        "hint": "暖米底 + 墨绿强调，原版",
        "colors": {
            "bg": "#FBF8F1", "card": "#FFFDF8", "card_alt": "#FAF6EC",
            "line": "#D8CFB8", "line_soft": "#E6E1D2", "line_str": "#CBBFA6",
            "ink": "#12253D", "ink_hov": "#1A314D", "accent": "#1D6B4A",
            "muted": "#968F80", "muted2": "#6C6860", "danger": "#83302A",
            "stage": "#101F33", "nav_on": "#EEF4E9", "hover": "#F3F0E6",
            "sel": "#F4F0E4", "dot_off": "#B9B09C", "btn_hov": "#EDF3EA",
            "btn_down": "#DCE4DC", "warn": "#B77A1E", "on_accent": "#FFFFFF",
        },
    },
}


def theme_ids():
    return list(THEMES)


def theme_meta(theme_id):
    return THEMES.get(theme_id) or THEMES[DEFAULT_THEME]


def load_theme_id():
    """读取当前主题 id；文件缺失或写坏一律回默认。"""
    try:
        with open(THEME_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        tid = str(data.get("theme") or "").strip()
        if tid in THEMES:
            return tid
    except Exception:  # noqa: BLE001 - 主题读不出来不该拦启动
        pass
    return DEFAULT_THEME


def save_theme_id(theme_id):
    if theme_id not in THEMES:
        raise ValueError("unknown theme: %s" % theme_id)
    os.makedirs(os.path.dirname(THEME_FILE), exist_ok=True)
    tmp = THEME_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"theme": theme_id}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, THEME_FILE)
    return theme_id


def load_palette(theme_id=None):
    """返回 {语义键: 十六进制色}。缺键用默认主题补齐，避免新增键时旧主题炸掉。"""
    tid = theme_id or load_theme_id()
    base = dict(THEMES[DEFAULT_THEME]["colors"])
    base.update(THEMES.get(tid, THEMES[DEFAULT_THEME])["colors"])
    return base
