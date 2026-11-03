# -*- coding: utf-8 -*-
"""知课 KnowTalking - Tkinter 原生桌面版（不依赖任何浏览器/WebView 内核）。

架构：
- 本进程负责 UI（Tkinter）；后台以子进程方式拉起原版 aiohttp 主服务（app.py），
  通过本机 HTTP 接口通信（localhost，与浏览器无关）。
- 数字人音视频用 aiortc 做原生 WebRTC 接收端（无浏览器）：音频轨 → sounddevice
  播放；视频轨 → OpenCV/PIL 帧 → Tk Canvas 显示。
- 语音输入：sounddevice 录麦克风 → 本地 whisper 转写（/api/stt/transcribe）。

运行：python desktop_app.py
（若 8010 主服务未运行会自动启动，并默认对接本机 XTTS 服务 http://127.0.0.1:50000；
 可在「音色设置」页切换 Fish（50001）或不用本地 TTS。）
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse

# ── 解释器自检（必须留在第一批第三方 import 之前）────────────────────
# 本进程要跑 requests / numpy / aiortc / sounddevice / tkinter，主服务子进程
# 还要 torch + aiohttp。从 VS Code 启动时，Python 扩展会自动挑中项目里的
# .venv 当默认解释器，而它没装这些依赖 —— 结果就是 import 在文件后半段直接
# 崩掉（界面根本出不来），或者界面出来了但主服务子进程秒退。
# 这里只用标准库先问一句"依赖齐不齐"，不齐就自动换到依赖齐全的解释器
# 重投一次自己，用户不必手工配置 IDE 的解释器。
ROOT = os.path.dirname(os.path.abspath(__file__))

# 桌面版自身需要的（决定要不要重投自己）
_UI_DEPS = ("requests", "numpy", "aiortc", "sounddevice", "tkinter")
# 主服务 app.py 需要的（决定子进程用哪个解释器，与 app.py 自检口径一致）
_SERVICE_DEPS = ("numpy", "aiohttp", "aiortc", "torch")


def _missing_deps(deps):
    import importlib.util as _u
    missing = []
    for name in deps:
        try:
            if _u.find_spec(name) is None:
                missing.append(name)
        except Exception:
            missing.append(name)
    return missing


def _deps_ok(exe, deps):
    probe = ("import importlib.util as u, sys;"
             "sys.exit(0 if all(u.find_spec(m) for m in %r) else 1)" % (deps,))
    try:
        done = subprocess.run([exe, "-c", probe], timeout=30,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    return done.returncode == 0


def _iter_python_candidates():
    """按可能性从高到低列候选解释器（只列路径，不查依赖）。"""
    seen = set()

    def _add(path):
        if not path or not os.path.isfile(path):
            return False
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return False
        seen.add(key)
        return True

    # 1. 项目里记录的那个（PyCharm 或上次启动留下的）
    for name in (".python-path.txt", "python-path.txt"):
        try:
            with open(os.path.join(ROOT, name), "r", encoding="utf-8",
                      errors="replace") as fh:
                line = fh.readline().strip().strip('"')
        except Exception:
            continue
        if _add(line):
            yield line

    # 2. conda 环境（livetalking 优先，其次其余环境）
    home = os.path.expanduser("~")
    bases = [os.path.join(home, ".conda"), os.path.join(home, "miniconda3"),
             os.path.join(home, "anaconda3"),
             os.path.join(home, "AppData", "Local", "miniconda3"),
             os.path.join(home, "AppData", "Local", "anaconda3"),
             "C:/ProgramData/miniconda3", "C:/ProgramData/anaconda3"]
    paths = []
    for base in bases:
        if not os.path.isdir(base):
            continue
        paths.append(os.path.join(base, "python.exe"))
        env_dir = os.path.join(base, "envs")
        if not os.path.isdir(env_dir):
            continue
        for preferred in ("livetalking", "knowtalk", "musetalk"):
            paths.append(os.path.join(env_dir, preferred, "python.exe"))
        try:
            for name in sorted(os.listdir(env_dir)):
                paths.append(os.path.join(env_dir, name, "python.exe"))
        except Exception:
            pass
    for path in paths:
        if _add(path):
            yield path

    # 3. 项目虚拟环境
    for name in (".venv", ".venv-rag", "venv"):
        path = os.path.join(ROOT, name, "Scripts", "python.exe")
        if _add(path):
            yield path

    # 4. 当前解释器和 PATH 上的
    if _add(sys.executable):
        yield sys.executable
    if _add(shutil.which("python")):
        yield shutil.which("python")


def _find_python(deps, skip_self=False):
    for exe in _iter_python_candidates():
        if skip_self and os.path.normcase(os.path.abspath(exe)) == \
                os.path.normcase(os.path.abspath(sys.executable)):
            continue
        if _deps_ok(exe, deps):
            return exe
    return None


def _relaunch_if_wrong_interpreter():
    """IDE 选错解释器时自救：换到依赖齐全的解释器，把 argv 原样重投一次。"""
    if os.environ.get("KNOWTALK_RELAUNCHED"):
        return
    missing = _missing_deps(_UI_DEPS)
    if not missing:
        return
    target = _find_python(_UI_DEPS, skip_self=True)
    banner = [
        "",
        "=" * 74,
        "当前 Python 解释器缺少桌面版依赖：%s" % ", ".join(missing),
        "  当前解释器：%s" % sys.executable,
    ]
    if not target:
        banner += [
            "",
            "没有在本机找到依赖齐全的解释器。请先在 VS Code 里按 Ctrl+Shift+P",
            "→ Python: Select Interpreter → 选择 C:\\Users\\24306\\.conda\\envs\\livetalking",
            "或双击 start_app.bat 启动。",
            "=" * 74, "",
        ]
        sys.stderr.write("\n".join(banner))
        sys.exit(1)
    # 原本是无控制台的 pythonw（双击 bat 的情况）就别多弹一个黑窗
    if os.path.basename(sys.executable or "").lower().startswith("pythonw"):
        alt = os.path.join(os.path.dirname(target), "pythonw.exe")
        if os.path.isfile(alt):
            target = alt
    banner += [
        "  已自动改用：%s" % target,
        "=" * 74, "",
    ]
    sys.stderr.write("\n".join(banner))
    try:
        subprocess.Popen([target, os.path.abspath(__file__)] + list(sys.argv[1:]),
                         cwd=ROOT, env=dict(os.environ, KNOWTALK_RELAUNCHED="1"))
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("自动切换解释器失败: %r\n" % (exc,))
        return
    sys.exit(0)


_relaunch_if_wrong_interpreter()

def _resolve_service_python():
    """当前解释器就够用时不折腾；不够用才去子进程里挨个问。"""
    if not _missing_deps(_SERVICE_DEPS):
        return sys.executable
    return _find_python(_SERVICE_DEPS, skip_self=True) or sys.executable


# 主服务子进程用的解释器：优先挑一个 torch/aiohttp 齐全的，而不是无脑
# sys.executable —— 后者正是「VS Code 里界面能开、服务却起不来」的根源。
PYTHON = _resolve_service_python()

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog, scrolledtext, simpledialog

import requests

# TTS 引擎清单以主服务的声明式设置为唯一来源，避免界面与后端各写一份而漂移
# （历史上高级设置的硬编码列表就漏掉了 doubao/tencent/azuretts/qwentts）。
from utils.settings_store import TTS_ENGINES

BASE = "http://127.0.0.1:8010"
TTS_SERVER_FILE = os.path.join(ROOT, "desktop_tts_server.json")
# 内嵌 Tk 预览维持与后端默认输出一致的 20fps。独立演示窗口则使用浏览器原生
# WebRTC/GPU 播放，不经过 Tk 的逐帧 PIL/Canvas 重建路径。
FRAME_PAINT_INTERVAL = 1.0 / 20.0

# ── 高 DPI ───────────────────────────────────────────────────────────
# 关键点：不声明 DPI 感知，Windows 会把整个窗口当作位图按 150%/200% 拉伸，
# 字全糊。声明之后 Tk 直接按物理像素绘制，再配合下面的 S() 把布局尺寸一起放大，
# 才能既有清晰字形又保持原来的视觉比例。
def _enable_dpi_awareness():
    if sys.platform != "win32":
        return
    import ctypes
    try:                                    # 每显示器感知 v2（Win10 1703+）
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def _system_dpi():
    if sys.platform != "win32":
        return 96
    try:
        import ctypes
        dpi = int(ctypes.windll.user32.GetDpiForSystem() or 0)
        if dpi > 0:
            return dpi
    except Exception:  # noqa: BLE001
        pass
    return 96


_enable_dpi_awareness()
UI_SCALE = max(1.0, _system_dpi() / 96.0)


def S(value):
    """96 DPI 下的设计尺寸 → 当前屏幕物理像素。"""
    return int(round(float(value) * UI_SCALE))


# ── 本地 TTS 服务预设 ────────────────────────────────────────────────
TTS_PRESETS = {
    "xtts": {"name": "XTTS-v2", "server": "http://127.0.0.1:50000", "engine": "xtts"},
    "fish": {"name": "Fish Speech", "server": "http://127.0.0.1:50001", "engine": "fishtts"},
    "none": {"name": "不使用本地 TTS（走在线/系统音色）", "server": "", "engine": ""},
}


def _port_open(host, port, timeout=0.4):
    """轻量端口探测：主要用于判断克隆服务是否已经起好。"""
    import socket
    s = socket.socket(); s.settimeout(timeout)
    try:
        s.connect((host, port)); return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        s.close()


def _configured_audio_settings():
    """读取设置里的桌面端音频选项。

    返回 ``(output_device, test_tone)``：
    - ``output_device``：None/"" 表示用系统默认输出设备。
    - ``test_tone``：是否在连上主服务后播一声提示音（用于真机自检）。

    读不出来时返回 (None, False)，即"沿用系统默认、不打扰用户"。
    """
    try:
        with open(os.path.join(ROOT, "data", "settings", "settings.json"),
                  encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:  # noqa: BLE001
        return None, False
    audio = d.get("audio") or {}
    device = audio.get("output_device")
    if device in ("", None):
        device = None
    elif isinstance(device, str):
        try:
            device = int(device)
        except (TypeError, ValueError):
            device = None
    else:
        device = None
    tone = audio.get("test_tone")
    return device, bool(tone) if tone is not None else False


def _configured_tts_engine():
    """读取设置里真正生效的 TTS 引擎（settings.json 的 tts.engine）。

    用户可能只在「高级设置」里把引擎选成 xtts，而本地 TTS 预设（desktop_tts_server.json）
    仍是 none——这时也要能把克隆服务拉起来，否则参考音色用不上。
    """
    try:
        with open(os.path.join(ROOT, "data", "settings", "settings.json"),
                  encoding="utf-8") as fh:
            d = json.load(fh)
        return (d.get("tts") or {}).get("engine") or ""
    except Exception:  # noqa: BLE001
        return ""


_CLONE_PROC = None


def auto_start_clone_server(tts_choice):
    """选了本地克隆引擎（xtts）时，确保克隆服务在跑；没在跑就拉起（非阻塞）。

    用参考音频（声音克隆）时，XTTS 引擎要连 50000 端口的克隆服务。该服务不会随
    主服务自动启动，过去只能手动在「声音克隆」页点一下，很容易忘——结果就是
    XTTS 拿不到 speaker、数字人静默。这里在启动主服务前自动把它拉起来，
    配合 xtts.py 的惰性重试，服务就绪后参考音色会自动生效。
    """
    global _CLONE_PROC
    preset = TTS_PRESETS.get(tts_choice)
    if not preset or not preset.get("server"):
        return
    from urllib.parse import urlparse
    parsed = urlparse(preset["server"])
    port = parsed.port or 50000
    host = parsed.hostname or "127.0.0.1"
    if _port_open(host, port):
        print("[克隆] 声音克隆服务已在 %s 运行，跳过自动拉起" % preset["server"], flush=True)
        return
    model_dir = os.path.join(ROOT, "data", "models", "xtts_v2")
    if not os.path.isfile(os.path.join(model_dir, "model.pth")):
        print("[克隆] 警告：XTTS 模型未下载（data/models/xtts_v2/model.pth 不存在），"
              "参考音色将不可用并回退到备用引擎；请先在「声音克隆」页下载模型。", flush=True)
    try:
        log_dir = os.path.join(ROOT, "logs"); os.makedirs(log_dir, exist_ok=True)
        log_file = open(os.path.join(log_dir, "voice_clone.log"), "a",
                        encoding="utf-8", errors="replace")
        log_file.write("\n--- desktop_app 自动拉起声音克隆服务 ---\n"); log_file.flush()
        try:
            _CLONE_PROC = subprocess.Popen(
                [PYTHON, os.path.join(ROOT, "scripts", "voice_clone_server.py"), "--port", str(port)],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        finally:
            log_file.close()             # 句柄已交给子进程
        print("[克隆] 已自动拉起声音克隆服务（端口 %d），模型加载约需几十秒" % port, flush=True)
    except Exception as exc:  # noqa: BLE001
        print("[克隆] 自动拉起声音克隆服务失败: %r" % (exc,), flush=True)


def stop_clone_server(timeout=5.0):
    """退出时结束自动拉起的声音克隆服务。

    它占着 50000 端口，还常驻一份 XTTS 的显存（几 GB）。不关的话：
    下次启动看到"端口已监听"就跳过拉起，实际连的是个正在退出的进程；
    显存也被白占着，后面加载 MuseTalk 更容易撞 OOM。
    """
    global _CLONE_PROC
    proc, _CLONE_PROC = _CLONE_PROC, None
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=3)
            return True
        except Exception:  # noqa: BLE001
            return False
    except Exception:  # noqa: BLE001
        return False


# ── 界面主题 ────────────────────────────────────────────────────────
# 色板放在 utils/theme.py，启动时按 data/settings/theme.json 取一套。
# 切换主题要重建界面才生效（Tk 控件颜色创建即固定），设置页里会提示并重启界面。
try:
    from utils.theme import load_palette as _load_palette
    from utils.theme import (THEMES as _THEMES, load_theme_id as _current_theme_id,
                             save_theme_id as _save_theme, theme_meta as _theme_meta)
    _PAL = _load_palette()
except Exception:  # noqa: BLE001 - 主题模块异常不该拦启动
    _PAL = {}
    _THEMES = {"cream": {"label": "米白", "hint": "", "colors": {}}}

    def _current_theme_id():
        return "cream"

    def _save_theme(_tid):
        return _tid

    def _theme_meta(_tid):
        return {"label": "米白", "hint": "", "colors": {}}


def _theme_items():
    return list(_THEMES.items())

C_BG       = _PAL.get("bg", "#FBF8F1")        # 主背景
C_CARD     = _PAL.get("card", "#FFFDF8")      # 面板/卡片
C_CARD_ALT = _PAL.get("card_alt", "#FAF6EC")
C_LINE     = _PAL.get("line", "#D8CFB8")      # 边框
C_LINE_SOFT= _PAL.get("line_soft", "#E6E1D2")
C_LINE_STR = _PAL.get("line_str", "#CBBFA6")
C_INK      = _PAL.get("ink", "#12253D")       # 正文/主按钮底
C_INK_HOV  = _PAL.get("ink_hov", "#1A314D")
C_GREEN    = _PAL.get("accent", "#1D6B4A")    # 强调色（状态/成功/选中）
C_MUTED    = _PAL.get("muted", "#968F80")     # 次要文字
C_MUTED2   = _PAL.get("muted2", "#6C6860")
C_DANGER   = _PAL.get("danger", "#83302A")    # 警示/断开
C_STAGE    = _PAL.get("stage", "#101F33")     # 数字人画面深色舞台
C_NAV_ON   = _PAL.get("nav_on", "#EEF4E9")    # 侧边栏选中项底色
C_HOVER    = _PAL.get("hover", "#F3F0E6")     # 导航悬停底色
C_SEL      = _PAL.get("sel", "#F4F0E4")       # 列表/下拉选中底色
C_DOT_OFF  = _PAL.get("dot_off", "#B9B09C")   # 状态点「未连接」
C_BTN_HOV  = _PAL.get("btn_hov", "#EDF3EA")   # 无边框按钮悬停
C_BTN_DOWN = _PAL.get("btn_down", "#DCE4DC")  # 无边框按钮按下
C_WARN     = _PAL.get("warn", "#B77A1E")      # 提醒
C_ON_ACCENT= _PAL.get("on_accent", "#FFFFFF") # 强调色底上的文字
FONT       = "Microsoft YaHei UI"


# ── 参考音色：一律用中文名展示 ────────────────────────────────────────
# Edge 神经语音的 ShortName 第三段（如 zh-CN-XiaoxiaoNeural → Xiaoxiao）→ 中文名。
# 未收录的语音退化成「普通话 · 女声」这类中文描述，界面里不再出现英文品牌名。
_VOICE_ZH = {
    "xiaoxiao": "晓晓", "xiaoyi": "晓伊", "xiaochen": "晓辰", "xiaohan": "晓涵",
    "xiaomeng": "晓梦", "xiaomo": "晓墨", "xiaoqiu": "晓秋", "xiaorui": "晓睿",
    "xiaoshuang": "晓双", "xiaoxuan": "晓萱", "xiaoyan": "晓颜", "xiaoyou": "晓悠",
    "xiaobei": "晓北", "xiaoni": "晓妮", "xiaofei": "晓飞",
    "yunxi": "云希", "yunjian": "云健", "yunxia": "云夏", "yunyang": "云扬",
    "yunfeng": "云枫", "yunhao": "云皓", "yunjie": "云杰", "yunye": "云野",
    "yunze": "云泽",
    "hiugaai": "曉佳", "hiumaan": "曉曼", "wanlung": "雲龍",
    "hsiaochen": "曉臻", "hsiaoyu": "曉雨", "yunjhe": "雲哲",
    "huihui": "慧慧", "kangkang": "康康", "yaoyao": "瑶瑶", "lili": "丽丽",
    "aria": "阿丽亚", "jenny": "珍妮", "guy": "盖伊", "christopher": "克里斯",
    "eric": "埃里克", "ana": "安娜", "sonia": "索妮娅", "ryan": "瑞安", "libby": "莉比",
}

_LOCALE_ZH = {
    "zh-CN": "普通话", "zh-TW": "台湾国语", "zh-HK": "粤语", "zh-MO": "粤语",
    "zh-SG": "华语", "en-US": "美式英语", "en-GB": "英式英语", "en-AU": "澳式英语",
    "ja-JP": "日语", "ko-KR": "韩语", "de-DE": "德语", "fr-FR": "法语",
    "it-IT": "意大利语", "es-ES": "西班牙语", "pt-BR": "葡萄牙语", "ru-RU": "俄语",
}

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _match_voice_zh(text):
    """从 'zh-CN-XiaoxiaoNeural' / 'Microsoft Huihui Desktop' 里认出中文名。"""
    for token in re.split(r"[^A-Za-z]+", str(text or "")):
        key = token.lower()
        if not key:
            continue
        if key.endswith("neural"):
            key = key[: -len("neural")]
        if key in _VOICE_ZH:
            return _VOICE_ZH[key]
    return ""


def voice_option(row):
    """系统音色 → (下拉里显示的中文文本, 真正该发给后端的取值)。

    取值必须是语音的 ShortName（如 zh-CN-XiaoxiaoNeural），edgetts 引擎直接拿它合成；
    早期版本把带「（系统）」后缀的显示文案当取值发出去，音色其实没生效。
    """
    rid = str(row.get("id") or "").strip()
    name = str(row.get("name") or rid).strip()
    engine = str(row.get("engine") or "").lower()
    locale = str(row.get("locale") or "").strip()
    gender = {"female": "女声", "male": "男声"}.get(str(row.get("gender") or "").lower(), "")
    loc = _LOCALE_ZH.get(locale) or ("中文" if locale.lower().startswith("zh") else locale)
    zh = _match_voice_zh(rid) or _match_voice_zh(name)
    bits = [b for b in (loc, gender) if b]
    if zh:
        label = zh + ("  ·  " + "  ·  ".join(bits) if bits else "")
    else:
        label = "  ·  ".join(bits) or "系统音色"
    if engine == "sapi":
        label += "  ·  本机"
    return label, (rid or name)


def setting_value(item, default=""):
    """设置接口给的是 {value, label, help, effect, source} 结构，这里统一取真值。

    早期接口直接返回裸值，两种形状都兼容，免得前后端任何一侧先升级就互相打脸。
    """
    if isinstance(item, dict):
        return item.get("value", default)
    return default if item is None else item


def setting_bool(item, default=True):
    value = setting_value(item, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


class SegmentedControl(tk.Canvas):
    """分段选择器：一排等宽胶囊，选中项实底强调色。

    替代原生 Radiobutton —— 原生圆点在深色主题下几乎看不清，且三个选项横排时
    "哪个是当前值"要靠找那个小圆点，分段器扫一眼就知道。
    """

    def __init__(self, parent, options, variable=None, command=None,
                 font_size=12, height=None, bg=None, min_seg=None):
        self._choices = [(str(v), str(label)) for v, label in options]
        self.var = variable if variable is not None else tk.StringVar(
            value=self._choices[0][0] if self._choices else "")
        self._command = command
        self._font_size = font_size
        self._h = height or S(34)
        self._min_seg = min_seg or S(84)
        bgc = bg if bg is not None else _widget_bg(parent)
        width = self._min_seg * max(1, len(self._choices))
        super().__init__(parent, height=self._h, width=width, bg=bgc,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._click)
        if self.var is not None:
            self.var.trace_add("write", lambda *a: self._draw())

    def set_options(self, options):
        self._choices = [(str(v), str(label)) for v, label in options]
        self._draw()

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20 or h < 10:
            return
        self.delete("all")
        self._photos = []
        n = max(1, len(self._choices))
        pad = S(3)
        r = (h - pad * 2) / 2.0
        outer = rounded_photo(w, h, r + pad, C_CARD_ALT, C_LINE)
        self._photos.append(outer)
        self.create_image(w / 2, h / 2, image=outer)

        seg_w = (w - pad * 2) / float(n)
        cur = self.var.get() if self.var is not None else ""
        for idx, (value, label) in enumerate(self._choices):
            x0 = pad + idx * seg_w
            on = (value == cur)
            if on:
                photo = rounded_photo(int(seg_w) - 2, h - pad * 2, r, C_GREEN)
                self._photos.append(photo)
                self.create_image(x0 + seg_w / 2, h / 2, image=photo)
            self.create_text(x0 + seg_w / 2, h / 2, text=label,
                             font=(FONT, self._font_size, "bold" if on else "normal"),
                             fill=C_ON_ACCENT if on else C_MUTED2)

    def _click(self, event):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20 or not self._choices:
            return
        pad = S(3)
        seg_w = (w - pad * 2) / float(len(self._choices))
        idx = int((event.x - pad) // seg_w) if seg_w else -1
        if idx < 0 or idx >= len(self._choices):
            return
        value = self._choices[idx][0]
        if self.var is not None:
            self.var.set(value)
        if callable(self._command):
            self._command(value)


class ToggleSwitch(tk.Canvas):
    """开关：圆角轨道 + 滑块。

    原生 Checkbutton 的方框在很多主题下几乎和背景同色，用户看不出是「关」还是
    「没这个功能」。开关有位置语义：滑块在右=开。
    """

    def __init__(self, parent, variable, command=None, bg=None, height=None):
        self.var = variable
        self._command = command
        self._h = height or S(24)
        self._w = int(self._h * 1.95)
        bgc = bg if bg is not None else _widget_bg(parent)
        super().__init__(parent, width=self._w, height=self._h, bg=bgc,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Button-1>", self._toggle)
        self.bind("<Configure>", lambda e: self._draw())
        self.var.trace_add("write", lambda *a: self._draw())

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 8:
            return
        self.delete("all")
        self._photos = []
        on = bool(self.var.get())
        track = rounded_photo(w, h, h / 2.0, C_GREEN if on else C_LINE_STR)
        self._photos.append(track)
        self.create_image(w / 2, h / 2, image=track)
        pad = max(2, int(h * 0.14))
        d = h - pad * 2
        cx = (w - pad - d / 2.0) if on else (pad + d / 2.0)
        self.create_oval(cx - d / 2.0, pad, cx + d / 2.0, pad + d,
                         fill=C_CARD, outline="")

    def _toggle(self, _event=None):
        self.var.set(not bool(self.var.get()))
        if callable(self._command):
            self._command()


def make_switch(parent, text, variable, command=None, bg=None, hint=""):
    """开关 + 可点文字，返回容器。文字也能点，不用精准点到那个小圆钮。"""
    bgc = bg if bg is not None else _widget_bg(parent)
    box = tk.Frame(parent, bg=bgc)
    switch = ToggleSwitch(box, variable, command=command, bg=bgc)
    switch.pack(side="left")
    label = tk.Label(box, text=text, font=(FONT, 11), bg=bgc, fg=C_MUTED2, cursor="hand2")
    label.pack(side="left", padx=(S(8), S(0)))

    def _hit(_e=None):
        switch._toggle()

    label.bind("<Button-1>", _hit)
    if hint:
        tk.Label(box, text=hint, font=(FONT, 10), bg=bgc, fg=C_MUTED
                 ).pack(side="left", padx=(S(8), S(0)))
    box.switch = switch
    return box


def _sens_word(value):
    """灵敏度数值 → 一句人话（滑块右侧显示）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.5
    if v < 0.35:
        return "迟钝"
    if v < 0.55:
        return "中等"
    if v < 0.75:
        return "灵敏"
    return "很灵敏"


class ThemedSlider(tk.Canvas):
    """自绘滑块：细轨道 + 已填充段 + 游标 + 右侧数值。

    原生 `tk.Scale` 在 Windows 上是 90 年代的沟槽加方块把手，而且默认把数值画在
    滑轨**上方**——放在一行表单里会把行高顶歪，数值还常被裁掉。这里数值放右侧，
    并允许附一段定性描述（"0.50 · 中等"），一眼看得出当前是偏灵敏还是偏迟钝。
    """

    def __init__(self, parent, variable, from_=0.0, to=1.0, step=0.05, bg=None,
                 width=None, font_size=11, describe=None, fmt="%.2f"):
        self.var = variable
        self._from = float(from_)
        self._to = float(to)
        self._step = abs(float(step)) or 0.01
        self._fmt = fmt
        self._describe = describe
        self._font_size = font_size
        # 右侧数值要给「0.65 · 灵敏」这种最长文案留够位置，否则会压到滑轨上
        self._text_w = S(96)
        bgc = bg if bg is not None else _widget_bg(parent)
        super().__init__(parent, width=width or S(300), height=S(26), bg=bgc,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._on_drag)
        self.bind("<B1-Motion>", self._on_drag)
        self.var.trace_add("write", lambda *a: self._draw())

    # ── 几何 ──
    def _track(self):
        w = self.winfo_width()
        return S(8), max(S(20), w - self._text_w - S(10))

    def _value_x(self, value):
        x0, x1 = self._track()
        ratio = (float(value) - self._from) / (self._to - self._from or 1.0)
        ratio = min(1.0, max(0.0, ratio))
        return x0 + (x1 - x0) * ratio

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20 or h < 8:
            return
        self.delete("all")
        x0, x1 = self._track()
        cy = h / 2.0
        bar = max(S(5), int(h * 0.22))
        self.create_line(x0, cy, x1, cy, fill=C_LINE_STR, width=bar, capstyle="round")
        knob = self._value_x(self.var.get())
        if knob > x0 + 1:
            self.create_line(x0, cy, knob, cy, fill=C_GREEN, width=bar, capstyle="round")
        r = max(S(7), int(h * 0.30))
        self.create_oval(knob - r, cy - r, knob + r, cy + r, fill=C_GREEN, outline=C_CARD,
                         width=max(2, S(2)))
        text = self._fmt % float(self.var.get())
        if callable(self._describe):
            extra = self._describe(float(self.var.get()))
            if extra:
                text = "%s · %s" % (text, extra)
        self.create_text(w - S(2), cy, text=text, anchor="e",
                         font=(FONT, self._font_size), fill=C_MUTED2)

    # ── 交互 ──
    def _on_drag(self, event):
        x0, x1 = self._track()
        if x1 <= x0:
            return
        ratio = min(1.0, max(0.0, (event.x - x0) / float(x1 - x0)))
        value = self._from + (self._to - self._from) * ratio
        value = round(value / self._step) * self._step
        value = min(self._to, max(self._from, value))
        # 浮点乘除会留一串尾数（0.6000000000000001），按 step 的小数位收一下
        digits = 0 if self._step >= 1 else 2
        self.var.set(round(value, digits))


class ActionPicker(tk.Frame):
    """「数字人 + 动作」两级选择器。

    动作素材是按形象分目录存的，所以选动作前必须先选形象；换形象时动作列表要
    重新拉 `/api/avatars/{id}/gestures`，否则会给人一个"看起来能选、实际播不出"的
    假选项。主服务没起来时只留提示，不抛错——配置页不该因为服务没连上就打不开。
    """

    def __init__(self, parent, app, on_change=None, bg=None):
        bgc = bg if bg is not None else _widget_bg(parent)
        super().__init__(parent, bg=bgc)
        self.app = app
        self._on_change = on_change
        self._bg = bgc
        self._gmap = {}                 # 动作下拉标签 → 动作 id
        self._avatars = []

        self.avatar_var = tk.StringVar()
        self.gesture_var = tk.StringVar()
        self.avatar_combo = RoundedCombo(self, textvariable=self.avatar_var,
                                         font_size=11, width=S(180))
        self.avatar_combo.pack(side="left")
        self.avatar_combo.bind_select(self._on_avatar)
        self.gesture_combo = RoundedCombo(self, textvariable=self.gesture_var,
                                          font_size=11, width=S(210))
        self.gesture_combo.pack(side="left", padx=(S(8), S(0)))
        self.gesture_combo.bind_select(self._changed)

    # ── 数据 ──
    def _ui(self, fn):
        """把回调投回主线程；窗口已销毁时静默丢弃。"""
        try:
            self.after(0, fn)
        except Exception:  # noqa: BLE001
            pass

    def load(self, done=None):
        def work():
            files, err = self._get("/api/avatars", timeout=8)
            avatars = files if isinstance(files, list) else []
            self._ui(lambda: self._fill_avatars(avatars, err, done))
        threading.Thread(target=work, daemon=True).start()

    def _get(self, tail, timeout=8):
        try:
            r = self.app.sess.get(BASE + tail, timeout=timeout)
            payload = r.json()
        except Exception as exc:  # noqa: BLE001
            return None, "%r" % (exc,)
        if payload.get("code", -1) != 0:
            return None, payload.get("msg") or "接口返回错误"
        return payload.get("data"), ""

    def _fill_avatars(self, avatars, err, done=None):
        self._avatars = [a.get("id") for a in avatars if isinstance(a, dict) and a.get("id")]
        self.avatar_combo["values"] = self._avatars
        if self._avatars and self.avatar_var.get() not in self._avatars:
            self.avatar_var.set(self._avatars[0])
        if err:
            self.gesture_combo["values"] = []
            self.gesture_var.set("")
        else:
            self._load_gestures()
        if callable(done):
            done()

    def _on_avatar(self):
        self.gesture_var.set("")
        self._load_gestures()
        self._changed()

    def _load_gestures(self):
        avatar = self.avatar_var.get().strip()
        self._gmap = {}
        if not avatar:
            self.gesture_combo["values"] = []
            return

        def work():
            data, err = self._get("/api/avatars/%s/gestures" % avatar, timeout=15)
            items = (data or {}).get("gestures") or []
            self._ui(lambda: self._fill_gestures(avatar, items, err))
        threading.Thread(target=work, daemon=True).start()

    def _fill_gestures(self, avatar, items, err):
        if avatar != self.avatar_var.get().strip():
            return                      # 期间又换了形象，旧响应丢掉
        labels = []
        self._gmap = {}
        for item in items:
            gid = str(item.get("id") or "")
            if not gid:
                continue
            label = str(item.get("label") or gid)
            if label in self._gmap:      # 显示名重名时补上 id，避免选错
                label = "%s（%s）" % (label, gid)
            self._gmap[label] = gid
            labels.append(label)
        self.gesture_combo["values"] = labels
        if labels:
            current = self.gesture_var.get()
            if current not in labels:
                self.gesture_var.set(labels[0])
        else:
            self.gesture_var.set("")

    # ── 取值 ──
    def get(self):
        avatar = self.avatar_var.get().strip()
        gesture = self._gmap.get(self.gesture_var.get().strip(), "")
        if not avatar or not gesture:
            return {"avatar": "", "gesture": ""}
        return {"avatar": avatar, "gesture": gesture}

    def set(self, spec):
        spec = spec if isinstance(spec, dict) else {}
        avatar = str(spec.get("avatar") or "").strip()
        gesture = str(spec.get("gesture") or "").strip()
        if avatar:
            self.avatar_var.set(avatar)
            self._load_gestures()
            self._pending_gesture = gesture
            # 动作列表是异步回来的：回来了再按 id 反查标签
            self.after(400, self._apply_pending)
        else:
            # 清空配置时要把挂起的回填一起清掉，否则上一张形象的待回填动作
            # 会在这张形象上被"认领"，选出一个实际并不存在的动作。
            self._pending_gesture = ""
            self.gesture_var.set("")

    def _apply_pending(self):
        gesture = getattr(self, "_pending_gesture", "")
        if not gesture:
            return
        for label, gid in self._gmap.items():
            if gid == gesture:
                self.gesture_var.set(label)
                self._pending_gesture = ""
                self._pending_tries = 0
                return
        # 列表还没回来就再等一轮（最多几轮，失败也不阻塞界面）
        tries = getattr(self, "_pending_tries", 0) + 1
        self._pending_tries = tries
        if tries < 8:
            self.after(400, self._apply_pending)
        else:
            self._pending_tries = 0
            self._pending_gesture = ""

    def _changed(self):
        if callable(self._on_change):
            self._on_change(self.get())


# 经典音色：只留常用的几把嗓子，每种给一句特征描述。
# 之前是把 /api/voices/system 返回的几十个语音全塞进下拉框，名字还是
# 「普通话 · 女声 2」这种，选音色变成了开盲盒。
VOICE_CLASSICS = (
    ("zh-CN-XiaoxiaoNeural", "晓晓", "女声 · 亲和自然"),
    ("zh-CN-YunxiNeural", "云希", "男声 · 年轻清亮"),
    ("zh-CN-YunjianNeural", "云健", "男声 · 沉稳解说"),
    ("zh-CN-XiaoyiNeural", "晓伊", "女声 · 温柔"),
    ("zh-CN-YunyangNeural", "云扬", "男声 · 新闻播报"),
    ("zh-CN-XiaochenNeural", "晓辰", "女声 · 活泼"),
)


def voice_display_name(value):
    """把 `tts.ref_file` 的取值翻成给人看的名字（开播页只显示、不编辑）。"""
    value = str(value or "").strip()
    if not value:
        return "跟随引擎默认"
    for v, name, _trait in VOICE_CLASSICS:
        if v == value:
            return name
    if value.startswith("S_"):
        return "云端复刻音色"
    base = os.path.basename(value.replace("\\", "/"))
    return os.path.splitext(base)[0] or value


class VoicePicker(tk.Frame):
    """音色卡片选择器：每张卡一个音色，点选即生效。

    value 可以是 EdgeTTS 的完整语音名，也可以是云端复刻的 S_xxx 音色 ID；
    卡片按 value 匹配选中态，对不上就都不选中（表示"当前是手填的自定义值"）。
    """

    def __init__(self, parent, variable=None, on_change=None, bg=None, cols=3, compact=False):
        bgc = bg if bg is not None else _widget_bg(parent)
        super().__init__(parent, bg=bgc)
        self.var = variable if variable is not None else tk.StringVar()
        self._on_change = on_change
        self._bg = bgc
        self._cols = cols
        self.compact = compact
        self._choices = list(VOICE_CLASSICS)
        self._cards = {}
        self._build()

    def _card_size(self):
        if self.compact:
            return S(132), S(50)
        return S(178), S(56)

    def _build(self):
        for child in self.winfo_children():
            child.destroy()
        self._cards = {}
        w, h = self._card_size()
        for idx, (value, name, trait) in enumerate(self._choices):
            row, col = divmod(idx, self._cols)
            holder = tk.Frame(self, bg=self._bg, width=w, height=h)
            holder.grid(row=row, column=col, padx=(S(0), S(8)), pady=(S(0), S(8)))
            holder.grid_propagate(False)
            canvas = tk.Canvas(holder, width=w, height=h, bg=self._bg,
                               highlightthickness=0, bd=0, cursor="hand2")
            canvas.pack(fill="both", expand=True)
            canvas.bind("<Button-1>", lambda e, v=value: self._pick(v))
            self._cards[value] = {"canvas": canvas, "name": name, "trait": trait,
                                  "w": w, "h": h}
        self._paint()
        if self.var is not None:
            self.var.trace_add("write", lambda *a: self._paint())

    def _paint(self):
        cur = self.var.get().strip() if self.var is not None else ""
        for value, card in self._cards.items():
            canvas, w, h = card["canvas"], card["w"], card["h"]
            on = (value == cur)
            canvas.delete("all")
            photo = rounded_photo(w, h, S(10), C_NAV_ON if on else C_CARD,
                                  C_GREEN if on else C_LINE, 2 if on else 1)
            canvas._card_photo = photo
            canvas.create_image(w / 2, h / 2, image=photo)
            pad = S(13)
            canvas.create_text(pad, h / 2 - S(8), text=card["name"], anchor="w",
                               font=(FONT, 12, "bold"), fill=C_GREEN if on else C_INK)
            canvas.create_text(pad, h / 2 + S(10), text=card["trait"], anchor="w",
                               font=(FONT, 10), fill=C_MUTED2 if on else C_MUTED)
            if on:
                canvas.create_oval(w - S(20), h / 2 - S(5), w - S(10), h / 2 + S(5),
                                   fill=C_GREEN, outline="")

    def _pick(self, value):
        if self.var is not None:
            self.var.set(value)
        if callable(self._on_change):
            self._on_change(value)

    def set_options(self, options):
        """options: [(value, name, trait), ...]；传空则回到经典音色。"""
        self._choices = list(options) or list(VOICE_CLASSICS)
        self._build()

    def set(self, value):
        if self.var is not None:
            self.var.set(value or "")
        else:
            self._paint()


def ask_text(parent, title, label, initial="", hint=""):
    """主题化输入弹窗；确定返回字符串，取消返回 None。

    不用 tkinter.simpledialog：那个是系统默认灰框，和深色主题并排很突兀。
    """
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(title)
    win.configure(bg=C_BG)
    win.resizable(False, False)
    body = tk.Frame(win, bg=C_BG)
    body.pack(fill="both", expand=True, padx=S(22), pady=S(18))
    tk.Label(body, text=label, font=(FONT, 12), bg=C_BG, fg=C_INK, anchor="w"
             ).pack(fill="x")
    var = tk.StringVar(value=initial)
    entry = make_entry(body, var, width=30)
    entry.pack(fill="x", pady=(S(8), S(2)))
    if hint:
        tk.Label(body, text=hint, font=(FONT, 10), bg=C_BG, fg=C_MUTED, anchor="w"
                 ).pack(fill="x")
    result = {"value": None}

    def _ok(_e=None):
        result["value"] = var.get().strip()
        win.destroy()

    bar = tk.Frame(body, bg=C_BG)
    bar.pack(fill="x", pady=(S(14), S(0)))
    make_btn(bar, "确定", _ok, primary=True, sm=True).pack(side="right")
    make_btn(bar, "取消", win.destroy, sm=True).pack(side="right", padx=(S(0), S(8)))
    entry.bind("<Return>", _ok)
    win.bind("<Escape>", lambda _e: win.destroy())

    win.update_idletasks()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    win.geometry("%dx%d+%d+%d" % (w, h, px + (pw - w) // 2, py + (ph - h) // 3))
    win.transient(parent)
    win.deiconify()
    entry.focus_set()
    entry.selection_range(0, "end")
    win.grab_set()
    win.wait_window()
    return result["value"]


def ask_confirm(parent, title, message, ok_text="确定", danger=False):
    """主题化确认弹窗，返回 True/False。"""
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(title)
    win.configure(bg=C_BG)
    win.resizable(False, False)
    body = tk.Frame(win, bg=C_BG)
    body.pack(fill="both", expand=True, padx=S(22), pady=S(18))
    tk.Label(body, text=message, font=(FONT, 11), bg=C_BG, fg=C_INK, justify="left",
             anchor="w", wraplength=S(380)).pack(fill="x")
    result = {"ok": False}

    def _ok():
        result["ok"] = True
        win.destroy()

    bar = tk.Frame(body, bg=C_BG)
    bar.pack(fill="x", pady=(S(16), S(0)))
    make_btn(bar, ok_text, _ok, primary=True, sm=True,
             fg=C_DANGER if danger else None).pack(side="right")
    make_btn(bar, "取消", win.destroy, sm=True).pack(side="right", padx=(S(0), S(8)))
    win.bind("<Escape>", lambda _e: win.destroy())

    win.update_idletasks()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    win.geometry("%dx%d+%d+%d" % (w, h, px + (pw - w) // 2, py + (ph - h) // 3))
    win.transient(parent)
    win.deiconify()
    win.grab_set()
    win.wait_window()
    return result["ok"]


def make_btn(parent, text, cmd, primary=False, sm=False, padx=S(20), pady=S(9), font_size=13, fg=None, tooltip=None, borderless=False):
    """主题化圆角按钮：主按钮=墨色实底，次按钮=白底细描边。

    borderless=True 用于深色舞台这类背景，去掉那圈描边。
    """
    return RoundButton(parent, text, cmd, primary=primary, sm=sm,
                       font_size=font_size, fg=fg, tooltip=tooltip,
                       borderless=borderless)


def make_entry(parent, textvariable=None, width=22, show=None, state="normal", placeholder="", height=1):
    """主题化输入框：白底 + 暖棕描边，聚焦变墨色。"""
    if height > 1:
        e = tk.Text(parent, width=width, height=height, font=(FONT, 12),
                    bg=C_CARD, fg=C_INK, relief="flat", bd=0,
                    highlightthickness=1, highlightbackground=C_LINE,
                    highlightcolor=C_INK, wrap="word")
        return e
    e = tk.Entry(parent, textvariable=textvariable, width=width, show=show, state=state,
                 font=(FONT, 12), bg=C_CARD, fg=C_INK, relief="flat", bd=0,
                 highlightthickness=1, highlightbackground=C_LINE,
                 highlightcolor=C_INK)
    if placeholder:
        # 占位提示必须能被认出来是"提示"而不是"用户填的内容"。
        # 早期版本只 insert 进输入框，结果 StringVar 里一直是"输入菜单名称搜索"，
        # 页面一加载就拿它当搜索词，表格永远空（这个坑只有真跑一遍才看得见）。
        e.insert(0, placeholder)
        e.configure(fg=C_MUTED)
        e._placeholder = placeholder
        e._ph_active = True

        def _clear(_event=None):
            if e._ph_active:
                e.delete(0, "end")
                e.configure(fg=C_INK)
                e._ph_active = False

        def _restore(_event=None):
            if not e.get():
                e._ph_active = True
                e.insert(0, placeholder)
                e.configure(fg=C_MUTED)

        e.bind("<FocusIn>", _clear)
        e.bind("<FocusOut>", _restore)
    return e


def entry_value(widget, variable=None):
    """输入框的真实取值：还显示着占位提示时算空字符串。"""
    if getattr(widget, "_ph_active", False):
        return ""
    if variable is not None:
        return variable.get()
    return widget.get()


def page_bg(widget, bg=C_BG):
    widget.configure(bg=bg)
    return widget


class RoundedCombo(tk.Canvas):
    """圆角下拉框：外层画圆角描边和自绘箭头，内部嵌一个无边框 ttk.Combobox。

    ttk 的下拉框是系统绘制的方块加三角箭头，跟圆角卡片放一起很突兀；
    这里只保留它的弹出列表能力，外观完全由自己画。
    """

    def __init__(self, parent, textvariable=None, values=(), font_size=12,
                 height=None, width=None, state="readonly"):
        h = S(height or 40)
        super().__init__(parent, height=h, width=width or S(180),
                         bg=_widget_bg(parent), highlightthickness=0, bd=0, cursor="hand2")
        self._var = textvariable if textvariable is not None else tk.StringVar()
        self.combo = ttk.Combobox(self, textvariable=self._var, values=list(values),
                                  state=state, font=(FONT, font_size),
                                  style="Bare.TCombobox", cursor="hand2")
        self.combo.place(x=S(14), y=S(5), relwidth=1.0, width=-S(46), height=h - S(10))
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._open)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self._hover = False
        self._draw()

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 8 or h < 8:
            return
        outline = C_LINE if self._hover else C_LINE_SOFT
        self._photo = rounded_photo(w, h, S(9), C_CARD, outline)
        self.delete("all")
        self.create_image(w / 2, h / 2, image=self._photo)
        cx, cy, r = w - S(20), h / 2, S(5)
        self.create_line(cx - r, cy - r * 0.4, cx, cy + r * 0.6, cx + r, cy - r * 0.4,
                         fill=C_MUTED2, width=max(1, S(1.5)),
                         capstyle="round", joinstyle="round")

    def _open(self, event=None):
        """点箭头那一侧等同于点 combobox 自身，交给 ttk 弹出列表。"""
        try:
            self.combo.focus_set()
            w = self.combo.winfo_width()
            self.combo.event_generate("<Button-1>", x=max(1, w - S(8)), y=self.winfo_height() // 2)
            self.combo.event_generate("<ButtonRelease-1>", x=max(1, w - S(8)),
                                      y=self.winfo_height() // 2)
        except Exception:  # noqa: BLE001
            pass

    # ── 兼容直接操作 ttk.Combobox 的写法 ─────────────────────────
    def __setitem__(self, key, value):
        if key == "values":
            self.combo["values"] = value
        else:
            self.combo[key] = value

    def __getitem__(self, key):
        if key == "values":
            return self.combo["values"]
        return self.combo[key]

    def config(self, **kw):
        if "values" in kw:
            self.combo["values"] = kw.pop("values")
        if "state" in kw:
            self.combo.config(state=kw.pop("state"))
        if kw:
            super().configure(**kw)

    configure = config

    def get(self):
        return self._var.get()

    def set(self, value):
        self._var.set(value)

    def bind_select(self, callback):
        self.combo.bind("<<ComboboxSelected>>", lambda e: callback())


# ── ttk 主题：把下拉框 / 表格 / 滚动条统一成扁平现代样式 ────────────────
def init_style(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")           # clam 才能自定义颜色，默认 vista 是系统绘制
    except Exception:  # noqa: BLE001
        pass
    style.configure(".", background=C_BG, foreground=C_INK, font=(FONT, 11),
                    focuscolor=C_CARD)
    style.configure("TFrame", background=C_BG)
    style.configure("TLabel", background=C_BG, foreground=C_INK)

    style.configure("TCombobox", fieldbackground=C_CARD, background=C_CARD,
                    foreground=C_INK, arrowcolor=C_MUTED2, bordercolor=C_LINE_SOFT,
                    lightcolor=C_CARD, darkcolor=C_CARD, insertcolor=C_INK,
                    padding=(S(8), S(5)), relief="flat", arrowsize=S(12))
    style.map("TCombobox",
              fieldbackground=[("readonly", C_CARD), ("disabled", C_CARD_ALT)],
              background=[("readonly", C_CARD), ("active", C_CARD)],
              foreground=[("disabled", C_MUTED)],
              bordercolor=[("focus", C_INK), ("hover", C_LINE)],
              arrowcolor=[("hover", C_INK), ("focus", C_INK)])
    root.option_add("*TCombobox*Listbox.background", C_CARD)
    root.option_add("*TCombobox*Listbox.foreground", C_INK)
    root.option_add("*TCombobox*Listbox.selectBackground", C_NAV_ON)
    root.option_add("*TCombobox*Listbox.selectForeground", C_INK)
    root.option_add("*TCombobox*Listbox.font", (FONT, 11))

    # 圆角下拉框里那个"裸"的 combobox：无边框、无箭头，只剩文本区
    style.configure("Bare.TCombobox", fieldbackground=C_CARD, background=C_CARD,
                    foreground=C_INK, bordercolor=C_CARD, lightcolor=C_CARD,
                    darkcolor=C_CARD, arrowcolor=C_CARD, insertcolor=C_INK,
                    padding=0, relief="flat")
    style.layout("Bare.TCombobox", [
        ("Combobox.field", {
            "children": [("Combobox.padding", {
                "children": [("Combobox.textarea", {"sticky": "nswe"})],
                "sticky": "nswe"})],
            "sticky": "nswe"}),
    ])
    style.map("Bare.TCombobox",
              fieldbackground=[("readonly", C_CARD), ("active", C_CARD)],
              background=[("readonly", C_CARD), ("active", C_CARD)],
              bordercolor=[("focus", C_CARD), ("hover", C_CARD)])

    # 无箭头细滚动条：去掉上下三角，只留一个圆角滑块
    for orient, axis in (("Horizontal", "ns"), ("Vertical", "ew")):
        style.layout("%s.TScrollbar" % orient, [
            ("%s.Scrollbar.trough" % orient, {
                "children": [("%s.Scrollbar.thumb" % orient,
                              {"expand": "1", "sticky": "nswe"})],
                "sticky": "ns" if orient == "Horizontal" else "ew"}),
        ])
    style.configure("TScrollbar", background=C_LINE_SOFT, troughcolor=C_BG,
                    bordercolor=C_BG, lightcolor=C_LINE_SOFT, darkcolor=C_LINE_SOFT,
                    arrowcolor=C_BG, relief="flat", borderwidth=0)
    style.map("TScrollbar", background=[("active", C_LINE)])

    style.configure("Treeview", background=C_CARD, fieldbackground=C_CARD,
                    foreground=C_INK, borderwidth=0, relief="flat",
                    bordercolor=C_CARD, lightcolor=C_CARD, darkcolor=C_CARD,
                    rowheight=S(30), font=(FONT, 11))
    style.configure("Treeview.Heading", background=C_CARD_ALT, foreground=C_MUTED2,
                    font=(FONT, 10, "bold"), relief="flat", borderwidth=0,
                    padding=(S(6), S(5)))
    style.map("Treeview.Heading", background=[("active", C_CARD_ALT)])
    style.map("Treeview", background=[("selected", C_NAV_ON)],
              foreground=[("selected", C_INK)])
    style.configure("TSeparator", background=C_LINE_SOFT)


# ── 圆角外观：卡片 / 按钮 ──────────────────────────────────────────────
# Tk 没有圆角，也没有抗锯齿。这里用 PIL 画超采样后的圆角矩形当背景图，
# 既拿到平滑边缘，又不用为每个控件写一套 Canvas 图元。
_ROUND_CACHE = {}


def _widget_bg(widget, fallback=C_BG):
    try:
        return widget.cget("bg") or fallback
    except Exception:  # noqa: BLE001
        return fallback


def rounded_photo(width, height, radius, fill, outline=None, outline_w=1):
    key = (width, height, radius, fill, outline, outline_w)
    hit = _ROUND_CACHE.get(key)
    if hit is not None:
        return hit
    from PIL import Image, ImageDraw, ImageTk
    ss = 3                                    # 3 倍超采样后缩小 = 抗锯齿
    W, H = max(1, width * ss), max(1, height * ss)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    ow = max(1, int(outline_w * ss)) if outline else 0
    draw.rounded_rectangle(
        [ow // 2, ow // 2, W - 1 - ow // 2, H - 1 - ow // 2],
        radius=max(0, int(radius * ss)), fill=fill, outline=outline, width=ow)
    img = img.resize((width, height), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    if len(_ROUND_CACHE) > 400:
        _ROUND_CACHE.clear()
    _ROUND_CACHE[key] = photo
    return photo


_FONT_CACHE = {}


def ui_font(size, weight="normal"):
    """按字号缓存的 tkinter 字体。

    气泡每 0.25 秒重绘一次，每次都 `tkfont.Font(...)` 会在 Tk 里不断注册新的
    命名字体，进程活久了字体表会一直涨。同一个字号复用一份即可。
    """
    key = (int(size), weight)
    font = _FONT_CACHE.get(key)
    if font is None:
        font = tkfont.Font(family=FONT, size=int(size), weight=weight)
        _FONT_CACHE[key] = font
    return font


def rounded_mask_photo(width, height, radius, corner_fill, outline=None):
    """圆角「遮罩」：四角铺成底色、中间透明，用来把方形画面切成圆角窗口。"""
    key = ("mask", width, height, radius, corner_fill, outline)
    hit = _ROUND_CACHE.get(key)
    if hit is not None:
        return hit
    from PIL import Image, ImageChops, ImageDraw, ImageTk
    ss = 3
    W, H = max(1, width * ss), max(1, height * ss)
    r = max(0, int(radius * ss))
    hole = Image.new("L", (W, H), 0)
    ImageDraw.Draw(hole).rounded_rectangle([0, 0, W - 1, H - 1], radius=r, fill=255)
    overlay = Image.new("RGBA", (W, H), corner_fill)
    overlay.putalpha(ImageChops.invert(hole))
    if outline:
        edge = Image.new("L", (W, H), 0)
        ImageDraw.Draw(edge).rounded_rectangle([0, 0, W - 1, H - 1], radius=r,
                                               outline=255, width=max(1, ss))
        ring = Image.new("RGBA", (W, H), outline)
        ring.putalpha(edge)
        overlay = Image.alpha_composite(overlay, ring)
    img = overlay.resize((width, height), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    if len(_ROUND_CACHE) > 400:
        _ROUND_CACHE.clear()
    _ROUND_CACHE[key] = photo
    return photo


class Card(tk.Frame):
    """圆角面板：外框是父容器的底色，内容铺在圆角背景上。

    用法与 tk.Frame 一致——构造时传进来的 bg / highlight* 会被忽略，
    圆角填充由样式统一决定。
    """

    def __init__(self, parent, radius=None, fill=C_CARD, outline=C_LINE_SOFT, **kw):
        kw.pop("bg", None)
        kw.pop("highlightbackground", None)
        kw.pop("highlightthickness", None)
        kw.pop("highlightcolor", None)
        kw.pop("relief", None)
        kw.pop("bd", None)
        super().__init__(parent, bg=_widget_bg(parent), **kw)
        self._radius = S(radius if radius is not None else 12)
        self._fill = fill
        self._outline = outline
        self._bg_label = tk.Label(self, bd=0, bg=self["bg"], highlightthickness=0)
        self._bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        self._bg_label.lower()
        self._last_size = (0, 0)
        self.bind("<Configure>", self._paint)

    def _paint(self, event=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4 or (w, h) == self._last_size:
            return
        self._last_size = (w, h)
        photo = rounded_photo(w, h, self._radius, self._fill, self._outline)
        self._bg_label.configure(image=photo)


class RoundButton(tk.Canvas):
    """圆角按钮：主按钮墨色实底，次按钮白底细描边，hover / 按下都有反馈。"""

    def __init__(self, parent, text="", command=None, primary=False, sm=False,
                 font_size=None, bg=None, fg=None, width=None, tooltip=None,
                 borderless=False):
        self._parent_bg = bg or _widget_bg(parent)
        self._primary = primary
        self._sm = sm
        # 深色舞台上的按钮不要那圈描边——浅底+细描边浮在深蓝上会显成青色方框
        self._borderless = borderless
        self._font = (FONT, font_size or (11 if sm else 13))
        self._text = text
        self._command = command
        self._fg_override = fg
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._photo = None
        self._radius = S(8 if sm else 9)
        self._tooltip = tooltip
        self._tip_win = None

        fnt = tkfont.Font(family=FONT, size=self._font[1])
        pad_x = S(14) if sm else S(22)
        height = S(30) if sm else S(40)
        width = width or (fnt.measure(text or " ") + pad_x * 2)
        super().__init__(parent, width=width, height=height, bg=self._parent_bg,
                         highlightthickness=0, bd=0, cursor="hand2")

        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    # ── 状态色 ────────────────────────────────────────────────────
    def _palette(self):
        if not self._enabled:
            return C_CARD_ALT, C_MUTED, (None if self._borderless else
                                         (C_LINE_SOFT if not self._primary else C_CARD_ALT))
        if self._primary:
            base = C_INK_HOV if (self._pressed or self._hover) else C_INK
            return base, C_BG, None
        if self._borderless:
            # 无描边时要靠底色变化体现交互，否则 hover 跟静止态几乎看不出差别
            if self._pressed:
                return C_BTN_DOWN, C_INK, None
            if self._hover:
                return C_BTN_HOV, C_INK, None
            return C_CARD, self._fg_override or C_INK, None
        if self._pressed:
            return C_BTN_DOWN, C_INK, C_LINE
        if self._hover:
            return C_CARD_ALT, C_INK, C_LINE
        return C_CARD, self._fg_override or C_INK, C_LINE_SOFT

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        fill, fg, outline = self._palette()
        self._photo = rounded_photo(w, h, self._radius, fill, outline)
        self.delete("all")
        self.create_image(w / 2, h / 2, image=self._photo)
        self.create_text(w / 2, h / 2 + S(1), text=self._text, fill=fg,
                         font=self._font, anchor="center")

    # ── 交互 ──────────────────────────────────────────────────────
    def _on_enter(self, _e=None):
        self._hover = True
        self._draw()
        self._show_tip()

    def _on_leave(self, _e=None):
        self._hover = False
        self._pressed = False
        self._draw()
        self._hide_tip()

    def _show_tip(self):
        if not self._tooltip or self._tip_win:
            return
        tip = tk.Toplevel(self)
        tip.wm_overrideredirect(True)
        tip.wm_attributes("-toolwindow", True)
        tip.configure(bg="#2B2B2B")
        lx = tk.Label(tip, text=self._tooltip, bg="#2B2B2B", fg="#FFFFFF",
                      font=(FONT, 11), padx=S(8), pady=S(4), justify="left",
                      wraplength=S(280))
        lx.pack()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + S(4)
        tip.update_idletasks()
        x = max(S(4), min(x, tip.winfo_screenwidth() - tip.winfo_width() - S(4)))
        tip.wm_geometry("+%d+%d" % (x, y))
        self._tip_win = tip

    def _hide_tip(self):
        if self._tip_win:
            try:
                self._tip_win.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._tip_win = None

    def _on_press(self, _e=None):
        if not self._enabled:
            return
        self._pressed = True
        self._draw()

    def _on_release(self, _e=None):
        was_pressed, self._pressed = self._pressed, False
        self._draw()
        if was_pressed and self._enabled and self._command:
            self._command()

    # ── 兼容 tk.Button 的常用接口 ─────────────────────────────────
    def config(self, **kw):
        if "text" in kw:
            self._text = kw.pop("text")
        if "state" in kw:
            self._enabled = kw.pop("state") != "disabled"
            try:
                super().configure(cursor="hand2" if self._enabled else "arrow")
            except Exception:  # noqa: BLE001
                pass
        if "command" in kw:
            self._command = kw.pop("command")
        if kw:
            super().configure(**kw)
        self._draw()

    configure = config

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return "normal" if self._enabled else "disabled"
        return super().cget(key)

    def invoke(self):
        if self._enabled and self._command:
            self._command()



def load_tts_choice():
    try:
        with open(TTS_SERVER_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh).get("choice", "xtts")
    except Exception:  # noqa: BLE001
        return "xtts"


def save_tts_choice(choice):
    with open(TTS_SERVER_FILE, "w", encoding="utf-8") as fh:
        json.dump({"choice": choice}, fh, ensure_ascii=False)


# ── 本地服务生命周期 ─────────────────────────────────────────────────
def service_alive(timeout=1.2):
    try:
        r = requests.get(BASE + "/api/status", timeout=timeout)
        # 200 = 就绪；401 = 服务在响应但需要登录（创建过账号后 /api/* 会拦未登录请求）
        # —— 同样说明主服务已经起来，就绪判定要覆盖它，登录态交给 _auth_check 处理。
        return r.status_code in (200, 401)
    except Exception:  # noqa: BLE001
        return False


def _free_ram_mb():
    """Windows 空闲物理内存（MB）；失败返回 None。"""
    try:
        import ctypes

        class _MSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        m = _MSEX()
        m.dwLength = ctypes.sizeof(m)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return int(m.ullAvailPhys / 1024 / 1024)
        return None
    except Exception:  # noqa: BLE001
        return None


def _commit_avail_mb():
    """Windows 剩余提交内存（commit 额度余量，MB）；失败返回 None。

    和物理内存不是一回事。提交上限 = 物理内存 + 页面文件，凡是「预留但未
    真正写入」的内存都算在内。

    Windows 走 WDDM 时，每个用显卡的进程都要为显存留一份等量的系统内存做
    后备（共享显存），CUDA context 一建立就会一次性吃掉好几 GB 提交额度。
    提交额度耗尽时，驱动报出来的却是 `CUDA out of memory`，而 nvidia-smi
    显示显存还剩好几个 GB —— 照着这句报错去"释放显存"完全不对症。

    2026-10-31 一次真实事故：C 盘只剩 0.2GB，页面文件无法扩展，提交上限
    卡在 38GB；19 个 GPU 进程把已提交量顶到 41.5GB，模型加载到 1.5GB 时
    失败，报的正是「显存不足」。
    """
    try:
        import ctypes

        class _MSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        m = _MSEX()
        m.dwLength = ctypes.sizeof(m)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return int(m.ullAvailPageFile / 1024 / 1024)
        return None
    except Exception:  # noqa: BLE001
        return None


def _port_bound(host="127.0.0.1", port=8010, timeout=0.6):
    """端口是否已被占用（说明已有主服务在跑、或仍在加载中），不依赖 HTTP 响应。"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            return s.connect_ex((host, port)) == 0
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        return False


def _classify_server_fail(detail):
    """按日志尾部判断真实失败原因，给出对症的解决办法。

    之前不管真实错误是什么，都固定提示「多半是显存不足」。但 WinError 1114/1455
    是 Windows 加载 CUDA 动态库失败，根因是**系统内存或页面文件不足**，跟显存无关
    ——照着旧提示去关 RAG/XTTS 释放显存完全不对症，只会让人反复重试。
    """
    if not detail:
        return ""
    low = detail.lower()
    if "1114" in detail or "1455" in detail:
        return (
            "\n\n失败原因：Windows 加载 CUDA 动态库失败（WinError 1114 / 1455）。\n"
            "这是**系统内存或页面文件不足**，不是显存不足——关闭 RAG / XTTS 释放显存"
            "对此无效。\n"
            "请关闭占用内存的程序（如 PyCharm、多余的浏览器标签页），留出 4GB 以上"
            "空闲内存，\n或调大 Windows 页面文件后重启桌面版。")
    if "cuda out of memory" in low or ("out of memory" in low and "cuda" in low):
        # 先看提交额度：WDDM 下把提交额度耗尽报出来的也是 CUDA OOM，
        # 但显存还剩好几 GB，按"释放显存"处理只会白折腾。
        commit_mb = _commit_avail_mb()
        if commit_mb is not None and commit_mb < 9000:
            return (
                "\n\n失败原因：**系统提交内存（commit）接近上限，不是显存不足**。\n"
                "显卡可能还剩好几 GB，但 Windows 提交额度用尽后，驱动会把分配失败\n"
                "报成 CUDA out of memory，很容易误判。\n\n"
                "提交上限 = 物理内存 + 页面文件。C 盘空间不足会让页面文件扩不起来，\n"
                "上限就一直卡着。当前剩余提交额度：%d MB（建议 12GB 以上）。\n\n"
                "处理办法（按顺序）：\n"
                "1) 清理 C 盘空间，留出 10GB 以上；\n"
                "2) 把 Windows 页面文件调大到 32–36GB，然后重启；\n"
                "3) 关掉浏览器 / VS Code 等占用显卡的程序再启动桌面版。"
                % commit_mb)
        return (
            "\n\n失败原因：**显存不足**（CUDA out of memory）。\n"
            "请先停止 RAG / XTTS / Fish 等常驻服务释放显存，再重启桌面版。")
    if "address already in use" in low or "10048" in detail or "端口已被占用" in detail:
        return "\n\n失败原因：端口 8010 已被占用，可能已有主服务在运行，无需重复启动。"
    if "no module named" in low or "modulenotfounderror" in low:
        return "\n\n失败原因：缺少 Python 依赖，请检查依赖是否安装完整。"
    return ""


class DataTable(tk.Frame):
    """后台式管理表格：勾选列 + 表头 + 文字/操作列 + 底部分页条。

    Treeview 本身不支持在单元格里放控件，也不支持复选框。这里的三条变通：

    * 勾选列用 ☐ / ☑ 两个字符配 unicode 字体实现，点击表头可整页全选；
    * 「操作」列写成 ``编辑 | 删除`` 这样的纯文本，靠点击横坐标落在哪一段
      来判断点的是哪个（行一列查一次，几千行也不慢）；
    * 分页在组件内部完成：调用方一次性把全量行喂进来，由组件切片，
      避免每个页面各写一套 limit/offset。

    行数据原样保留在 ``self._rows`` 里，勾选项返回的是原始 dict，
    调用方不需要自己维护 id → 行的映射。
    """

    CHECK_ON = "\u2611"          # ☑
    CHECK_OFF = "\u2610"         # ☐
    SEP = "  |  "

    def __init__(self, parent, columns, actions=(), page_size=20, height=16,
                 empty_text="没有符合条件的数据", bg=None, selectable=False,
                 on_select=None):
        """
        columns —— [(key, 标题, 宽度, stretch, anchor)，…]，首列自动作为勾选列。
        actions —— [(标签, 回调(行 dict))，…]，显示在最后的「操作」列。
        selectable —— 允许选中整行（用于"选中后再点上移/下移"这类按钮）。
        """
        super().__init__(parent, bg=bg if bg is not None else C_CARD)
        self.columns = [self._norm_col(c) for c in columns]
        self.actions = list(actions)
        self.page_size = max(1, int(page_size or 20))
        self.empty_text = empty_text
        self._rows = []
        self._checked = set()        # 行的 id() 不足以标识（dict 会重建），用行 key
        self._page = 1
        self._iid = {}               # Treeview item -> 原始行 dict
        self._selectable = bool(selectable)
        self._on_select = on_select

        self._base_widths = {}
        self._min_widths = {}
        # 先装分页条：pack 在空间不够时会先满足先装的控件，分页条装晚了会被挤成 0 高
        self._build_pager()
        self._build_tree(height)
        self.xscroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=self.xscroll.set)
        self.bind("<Configure>", self._on_resize)

    # ── 列宽自适应 ──
    MIN_COL = 44

    def _on_resize(self, event):
        """窗口缩放后重排列宽（去抖：拖动过程中会连发几十个 Configure）。"""
        if getattr(self, "_fit_job", None):
            self.after_cancel(self._fit_job)
        self._fit_job = self.after(80, self._fit_columns)

    def _fit_columns(self):
        """总宽超出可视宽度时按比例压缩列宽。

        Treeview 只会在"还有富余"时拉伸 stretch 列，**超出时它不收缩**，
        结果是右侧几列（显示素材/状态/操作）被推出可视区、分页条也跟着看不见。
        所以这里自己按比例回收：先压可拉伸列，压到底还不够再压固定列。
        """
        self._fit_job = None
        avail = self.tree.winfo_width()
        if avail < S(200):
            return
        base = self._base_widths
        if not base:
            return
        total = sum(base.values())
        if total <= avail:
            widths = dict(base)
        else:
            # 所有列按"自己还剩多少富余"等比让出空间。只压可拉伸列的话，
            # 文字长的那两列会被压到只剩一个字，而窄列照样占着宽位置。
            widths = dict(base)
            gap = total - avail
            floors = self._min_widths
            room = sum(max(0, base[c] - floors.get(c, self.MIN_COL)) for c in base)
            if room > 0:
                take = min(gap, room)
                frac = 0.0
                for col, width in base.items():
                    floor = floors.get(col, self.MIN_COL)
                    share = max(0, width - floor)
                    cut = take * share / room
                    whole = int(cut)
                    frac += cut - whole        # 逐列取整会把零头攒成十几像素，分摊掉
                    if frac >= 1.0:
                        whole += 1
                        frac -= 1.0
                    widths[col] = width - whole
        final = 0
        for col, width in widths.items():
            want = max(self.MIN_COL, int(width))
            if int(float(self.tree.column(col, "width"))) != want:
                self.tree.column(col, width=want)
            final += want
        # 压到底还放不下（窗口被拉窄了）才给横向滚动条，别一上来就横竖两条
        if final > avail + S(4):
            self.xscroll.pack(side="bottom", fill="x")
        else:
            self.xscroll.pack_forget()

    @staticmethod
    def _norm_col(col):
        """(key, 标题, 宽度, stretch, anchor) —— 后三项可省，默认左对齐、参与拉伸。"""
        parts = tuple(col) + (S(120), True, "w")[len(col) - 2:] if len(col) < 5 else tuple(col)
        return parts

    # ── 组装 ──
    def _build_tree(self, height):
        keys = ["#chk"] + [c[0] for c in self.columns]
        self.ACTION_COLUMN = "#0"
        if self.actions:
            keys.append("#act")
            self.ACTION_COLUMN = "#%d" % len(keys)    # Treeview 列号从 "#1" 起
        self.tree = ttk.Treeview(self, columns=keys, show="headings",
                                 height=height,
                                 selectmode=("browse" if self._selectable else "none"))
        self.tree.heading("#chk", text="", anchor="center")
        self.tree.column("#chk", width=S(40), stretch=False, anchor="center")
        self._base_widths["#chk"] = S(40)
        self._min_widths["#chk"] = S(40)
        for key, title, width, stretch, anchor in self.columns:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=S(width), stretch=bool(stretch), anchor=anchor)
            self._base_widths[key] = S(width)
            # 收缩的底线：至少把表头完整显示出来，长文本列再留一点内容宽度
            floor = ui_font(11).measure(title) + S(24) + (S(30) if stretch else 0)
            self._min_widths[key] = max(self.MIN_COL, int(floor))
        if self.actions:
            label = self.SEP.join(a[0] for a in self.actions)
            width = max(S(90), ui_font(11).measure(label) + S(24))
            self.tree.heading("#act", text="操作")
            self.tree.column("#act", width=width, stretch=False, anchor="center")
            self._base_widths["#act"] = width
            self._min_widths["#act"] = width

        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        grid = tk.Frame(self, bg=C_CARD)
        grid.pack(side="top", fill="both", expand=True)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.after_idle(self._fit_columns)

        self.tree.bind("<Button-1>", self._on_click)
        if self._selectable and self._on_select is not None:
            self.tree.bind("<<TreeviewSelect>>",
                           lambda _e: self._on_select(self.selected_row()))

    def selected_row(self):
        """当前选中的原始行（没选中返回 None）。"""
        return self._iid.get(self.tree.focus())

    def _build_pager(self):
        bar = tk.Frame(self, bg=C_CARD)
        bar.pack(side="bottom", fill="x", pady=(S(8), S(0)))
        self.info = tk.Label(bar, text="共 0 条", font=(FONT, 10), bg=C_CARD, fg=C_MUTED2)
        self.info.pack(side="left")
        nav = tk.Frame(bar, bg=C_CARD)
        nav.pack(side="right")
        self.prev_btn = make_btn(nav, "<", lambda: self.goto(self._page - 1),
                                 sm=True, font_size=10, padx=S(12))
        self.prev_btn.pack(side="left")
        self.page_label = tk.Label(nav, text="1", font=(FONT, 10), bg=C_CARD,
                                   fg=C_INK, width=4, anchor="center")
        self.page_label.pack(side="left")
        self.next_btn = make_btn(nav, ">", lambda: self.goto(self._page + 1),
                                 sm=True, font_size=10, padx=S(12))
        self.next_btn.pack(side="left", padx=(S(4), S(0)))

    # ── 数据 ──
    def row_key(self, row):
        """行的稳定标识：优先用 id，其次用第一列内容兜底。"""
        value = row.get("id")
        return str(value) if value else "|".join(
            str(row.get(c[0], "")) for c in self.columns[:2])

    def set_rows(self, rows, reset_page=True):
        self._rows = list(rows or [])
        if reset_page:
            self._page = 1
        self._checked &= {self.row_key(r) for r in self._rows}   # 数据变了就丢掉失效勾选
        self._render()

    def checked_rows(self):
        wanted = set(self._checked)
        return [r for r in self._rows if self.row_key(r) in wanted]

    def goto(self, page):
        last = max(1, -(-len(self._rows) // self.page_size))
        self._page = min(max(1, int(page or 1)), last)
        self._render()

    def _render(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._iid = {}
        page_rows = self._page_rows()
        for row in page_rows:
            key = self.row_key(row)
            values = [self.CHECK_ON if key in self._checked else self.CHECK_OFF]
            for col in self.columns:
                values.append(self._cell_text(row, col))
            if self.actions:
                values.append(self.SEP.join(a[0] for a in self.actions))
            item = self.tree.insert("", "end", values=values, tags=(key,))
            self._iid[item] = row
        if not page_rows:
            values = [""] + [""] * len(self.columns)
            if self.actions:
                values.append("")
            if self.empty_text:
                values[min(1, len(values) - 1)] = self.empty_text
            self.tree.insert("", "end", values=values)

        last = max(1, -(-len(self._rows) // self.page_size))
        self.info.config(text="共 %d 条　%d / %d 页" % (len(self._rows), self._page, last))
        self.page_label.config(text=str(self._page))
        state = lambda ok: "normal" if ok else "disabled"
        self.prev_btn.config(state=state(self._page > 1))
        self.next_btn.config(state=state(self._page < last))

    def _cell_text(self, row, col):
        key, _title, _width, _stretch, _anchor = col
        value = row.get(key)
        if isinstance(value, bool):
            return "是" if value else "否"
        if value is None:
            return "—"
        text = str(value).replace("\n", " ").replace("\r", " ")
        return text or "—"

    # ── 交互 ──
    def _on_click(self, event):
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if column == "#1":                       # 勾选列
            if not row:
                self._toggle_all()
            else:
                target = self._iid.get(row)
                if target is not None:
                    self._toggle_row(target)
            return
        if not row or not self.actions:
            return
        if column != self.ACTION_COLUMN:
            return
        target = self._iid.get(row)
        if target is not None:
            self._run_action(target, event.x)

    def _toggle_row(self, row):
        key = self.row_key(row)
        if key in self._checked:
            self._checked.discard(key)
        else:
            self._checked.add(key)
        self._render()

    def _toggle_all(self):
        page_keys = {self.row_key(r) for r in self._page_rows()}
        if page_keys and page_keys <= self._checked:
            self._checked -= page_keys           # 整页已全选 → 整页取消
        else:
            self._checked |= page_keys
        self._render()

    def _page_rows(self):
        start = (self._page - 1) * self.page_size
        return self._rows[start:start + self.page_size]

    def _run_action(self, row, x):
        """按点击横坐标判断落在「编辑 | 删除」的哪一段。

        Treeview 的单元格里放不了控件，所以操作列是纯文本。命中判定用字宽
        累加：Treeview 的 x 原点是整棵树的左边（不含滚动条），减去前面各列
        宽度之和就是操作列的起点。
        """
        offset = x - self._first_columns_width()
        pos = 0.0
        for idx, (label, callback) in enumerate(self.actions):
            pos += ui_font(11).measure(label)
            if offset <= pos + S(6):
                callback(row)
                return
            pos += ui_font(11).measure(self.SEP)

    def _first_columns_width(self):
        """操作列之前所有列的宽度之和。"""
        total = 0
        for col in ["#chk"] + [c[0] for c in self.columns]:
            total += int(float(self.tree.column(col, "width")))
        return total


class ServiceLauncher(threading.Thread):
    """后台启动原版主服务（app.py 子进程），就绪后回调。"""

    def __init__(self, tts_choice, ready_cb, fail_cb, progress_cb=None):
        super().__init__(daemon=True)
        self.tts_choice = tts_choice
        self.ready_cb = ready_cb
        self.fail_cb = fail_cb
        self.progress_cb = progress_cb
        self.proc = None

    def _need_clone_server(self):
        """是否需要本地声音克隆服务：本地 TTS 预设选了 xtts，或设置里的引擎是 xtts。"""
        return self.tts_choice == "xtts" or _configured_tts_engine() == "xtts"

    def _tail(self, path, n=8):
        try:
            lines = io_lines = None
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            return "".join(lines[-n:]).strip()
        except Exception:  # noqa: BLE001
            return ""

    def run(self):
        # 内存预检：主服务要加载 torch + MuseTalk，空闲内存过少时先提示，
        # 避免反复拉起后因页面文件不足（WinError 1455）崩溃。
        free_mb = _free_ram_mb()
        if free_mb is not None and free_mb < 2500:
            self.fail_cb(
                "系统可用内存仅 %d MB，不足以加载主服务\n"
                "（torch + MuseTalk 模型通常需要 4GB 以上空闲内存）。\n\n"
                "注意：这是**系统内存**不足，不是显存不足，\n"
                "关闭 RAG / XTTS / Fish 释放的是显存，对此没有帮助。\n\n"
                "请关闭占用内存的程序（如 PyCharm、多余浏览器标签页），\n"
                "留出 4GB 以上空闲内存，或调大 Windows 页面文件后重启桌面版。" % free_mb)
            return
        # 物理内存够 ≠ 能装下。WDDM 下建 CUDA context 要一次性吃掉好几 GB
        # 提交额度（为显存留系统内存后备），额度不足时驱动报的是 CUDA OOM，
        # 看起来像显存不足，实际显存还剩好几个 GB。实测余 7GB 仍会失败。
        commit_mb = _commit_avail_mb()
        if commit_mb is not None and commit_mb < 9000:
            self.fail_cb(
                "系统提交内存（commit）仅剩 %d MB，不足以加载主服务。\n"
                "（建 CUDA context + 加载模型约需 10GB 提交额度）\n\n"
                "这不是显存不足——显卡可能还剩好几个 GB。提交上限 =\n"
                "物理内存 + 页面文件，C 盘空间不足会让页面文件扩不起来。\n\n"
                "处理：清理 C 盘空间后把页面文件调到 32–36GB 并重启，\n"
                "或关掉浏览器 / VS Code 等占用显卡的程序再试。" % commit_mb)
            return
        # 主服务已在线（上次残留/手动拉起）时直接复用：重复 Popen 会因端口
        # 占用退出并被误报为「启动失败」，且并发 CUDA 初始化可能触发 1114。
        if service_alive():
            # 复用既有主服务时，本地克隆引擎仍需要克隆服务在线，顺手拉起。
            if self._need_clone_server():
                auto_start_clone_server("xtts")
            self.ready_cb()
            return
        # 选了本地克隆引擎（xtts），或设置里把引擎选成了 xtts 时，先确保声音克隆
        # 服务在跑，否则 XTTS 会因连不上克隆服务而静默失败。克隆服务加载模型要几十
        # 秒，与主服务并行启动；xtts.py 还有惰性重试，服务就绪后参考音色自动生效。
        if self._need_clone_server():
            auto_start_clone_server("xtts")
        preset = TTS_PRESETS.get(self.tts_choice, TTS_PRESETS["xtts"])
        cmd = [PYTHON, "app.py"]
        if preset["server"]:
            cmd += ["--TTS_SERVER", preset["server"]]
        log_dir = os.path.join(ROOT, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "desktop_server.log")

        # 端口已被占用、但服务还没响应 = 上一次的实例仍在加载模型。此时再 Popen 一个，
        # 两个进程抢内存，新的那个很可能在加载 CUDA DLL 时就 1114 崩掉——表现很像
        # "显存不足"，其实是系统内存被前一个实例吃掉了。这里复用它，不再拉第二个。
        reuse = _port_bound()
        if not reuse:
            log_file = open(log_path, "a", encoding="utf-8", errors="replace")
            log_file.write("\n--- desktop_app 启动主服务 ---\n")
            log_file.flush()
            try:
                self.proc = subprocess.Popen(
                    cmd, cwd=ROOT,
                    stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
            except Exception as exc:  # noqa: BLE001
                self.fail_cb("主服务启动失败: %r" % (exc,))
                return
            finally:
                # 句柄已交给子进程，父进程留着只是白占一个 fd（每启动一次漏一个）
                log_file.close()
            if self.progress_cb:
                self.progress_cb("主服务进程已启动，正在加载模型（约 1-2 分钟）…")
        elif self.progress_cb:
            self.progress_cb("检测到已有主服务实例（端口已占用），等待其加载完成…")

        deadline = time.time() + 300
        last_report = 0.0
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                detail = self._tail(log_path)
                self.fail_cb("主服务进程已退出（exit=%s）\n日志：%s\n尾部：%s%s" % (
                    self.proc.returncode, log_path, detail or "(空)",
                    _classify_server_fail(detail)))
                return
            if service_alive():
                self.ready_cb()
                return
            now = time.time()
            if self.progress_cb and now - last_report >= 10:
                last_report = now
                self.progress_cb("正在等待主服务就绪（已 %ds，模型加载中…）" % int(now - deadline + 300))
            time.sleep(2)
        detail = self._tail(log_path)
        self.fail_cb("主服务启动超时（300s）\n日志：%s\n尾部：%s%s" % (
            log_path, detail or "(空)",
            _classify_server_fail(detail)
            or "\n\n可能仍在加载模型，或上一次的实例卡住了；可稍等片刻后重启桌面版。"))

    def stop(self, timeout=10.0):
        """结束主服务：先请求它自己优雅退出，再兜底杀进程。

        直接 `TerminateProcess` 不会跑 `on_shutdown` —— RAG sidecar（主服务拉起的
        独立进程）会变成孤儿继续占端口和内存，PeerConnection 也不走 close。
        先发 `POST /api/system/shutdown`（本机回环放行），它关完 pc、停完 RAG 后
        自己 `loop.stop()`；请求不通（还没开始监听 / 版本旧）才退回硬杀。

        复用了外部实例（`proc is None`）时不动手：那可能是开发者手动起的服务，
        不该被界面连坐。
        """
        if self.proc is None or self.proc.poll() is not None:
            return False
        try:
            requests.post(BASE + "/api/system/shutdown", timeout=3)
        except Exception:  # noqa: BLE001 - 没监听/版本旧都正常，走下面的硬杀
            pass
        else:
            try:
                self.proc.wait(timeout=timeout)
                return True
            except subprocess.TimeoutExpired:
                pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            try:
                self.proc.kill()
                self.proc.wait(timeout=3)
                return True
            except Exception:  # noqa: BLE001
                return False
        except Exception:  # noqa: BLE001
            return False


# ── WebRTC 数字人客户端（aiortc，原生无浏览器）──────────────────────
import asyncio

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.exceptions import InvalidStateError


def describe_conn_error(exc):
    """把底层异常翻成人能看懂的中文，界面里不再出现 Python repr。"""
    text = str(exc)
    lowered = text.lower()
    if "closed" in lowered or isinstance(exc, InvalidStateError):
        return "连接已关闭，请重新连接"
    if isinstance(exc, requests.exceptions.Timeout) or "timeout" in lowered:
        return "主服务响应超时，请稍后重试"
    if isinstance(exc, requests.exceptions.ConnectionError) or "connection" in lowered:
        return "连不上主服务，请确认主服务已启动"
    if "ice" in lowered and ("fail" in lowered or "gather" in lowered):
        return "媒体通道建立失败，请检查网络后重试"
    return text.strip() or type(exc).__name__


class AvatarClient:
    """aiortc 接收端：连主服务 /offer，收音视频轨并回调。

    连接的**代际**（_gen）是这里最关键的一条：每次 connect/disconnect 都会 +1，
    在途的握手每一段 await 之后都要核对代际和自己的 pc 是否还是当前那个。
    否则「连上再点一次连接」「连上马上断开」这类操作会让旧的 answer 落到已经
    close 掉的 pc 上，抛 InvalidStateError: Cannot handle answer in signaling
    state "closed"——那不是真的故障，只是迟到的旧结果。
    """

    STATE_TEXT = {
        "new": "连接中…", "connecting": "连接中…", "connected": "已连接",
        "disconnected": "连接已断开", "failed": "连接失败：网络或服务异常",
        "closed": "连接已关闭",
    }

    def __init__(self, base, session_cb, status_cb, frame_cb, audio_cb, sess=None):
        self.base = base
        self.sess = sess
        self.session_cb = session_cb
        self.status_cb = status_cb
        self.frame_cb = frame_cb
        self.audio_cb = audio_cb
        self.loop = None
        self.pc = None
        self.sessionid = ""
        self._thread = None
        self._ready = threading.Event()
        self._gen = 0

    def start(self):
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def connect(self):
        if self.loop is None or not self._ready.is_set():
            self.start()
            self._ready.wait(5)
        self._gen += 1                     # 让上一轮没跑完的连接作废
        asyncio.run_coroutine_threadsafe(self._do_connect(self._gen), self.loop)

    def disconnect(self):
        self._gen += 1                     # 让在途的 answer 处理直接失效
        # 给用户即时反馈；不能等到 ICE 超时后才把界面从“已连接”改掉。
        self.status_cb("正在断开…")
        if self.loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._do_disconnect(), self.loop)
            except Exception:  # noqa: BLE001 - 事件循环正在退出
                self.sessionid = ""
                self.session_cb("")
                self.status_cb("已断开")
        else:
            self.sessionid = ""
            self.session_cb("")
            self.status_cb("已断开")

    def shutdown(self, timeout=3.0):
        """退出时调用：断开连接并把事件循环停掉。

        `disconnect()` 只是把关闭协程丢进循环，循环本身还在 `run_forever`。
        进程退出时 daemon 线程会被强杀，但那时 `pc.close()` 可能还没跑完，
        UDP 端口和 aiortc 的内部线程要等 OS 慢慢回收——展项机上表现为
        「关掉再立刻打开」时第一个连接必然失败。
        """
        future = None
        try:
            self._gen += 1
            if self.loop is not None:
                future = asyncio.run_coroutine_threadsafe(self._do_disconnect(), self.loop)
        except Exception:  # noqa: BLE001
            future = None
        if future is not None:
            try:
                future.result(timeout)     # 等 pc.close() 真的跑完
            except Exception:  # noqa: BLE001 - 超时/已关闭都不该挡住退出
                pass
        loop, thread = self.loop, self._thread
        self.loop, self._thread = None, None
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:  # noqa: BLE001
                pass
        if thread is not None and thread.is_alive():
            try:
                thread.join(2.0)
            except Exception:  # noqa: BLE001
                pass
        if loop is not None:
            try:
                loop.close()
            except Exception:  # noqa: BLE001 - 还有未完成的回调时 close 会抛，无所谓
                pass

    # ── 内部 ───────────────────────────────────────────────────────
    def _alive(self, gen, pc):
        return gen == self._gen and self.pc is pc and pc.signalingState != "closed"

    def _post_offer(self, sdp):
        """阻塞式 HTTP 放线程池：事件循环不被 30s 请求卡住，
        期间用户点「断开 / 重新连接」还能正常排队执行。"""
        _sess = self.sess if self.sess is not None else requests
        return _sess.post(
            self.base + "/offer",
            json={"sdp": sdp, "type": "offer", "sessionid": ""},
            timeout=30,
        )

    async def _do_connect(self, gen):
        pc = None
        try:
            if self.pc is not None:
                await self._close_pc()
            if gen != self._gen:
                return
            pc = RTCPeerConnection()
            self.pc = pc

            @pc.on("connectionstatechange")
            async def _state():
                if self.pc is not pc:
                    return
                try:
                    self.status_cb(self.STATE_TEXT.get(pc.connectionState, pc.connectionState))
                except Exception:  # noqa: BLE001
                    pass

            @pc.on("track")
            async def _track(track):
                if track.kind == "audio":
                    self._handle_audio(track)
                elif track.kind == "video":
                    self._handle_video(track)

            # 声明接收轨道
            for kind in ("audio", "video"):
                tr = pc.addTransceiver(kind)
                try:
                    tr.direction = "recvonly"
                except Exception:  # noqa: BLE001
                    pass

            offer = await pc.createOffer()
            if not self._alive(gen, pc):
                return
            await pc.setLocalDescription(offer)
            resp = await asyncio.get_running_loop().run_in_executor(
                None, self._post_offer, pc.localDescription.sdp)
            if not self._alive(gen, pc):
                return                       # 期间被断开 / 被新的连接取代
            try:
                data = resp.json()
            except ValueError:               # 500 时服务端返回的是纯文本
                data = {}
            if resp.status_code != 200 or data.get("code", 0) != 0:
                msg = str(data.get("msg") or "").strip()
                if not msg:
                    msg = ("主服务建会话失败（HTTP %d），请查看服务日志，或换一个形象重试"
                           % resp.status_code)
                self.status_cb("连接失败：" + msg)
                return
            answer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
            await pc.setRemoteDescription(answer)
            if not self._alive(gen, pc):
                return
            self.sessionid = str(data.get("sessionid", ""))
            self.session_cb(self.sessionid)
            self.status_cb("已连接")      # 会话号由界面单独展示，避免长 UUID 撑爆状态行
        except InvalidStateError:
            # 握手途中连接被关掉：语义上等于「已取消」，不该报成异常
            pass
        except Exception as exc:  # noqa: BLE001
            if gen == self._gen:
                self.status_cb("连接失败：" + describe_conn_error(exc))

    async def _do_disconnect(self):
        await self._close_pc()
        self.status_cb("已断开")

    async def _close_pc(self):
        pc, self.pc = self.pc, None
        # 先废弃会话号，界面与轮询立即停止使用这条已关闭的连接；随后再等待
        # aiortc 收尾，避免 pc.close() 的异步过程让“断开”看起来没有反应。
        self.sessionid = ""
        self.session_cb("")
        if pc is not None:
            try:
                await pc.close()
            except Exception:  # noqa: BLE001
                pass

    def _handle_audio(self, track):
        self._pump(track, audio=True)

    def _handle_video(self, track):
        self._pump(track, audio=False)

    def _pump(self, track, audio=False):
        """主动从轨道拉帧。

        aiortc 的 MediaStreamTrack 没有 `frame` 事件（只有 `ended`），
        挂 `@track.on("frame")` 回调永远收不到东西——必须自己 await recv()。
        以前桌面端就卡在这里：连上了、服务端也在推，客户端一帧不显示。
        """
        def _task():
            loop = self.loop or asyncio.get_event_loop()
            loop.create_task(self._pump_loop(track, audio))

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(self._pump_loop(track, audio))
        else:                                  # 不在事件循环线程里（理论上不会走到）
            self.loop.call_soon_threadsafe(_task)

    async def _pump_loop(self, track, audio):
        while True:
            try:
                frame = await track.recv()
            except Exception:              # noqa: BLE001 - 连接断开/轨道结束
                break
            if frame is None:
                break
            try:
                if audio:
                    # 客户端收到的是 Opus 解码帧（s16/stereo/48k/packed），
                    # to_ndarray() 给的是交织的一行 (1, samples*channels)，
                    # 声道数必须取自帧元数据——形状本身区分不出单/双声道。
                    arr, channels = prepare_audio_frame(frame)
                    self.audio_cb(arr, frame.sample_rate, channels)
                else:
                    # 必须取 rgb24：后面是 Image.fromarray 按 RGB 解释的。
                    # 取 bgr24 会让红蓝互换，人脸发蓝紫、西装变土黄。
                    self.frame_cb(frame.to_ndarray(format="rgb24"))
            except Exception as exc:  # noqa: BLE001
                logger.error("[WebRTC] 处理收到的帧失败: %r", exc)


# ── 音频播放（sounddevice）───────────────────────────────────────────
import numpy as np
import sounddevice as sd
from utils.logger import logger
from utils.audio_playback import reshape_for_sounddevice, prepare_audio_frame


class AudioPlayer(threading.Thread):
    def __init__(self, device=None):
        super().__init__(daemon=True)
        self._q = queue.Queue(maxsize=400)
        self._stop = threading.Event()
        self._stream = None
        # 输出设备：None = 系统默认。真机多屏/HDMI 时系统默认常是显示器而非
        # 音箱，必须允许显式指定，否则"画面在动就是没声音"。
        self._device = device

    def _open_kwargs(self, samplerate, channels):
        kwargs = {"samplerate": samplerate, "channels": channels, "dtype": "int16"}
        if self._device not in (None, ""):
            kwargs["device"] = self._device
        return kwargs

    def run(self):
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if len(item) == 3:
                    arr, sr, channels = item
                else:                              # 兼容旧的 (arr, sr)
                    arr, sr = item
                    arr, channels = reshape_for_sounddevice(arr)
                if self._stream is None or self._stream.samplerate != sr or self._stream.channels != channels:
                    self._close_stream()
                    self._stream = sd.OutputStream(**self._open_kwargs(sr, channels))
                    self._stream.start()
                    logger.info("[AudioPlayer] 音频输出已打开: samplerate=%s channels=%s device=%s",
                                sr, channels, self._device if self._device else "(系统默认)")
                self._stream.write(arr)
            except Exception as exc:  # noqa: BLE001
                # 绝不能静默：设备打不开（无输出设备/被独占）或写入失败，
                # 都表现为"画面在动却没声音"，必须留痕以便排查。
                logger.error("[AudioPlayer] 音频播放失败（已静音）: %r", exc)

    def play(self, arr, sr, channels=None):
        if channels is None:
            arr, channels = reshape_for_sounddevice(arr)
        try:
            self._q.put_nowait((arr, sr, channels))
        except queue.Full:
            try:
                self._q.get_nowait()
                self._q.put_nowait((arr, sr, channels))
            except Exception:  # noqa: BLE001
                pass

    def play_test_tone(self, samplerate=48000):
        """播放 0.4 秒 880Hz 提示音，用于验证本机扬声器通路。

        真机排查"没声音"时，先确认这台机器能不能出声，再谈数字人。
        """
        try:
            n = int(samplerate * 0.4)
            t = np.arange(n, dtype=np.float32) / float(samplerate)
            wave = (0.25 * np.sin(2 * np.pi * 880.0 * t) * 32767.0).astype(np.int16)
            self.play(wave.reshape(-1, 1), samplerate, 1)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("[AudioPlayer] 测试提示音播放失败: %r", exc)
            return False

    def clear_pending(self):
        """丢弃尚未播放的音频，但保留播放线程，供下次连接继续使用。"""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    def _close_stream(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None

    def stop(self):
        self._stop.set()
        self._close_stream()


# ── 主窗口 ────────────────────────────────────────────────────────────
class DesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        # 字号按真实 DPI 换算（点 → 像素），字形才不会被系统二次拉伸
        try:
            self.tk.call("tk", "scaling", UI_SCALE * 96.0 / 72.0)
        except Exception:  # noqa: BLE001
            pass
        self.title("知课 KnowTalking - 桌面版")
        self.geometry("%dx%d" % (S(1360), S(860)))
        self.minsize(S(1024), S(700))
        # 窗口底色必须跟内容一致：最大化 / 拖动边缘时，Tk 会先把新露出来的区域
        # 刷成窗口底色、再逐个重排子控件；底色不设就是黑的，看上去像"控件变黑了"。
        self.configure(bg=C_BG)
        init_style(self)
        self._resize_job = None
        self.bind("<Configure>", self._on_root_configure)

        self.sess = requests.Session()
        self.tts_choice = load_tts_choice()
        self.launcher = None
        # 感应检测子进程的句柄。必须留着：它是个独立进程，只靠 enabled 开关自检
        # 退出，用户开着感应直接关窗口时没人通知它 —— 摄像头会一直被占着。
        self._sensor_proc = None
        self._closing = False
        self.sessionid = ""
        self.avatar = AvatarClient(
            BASE,
            session_cb=self._on_session,
            status_cb=self._on_avatar_status,
            frame_cb=self._on_frame,
            audio_cb=self._on_audio,
            sess=self.sess,
        )
        # 音频输出设备来自设置（真机多屏/HDMI 时系统默认常是显示器而非音箱）
        audio_device, self._audio_test_tone = _configured_audio_settings()
        self.audio_player = AudioPlayer(device=audio_device)
        self.audio_player.start()

        # UI 绘制慢时只保留最新两帧；无界队列会积压旧画面并持续抬高内存。
        self.frame_q = queue.Queue(maxsize=2)
        self.ui_q = queue.Queue()
        self._photos = []
        self._last_frame_painted_at = 0.0

        self._build_root_ui()
        # 事件泵必须从启动那一刻就跑：ready/login_ok/fail 等事件都经它分发，
        # 若只在 _show_main 里启动会形成循环依赖（进主界面要等事件泵处理登录事件）。
        self.after(120, self._drain_ui_q)
        self._start_frame_painter()
        self._bootstrap()

    # ── 窗口尺寸变化后的统一重绘 ──────────────────────────────────
    def _on_root_configure(self, event=None):
        """最大化 / 还原时子控件不会跟着立刻重绘，深色舞台和自绘控件在这个
        间隙里就是一块黑。尺寸稳定后统一催一次重排 + 通知各页重画。"""
        if event is not None and event.widget is not self:
            return
        if self._resize_job:
            try:
                self.after_cancel(self._resize_job)
            except Exception:  # noqa: BLE001
                pass
        self._resize_job = self.after(80, self._after_resize)

    def _after_resize(self):
        self._resize_job = None
        try:
            self.update_idletasks()
        except Exception:  # noqa: BLE001
            pass
        main = getattr(self, "_main", None)
        for page in set((getattr(main, "pages", None) or {}).values()):
            hook = getattr(page, "on_resize", None)
            if callable(hook):
                try:
                    hook()
                except Exception:  # noqa: BLE001
                    pass

    # ── 启动流程 ──────────────────────────────────────────────────
    def _bootstrap(self):
        self.status_var.set("正在检测主服务…")
        threading.Thread(target=self._bootstrap_worker, daemon=True).start()

    def _bootstrap_worker(self):
        if not service_alive():
            self.ui_q.put(("busy", "主服务未运行，正在启动（首次加载模型约 1-2 分钟）…"))
            self.launcher = ServiceLauncher(
                self.tts_choice,
                ready_cb=lambda: self.ui_q.put(("ready", True)),
                fail_cb=lambda m: self.ui_q.put(("fail", m)),
                progress_cb=lambda m: self.ui_q.put(("busy", m)),
            )
            self.launcher.start()
        else:
            self.ui_q.put(("ready", True))

    # ── UI 构建 ───────────────────────────────────────────────────
    def _build_root_ui(self):
        self.container = tk.Frame(self, bg=C_BG)
        self.container.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(value="就绪")
        # 启动阶段先给个可见提示，避免窗口空着一片白让人以为卡死。
        # 登录页/主界面接管时会把 container 的子控件整体销毁，自然盖掉这里。
        self._show_connecting("正在检测主服务…")

    def _show_connecting(self, text="正在启动主服务…"):
        for w in self.container.winfo_children():
            w.destroy()
        self._connect_label = None
        wrap = tk.Frame(self.container, bg=C_BG)
        wrap.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(wrap, text="知课 KnowTalking", font=(FONT, 18, "bold"),
                 bg=C_BG, fg=C_INK).pack()
        self._connect_label = tk.Label(wrap, text=text, font=(FONT, 12),
                                       bg=C_BG, fg=C_MUTED)
        self._connect_label.pack(pady=(S(10), S(0)))

    def _show_login(self):
        for w in self.container.winfo_children():
            w.destroy()
        LoginPage(self.container, self).pack(fill="both", expand=True)

    def _show_main(self):
        for w in self.container.winfo_children():
            w.destroy()
        MainPage(self.container, self).pack(fill="both", expand=True)

    # ── 线程事件泵 ────────────────────────────────────────────────
    def _drain_ui_q(self):
        try:
            while True:
                item = self.ui_q.get_nowait()
                kind = item[0]
                if kind == "busy":
                    self.status_var.set(item[1])
                    lbl = getattr(self, "_connect_label", None)
                    if lbl is not None and lbl.winfo_exists():
                        lbl.config(text=item[1])
                elif kind == "ready":
                    self.status_var.set("主服务就绪")
                    self._auth_check()
                elif kind == "fail":
                    messagebox.showerror("启动失败", item[1])
                    self.status_var.set(item[1])
                elif kind == "login":
                    self._show_login_with_info()
                elif kind == "login_ok":
                    self._show_main()
                elif kind == "avatar_status":
                    self.status_var.set(item[1])
                    main = getattr(self, "_main", None)
                    if main is not None:
                        main.on_avatar_status(item[1])
                elif kind == "session":
                    self.sessionid = item[1]
                    main = getattr(self, "_main", None)
                    if main is not None:
                        main.on_session_changed(item[1])
                    # 会话建立 = 音频通路已通。播一声提示音，让用户能立刻
                    # 分辨"这台机器根本没声音" vs "数字人没出声"。
                    if getattr(self, "_audio_test_tone", False):
                        self.audio_player.play_test_tone()
                elif kind == "frame":
                    self._paint_frame(item[1])
                elif kind == "voices":
                    main = getattr(self, "_main", None)
                    if main is not None:
                        # 音色只在「形象与设置」里维护，开播页不再需要这份列表
                        main.settings_page.handle_voices(item[1])
                elif kind == "stt_done":
                    main = getattr(self, "_main", None)
                    if main is not None:
                        main.avatar_page.handle_stt_done(item[1])
                elif kind == "stt_err":
                    main = getattr(self, "_main", None)
                    if main is not None:
                        main.avatar_page.handle_stt_err(item[1])
        except queue.Empty:
            pass
        self.after(120, self._drain_ui_q)

    def _auth_check(self):
        try:
            r = self.sess.get(BASE + "/api/auth/session", timeout=5)
            info = r.json().get("data", {})
        except Exception:  # noqa: BLE001
            info = {}
        if info.get("authenticated"):
            self.ui_q.put(("login_ok", None))
        else:
            # 未登录：先把登录态存下，再直接弹出登录页。
            # 注意必须在这里（或事件泵里）触发，不能只依赖启动时的
            # after(200) 一次性调用——那时主服务多半还没就绪，
            # _pending_login 还是 None，登录页就永远不会出现，表现为白屏。
            self._pending_login = info
            self._show_login_with_info()

    # 登录页就绪回调（LoginPage 构建前需要账号状态）
    def _show_login_with_info(self):
        if getattr(self, "_pending_login", None) is not None:
            self._show_login()
            login_page = self.container.winfo_children()[0]
            login_page.apply_session_info(self._pending_login)
            self._pending_login = None

    # ── 头像回调 ──────────────────────────────────────────────────
    def _on_session(self, sid):
        self.ui_q.put(("session", sid))

    def _on_avatar_status(self, text):
        self.ui_q.put(("avatar_status", text))

    def _on_frame(self, arr):
        """在 aiortc 的线程里被调用：只入队，不碰 Tk（Tk 不是线程安全的）。"""
        try:
            self.frame_q.put_nowait(arr)
        except queue.Full:
            try:
                self.frame_q.get_nowait()
                self.frame_q.put_nowait(arr)
            except Exception:  # noqa: BLE001
                pass

    def _start_frame_painter(self):
        """主线程限速显示最新帧，优先保证交互而不是堆积低延迟视频帧。"""
        def tick():
            arr = None
            try:
                while True:
                    arr = self.frame_q.get_nowait()
            except queue.Empty:
                pass
            now = time.monotonic()
            if arr is not None and now - self._last_frame_painted_at >= FRAME_PAINT_INTERVAL:
                self._last_frame_painted_at = now
                self._paint_frame(arr)
            self.after(20, tick)

        self.after(20, tick)

    def _paint_frame(self, arr):
        main = getattr(self, "_main", None)
        if main is None:
            return
        try:
            main.show_video_frame(arr)
        except Exception:  # noqa: BLE001
            pass

    def _on_audio(self, arr, sr, channels=None):
        self.audio_player.play(arr, sr, channels)

    # ── 感应子进程 ────────────────────────────────────────────────
    def _kill_sensor(self, timeout=3.0):
        """结束检测子进程、释放摄像头。返回是否确实收掉了一个活进程。

        三重兜底，因为摄像头是独占资源，漏一次就得重启整台机器才恢复：
        1. 结束 `Popen` 句柄（本次会话拉起的那个，能等它自己走完 release）；
        2. 按 `proximity.pid` 兜底（上一次会话残留、或句柄丢了的情况）；
        3. 删掉 pid 文件——留着会让下次启动按旧 pid 误杀别的进程。
        """
        killed = False
        proc = self._sensor_proc
        self._sensor_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
                killed = True
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                    killed = True
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass

        pid_file = os.path.join(ROOT, "data", "kiosk", "proximity.pid")
        pid = 0
        if os.path.isfile(pid_file):
            try:
                with open(pid_file, "r", encoding="utf-8") as fh:
                    pid = int((fh.read() or "0").strip())
            except Exception:  # noqa: BLE001
                pid = 0
        if pid and pid != os.getpid():
            try:
                os.kill(pid, 9)          # Windows 上等价于 TerminateProcess
                killed = True
            except Exception:  # noqa: BLE001 - 进程可能已经自己退了
                pass
        try:
            if os.path.isfile(pid_file):
                os.remove(pid_file)
        except OSError:
            pass
        if killed:
            logger.info("[感应] 已结束检测进程，摄像头已释放")
        return killed

    def _shutdown_sensor(self):
        """退出流程：先把开关写死成关闭，再收进程。

        顺序不能反：脚本每 2 秒重读一次配置，先关开关能让它走正常路径
        （`finally: det.release()`）；直接杀进程虽然 OS 也会回收句柄，
        但 `proximity.pid` 会残留，下次启动可能按旧 pid 误杀别的进程。
        """
        try:
            from utils import kiosk_store
            kiosk_store.set_proximity({"enabled": False})
        except Exception:  # noqa: BLE001 - 主服务可能已停，配置写不进去也不影响收进程
            pass
        return self._kill_sensor(timeout=3.0)

    # ── 退出 ──────────────────────────────────────────────────────
    def on_close(self):
        """关窗口 = 把所有外部资源都收掉，不留后台残兵。

        以前只断了 WebRTC 和音频，感应子进程、requests 连接池、aiortc 事件循环
        都留着——最要命的是感应子进程，它继续占着摄像头，下次启动就报
        「打不开摄像头（被其它程序占用）」，且没有任何提示指向真正的原因。
        """
        if self._closing:
            return                          # 重入保护：连点两次关闭按钮
        self._closing = True

        # 收资源要几秒（等子进程真的退出），给个明确的反馈，别让用户以为卡死了
        try:
            self.configure(cursor="watch")
            self.status_var.set("正在退出：关闭摄像头与主服务…")
            self.update_idletasks()
        except Exception:  # noqa: BLE001
            pass

        # 1. 摄像头优先——它是最难恢复的资源
        try:
            self._shutdown_sensor()
        except Exception:  # noqa: BLE001
            pass
        # 2. 媒体通道与音频输出
        try:
            self.avatar.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.audio_player.stop()
        except Exception:  # noqa: BLE001
            pass
        # 3. 主服务子进程（复用的外部实例不动，避免误杀开发者手动起的服务）
        if self.launcher is not None:
            try:
                self.launcher.stop()
            except Exception:  # noqa: BLE001
                pass
        # 4. 自动拉起的声音克隆服务（占 50000 端口 + 一份 XTTS 显存）
        try:
            stop_clone_server()
        except Exception:  # noqa: BLE001
            pass
        # 5. 挂起的定时器（尺寸重排等）与 HTTP 连接池
        for attr in ("_resize_job", "_redraw_job"):
            job = getattr(self, attr, None)
            if job:
                try:
                    self.after_cancel(job)
                except Exception:  # noqa: BLE001
                    pass
                setattr(self, attr, None)
        try:
            self.sess.close()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    def restart_ui(self):
        """只重启界面进程（换主题用）。

        刻意不复用 on_close：它会把主服务一起停掉，而换主题只是换皮，
        没必要让 MuseTalk 重新加载一遍模型（冷启动要几十秒）。
        新进程起来时 8010 还在监听，会直接连上原有服务。
        """
        try:
            self.avatar.disconnect()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.audio_player.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            subprocess.Popen([sys.executable, os.path.join(ROOT, "desktop_app.py")],
                             cwd=ROOT)
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


# ── 登录页（左右分栏：左产品介绍 / 右登录表单）────────────────────
class LoginPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=C_BG)
        self.app = app
        self.info = {}

        # ── 左栏：产品介绍 ──
        left = tk.Frame(self, bg=C_BG, width=S(700))
        left.pack(side="left", fill="both", expand=True)
        left.pack_propagate(False)

        wm = tk.Frame(left, bg=C_BG)
        wm.pack(anchor="w", padx=S(46), pady=(S(36), S(0)))
        tk.Label(wm, text="知课", font=(FONT, 19, "bold"), bg=C_BG, fg=C_INK).pack(side="left")
        tk.Label(wm, text="KnowTalking", font=(FONT, 19, "bold"), bg=C_BG, fg=C_GREEN).pack(side="left")
        tk.Label(wm, text="桌面版", font=(FONT, 11), bg=C_BG, fg=C_MUTED,
                 highlightbackground=C_LINE, highlightthickness=1, padx=S(6), pady=S(2)).pack(side="left", padx=(S(10), S(0)))

        tk.Label(left, text="给课件开口说话", font=(FONT, 30, "bold"), bg=C_BG, fg=C_INK
                 ).pack(anchor="w", padx=S(46), pady=(S(58), S(0)))
        tk.Label(left, text="数字人照着讲稿讲，课件翻到哪页就讲到哪页；\n讲到重点停下来提问，答完接着讲。",
                 font=(FONT, 13), bg=C_BG, fg=C_MUTED2, justify="left",
                 wraplength=S(520)).pack(anchor="w", padx=S(46), pady=(S(16), S(0)))

        feats = tk.Frame(left, bg=C_BG)
        feats.pack(anchor="w", padx=S(46), pady=(S(40), S(0)))
        for ftext in ("形象、音色、动作一套配好", "课件与知识库各自成库", "一次开播，全程自动"):
            row = tk.Frame(feats, bg=C_BG)
            row.pack(anchor="w", pady=S(7))
            badge = tk.Label(row, text="✓", font=(FONT, 11, "bold"), bg=C_CARD, fg=C_GREEN,
                             width=3, height=1, highlightbackground=C_LINE, highlightthickness=1)
            badge.pack(side="left")
            tk.Label(row, text=ftext, font=(FONT, 14), bg=C_BG, fg=C_INK).pack(side="left", padx=(S(12), S(0)))

        bottom = tk.Frame(left, bg=C_BG)
        bottom.pack(side="bottom", fill="x", padx=S(46), pady=S(26))
        tk.Label(bottom, text="本地部署 · 数据不出机器", font=(FONT, 12), bg=C_BG,
                 fg=C_MUTED).pack(side="left")
        tk.Label(bottom, textvariable=self.app.status_var, font=(FONT, 11), bg=C_BG,
                 fg=C_MUTED, anchor="e").pack(side="right")

        # ── 右栏：登录表单 ──
        right = Card(self, radius=0)      # 贴满右半屏，圆角会贴到窗口边上，这里用直角面板
        right.pack(side="left", fill="both", expand=True)

        card = tk.Frame(right, bg=C_CARD)
        self.card_shell = card
        card.pack(pady=(S(96), S(0)))
        tk.Label(card, text="登录", font=(FONT, 20, "bold"), bg=C_CARD, fg=C_INK).pack(anchor="w")
        self.state_label = tk.StringVar(value="正在检查账号状态…")
        tk.Label(card, textvariable=self.state_label, font=(FONT, 12), bg=C_CARD,
                 fg=C_MUTED).pack(anchor="w", pady=(S(6), S(0)))

        self.user_var = tk.StringVar()
        self.pass_var = tk.StringVar()
        self.name_var = tk.StringVar()
        for label, var, show, ph in (("用户名", self.user_var, None, ""), ("密码", self.pass_var, "*", "")):
            tk.Label(card, text=label, font=(FONT, 12), bg=C_CARD, fg=C_MUTED2).pack(anchor="w", pady=(S(14), S(4)))
            tk.Entry(card, textvariable=var, show=show, font=(FONT, 13), width=30,
                     bg=C_CARD, fg=C_INK, relief="flat", bd=0,
                     highlightthickness=1, highlightbackground=C_LINE,
                     highlightcolor=C_INK).pack(fill="x")
        self.extra_row = None
        self.remember_var = tk.BooleanVar(value=False)
        self.remember_file = os.path.join(ROOT, "data", "settings", "login_remember.json")

        rem = tk.Frame(card, bg=C_CARD)
        rem.pack(fill="x", pady=(S(10), S(0)))
        make_switch(rem, "记住密码（仅保存在本机）", self.remember_var,
                    bg=C_CARD).pack(side="left")

        self.btn = make_btn(card, "登录", self._submit, primary=True, padx=S(20), pady=S(11), font_size=14)
        self.btn.pack(fill="x", pady=(S(24), S(0)))

        div = tk.Frame(card, bg=C_CARD)
        div.pack(fill="x", pady=(S(22), S(12)))
        tk.Frame(div, bg=C_LINE_SOFT, height=S(1)).pack(side="left", fill="x", expand=True)
        tk.Label(div, text="或", font=(FONT, 11), bg=C_CARD, fg=C_MUTED, padx=S(10)).pack(side="left")
        tk.Frame(div, bg=C_LINE_SOFT, height=S(1)).pack(side="left", fill="x", expand=True)

        wcrow = tk.Frame(card, bg=C_CARD)
        wcrow.pack(fill="x")
        wechat = tk.Label(wcrow, text="微信扫码登录", font=(FONT, 12), bg=C_CARD_ALT, fg=C_MUTED2,
                          cursor="hand2", highlightbackground=C_LINE, highlightthickness=1,
                          padx=S(8), pady=S(8))
        wechat.pack(side="left", fill="x", expand=True)
        wechat.bind("<Button-1>", lambda e: self.info_label.config(
            text="扫码能力尚未接通，请先用账号密码登录。"))
        tk.Label(wcrow, text="即将开放", font=(FONT, 10), bg=C_CARD, fg=C_GREEN,
                 highlightbackground=C_LINE_STR, highlightthickness=1, padx=S(4), pady=S(1)
                 ).pack(side="right", padx=(S(6), S(0)))

        forgot = tk.Label(card, text="忘记密码？", font=(FONT, 12), bg=C_CARD, fg=C_GREEN,
                          cursor="hand2")
        forgot.pack(anchor="center", pady=(S(14), S(0)))
        forgot.bind("<Button-1>", lambda e: self.info_label.config(
            text="密码重置尚未开通，请联系管理员。"))

        self.info_label = tk.Label(self, text="", font=(FONT, 12), bg=C_BG, fg=C_DANGER)
        self.info_label.pack(side="bottom", pady=(S(0), S(12)))
        self._load_remembered()

    def _load_remembered(self):
        try:
            with open(self.remember_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("username"):
                self.user_var.set(data["username"])
                self.pass_var.set(data.get("password", ""))
                self.remember_var.set(True)
        except Exception:  # noqa: BLE001
            pass

    def _save_remembered(self):
        try:
            os.makedirs(os.path.dirname(self.remember_file), exist_ok=True)
            with open(self.remember_file, "w", encoding="utf-8") as fh:
                json.dump({"username": self.user_var.get().strip(),
                           "password": self.pass_var.get()}, fh)
        except Exception:  # noqa: BLE001
            pass

    def _clear_remembered(self):
        try:
            if os.path.isfile(self.remember_file):
                os.remove(self.remember_file)
        except Exception:  # noqa: BLE001
            pass

    def apply_session_info(self, info):
        self.info = info
        if not info.get("has_users", True):
            self.state_label.set("系统还没有账号，请创建管理员账号")
            self.btn.config(text="创建账号")
            if self.extra_row is None:
                tk.Label(self.card_shell, text="昵称", font=(FONT, 12), bg=C_CARD, fg=C_MUTED2
                         ).pack(anchor="w", pady=(S(14), S(4)))
                tk.Entry(self.card_shell, textvariable=self.name_var, font=(FONT, 13), width=30,
                         bg=C_CARD, fg=C_INK, relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=C_LINE,
                         highlightcolor=C_INK).pack(fill="x")
                self.extra_row = True
        else:
            self.state_label.set("请输入账号密码登录")
            self.btn.config(text="登录")

    def _submit(self):
        user = self.user_var.get().strip()
        pwd = self.pass_var.get()
        if not user or not pwd:
            self.info_label.config(text="请填写用户名和密码")
            return
        try:
            if not self.info.get("has_users", True):
                r = self.app.sess.post(BASE + "/api/auth/setup",
                                       json={"username": user, "password": pwd,
                                             "display_name": self.name_var.get().strip() or user},
                                       timeout=8)
            else:
                r = self.app.sess.post(BASE + "/api/auth/login",
                                       json={"username": user, "password": pwd}, timeout=8)
            data = r.json()
            if data.get("code", -1) != 0:
                self.info_label.config(text=str(data.get("msg", "登录失败")))
                return
            if self.remember_var.get():
                self._save_remembered()
            else:
                self._clear_remembered()
            self.app.ui_q.put(("login_ok", None))
        except Exception as exc:  # noqa: BLE001
            self.info_label.config(text="网络错误: %r" % (exc,))


class GestureMakeDialog(tk.Toplevel):
    """制作新动作：选预设 → 用形象首帧生成 5 秒视频 → 切成动作帧。

    后端（utils/gesture_make.py + /api/gestures/make）早就实现了，缺的是入口。
    """

    def __init__(self, parent, app, avatar_id, on_done=None):
        super().__init__(parent)
        self.app = app
        self.avatar_id = avatar_id
        self._on_done = on_done
        self._presets = []
        self._job_id = ""
        self.withdraw()
        self.title("制作新动作 · %s" % avatar_id)
        self.configure(bg=C_BG)
        self.resizable(False, False)
        self._build()
        self._center(parent)
        self.transient(parent)
        self.deiconify()
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())
        self._load_presets()

    def _build(self):
        body = tk.Frame(self, bg=C_BG)
        body.pack(fill="both", expand=True, padx=S(22), pady=S(18))
        tk.Label(body, text="制作新动作", font=(FONT, 16, "bold"), bg=C_BG, fg=C_INK
                 ).pack(anchor="w")
        tk.Label(body, text="用该形象的首帧生成 5 秒动作视频再切帧；一次约 2–4 分钟，按次计费。",
                 font=(FONT, 10), bg=C_BG, fg=C_MUTED, anchor="w").pack(fill="x",
                                                                       pady=(S(2), S(10)))

        self.listbox = tk.Listbox(body, height=8, width=52, font=(FONT, 11), relief="flat",
                                  bd=0, bg=C_CARD, fg=C_INK, highlightthickness=1,
                                  highlightbackground=C_LINE, selectbackground=C_NAV_ON,
                                  selectforeground=C_INK)
        self.listbox.pack(fill="x")
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._on_pick())

        row = tk.Frame(body, bg=C_BG)
        row.pack(fill="x", pady=(S(10), S(0)))
        tk.Label(row, text="动作名称", font=(FONT, 11), bg=C_BG, fg=C_MUTED2,
                 width=8, anchor="w").pack(side="left")
        self.name_var = tk.StringVar()
        make_entry(row, self.name_var, width=24).pack(side="left")

        self.status = tk.Label(body, text="", font=(FONT, 10), bg=C_BG, fg=C_MUTED,
                               anchor="w", justify="left", wraplength=S(420))
        self.status.pack(fill="x", pady=(S(10), S(0)))

        bar = tk.Frame(body, bg=C_BG)
        bar.pack(fill="x", pady=(S(12), S(0)))
        self.go_btn = make_btn(bar, "开始生成", self._submit, primary=True, sm=True)
        self.go_btn.pack(side="right")
        make_btn(bar, "关闭", self.destroy, sm=True).pack(side="right", padx=(S(0), S(8)))

    def _center(self, parent):
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        self.geometry("%dx%d+%d+%d" % (w, h, px + (pw - w) // 2, py + (ph - h) // 3))

    def _load_presets(self):
        def work():
            try:
                r = self.app.sess.get(BASE + "/api/gestures/presets", timeout=10)
                data = r.json().get("data", {}) or {}
                presets = data.get("presets") or []
            except Exception as exc:  # noqa: BLE001
                presets, data = [], {}
                self._post(self._fail, "读取预设失败（主服务未运行？）：%r" % (exc,))
                return
            self._post(self._fill, presets, data)

        threading.Thread(target=work, daemon=True).start()

    def _post(self, fn, *args):
        try:
            self.after(0, lambda: fn(*args))
        except Exception:  # noqa: BLE001
            pass

    def _fail(self, msg):
        if self.winfo_exists():
            self.status.config(text=msg, fg=C_DANGER)

    def _fill(self, presets, meta):
        self._presets = presets or []
        self.listbox.delete(0, "end")
        for item in self._presets:
            self.listbox.insert("end", "%s  ·  %s" % (item.get("label", ""),
                                                      item.get("hint", "")))
        if self._presets:
            self.listbox.selection_set(0)
            self._on_pick()
        if meta and meta.get("configured") is False:
            self.status.config(text="还没配置 MiniMax Key：右上角「设置 → API 密钥」填好再来。",
                               fg=C_WARN)
        elif not self._presets:
            self.status.config(text="没有可用预设。", fg=C_DANGER)

    def _on_pick(self):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self._presets):
            return
        item = self._presets[sel[0]]
        self.name_var.set(item.get("label", ""))

    def _submit(self):
        if not self._presets:
            return
        sel = self.listbox.curselection()
        item = self._presets[sel[0]] if sel else self._presets[0]
        payload = {"avatar_id": self.avatar_id, "preset_id": item.get("id"),
                   "name": self.name_var.get().strip() or item.get("label")}
        self.go_btn.config(state="disabled")
        self.status.config(text="提交中…", fg=C_MUTED)

        def work():
            try:
                r = self.app.sess.post(BASE + "/api/gestures/make", json=payload, timeout=20)
                data = r.json()
                if data.get("code", -1) != 0:
                    self._post(self._fail, data.get("msg") or "提交失败")
                    self._post(lambda: self.go_btn.config(state="normal"))
                    return
                job = (data.get("data") or {}).get("job") or {}
                self._post(self._start_poll, job.get("id", ""))
            except Exception as exc:  # noqa: BLE001
                self._post(self._fail, "提交失败：%r" % (exc,))
                self._post(lambda: self.go_btn.config(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    def _start_poll(self, job_id):
        self._job_id = job_id
        if not job_id:
            self._fail("没有拿到作业 id")
            return
        self._tick()

    def _tick(self):
        if not self.winfo_exists() or not self._job_id:
            return

        def work():
            try:
                r = self.app.sess.get(BASE + "/api/gestures/make/%s" % self._job_id, timeout=8)
                job = (r.json().get("data") or {}).get("job") or {}
            except Exception:  # noqa: BLE001
                job = {}
            self._post(self._apply_job, job)

        threading.Thread(target=work, daemon=True).start()
        self.after(2500, self._tick)

    def _apply_job(self, job):
        if not job or not self.winfo_exists():
            return
        stage = str(job.get("stage") or "")
        text = "%s %s%%" % (job.get("stage_text") or stage, job.get("progress") or 0)
        done = stage in ("done", "failed")
        color = C_GREEN if stage == "done" else (C_DANGER if stage == "failed" else C_MUTED2)
        self.status.config(text=text if not job.get("error") else
                           ("%s：%s" % (text, job.get("error"))), fg=color)
        if done:
            self._job_id = ""
            self.go_btn.config(state="normal")
            if stage == "done" and callable(self._on_done):
                try:
                    self._on_done()
                except Exception:  # noqa: BLE001
                    pass


class AppSettingsDialog(tk.Toplevel):
    """应用设置：对外 API 密钥 / 主题外观 / 关于。

    与侧栏「形象与设置」分工不同：那页配的是数字人与语音本身，这里是软件层面的
    凭证与外观，一处改完影响所有功能，所以放在右上角、独立窗口，不混进业务页。
    """

    TABS = [("api", "API 密钥"), ("theme", "外观主题"), ("about", "关于")]
    # (分组, 字段, 标题, 提示, 是否密文)
    API_FIELDS = [
        ("llm", "api_key", "对话模型 Key", "DeepSeek 或任何 OpenAI 兼容端点", True),
        ("llm", "base_url", "接口地址", "形如 https://api.deepseek.com/v1", False),
        ("llm", "model", "模型名", "例如 deepseek-v4-pro", False),
        ("minimax", "api_key", "MiniMax Key", "动作制作（H3 图生视频）用，按次计费", True),
        ("minimax", "video_model", "视频模型", "默认 MiniMax-H3", False),
    ]

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.withdraw()
        self.title("应用设置")
        self.configure(bg=C_BG)
        self.resizable(False, False)
        self._vars = {}
        self._tab_frames = {}
        self._theme_var = tk.StringVar(value=_current_theme_id())
        self._status = {}
        self._build()
        self._center(parent)
        # transient 必须在 deiconify 之前：对已隐藏的窗口设 transient，
        # Tk 会一直不把它映射出来（表现为窗口"打不开"）。
        self.transient(parent)
        self.deiconify()
        self.lift()
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())
        self._load_settings()

    # ── 布局 ──
    def _build(self):
        head = tk.Frame(self, bg=C_BG)
        head.pack(fill="x", padx=S(24), pady=(S(18), S(0)))
        tk.Label(head, text="应用设置", font=(FONT, 18, "bold"), bg=C_BG, fg=C_INK
                 ).pack(side="left")
        tk.Label(head, text="对外服务的密钥与界面外观", font=(FONT, 11), bg=C_BG, fg=C_MUTED
                 ).pack(side="left", padx=(S(10), S(0)), pady=(S(6), S(0)))
        make_btn(head, "关闭", self.destroy, sm=True, font_size=11).pack(side="right")

        strip = tk.Frame(self, bg=C_BG)
        strip.pack(fill="x", padx=S(24), pady=(S(14), S(6)))
        self._tab_var = tk.StringVar(value="api")
        self.tabs = SegmentedControl(strip, self.TABS, variable=self._tab_var,
                                     command=self._show_tab, min_seg=S(104), height=S(32))
        self.tabs.pack(side="left")

        self.body = tk.Frame(self, bg=C_BG, width=S(720), height=S(520))
        self.body.pack(fill="both", expand=True, padx=S(24), pady=(S(4), S(18)))
        self.body.pack_propagate(False)

        self._build_api_tab()
        self._build_theme_tab()
        self._build_about_tab()
        self._show_tab("api")

    def _show_tab(self, key):
        for name, frame in self._tab_frames.items():
            if name == key:
                frame.place(relx=0, rely=0, relwidth=1, relheight=1)
                frame.tkraise()
            else:
                frame.place_forget()
        if hasattr(self, "tabs"):
            self.tabs.var.set(key)

    def _card(self, parent, title, hint=""):
        card = Card(parent)
        card.pack(fill="x", pady=(S(0), S(10)))
        tk.Label(card, text=title, font=(FONT, 13, "bold"), bg=C_CARD, fg=C_INK
                 ).pack(anchor="w", padx=S(18), pady=(S(14), S(2)))
        if hint:
            tk.Label(card, text=hint, font=(FONT, 10), bg=C_CARD, fg=C_MUTED,
                     justify="left", wraplength=S(620),
                     anchor="w").pack(fill="x", padx=S(18), pady=(0, S(6)))
        return card

    def _field_row(self, card, group, name, label, hint, secret):
        row = tk.Frame(card, bg=C_CARD)
        row.pack(fill="x", padx=S(18), pady=(S(7), S(0)))
        tk.Label(row, text=label, font=(FONT, 11), bg=C_CARD, fg=C_MUTED2,
                 width=12, anchor="w").pack(side="left")
        var = tk.StringVar()
        self._vars[(group, name)] = var
        make_entry(row, var, width=34, show="*" if secret else None
                   ).pack(side="left", fill="x", expand=True)
        # 提示语放输入框下面一行：挨在右边会被输入框挤掉，长文本还会顶出卡片
        if hint:
            tk.Label(card, text=hint, font=(FONT, 10), bg=C_CARD, fg=C_MUTED,
                     anchor="w").pack(fill="x", padx=(S(150), S(18)), pady=(S(1), S(0)))

    def _build_api_tab(self):
        wrap = ScrollFrame(self.body, bg=C_BG)
        wrap.pack(fill="both", expand=True)
        host = wrap.inner

        card = self._card(host, "对话模型（LLM）",
                          "数字人回答问题、生成讲解都走这里。密钥只存在本机，不会上传。")
        for group, name, label, hint, secret in self.API_FIELDS[:3]:
            self._field_row(card, group, name, label, hint, secret)
        bar = tk.Frame(card, bg=C_CARD)
        bar.pack(fill="x", padx=S(18), pady=(S(8), S(14)))
        make_btn(bar, "保存", lambda: self._save_group("llm"), sm=True, primary=True
                 ).pack(side="left")
        make_btn(bar, "测试连通性", self._test_llm, sm=True).pack(side="left", padx=S(8))
        self._status["llm"] = tk.Label(bar, text="", font=(FONT, 10), bg=C_CARD, fg=C_MUTED)
        self._status["llm"].pack(side="left", padx=S(12))

        card2 = self._card(host, "MiniMax（动作制作）",
                           "形象动作靠它做图生视频：用形象首帧生成 5 秒片段再切成动作帧，按次计费。")
        for group, name, label, hint, secret in self.API_FIELDS[3:]:
            self._field_row(card2, group, name, label, hint, secret)
        bar2 = tk.Frame(card2, bg=C_CARD)
        bar2.pack(fill="x", padx=S(18), pady=(S(8), S(14)))
        make_btn(bar2, "保存", lambda: self._save_group("minimax"), sm=True, primary=True
                 ).pack(side="left")
        self._status["minimax"] = tk.Label(bar2, text="", font=(FONT, 10), bg=C_CARD,
                                           fg=C_MUTED)
        self._status["minimax"].pack(side="left", padx=S(12))

        card3 = self._card(host, "火山引擎（云端声音复刻）",
                           "豆包的 AppID / Token 属于部署级凭证，仍写在项目根目录的 .env 里"
                           "（DOUBAO_APPID / DOUBAO_TOKEN），本窗口不接管，避免误改后连不上。")
        tk.Frame(card3, bg=C_CARD, height=S(8)).pack()
        self._tab_frames["api"] = wrap

    def _build_theme_tab(self):
        host = ScrollFrame(self.body, bg=C_BG)
        host.pack(fill="both", expand=True)
        inner = host.inner
        card = self._card(inner, "界面主题",
                          "主题在启动时套用，切换后需重启界面才生效；重启不会中断正在运行的主服务。")
        grid = tk.Frame(card, bg=C_CARD)
        grid.pack(fill="x", padx=S(18), pady=(S(4), S(12)))
        for idx, (tid, meta) in enumerate(_theme_items()):
            row, col = divmod(idx, 2)
            self._theme_card(grid, tid, meta, row, col)
        bar = tk.Frame(card, bg=C_CARD)
        bar.pack(fill="x", padx=S(18), pady=(S(0), S(14)))
        make_btn(bar, "应用并重启界面", self._apply_theme, primary=True
                 ).pack(side="left")
        self._status["theme"] = tk.Label(bar, text="", font=(FONT, 10), bg=C_CARD, fg=C_MUTED)
        self._status["theme"].pack(side="left", padx=S(12))

        note = self._card(inner, "重启界面会发生什么",
                          "只重启桌面界面进程，不动主服务。")
        for line in ("界面会用新配色重建，正在播放的数字人会断开并自动重连；",
                     "主服务（含 MuseTalk 模型）不重启，不需要重新等模型加载；",
                     "主题记在 data/settings/theme.json，下次启动沿用。"):
            tk.Label(note, text="·  " + line, font=(FONT, 10), bg=C_CARD, fg=C_MUTED2,
                     anchor="w", justify="left").pack(fill="x", padx=S(18), pady=S(2))
        tk.Frame(note, bg=C_CARD, height=S(10)).pack()
        self._tab_frames["theme"] = host

    def _theme_card(self, parent, tid, meta, row, col):
        w, h = S(320), S(100)
        holder = tk.Frame(parent, bg=C_CARD, width=w, height=h)
        holder.grid(row=row, column=col, padx=(S(0), S(10)), pady=(S(0), S(10)))
        holder.grid_propagate(False)
        canvas = tk.Canvas(holder, width=w, height=h, bg=C_CARD, highlightthickness=0,
                           bd=0, cursor="hand2")
        canvas.pack(fill="both", expand=True)
        canvas.bind("<Button-1>", lambda _e, t=tid: self._pick_theme(t))
        self._theme_cards = getattr(self, "_theme_cards", {})
        self._theme_cards[tid] = {"canvas": canvas, "w": w, "h": h, "meta": meta}

    def _paint_themes(self):
        cur = self._theme_var.get()
        for tid, item in getattr(self, "_theme_cards", {}).items():
            canvas, w, h, meta = item["canvas"], item["w"], item["h"], item["meta"]
            on = (tid == cur)
            canvas.delete("all")
            photo = rounded_photo(w, h, S(10), C_NAV_ON if on else C_CARD,
                                  C_GREEN if on else C_LINE, 2 if on else 1)
            canvas._photo = photo
            canvas.create_image(w / 2, h / 2, image=photo)
            colors = meta["colors"]
            x = S(16)
            for key in ("bg", "card", "accent", "ink"):
                canvas.create_rectangle(x, S(16), x + S(26), S(42),
                                        fill=colors[key] or C_CARD, outline=C_LINE)
                x += S(30)
            # 名称与说明分两行、留够行距：原来 58/72 相差 14 设计像素，
            # 在 1.5 倍缩放下中文字形高度就吃掉了这点距离，两行会叠在一起。
            canvas.create_text(S(16), S(60), text=meta["label"], anchor="w",
                               font=(FONT, 12, "bold"), fill=C_GREEN if on else C_INK)
            canvas.create_text(S(16), S(84), text=meta["hint"], anchor="w",
                               font=(FONT, 10), fill=C_MUTED2 if on else C_MUTED)

    def _pick_theme(self, tid):
        self._theme_var.set(tid)
        self._paint_themes()
        self._status["theme"].config(text="已选「%s」，点右侧按钮生效" % _theme_meta(tid)["label"],
                                      fg=C_GREEN)

    def _build_about_tab(self):
        host = tk.Frame(self.body, bg=C_BG)
        card = self._card(host, "关于",
                          "知课 KnowTalking 本地桌面版：界面在本机，数字人与语音由本机主服务提供。")
        info = [
            ("主服务地址", BASE),
            ("配置文件", os.path.join(ROOT, "data", "settings", "settings.json")),
            ("主题配置", os.path.join(ROOT, "data", "settings", "theme.json")),
            ("当前主题", _theme_meta(_current_theme_id())["label"]),
        ]
        for k, v in info:
            row = tk.Frame(card, bg=C_CARD)
            row.pack(fill="x", padx=S(18), pady=S(3))
            tk.Label(row, text=k, font=(FONT, 11), bg=C_CARD, fg=C_MUTED2, width=10,
                     anchor="w").pack(side="left")
            tk.Label(row, text=v, font=(FONT, 11), bg=C_CARD, fg=C_INK, anchor="w",
                     wraplength=S(520), justify="left").pack(side="left")
        tk.Frame(card, bg=C_CARD, height=S(12)).pack()
        self._tab_frames["about"] = host

    # ── 行为 ──
    def _center(self, parent):
        self.update_idletasks()
        w, h = S(760), S(640)
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
        except Exception:  # noqa: BLE001
            px = py = 0
            pw, ph = w, h
        self.geometry("%dx%d+%d+%d" % (w, h, px + max(0, (pw - w) // 2),
                                       py + max(0, (ph - h) // 3)))

    def _load_settings(self):
        self._paint_themes()

        def work():
            data = {}
            try:
                r = self.app.sess.get(BASE + "/api/settings", timeout=8)
                data = r.json().get("data", {}) or {}
            except Exception as exc:  # noqa: BLE001
                self._after_load({}, str(exc))
                return
            self._after_load(data, "")

        threading.Thread(target=work, daemon=True).start()

    def _after_load(self, data, error):
        def apply():
            if error:
                for key, lbl in self._status.items():
                    if lbl.winfo_exists():
                        lbl.config(text="读取设置失败：主服务未运行？", fg=C_DANGER)
                return
            for (group, name), var in self._vars.items():
                node = data.get(group) or {}
                cell = node.get(name)
                var.set(str(setting_value(cell, "")))
        try:
            self.app.after(0, apply)
        except Exception:  # noqa: BLE001
            pass

    def _save_group(self, group):
        payload = {group: {}}
        for (g, name), var in self._vars.items():
            if g != group:
                continue
            payload[group][name] = var.get().strip()
        label = self._status.get(group)
        try:
            r = self.app.sess.put(BASE + "/api/settings", json=payload, timeout=10)
            data = r.json()
            ok = data.get("code", -1) == 0
            if label:
                label.config(text="已保存" if ok else ("保存失败：%s" % data.get("msg")),
                             fg=C_GREEN if ok else C_DANGER)
        except Exception as exc:  # noqa: BLE001
            if label:
                label.config(text="保存失败：%r" % (exc,), fg=C_DANGER)

    def _test_llm(self):
        label = self._status.get("llm")
        if label:
            label.config(text="测试中…", fg=C_MUTED)
        payload = {
            "api_key": self._vars[("llm", "api_key")].get().strip(),
            "base_url": self._vars[("llm", "base_url")].get().strip(),
            "model": self._vars[("llm", "model")].get().strip(),
        }

        def work():
            try:
                r = self.app.sess.post(BASE + "/api/settings/llm/test", json=payload,
                                       timeout=40)
                data = r.json()
                ok = data.get("code", -1) == 0
                msg = data.get("msg") or ("连通正常" if ok else "连不通")
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, repr(exc)

            def apply():
                if label and label.winfo_exists():
                    label.config(text=msg, fg=C_GREEN if ok else C_DANGER)

            try:
                self.app.after(0, apply)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=work, daemon=True).start()

    def _apply_theme(self):
        tid = self._theme_var.get()
        try:
            _save_theme(tid)
        except Exception as exc:  # noqa: BLE001
            self._status["theme"].config(text="写入失败：%r" % (exc,), fg=C_DANGER)
            return
        self._status["theme"].config(
            text="已保存，正在重启界面…", fg=C_GREEN)
        try:
            self.update_idletasks()
        except Exception:  # noqa: BLE001
            pass
        restart = getattr(self.app, "restart_ui", None)
        if callable(restart):
            restart()
        else:
            self.destroy()


class StudioPage(tk.Frame):
    """形象与设置：形象库 / 形象生成 / 语音与音频 合成一页，页内分栏切换。

    这三块原来分散在「实时讲解」和「资源与管理」两组里，同一件事要跑两个
    入口，还跟顶栏的「设置」按钮重名。现在收成一个页面，子页用 embedded=True
    隐藏各自的大标题，只留本页标题 + 顶部标签。
    """

    TABS = [("avatar", "形象库"), ("gen", "形象生成"), ("voice", "语音与音频")]
    # 页 key → 分栏 key。material 是老名字（顶栏「设置」按钮和旧链接还在用），
    # 它对的是语音与音频那一栏；avatar / gen 正好同名，直接透传。
    KEY_TO_TAB = {"avatar": "avatar", "gen": "gen", "material": "voice"}

    def __init__(self, parent, app):
        super().__init__(parent, bg=C_BG)
        self.app = app
        self._tab_btns = {}
        self.cur_tab = None

        head = tk.Frame(self, bg=C_BG)
        head.pack(fill="x", padx=S(24), pady=(S(18), S(0)))
        tk.Label(head, text="形象与设置", font=(FONT, 20, "bold"), bg=C_BG, fg=C_INK
                 ).pack(side="left")
        tk.Label(head, text="形象、音色、音频输出与声音克隆", font=(FONT, 12),
                 bg=C_BG, fg=C_MUTED2).pack(side="left", padx=(S(12), S(0)),
                                             pady=(S(7), S(0)))

        strip = tk.Frame(self, bg=C_BG)
        strip.pack(fill="x", padx=S(24), pady=(S(12), S(2)))
        for key, label in self.TABS:
            btn = tk.Label(strip, text=label, font=(FONT, 12, "bold"), bg=C_BG,
                           fg=C_MUTED2, padx=S(16), pady=S(7), cursor="hand2")
            btn.pack(side="left", padx=(S(0), S(6)))
            btn.bind("<Button-1>", lambda e, k=key: self.show_tab(k))
            self._tab_btns[key] = btn

        self.body = tk.Frame(self, bg=C_BG)
        self.body.pack(fill="both", expand=True)
        self.avatar_tab = AvatarMgrPage(self.body, app, embedded=True)
        self.gen_tab = GenAvatarPage(self.body, app, embedded=True)
        self.voice_tab = SettingsPage(self.body, app, embedded=True)
        self._tabs = {"avatar": self.avatar_tab, "gen": self.gen_tab,
                      "voice": self.voice_tab}
        for page in self._tabs.values():
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
            page._host = self          # 让 MainPage._show_page 先抬本页再切分栏
        self.show_tab("avatar")

    def show_tab(self, key):
        page = self._tabs.get(self.KEY_TO_TAB.get(key, key))
        if page is None:
            return
        key = self.KEY_TO_TAB.get(key, key)
        self.cur_tab = key
        page.tkraise()
        for k, btn in self._tab_btns.items():
            on = (k == key)
            btn.config(bg=C_NAV_ON if on else C_BG,
                       fg=C_GREEN if on else C_MUTED2)


# ── 主工作台（顶栏 + 左侧导航 + 页面区）────────────────────────────
class MainPage(tk.Frame):
    # 两种使用模式。线上讲课和线下展项是两拨人、两套动线，混在一个列表里
    # 会让老师在一堆展项配置里找讲稿。
    MODE_TABS = [("speech", "演讲模式"), ("expo", "展览模式")]
    # 「开播」是每天的第一个动作，两个模式都必须排在最前面——之前它挂在
    # 「上课 / 启动」分组末尾，运营要在导航里先找分组才点得到。
    MODE_NAV = {
        "speech": [
            ("上课", [("broadcast", "开播"), ("bg", "背景设置")]),
            ("备课", [("script", "讲稿"), ("quiz", "提问"),
                     ("course", "课件"), ("kb", "知识库管理")]),
        ],
        "expo": [
            ("启动", [("broadcast", "开播")]),
            ("全息舱", [("bg", "背景设置"), ("wake", "唤醒与打断"), ("menu", "菜单库"),
                       ("idle", "待机与迎宾"), ("qalog", "问答记录")]),
        ],
    }
    # 两种模式都要用的入口
    COMMON_NAV = ("通用", [("studio", "形象与设置"), ("admin", "管理后台"),
                          ("status", "系统状态")])

    def __init__(self, parent, app):
        super().__init__(parent, bg=C_BG)
        self.app = app
        app._main = self
        self.sessionid = ""
        self.script_text = ""
        self.cur_page = None
        self.tts_engine = "-"
        self.tts_voice = ""
        self.avatar_id = "-"
        self.mode = "speech"                    # 默认线上讲课
        self._mode_last = {"speech": "broadcast", "expo": "broadcast"}
        self._status_poll_busy = False

        self._build_topbar()
        body = tk.Frame(self, bg=C_BG)
        body.pack(fill="both", expand=True)
        self._build_sidebar(body)
        self.page_area = tk.Frame(body, bg=C_BG)
        self.page_area.pack(side="left", fill="both", expand=True)

        # 页面实例（属性名保持与线程事件泵的调用一致）
        self.avatar_page = AvatarPage(self.page_area, app)
        self.status_page = StatusPage(self.page_area, app)
        self.script_page = ScriptPage(self.page_area, app)
        self.quiz_page = QuizPage(self.page_area, app)
        self.course_page = CoursePage(self.page_area, app)
        self.kb_page = KnowledgePage(self.page_area, app)
        self.admin_page = AdminPage(self.page_area, app)
        self.wake_page = WakePage(self.page_area, app)
        self.menu_page = MenuPage(self.page_area, app)
        self.idle_page = IdlePage(self.page_area, app)
        self.qalog_page = QaLogPage(self.page_area, app)
        self.bg_page = BackgroundPage(self.page_area, app)
        # 形象库 / 形象生成 / 语音与音频 收进一页，属性名沿用原来的，
        # 免得线程事件泵里 app._main.settings_page.xxx 这类调用全部失效。
        self.studio_page = StudioPage(self.page_area, app)
        self.avatar_mgr = self.studio_page.avatar_tab
        self.gen_page = self.studio_page.gen_tab
        self.settings_page = self.studio_page.voice_tab
        self.pages = {
            "broadcast": self.avatar_page, "script": self.script_page,
            "quiz": self.quiz_page, "course": self.course_page,
            "kb": self.kb_page, "admin": self.admin_page,
            "status": self.status_page,
            "studio": self.studio_page,
            "avatar": self.avatar_mgr, "gen": self.gen_page,
            "material": self.settings_page,
            "wake": self.wake_page, "menu": self.menu_page,
            "idle": self.idle_page, "qalog": self.qalog_page,
            "bg": self.bg_page,
        }
        # 只铺顶层页；studio 的三个子页已经由 StudioPage 在内部铺好了，
        # 再在这里 place 一次会把它们拽到 page_area 层，和宿主页错位。
        for p in {v for v in self.pages.values() if getattr(v, "_host", None) is None}:
            p.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._show_page("broadcast")
        self.app.after(2000, self._poll_status)

    # ── 顶栏 ──
    def _build_topbar(self):
        bar = tk.Frame(self, bg=C_BG)
        bar.pack(fill="x", side="top")
        tk.Label(bar, text="知课", font=(FONT, 15, "bold"), bg=C_BG, fg=C_INK
                 ).pack(side="left", padx=(S(20), S(0)), pady=S(13))
        tk.Label(bar, text="KnowTalking", font=(FONT, 15, "bold"), bg=C_BG, fg=C_GREEN
                 ).pack(side="left", padx=(S(4), S(0)))
        tk.Label(bar, textvariable=self.app.status_var, font=(FONT, 11), bg=C_BG, fg=C_MUTED
                 ).pack(side="left", padx=(S(22), S(0)))

        right = tk.Frame(bar, bg=C_BG)
        right.pack(side="right", padx=(S(0), S(18)))
        make_btn(right, "设置", self._open_app_settings, sm=True, font_size=11
                 ).pack(side="right")
        self.sid_label = tk.Label(right, text="SID:-", font=("Consolas", 11), bg=C_BG, fg=C_MUTED2)
        self.sid_label.pack(side="right", padx=(S(0), S(14)))
        chip = tk.Frame(right, bg=C_CARD_ALT, padx=S(10), pady=S(3))
        chip.pack(side="right", padx=(S(0), S(14)))
        self.conn_dot = tk.Label(chip, text="●", font=(FONT, 9), bg=C_CARD_ALT, fg=C_DOT_OFF)
        self.conn_dot.pack(side="left")
        self.conn_label = tk.Label(chip, text="未连接", font=(FONT, 11), bg=C_CARD_ALT, fg=C_MUTED2)
        self.conn_label.pack(side="left", padx=(S(5), S(0)))
        tk.Frame(bar, bg=C_LINE_SOFT, height=1).pack(side="bottom", fill="x")

    def _open_app_settings(self):
        AppSettingsDialog(self.winfo_toplevel(), self.app)

    # ── 左侧导航 ──
    # 导航项整体画在一块 Canvas 上：圆角选中底 + 左侧指示条 + 悬停态。
    # 用 Frame+Label 拼的话，选中底色只能是硬邦邦的方角，跟圆角卡片不搭。
    def _build_sidebar(self, body):
        side = Card(body, width=S(212))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.nav_btns = {}
        self._nav_hover_key = None
        # 两种模式切换后导航项数量会变，仍套一层滚动容器兜住最小窗口
        self.nav_scroll = ScrollFrame(side, bg=C_BG)
        self.nav_scroll.pack(fill="both", expand=True)
        self._build_mode_switch(self.nav_scroll.inner)
        self.nav_holder = tk.Frame(self.nav_scroll.inner, bg=_widget_bg(self.nav_scroll.inner))
        self.nav_holder.pack(fill="x")
        self._render_nav()

        # 底部一行说明：告诉用户这是本地桌面版，跟云端平台不是一回事
        foot_bg = _widget_bg(side)
        tk.Frame(side, bg=C_LINE_SOFT, height=1).pack(side="bottom", fill="x",
                                                      padx=S(14), pady=(S(0), S(10)))
        foot = tk.Frame(side, bg=foot_bg)
        foot.pack(side="bottom", fill="x", padx=S(16), pady=(S(0), S(10)))
        tk.Label(foot, text="本地桌面版", font=(FONT, 10),
                 bg=foot_bg, fg=C_MUTED).pack(anchor="w")

    # ── 模式切换 / 导航渲染 ──
    def _build_mode_switch(self, parent):
        bg = _widget_bg(parent)
        box = tk.Frame(parent, bg=C_CARD_ALT)
        box.pack(fill="x", padx=S(10), pady=(S(9), S(2)))
        self._mode_btns = {}
        for key, label in self.MODE_TABS:
            btn = tk.Label(box, text=label, font=(FONT, 11, "bold"), bg=C_CARD_ALT,
                           fg=C_MUTED2, pady=S(7), cursor="hand2")
            btn.pack(side="left", fill="x", expand=True)
            btn.bind("<Button-1>", lambda e, k=key: self.set_mode(k))
            self._mode_btns[key] = btn
        self._paint_mode()

    def _paint_mode(self):
        for key, btn in self._mode_btns.items():
            on = (key == self.mode)
            btn.config(bg=C_GREEN if on else C_CARD_ALT,
                       fg=C_ON_ACCENT if on else C_MUTED2)

    def set_mode(self, mode):
        if mode == self.mode:
            return
        # 记住各模式上次停的页，切回来不用重新找
        if self.cur_page in self.pages:
            self._mode_last[self.mode] = self.cur_page
        self.mode = mode
        self._paint_mode()
        self._render_nav()
        self._show_page(self._mode_last.get(mode, "broadcast"))

    def _render_nav(self):
        for child in self.nav_holder.winfo_children():
            child.destroy()
        self.nav_btns.clear()
        self._nav_hover_key = None
        for title, items in self.MODE_NAV[self.mode]:
            self._nav_group(self.nav_holder, title, items)
        title, items = self.COMMON_NAV
        self._nav_group(self.nav_holder, title, items)
        for key in self.nav_btns:
            self._draw_nav(key)
        # 刚建出来的 Canvas 还没有尺寸，`_draw_nav` 会因为 w<8 直接返回；
        # 而重建内容不会触发外层 Canvas 的 <Configure>，于是整条导航停在空白。
        # 等一轮布局后补一次：顺手把滚动窗口的宽度同步成容器宽度。
        self.after(30, self._refresh_nav)

    def _refresh_nav(self):
        try:
            # 必须先把积压的几何请求算完：新 Canvas 的尺寸、滚动窗口的宽度都在
            # idle 队列里，不等这一次，`_draw_nav` 拿到的还是 1x1。
            self.update_idletasks()
        except Exception:  # noqa: BLE001 - 窗口已销毁
            return
        try:
            canvas = self.nav_scroll.canvas
            canvas.itemconfig(self.nav_scroll._win, width=canvas.winfo_width())
        except Exception:  # noqa: BLE001
            return
        for key in list(self.nav_btns):
            self._draw_nav(key)

    def _nav_group(self, parent, title, items):
        bg = _widget_bg(parent)
        tk.Label(parent, text=title, font=(FONT, 10, "bold"), bg=bg, fg=C_MUTED
                 ).pack(anchor="w", padx=S(20), pady=(S(9), S(3)))
        for item in items:
            key, label = item[0], item[1]
            none_tag = item[2] if len(item) > 2 else False
            self._nav_item(parent, key, label, none_tag)

    def _nav_item(self, parent, key, label, none_tag):
        bg = _widget_bg(parent)
        h = S(34)
        holder = tk.Frame(parent, bg=bg, height=h)
        holder.pack(fill="x", padx=S(10), pady=S(1))
        holder.pack_propagate(False)
        canvas = tk.Canvas(holder, bg=bg, highlightthickness=0, bd=0,
                           height=h, cursor="hand2")
        canvas.pack(fill="both", expand=True)
        self.nav_btns[key] = (canvas, label, none_tag)
        canvas.bind("<Configure>", lambda e, k=key: self._draw_nav(k))
        canvas.bind("<Button-1>", lambda e, k=key: self._show_page(k))
        canvas.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
        canvas.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))

    def _nav_hover(self, key, on):
        self._nav_hover_key = key if on else None
        self._draw_nav(key)

    def _draw_nav(self, key):
        entry = self.nav_btns.get(key)
        if entry is None:
            return
        canvas, label, none_tag = entry
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w < 8 or h < 8:
            return
        on = (self.cur_page == key)
        hover = (self._nav_hover_key == key)
        canvas.delete("all")
        if on or hover:
            fill = C_NAV_ON if on else C_HOVER
            photo = rounded_photo(w, h, S(10), fill, None)
            canvas.create_image(w / 2, h / 2, image=photo)
            canvas._nav_photo = photo          # 防被 GC 回收后空白
        if on:
            bar_h = max(S(14), int(h * 0.44))
            canvas.create_rectangle(S(7), (h - bar_h) / 2, S(7) + S(3), (h + bar_h) / 2,
                                    fill=C_GREEN, outline="")
        canvas.create_text(S(20), h / 2, text=label, anchor="w",
                           font=(FONT, 13, "bold") if on else (FONT, 13),
                           fill=C_GREEN if on else C_INK)
        if none_tag:
            canvas.create_text(w - S(14), h / 2, text="暂无", anchor="e",
                               font=(FONT, 10), fill=C_MUTED)

    def _show_page(self, key):
        page = self.pages.get(key)
        if page is None:
            return
        self.cur_page = key
        # 子分栏页（形象库/形象生成/语音与音频）要先把宿主页抬起来，
        # 否则子页被 tkraise 在自己的 body 里，宿主还压在别的页下面。
        host = getattr(page, "_host", None) or page
        host.tkraise()
        switch = getattr(host, "show_tab", None)
        if callable(switch):
            switch(key)
        # 页面进出回调：需要定时刷新的页（如系统状态）在这里开关轮询，
        # 切走就停，避免后台一直发请求。
        for k, p in self.pages.items():
            hook = getattr(p, "on_hide", None) if k != key else getattr(p, "on_show", None)
            if callable(hook):
                try:
                    hook()
                except Exception:  # noqa: BLE001
                    pass
        if key == "broadcast":
            # 进开播页顺手刷新形象下拉：新生成的形象不用重启就能选到
            try:
                self.avatar_page._load_avatars()
            except Exception:  # noqa: BLE001
                pass
        if key == "bg":
            try:
                self.bg_page._load()
            except Exception:  # noqa: BLE001
                pass
        for k in self.nav_btns:
            self._draw_nav(k)

    def open_voice_settings(self):
        """从开播页跳到「形象与设置 → 语音与音频」。"""
        self._show_page("studio")
        try:
            self.studio_page.show_tab("voice")
        except Exception:  # noqa: BLE001
            pass

    # ── 会话 / 状态 / 帧 回调（线程事件泵入口）──────────────────
    def on_session_changed(self, sid):
        self.sessionid = sid
        self.sid_label.config(text="SID:%s" % (sid or "-"))
        self.avatar_page.set_session(sid)

    def on_avatar_status(self, text):
        text = str(text or "")
        if "已连接" in text:
            self.conn_dot.config(fg=C_GREEN)
            self.conn_label.config(text="已连接", fg=C_GREEN)
        elif "失败" in text or "异常" in text:
            self.conn_dot.config(fg=C_DANGER)
            self.conn_label.config(text="连接异常", fg=C_DANGER)
        else:
            self.conn_dot.config(fg=C_DOT_OFF)
            self.conn_label.config(text="未连接", fg=C_MUTED2)
        self.avatar_page.set_status_line(text)

    def on_script_changed(self, text):
        self.script_text = text or ""
        self.avatar_page.set_script_count(len(self.script_text))

    def show_video_frame(self, arr):
        self.avatar_page.show_frame(arr)

    def refresh_status(self):
        """后台取状态快照，避免网络与 nvidia-smi 采样卡住 Tk 主线程。"""
        if self._status_poll_busy:
            return
        self._status_poll_busy = True
        threading.Thread(target=self._poll_status_work, daemon=True).start()

    def _poll_status(self):
        self.refresh_status()
        self.app.after(5000, self._poll_status)

    def _poll_status_work(self):
        status_data = None
        settings_data = None
        try:
            r = self.app.sess.get(BASE + "/api/status", timeout=2)
            status_data = r.json().get("data", {}) or {}
        except Exception:  # noqa: BLE001
            pass
        try:
            r = self.app.sess.get(BASE + "/api/settings", timeout=3)
            settings_data = r.json().get("data", {}) or {}
        except Exception:  # noqa: BLE001
            pass
        try:
            self.app.after(0, lambda: self._apply_status_poll(status_data, settings_data))
        except Exception:  # noqa: BLE001 - 窗口已关闭
            pass

    def _apply_status_poll(self, status_data, settings_data):
        self._status_poll_busy = False
        if status_data is not None:
            self.status_page.data = status_data
            self.status_page.update_status(status_data)
        if settings_data is None:
            return
        tts = settings_data.get("tts") or {}
        engine = setting_value(tts.get("engine"), "-") or "-"
        voice = setting_value(tts.get("ref_file"), "") or ""
        avatar = setting_value((settings_data.get("avatar") or {}).get("id"), "-") or "-"
        if (engine, voice, avatar) != (self.tts_engine, self.tts_voice, self.avatar_id):
            self.tts_engine, self.tts_voice, self.avatar_id = engine, voice, avatar
            self.avatar_page.set_voice_info(engine, avatar, voice)


# ── 开播页（数字人连接 + 全屏画面）────────────────────────────────
class AvatarPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=C_BG)
        self.app = app
        self.sessionid = ""
        self.paused = False
        self._last_frame = None      # 最近一帧：窗口缩放 / 全屏切换时重绘用
        self._fs_win = None          # 全屏窗口（None = 未全屏）
        self._fs_canvas = None
        self._fs_fit = "fit"         # fit=完整显示，cover=铺满裁切
        self._redraw_job = None
        self._fs_transitioning = False
        self._status_text = "未连接"
        self._voice_text = "-"
        self._voice_name = "跟随引擎默认"
        self._script_count = 0
        self._hinted = False

        # 端侧菜单按钮叠加层（显示在数字人屏幕上，点击让数字人介绍）
        self._menus = []
        self._menu_overlay = None
        self._menu_bar = None
        self._menu_tip = None
        self._fs_menu_overlay = None
        self._fs_menu_bar = None
        self._fs_menu_tip = None
        self._menu_loading = False

        tk.Label(self, text="开播", font=(FONT, 20, "bold"), bg=C_BG, fg=C_INK
                 ).pack(anchor="w", padx=S(24), pady=(S(18), S(2)))
        tk.Label(self, text="选好数字人与音色，一键连接；画面可双击或点右上角放大到全屏。",
                 font=(FONT, 12), bg=C_BG, fg=C_MUTED2).pack(anchor="w", padx=S(24))

        body = tk.Frame(self, bg=C_BG)
        body.pack(fill="both", expand=True, padx=S(20), pady=(S(14), S(16)))

        # ── 左列：数字人连接配置（只留 数字人 ID / 参考音频 两个选择框）──
        left = tk.Frame(body, bg=C_BG, width=S(340))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        cfg = Card(left)
        cfg.pack(fill="x")
        tk.Label(cfg, text="数字人连接", font=(FONT, 14, "bold"), bg=C_CARD, fg=C_INK
                 ).pack(anchor="w", padx=S(16), pady=(S(14), S(12)))

        self.f_avatar = tk.StringVar()
        self.avatar_combo = None

        # 每项都是「标签在上、选择框在下」的纵向结构，两项上下排列。
        tk.Label(cfg, text="数字人 ID", font=(FONT, 12), bg=C_CARD, fg=C_MUTED2,
                 anchor="w").pack(fill="x", padx=S(16))
        self.avatar_combo = RoundedCombo(cfg, textvariable=self.f_avatar, font_size=12)
        self.avatar_combo.pack(fill="x", padx=S(16), pady=(S(5), S(3)))
        self.avatar_combo.bind_select(self._refresh_avatar_hint)
        self.avatar_hint = tk.Label(cfg, text="本机已有的形象；在「形象」页可管理", font=(FONT, 10),
                                    bg=C_CARD, fg=C_MUTED, anchor="w", justify="left",
                                    wraplength=S(300))
        self.avatar_hint.pack(fill="x", padx=S(16), pady=(S(0), S(14)))

        # 音色只在这里**显示**：音色是上课前一次性配好的东西，跟"今天用什么形象"
        # 无关。以前这里放了一整墙音色卡片，和「形象与设置」各改一份，说不清哪份生效。
        vrow = tk.Frame(cfg, bg=C_CARD)
        vrow.pack(fill="x", padx=S(16), pady=(S(0), S(12)))
        tk.Label(vrow, text="音色", font=(FONT, 12), bg=C_CARD, fg=C_MUTED2,
                 anchor="w", width=5).pack(side="left")
        self.voice_label = tk.Label(vrow, text=self._voice_name, font=(FONT, 12, "bold"),
                                    bg=C_CARD, fg=C_INK, anchor="w")
        self.voice_label.pack(side="left")
        make_btn(vrow, "去设置音色", self._open_voice_settings, sm=True, font_size=11
                 ).pack(side="right")

        tk.Label(cfg, text="数字人单独模式：画面仅显示数字人形象，课件 / PPT 暂不联动展示。"
                           "画面背景在「背景设置」页。",
                 font=(FONT, 11), bg=C_CARD, fg=C_MUTED, justify="left", wraplength=S(300),
                 anchor="w", padx=S(16), pady=S(8)).pack(fill="x")

        self.status_line = tk.Label(left, text="● 未连接 · 未选择数字人", font=(FONT, 12),
                                    bg=C_BG, fg=C_DANGER, wraplength=S(320), justify="left")
        self.status_line.pack(anchor="w", padx=(S(4), S(0)), pady=(S(12), S(4)))
        btns = tk.Frame(left, bg=C_BG)
        btns.pack(fill="x", pady=(S(6), S(0)))
        make_btn(btns, "开始连接", self._connect, primary=True).pack(side="left")
        make_btn(btns, "断开连接", self._disconnect).pack(side="left", padx=(S(8), S(0)))
        btns2 = tk.Frame(left, bg=C_BG)
        btns2.pack(fill="x", pady=(S(8), S(0)))
        make_btn(btns2, "播放/暂停", self._toggle_play).pack(side="left")
        make_btn(btns2, "打断朗读", self._interrupt).pack(side="left", padx=(S(8), S(0)))
        self.meta_line = tk.Label(left, text="音色 - · 讲稿 0 字", font=(FONT, 11), bg=C_BG,
                                  fg=C_MUTED)
        self.meta_line.pack(anchor="w", padx=(S(4), S(0)), pady=(S(10), S(0)))

        # ── 右侧：数字人画面（圆角深色舞台，可全屏自适应显示）──
        stage = tk.Frame(body, bg=C_BG)
        stage.pack(side="left", fill="both", expand=True, padx=(S(16), S(0)))
        self.stage = stage
        self.canvas = tk.Canvas(stage, bg=C_STAGE, highlightthickness=0)
        self.canvas._round_radius = S(14)
        self.canvas._round_corner = C_BG
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_stage_resize)
        self.canvas.bind("<Double-Button-1>", lambda e: self._toggle_fullscreen())
        # 提示语直接画在画布上，免得给圆角舞台加一条方角深色横条
        self._hint_text = "等待数字人播报时自动切换"
        self._hint_shown = "等待数字人播报" in self._hint_text
        make_btn(stage, "全屏显示", self._toggle_fullscreen, sm=True,
                 font_size=12).place(relx=1.0, rely=0.0, x=-S(24), y=S(22), anchor="ne")
        self._load_avatars()
        self._init_chat_ui()
        self._build_menu_overlay()

    def _load_avatars(self, force=False):
        now = time.time()
        # 建页时和首次进页会连着触发两次，1.5s 内不重复打接口
        if not force and now - getattr(self, "_avatars_at", 0) < 1.5:
            return
        self._avatars_at = now
        try:
            r = self.app.sess.get(BASE + "/api/avatars", timeout=8)
            data = r.json().get("data", []) or []
            avatars = data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            avatars = []
        self._avatars = {a["id"]: a for a in avatars}
        ids = [a["id"] for a in avatars]
        self.avatar_combo["values"] = ids
        # 拿不到形象列表（服务未就绪）时退回可手动输入，免得整页不可用
        self.avatar_combo.config(state="readonly" if ids else "normal")
        if ids and not self.f_avatar.get():
            # 默认选一个「素材齐全」的形象：不完整的选择进去会在服务端建会话失败
            ready = [a["id"] for a in avatars if a.get("ready", True)]
            self.f_avatar.set((ready or ids)[0])
        self._refresh_avatar_hint()
        self._refresh_meta()

    def _avatar_ready(self, avatar_id=None):
        aid = avatar_id or self.f_avatar.get()
        item = getattr(self, "_avatars", {}).get(aid)
        if not item:
            return True                      # 列表没取到时不拦
        return bool(item.get("ready", True))

    def _refresh_avatar_hint(self):
        aid = self.f_avatar.get()
        if not aid:
            self.avatar_hint.config(text="本机已有的形象；在「形象」页可管理", fg=C_MUTED)
            return
        if self._avatar_ready(aid):
            self.avatar_hint.config(text="本机已有的形象；在「形象」页可管理", fg=C_MUTED)
        else:
            self.avatar_hint.config(
                text="%s 素材不完整（缺 latents.pt / coords.pkl），无法开播，请换一个形象" % aid,
                fg=C_DANGER)

    # ── 对话浮层（流式显示在数字人画面上）──────────────────────────
    def _init_chat_ui(self):
        self._chat_q = ""            # 本轮问题（只用于判断换轮）
        self._chat_text = ""         # 本轮已生成的回答（服务端快照）
        self._chat_done = True
        self._chat_key = ""          # 本轮标识，变了就是新的一轮
        self._chat_speaking = False  # 数字人此刻是否真的在出声
        self._chat_busy = False      # 轮询请求在飞，别叠加
        self._chat_at = 0.0          # 最后更新时间（用于自动收起）
        self._chat_tick = 0
        self._chat_polling = True
        self.after(600, self._poll_chat)

    # ── 端侧菜单按钮（显示在数字人屏幕上，点击让数字人介绍）────────
    def _build_menu_overlay(self):
        """在舞台底部居中铺一条菜单按钮条 + 一行提示。"""
        if self._menu_overlay is not None:
            return
        overlay = tk.Frame(self.stage, bg=C_STAGE)
        bar = tk.Frame(overlay, bg=C_STAGE)
        bar.pack()
        tip = tk.Label(overlay, text="", bg=C_STAGE, fg="#9FB4CC",
                       font=(FONT, 11), wraplength=S(900), justify="center")
        tip.pack(pady=(S(4), 0))
        overlay.place(relx=0.5, rely=1.0, anchor="s", y=-S(14))
        self._menu_overlay, self._menu_bar, self._menu_tip = overlay, bar, tip
        self._load_kiosk_menus()

    def _build_fs_menu_overlay(self):
        """全屏视图里的菜单按钮条（点右上角全屏时调用）。"""
        if self._fs_menu_overlay is not None or self._fs_canvas is None:
            return
        overlay = tk.Frame(self._fs_canvas, bg="#0E1726")
        bar = tk.Frame(overlay, bg="#0E1726")
        bar.pack()
        tip = tk.Label(overlay, text="", bg="#0E1726", fg="#9FB4CC",
                       font=(FONT, 12), wraplength=S(1100), justify="center")
        tip.pack(pady=(S(6), 0))
        overlay.place(relx=0.5, rely=1.0, anchor="s", y=-S(18))
        self._fs_menu_overlay, self._fs_menu_bar, self._fs_menu_tip = overlay, bar, tip
        self._rebuild_menu_bar(self._fs_menu_bar, self._fs_menu_tip)

    def _rebuild_menu_bar(self, bar, tip):
        if bar is None:
            return
        for w in list(bar.winfo_children()):
            w.destroy()
        if not self._menus:
            tip.config(text="（菜单库里没有启用项，端侧不显示按钮）")
            return
        tip.config(text="")
        # 菜单多了会顶出舞台宽度（按钮条居中摆放，超出的部分直接被裁掉），
        # 所以 5 个往上就收紧按钮间距，宁可挤一点也别显示不全。
        gap = S(10) if len(self._menus) <= 4 else S(6)
        for item in self._menus:
            title = item.get("title") or ""
            tip_text = item.get("question") or item.get("link") or ""
            make_btn(bar, title, lambda i=item: self._activate_menu(i["id"], i.get("title", "")),
                     sm=True, font_size=12, tooltip=tip_text, borderless=True
                     ).pack(side="left", padx=(0, gap))

    def _load_kiosk_menus(self):
        """拉启用菜单并重建两条按钮条；每 15s 刷新一次以便运营改完即时生效。"""
        if self._menu_loading:
            return
        self._menu_loading = True

        def work():
            items = []
            try:
                r = self.app.sess.get(BASE + "/api/kiosk/menus/enabled", timeout=8)
                items = (r.json().get("data") or {}).get("items", []) or []
            except Exception:  # noqa: BLE001
                items = []
            self._menus = items

            def ui():
                self._rebuild_menu_bar(self._menu_bar, self._menu_tip)
                self._rebuild_menu_bar(self._fs_menu_bar, self._fs_menu_tip)
                self._menu_loading = False
            self.after(0, ui)

        threading.Thread(target=work, daemon=True).start()
        self.after(15000, self._load_kiosk_menus)

    def _activate_menu(self, item_id, title):
        """端侧按钮点击：调后端 activate 让数字人开口，并给一行即时反馈。"""
        self._set_menu_tip("正在介绍：%s …" % title, fs=(self._fs_win is not None))

        def work():
            try:
                r = self.app.sess.post(
                    BASE + "/api/kiosk/menus/%s/activate" % item_id,
                    json={"sessionid": self.sessionid, "speak": True}, timeout=90)
                data = r.json() or {}
                ok = data.get("code", -1) == 0
                ans = (data.get("data") or {}).get("answer", "")
                if ok:
                    shown = (ans[:60] + "…") if len(ans) > 60 else ans
                    tip = "（已播报）%s" % shown if shown else "（已播报）"
                else:
                    tip = "介绍失败：%s" % (data.get("msg") or "未知错误")
            except Exception as exc:  # noqa: BLE001
                tip = "请求失败：%r" % (exc,)
            self._set_menu_tip(tip, fs=(self._fs_win is not None))

        threading.Thread(target=work, daemon=True).start()

    def _set_menu_tip(self, text, fs=False):
        target = self._fs_menu_tip if fs else self._menu_tip
        if target is not None:
            try:
                target.config(text=text)
            except Exception:  # noqa: BLE001
                pass

    def _poll_chat(self):
        """轮询 /llm_status 拿"本轮要说的话"，做增量渲染。

        用轮询而不是 SSE：文本量很小（几百字），300ms 一次对服务端几乎无压力，
        也不必在客户端维护长连接和断线重连。

        **请求必须在后台线程发**：以前这里是主线程直接 `sess.post(timeout=3)`，
        每 300ms 一次同步 I/O，主服务稍一卡顿界面就整片发白——和"启动黑屏"是同一
        个病根（UI 线程被 I/O 堵住）。
        """
        if not self._chat_polling:
            return
        sid = self.sessionid
        if sid and not self._chat_busy:
            self._chat_busy = True
            threading.Thread(target=self._poll_chat_work, args=(sid,),
                             daemon=True).start()
        self.after(300, self._poll_chat)

    def _poll_chat_work(self, sid):
        data = None
        try:
            r = self.app.sess.post(BASE + "/llm_status",
                                   json={"sessionid": sid}, timeout=3)
            data = (r.json() or {}).get("data") or {}
        except Exception:  # noqa: BLE001 - 主服务没起来/正在重启
            data = None
        self._chat_busy = False
        if data is not None:
            try:
                self.after(0, lambda: self._apply_chat(data))
            except Exception:  # noqa: BLE001 - 窗口已销毁
                pass

    def _apply_chat(self, d):
        """把服务端快照并进本地状态，必要时重绘气泡。"""
        key = str(d.get("bubble_key") or "")
        text = str(d.get("text") or "")
        question = str(d.get("question") or "").strip()
        new_round = bool(key) and key != self._chat_key
        if new_round:
            self._chat_key = key
            self._chat_q, self._chat_text = question, ""
            self._chat_at = time.time()
        elif question:
            self._chat_q = question
        done, speaking = bool(d.get("done")), bool(d.get("speaking"))
        # 结束标志变了也要重绘：否则气末尾那个"还在生成"的光标会一直亮着
        changed = new_round or done != self._chat_done or speaking != self._chat_speaking
        self._chat_done, self._chat_speaking = done, speaking
        if text and text != self._chat_text:
            self._chat_text = text
            self._chat_at = time.time()
            changed = True
        if self._chat_speaking:
            self._chat_at = time.time()
        # 说完（且真的不再出声）8 秒后自动收起；正在说话就一直挂着
        if (self._chat_text and self._chat_done and not self._chat_speaking
                and time.time() - self._chat_at > 8):
            self._chat_q, self._chat_text = "", ""
            self._chat_key = ""
            changed = True
        if changed:
            self._draw_chat_ui()

    # ── 回答字幕（贴在 9:16 画面下方，菜单之上）────────────────────
    # 菜单是固定的主操作入口，不应为了回答文字上下跳动。回答采用影视字幕
    # 的样式，始终预留菜单高度，既不挡按钮也不会让画面布局发生变化。
    SUBTITLE_MARGIN = 14
    SUBTITLE_WIDTH_RATIO = 0.88
    SUBTITLE_KEEP_LINES = 2

    def _frame_rect(self, canvas):
        """画面在画布上的实际矩形 (x0, y0, w, h)。"""
        geom = getattr(canvas, "_frame_geom", None)
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        if geom:
            _scale, nw, nh, ox, oy, _cw, _ch = geom
            if nw > 8 and nh > 8:
                return ox, oy, nw, nh
        return 0, 0, max(1, cw), max(1, ch)

    def _bubble_font_size(self, frame_w):
        """字号跟着画面宽度走：小窗里别糊成一片，全屏时也别小得看不见。

        系数 34 是量出来的：9:16 画面在舞台上约 600px 宽时约 17 号字，
        一行二十来个汉字，既能看清又不至于把画面下半截全糊住。
        """
        return max(10, min(17, int(round(frame_w / 34.0))))

    @staticmethod
    def _wrap_text(font, text, width):
        """按像素宽度逐字折行。中文没有空格，Tk 自己也是这么断的。"""
        lines, cur = [], ""
        for ch in text:
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            if cur and font.measure(cur + ch) > width:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines

    def _fit_bubble_text(self, font, text, width, keep):
        """只留最后几行：流式字幕要让人看见"正在说的话"，而不是从头背。"""
        while True:
            lines = self._wrap_text(font, text, width)
            if len(lines) <= keep or len(text) <= 1:
                return text, lines
            cut = max(1, len(text) // max(1, len(lines)))
            text = "…" + text[cut:]

    def _frosted_bg(self, canvas, w, h, x0, y0):
        """把气泡位置下的画面裁下来做磨砂玻璃，返回 PIL 图（失败返回 None）。

        Tk 的画布没有透明度，所以"半透明"只能靠**画面本身**做底：裁一块、
        缩小、模糊、压暗，再贴回去。人一动底子就旧了，调用方按帧重铺。
        """
        from PIL import Image, ImageFilter
        frm = self._last_frame
        geom = getattr(canvas, "_frame_geom", None)
        if frm is None or geom is None or w < 8 or h < 8:
            return None
        scale, _nw, _nh, ox, oy, _cw, _ch = geom
        if scale <= 0:
            return None
        H, W = frm.shape[:2]
        sx, sy = (x0 - ox) / scale, (y0 - oy) / scale
        sw, sh = w / scale, h / scale
        x0i, y0i = max(0, int(sx)), max(0, int(sy))
        x1i, y1i = min(W, int(sx + sw)), min(H, int(sy + sh))
        if x1i - x0i < 8 or y1i - y0i < 8:
            return None
        crop = Image.fromarray(frm[y0i:y1i, x0i:x1i])
        small = crop.resize((max(8, w // 5), max(8, h // 5)), Image.BILINEAR)
        small = small.filter(ImageFilter.GaussianBlur(3))
        bg = small.resize((w, h), Image.BILINEAR)
        tint = Image.new("RGB", (w, h), (11, 20, 32))
        return Image.blend(bg, tint, 0.62)

    def _bubble_image(self, canvas, w, h, x0, y0):
        """圆角 + 小尾巴 + 磨砂底，做成一张带 alpha 的图。

        带 alpha 是为了圆角真的圆：Tk 画不出圆角矩形，只能把四角留透明，
        靠画布合成透出后面的画面（圆角遮罩那套已经在用同一条路子）。
        """
        from PIL import Image, ImageDraw, ImageTk
        ss = 3                                  # 超采样，圆角和尾巴才不毛边
        tail = S(self.BUBBLE_TAIL)
        W, H = w * ss, (h + tail) * ss
        radius = min(h, S(18)) * ss

        mask = Image.new("L", (W, H), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle([0, 0, W - 1, h * ss - 1], radius=radius, fill=255)
        # 尾巴挂在右下角，朝画面角落指——观众一眼看出"这句话是谁说的"
        tw = S(26) * ss
        draw.polygon([(W - tw - S(6) * ss, h * ss - 2),
                      (W - S(3) * ss, H - 1),
                      (W - tw * 0.45, h * ss - 2)], fill=255)

        bg = self._frosted_bg(canvas, w, h + tail, int(x0), int(y0))
        if bg is None:
            bg = Image.new("RGB", (w, h + tail), (13, 24, 38))
        panel = bg.convert("RGBA")
        panel.putalpha(mask.resize((w, h + tail), Image.LANCZOS))
        return ImageTk.PhotoImage(panel)

    def _draw_chat_ui(self):
        """把回答字幕画到舞台画布（全屏时也画一份）。"""
        for canvas in (self.canvas, self._fs_canvas):
            if canvas is not None:
                self._draw_chat_on(canvas)
        self._sync_menu_overlay()

    def _sync_menu_overlay(self):
        """菜单始终固定在底部；字幕会自行避开这块区域。"""
        for attr, bottom_y in (
            ("_menu_overlay", -S(14)),
            ("_fs_menu_overlay", -S(18)),
        ):
            overlay = getattr(self, attr, None)
            if overlay is None:
                continue
            try:
                overlay.place(relx=0.5, rely=1.0, anchor="s", y=bottom_y)
            except Exception:  # noqa: BLE001 - 控件已销毁
                pass

    def _menu_bottom_inset(self, canvas):
        """返回字幕需要给固定菜单预留的底部空间。"""
        overlay = (self._fs_menu_overlay if canvas is self._fs_canvas
                   else self._menu_overlay)
        try:
            menu_h = overlay.winfo_height() if overlay is not None else 0
        except Exception:  # noqa: BLE001
            menu_h = 0
        return max(S(58), int(menu_h) + S(24))

    def _draw_chat_on(self, canvas):
        try:
            canvas.delete("ui")
            if not (self._chat_q or self._chat_text):
                return
            canvas.delete("hint")   # 提示语和字幕抢同一块地方
            fx, fy, fw, fh = self._frame_rect(canvas)
            if fw < 120 or fh < 160:
                return

            margin = max(S(8), int(fw * 0.035))
            size = self._bubble_font_size(fw)
            font = ui_font(size)
            line_h = font.metrics("linespace")
            pad_x, pad_y = S(15), S(12)

            max_w = min(int(fw - 2 * margin), int(fw * self.SUBTITLE_WIDTH_RATIO))
            text_w = max(S(90), max_w - 2 * pad_x)
            body = (self._chat_text or "").strip()
            if not body:
                body = "…" if self._chat_done else "正在想…"
            body, lines = self._fit_bubble_text(font, body, text_w,
                                                self.SUBTITLE_KEEP_LINES)
            if not self._chat_done:
                lines = list(lines)
                lines[-1] = lines[-1] + "▌"     # 还在生成：末尾给个光标

            # 字幕按实际文字收紧，但固定居中，视觉上不会像聊天气泡那样干扰画面。
            widest = max((font.measure(line) for line in lines), default=0)
            w = int(min(max_w, max(S(120), widest + 2 * pad_x)))
            h = pad_y * 2 + len(lines) * line_h
            bx = int(fx + (fw - w) / 2)
            by = max(int(fy + margin), int(fy + fh - self._menu_bottom_inset(canvas) - h))
            canvas.create_rectangle(bx, by, bx + w, by + h, fill="#202B3A",
                                    outline="#4C5B6E", width=1, tags="ui")
            canvas.create_text(fx + fw / 2, by + h / 2, text="\n".join(lines),
                               anchor="center", fill="#F2F7FC", font=font,
                               justify="center", tags="ui")
            canvas.tag_raise("ui")
        except Exception:  # noqa: BLE001 - 气泡画失败不该影响画面
            pass

    # ── 画面渲染（自适应缩放；页面与全屏共用）──────────────────────
    def _render_frame(self, canvas, arr, mode="fit"):
        if canvas is None or arr is None:
            return
        from PIL import Image, ImageTk
        img = Image.fromarray(arr)
        cw = canvas.winfo_width()
        ch = canvas.winfo_height()
        if cw < 16 or ch < 16:            # 还没完成布局，先按常见尺寸占位
            cw, ch = S(960), S(540)
        w, h = img.size
        if not w or not h:
            return
        if mode == "cover":               # 铺满：宁可裁掉边角也不留黑边
            scale = max(cw / w, ch / h)
        else:                             # 完整：保持比例，两侧补黑边
            scale = min(cw / w, ch / h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        img = img.resize((nw, nh), Image.BILINEAR)
        photo = ImageTk.PhotoImage(img)
        canvas._photo = photo             # 独占引用，避免被回收；也避免帧序列堆积
        canvas.delete("frame")
        canvas.create_image(cw / 2, ch / 2, image=photo, tags="frame")
        # 记录画面几何：对话浮层要从"画面上的同一块区域"取背景做毛玻璃
        canvas._frame_geom = (scale, nw, nh, (cw - nw) / 2.0, (ch - nh) / 2.0, cw, ch)
        # 舞台是圆角窗口：画面本身是方的，用一层四角遮罩把边角切圆
        radius = getattr(canvas, "_round_radius", 0)
        if radius:
            mask = rounded_mask_photo(cw, ch, radius,
                                      getattr(canvas, "_round_corner", C_BG), "#22344B")
            canvas._mask_photo = mask
            canvas.create_image(cw / 2, ch / 2, image=mask, tags="frame")
            if self._hint_text and not (self._chat_q or self._chat_text):
                canvas.create_text(cw / 2, ch - S(26), text=self._hint_text,
                                   fill="#8FA0B5", font=(FONT, 11),
                                   tags=("frame", "hint"))
        # 对话浮层要始终压在画面之上
        if canvas.find_withtag("ui"):
            canvas.tag_raise("ui")
        # 回答改为不依赖视频底图的字幕；不用每几帧重复绘制，避免增加 Tk 开销。
        if canvas is getattr(self, "canvas", None) or canvas is getattr(self, "_fs_canvas", None):
            if self._hint_text and not (self._chat_q or self._chat_text):
                canvas.create_text(cw / 2, ch - S(26), text=self._hint_text,
                                   fill="#8FA0B5", font=(FONT, 11), tags="hint")

    def _paint_stage_shell(self):
        """还没收到画面时，先把圆角舞台的边角画出来。"""
        canvas = self.canvas
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        radius = getattr(self.canvas, "_round_radius", 0)
        if not radius or cw < 4 or ch < 4:
            return
        canvas.delete("shell")
        mask = rounded_mask_photo(cw, ch, radius, C_BG, "#22344B")
        canvas._shell_photo = mask
        canvas.create_image(cw / 2, ch / 2, image=mask, tags="shell")
        if self._hint_text and not (self._chat_q or self._chat_text):
            canvas.create_text(cw / 2, ch - S(26), text=self._hint_text,
                               fill="#8FA0B5", font=(FONT, 11),
                               tags=("shell", "hint"))

    def show_frame(self, arr):
        try:
            self._last_frame = arr
            if self._hint_shown:
                self._hint_shown = False
                self._hint_text = "双击画面或点右上角「全屏显示」放大"
            self._render_frame(self.canvas, arr, "fit")
            if self._fs_canvas is not None and not self._fs_transitioning:
                self._render_frame(self._fs_canvas, arr, self._fs_fit)
        except Exception:  # noqa: BLE001
            pass

    def _on_stage_resize(self, event=None):
        """窗口 / 全屏尺寸变化后重绘最近一帧，画面跟着自适应。

        先同步重绘一次：舞台底色是很深的 #101F33，最大化后如果只等去抖重绘，
        新尺寸下的舞台会先空着一片深色，看起来就像"控件变黑了"。
        """
        if self._last_frame is None:
            self._paint_stage_shell()
            return
        try:
            self._render_frame(self.canvas, self._last_frame, "fit")
        except Exception:  # noqa: BLE001
            pass
        if self._redraw_job:
            try:
                self.after_cancel(self._redraw_job)
            except Exception:  # noqa: BLE001
                pass
        self._redraw_job = self.after(60, self._redraw_main)

    def on_resize(self):
        """主窗口尺寸稳定后由 DesktopApp 统一回调。"""
        self._on_stage_resize()

    def _redraw_main(self):
        self._redraw_job = None
        if self._last_frame is None:
            return
        self._render_frame(self.canvas, self._last_frame, "fit")

    def _on_fullscreen_resize(self, _event=None):
        """全屏窗口稳定后只重画它自己，避免把主窗口也卷入尺寸风暴。"""
        if self._fs_transitioning or self._fs_canvas is None:
            return
        if self._redraw_job:
            try:
                self.after_cancel(self._redraw_job)
            except Exception:  # noqa: BLE001
                pass
        self._redraw_job = self.after(80, self._redraw_fullscreen)

    def _redraw_fullscreen(self):
        self._redraw_job = None
        if self._fs_canvas is not None and self._last_frame is not None:
            self._render_frame(self._fs_canvas, self._last_frame, self._fs_fit)

    # ── 全屏显示 ───────────────────────────────────────────────────
    def _toggle_fullscreen(self):
        if self._fs_win is not None:
            self._exit_fullscreen()
        else:
            self._enter_fullscreen()

    def _enter_fullscreen(self):
        win = tk.Toplevel(self)
        # 先隐藏、完成无边框布局、再显示：Windows 在可见窗口上切 fullscreen
        # 会连续触发多次 DPI/尺寸重算，旧实现会把主窗口和两块画布交错重绘。
        win.withdraw()
        win.title("数字人画面 · 全屏（Esc 退出）")
        win.configure(bg="#000000")
        try:
            win.attributes("-fullscreen", True)
        except Exception:  # noqa: BLE001 - 少数窗口管理器不支持，退回最大化
            try:
                win.state("zoomed")
            except Exception:  # noqa: BLE001
                win.geometry("%dx%d" % (S(1280), S(800)))
        canvas = tk.Canvas(win, bg="#000000", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        self._fs_win, self._fs_canvas = win, canvas
        self._fs_transitioning = True
        canvas.bind("<Configure>", self._on_fullscreen_resize)
        for seq in ("<Escape>", "<F11>"):
            win.bind(seq, lambda e: self._exit_fullscreen())
        win.bind("<Double-Button-1>", lambda e: self._exit_fullscreen())

        bar = tk.Frame(canvas, bg=C_STAGE, highlightbackground="#2A3F5C", highlightthickness=1)
        bar.place(relx=1.0, rely=0.0, x=-S(20), y=S(20), anchor="ne")
        self.fs_fit_btn = make_btn(bar, self._fit_btn_text(), self._toggle_fs_fit, sm=True,
                                   font_size=11)
        self.fs_fit_btn.pack(side="left", padx=(S(6), S(0)), pady=S(5))
        make_btn(bar, "退出全屏", self._exit_fullscreen, sm=True,
                 font_size=11).pack(side="left", padx=S(6), pady=S(5))
        win.protocol("WM_DELETE_WINDOW", self._exit_fullscreen)
        self._build_fs_menu_overlay()
        win.deiconify()
        win.lift()
        win.after(120, self._finish_enter_fullscreen)
        win.focus_force()

    def _finish_enter_fullscreen(self):
        if self._fs_canvas is None:
            return
        self._fs_transitioning = False
        self._redraw_fullscreen()

    def _fit_btn_text(self):
        return "缩放：完整显示" if self._fs_fit == "fit" else "缩放：铺满屏幕"

    def _toggle_fs_fit(self):
        self._fs_fit = "cover" if self._fs_fit == "fit" else "fit"
        try:
            self.fs_fit_btn.config(text=self._fit_btn_text())
        except Exception:  # noqa: BLE001
            pass
        if self._fs_canvas is not None:
            self._fs_canvas.delete("all")
            self._render_frame(self._fs_canvas, self._last_frame, self._fs_fit)

    def _exit_fullscreen(self):
        win, self._fs_win = self._fs_win, None
        self._fs_canvas = None
        self._fs_transitioning = False
        self._fs_menu_overlay = None
        self._fs_menu_bar = None
        self._fs_menu_tip = None
        if win is None:
            return
        try:
            win.attributes("-fullscreen", False)
        except Exception:  # noqa: BLE001
            pass
        try:
            win.destroy()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.focus_set()
        except Exception:  # noqa: BLE001
            pass
        self.after_idle(self._redraw_main)

    def set_session(self, sid):
        self.sessionid = sid
        self._refresh_status_line()
        if not sid:
            # 断开就把浮层收掉，免得停在一个过期的问答上
            self._chat_q = self._chat_text = ""
            try:
                self.canvas.delete("ui")
            except Exception:  # noqa: BLE001
                pass

    def set_status_line(self, text):
        self._status_text = str(text or "未连接")
        self._refresh_status_line()

    def _refresh_status_line(self):
        """一条状态行兼做原先「开播实况」的连接/会话信息位。"""
        text = self._status_text
        if self.sessionid and self.sessionid not in text:
            sid = self.sessionid
            short = sid if len(sid) <= 12 else sid[:8] + "…"
            text += "  ·  SID:%s" % short
        self.status_line.config(text="● " + text)
        connected = "已连接" in text
        failed = ("失败" in text) or ("异常" in text)
        self.status_line.config(fg=C_GREEN if connected else (C_DANGER if failed else C_MUTED2))

    def set_voice_info(self, engine, avatar, voice=""):
        self._voice_text = engine or "-"
        if voice:
            self._voice_name = voice_display_name(voice)
        self.voice_label.config(text=self._voice_name)
        if not self.f_avatar.get():
            self.f_avatar.set(avatar)
        self._refresh_meta()

    def _open_voice_settings(self):
        main = getattr(self.app, "_main", None)
        if main is not None:
            main.open_voice_settings()

    def set_script_count(self, n):
        self._script_count = int(n or 0)
        self._refresh_meta()

    def _refresh_meta(self):
        self.meta_line.config(text="音色 %s · 讲稿 %d 字" % (self._voice_text, self._script_count))

    def _gpu_free_mb(self):
        """查询当前 GPU 显存余量（MiB）；查不到返回 None。"""
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10).stdout.strip()
            return int(float(out.splitlines()[0]))
        except Exception:  # noqa: BLE001
            return None

    def _rag_resident_mb(self):
        """Ollama 当前驻留显存的模型合计（MiB）；keep_alive=0 空闲时约 0。"""
        try:
            r = requests.get("http://127.0.0.1:11434/api/ps", timeout=3)
            models = (r.json().get("models") or [])
            return sum(int(m.get("size_vram", 0) or 0) for m in models) // (1024 * 1024)
        except Exception:  # noqa: BLE001
            return 0

    def _vram_check_before_connect(self):
        """连接前显存预检。返回 True 继续连接，False 表示用户选择放弃。

        只对"需要冷启动"的情况有意义：主服务已在跑时，数字人引擎（MuseTalk 等模型）
        已经常驻显存了，这时候再要求"空闲 >= 3.5G"必然误报——那几 G 正是被引擎自己
        占着的。所以先看服务是否在线：在线就直接放行，只有要现拉服务时才检查余量。

        注意必须把结果返回给调用方：以前这里只是 return 出函数，_connect 照常往下
        走，导致用户在弹窗里点了"否"也照样连上——确认框等于白弹。
        """
        try:
            if service_alive():
                return True           # 引擎已加载，连接只是建会话，不再额外占显存
        except Exception:  # noqa: BLE001
            pass
        try:
            free_mb = self._gpu_free_mb()
            if free_mb is None:
                return True           # 查不到显存信息，不拦
            rag_mb = self._rag_resident_mb()
            # 冷启动时数字人（musetalk）加载约需 3.5G；余量 >= 需求即放行
            need_mb = 3500
            if free_mb - rag_mb >= need_mb:
                return True
            msg = "检测到显存可能不足：\n当前空闲 %.1f GB，数字人加载约需 3.5 GB" % (free_mb / 1024.0)
            if rag_mb > 0:
                msg += "，RAG 模型正驻留显存约 %.1f GB" % (rag_mb / 1024.0)
            msg += "。\n建议先在「知识库管理」页停止 RAG 再连接。\n\n仍要现在连接吗？"
            return bool(messagebox.askyesno("显存提醒", msg))
        except Exception:  # noqa: BLE001
            return True

    def _connect(self):
        if not self._avatar_ready():
            self._refresh_avatar_hint()
            messagebox.showwarning(
                "形象不可用",
                "形象 %s 的素材不完整（缺 latents.pt / coords.pkl），服务端无法创建会话。\n\n"
                "请换一个形象，或到「形象生成」页重新生成。" % self.f_avatar.get())
            return
        # 用户在显存提醒里点"否"时真正中止（以前只是从检查函数 return，仍会连上）
        if not self._vram_check_before_connect():
            return
        try:
            # 只切形象：音色归「形象与设置」管，这里再下发一次会把用户刚在
            # 设置里选的音色覆盖成开播页上的旧值。
            payload = {"avatar": {"id": self.f_avatar.get().strip()}}
            self.app.sess.put(BASE + "/api/settings", json=payload, timeout=8)
        except Exception:  # noqa: BLE001
            pass
        self.app.avatar.connect()
        self.app.status_var.set("正在连接数字人…")

    def _disconnect(self):
        sid = self.sessionid
        self.paused = False
        self.app.status_var.set("正在断开数字人…")
        self._set_menu_tip("")
        # 丢弃本机声卡尚未播放的音频，但不能 stop()：后者会结束线程，
        # 下次重新连接就无法再出声。
        try:
            self.app.audio_player.clear_pending()
        except Exception:  # noqa: BLE001
            pass
        self.app.avatar.disconnect()
        # 服务端收到 WebRTC close 后也会回收会话；这里额外发打断请求，使朗读
        # 在 ICE 状态收敛前就停止。网络请求放后台，不得卡住按钮反馈。
        if sid:
            def interrupt():
                try:
                    self.app.sess.post(BASE + "/interrupt_talk",
                                       json={"sessionid": sid}, timeout=5)
                except Exception:  # noqa: BLE001
                    pass
            threading.Thread(target=interrupt, daemon=True).start()

    def _toggle_play(self):
        if not self.sessionid:
            self.app.status_var.set("请先连接数字人")
            return
        self.paused = not self.paused
        try:
            r = self.app.sess.post(BASE + "/api/playback",
                                   json={"sessionid": self.sessionid, "paused": self.paused}, timeout=5)
            if r.json().get("code", -1) == 0:
                self.app.status_var.set("已暂停" if self.paused else "已恢复播放")
        except Exception as exc:  # noqa: BLE001
            self.app.status_var.set("播放控制失败: %r" % (exc,))

    def _interrupt(self):
        if not self.sessionid:
            self.app.status_var.set("请先连接数字人")
            return
        sid = self.sessionid
        self.app.status_var.set("正在打断朗读…")

        def work():
            try:
                response = self.app.sess.post(BASE + "/interrupt_talk",
                                              json={"sessionid": sid}, timeout=5)
                data = response.json() or {}
                text = "已打断朗读" if data.get("code", -1) == 0 else (
                    "打断失败：%s" % (data.get("msg") or "服务未确认"))
            except Exception as exc:  # noqa: BLE001
                text = "打断失败: %r" % (exc,)
            try:
                self.app.after(0, lambda: self.app.status_var.set(text))
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=work, daemon=True).start()

    # ── 语音输入（原聊天页的输入方式，聊天功能移除后改由开播页承接）──
    # 识别结果不走 LLM，直接当讲稿交给数字人朗读：保留"说话 → 数字人复述"的
    # 演示能力，但不再有问答/对话语义。
    def handle_stt_done(self, text):
        text = (text or "").strip()
        if not text:
            self.app.status_var.set("转写结果为空")
            return
        if not self.sessionid:
            self.app.status_var.set("请先连接数字人再使用语音输入")
            return
        try:
            r = self.app.sess.post(BASE + "/human",
                                   json={"sessionid": self.sessionid,
                                         "type": "echo", "text": text,
                                         "interrupt": True}, timeout=20)
            self.app.status_var.set(
                "语音已识别，正在朗读" if r.json().get("code", -1) == 0 else "朗读失败")
        except Exception as exc:  # noqa: BLE001
            self.app.status_var.set("朗读失败: %r" % (exc,))

    def handle_stt_err(self, msg):
        self.app.status_var.set("转写失败: %s" % (msg,))


# ── 讲稿页 ───────────────────────────────────────────────────────────
class ScriptPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=C_BG)
        self.app = app
        tk.Label(self, text="讲稿", font=(FONT, 20, "bold"), bg=C_BG, fg=C_INK
                 ).pack(anchor="w", padx=S(24), pady=(S(18), S(2)))
        tk.Label(self, text="写好的讲稿支持 [PPT:N] 标签——讲到第 N 页时，数字人会停下来等你翻课件；\n也可以直接点「朗读讲稿」，数字人照着稿子讲。",
                 font=(FONT, 12), bg=C_BG, fg=C_MUTED2, justify="left").pack(anchor="w", padx=S(24))
        box = Card(self)
        box.pack(fill="both", expand=True, padx=S(24), pady=(S(16), S(10)))
        self.text = scrolledtext.ScrolledText(box, font=(FONT, 12), wrap="word", relief="flat",
                                              bg=C_CARD, fg=C_INK, highlightthickness=0)
        self.text.pack(fill="both", expand=True, padx=S(4), pady=S(4))
        self.text.bind("<KeyRelease>", self._on_change)
        bar = tk.Frame(self, bg=C_BG)
        bar.pack(fill="x", padx=S(24), pady=(S(0), S(16)))
        self.word_label = tk.Label(bar, text="0 字", font=(FONT, 12), bg=C_BG, fg=C_MUTED2)
        self.word_label.pack(side="left")
        make_btn(bar, "保存讲稿", self._save_script).pack(side="left", padx=(S(16), S(0)))
        make_btn(bar, "朗读讲稿", self._speak_script, primary=True).pack(side="left", padx=(S(8), S(0)))
        make_btn(bar, "打断", self._interrupt).pack(side="left", padx=(S(8), S(0)))
        self.hint = tk.Label(bar, text="", font=(FONT, 11), bg=C_BG, fg=C_GREEN)
        self.hint.pack(side="left", padx=(S(14), S(0)))

    def _on_change(self, _e=None):
        text = self.text.get("1.0", "end").strip()
        self.word_label.config(text="%d 字" % len(text))
        self.app._main.on_script_changed(text)

    def _save_script(self):
        text = self.text.get("1.0", "end").strip()
        self.app._main.on_script_changed(text)
        self.hint.config(text="已保存（本地会话内）")
        self.app.after(2000, lambda: self.hint.config(text=""))

    def _speak_script(self):
        text = self.text.get("1.0", "end").strip()
        if not text:
            self.hint.config(text="请先写一段讲稿", fg=C_DANGER)
            self.app.after(2000, lambda: self.hint.config(text="", fg=C_GREEN))
            return
        if not self.app._main.sessionid:
            self.hint.config(text="请先在「开播」页连接数字人", fg=C_DANGER)
            self.app.after(2000, lambda: self.hint.config(text="", fg=C_GREEN))
            return
        try:
            r = self.app.sess.post(BASE + "/human",
                                   json={"sessionid": self.app._main.sessionid,
                                         "type": "echo", "text": text}, timeout=20)
            if r.json().get("code", -1) != 0:
                self.hint.config(text=str(r.json().get("msg", "朗读失败")), fg=C_DANGER)
            else:
                self.hint.config(text="正在朗读…")
                self.app.after(3000, lambda: self.hint.config(text=""))
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="朗读失败: %r" % (exc,), fg=C_DANGER)

    def _interrupt(self):
        sid = self.app._main.sessionid
        if not sid:
            return
        try:
            self.app.sess.post(BASE + "/interrupt_talk",
                               json={"sessionid": sid}, timeout=5)
            self.hint.config(text="已打断")
            self.app.after(1500, lambda: self.hint.config(text=""))
        except Exception:  # noqa: BLE001
            pass


# ── 占位页（尚未开放的模块）─────────────────────────────────────────
class PlaceholderPage(tk.Frame):
    def __init__(self, parent, title, hint):
        super().__init__(parent, bg=C_BG)
        tk.Label(self, text=title, font=(FONT, 20, "bold"), bg=C_BG, fg=C_INK
                 ).pack(anchor="w", padx=S(24), pady=(S(18), S(8)))
        card = Card(self)
        card.pack(fill="both", expand=True, padx=S(24), pady=(S(8), S(16)))
        tk.Label(card, text="暂无", font=(FONT, 15, "bold"), bg=C_CARD, fg=C_MUTED
                 ).pack(pady=(S(60), S(6)))
        tk.Label(card, text=hint, font=(FONT, 12), bg=C_CARD, fg=C_MUTED2, justify="center"
                 ).pack()


# ── 课件页（课件库管理）──────────────────────────────────────────────────
class CoursePage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=C_BG)
        self.app = app
        self.slides = []
        tk.Label(self, text="课件", font=(FONT, 20, "bold"), bg=C_BG, fg=C_INK
                 ).pack(anchor="w", padx=S(24), pady=(S(18), S(2)))
        tk.Label(self, text="把 PPT 或单张图片收进课件库；讲稿里 [PPT:N] 对应第 N 张，数字人讲到时会停下来等你翻页。",
                 font=(FONT, 12), bg=C_BG, fg=C_MUTED2).pack(anchor="w", padx=S(24))
        body = tk.Frame(self, bg=C_BG)
        body.pack(fill="both", expand=True, padx=S(24), pady=(S(14), S(10)))
        left = tk.Frame(body, bg=C_BG, width=S(560))
        left.pack(side="left", fill="both", expand=True)
        card = Card(left)
        card.pack(fill="both", expand=True)
        head = tk.Frame(card, bg=C_CARD)
        head.pack(fill="x", padx=S(14), pady=(S(12), S(6)))
        tk.Label(head, text="课件列表", font=(FONT, 14, "bold"), bg=C_CARD, fg=C_INK).pack(side="left")
        self.count_label = tk.Label(head, text="0 页", font=(FONT, 11), bg=C_CARD, fg=C_MUTED2)
        self.count_label.pack(side="right")
        self.tree = ttk.Treeview(card, columns=("name", "ppt"), show="headings", height=14)
        self.tree.heading("name", text="名称")
        self.tree.heading("ppt", text="来源")
        self.tree.column("name", width=300)
        self.tree.column("ppt", width=150)
        self.tree.pack(fill="both", expand=True, padx=S(10), pady=(S(0), S(10)))
        bar = tk.Frame(card, bg=C_CARD)
        bar.pack(fill="x", padx=S(10), pady=(S(0), S(10)))
        make_btn(bar, "上传 PPT", self._upload_ppt, primary=True, sm=True).pack(side="left")
        make_btn(bar, "上传图片", self._upload_image, sm=True).pack(side="left", padx=(S(8), S(0)))
        make_btn(bar, "重命名", self._rename, sm=True).pack(side="left", padx=(S(6), S(0)))
        make_btn(bar, "删除选中", self._delete, sm=True).pack(side="left", padx=(S(8), S(0)))
        make_btn(bar, "清空", self._clear, sm=True).pack(side="left", padx=(S(8), S(0)))
        self.hint = tk.Label(card, text="", font=(FONT, 11), bg=C_CARD, fg=C_GREEN)
        self.hint.pack(anchor="w", padx=S(10), pady=(S(0), S(8)))
        right = tk.Frame(body, bg=C_BG, width=S(260))
        right.pack(side="left", fill="y", padx=(S(14), S(0)))
        info = Card(right)
        info.pack(fill="x")
        tk.Label(info, text="当前课件页", font=(FONT, 13, "bold"), bg=C_CARD, fg=C_INK
                 ).pack(anchor="w", padx=S(12), pady=(S(12), S(4)))
        self.cur_label = tk.Label(info, text="尚未开始授课", font=(FONT, 12), bg=C_CARD,
                                  fg=C_MUTED2, justify="left", wraplength=S(230))
        self.cur_label.pack(anchor="w", padx=S(12), pady=(S(0), S(12)))
        tk.Label(right, text="提示：连接数字人后，讲稿里写\n[PPT:N]，讲到第 N 页会停\n下来等你切到对应课件页。",
                 font=(FONT, 11), bg=C_CARD_ALT, fg=C_MUTED, justify="left",
                 highlightbackground=C_LINE, highlightthickness=1, padx=S(10), pady=S(8)).pack(pady=(S(12), S(0)))
        self._refresh()
        self.app.after(3000, self._poll_current)

    def _refresh(self):
        try:
            r = self.app.sess.get(BASE + "/api/slides", timeout=8)
            data = r.json().get("data", []) or []
            if not isinstance(data, list):
                data = []
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="读取课件失败: %r" % (exc,), fg=C_DANGER)
            return
        self.slides = data
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(data):
            self.tree.insert("", "end", iid=str(i),
                             values=(s.get("name", "?"), s.get("ppt_name", "") or "-"))
        self.count_label.config(text="%d 页" % len(data))

    def _upload_ppt(self):
        path = filedialog.askopenfilename(
            title="选择 PPT", filetypes=[("PPT", "*.pptx *.ppt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                r = self.app.sess.post(
                    BASE + "/api/materials/upload-ppt",
                    files={"file": (os.path.basename(path), fh,
                                    "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
                    timeout=120)
            data = r.json()
            if data.get("code", -1) != 0:
                self.hint.config(text="上传失败: %s" % data.get("msg", ""), fg=C_DANGER)
                return
            pages = data.get("data", {}).get("pages", [])
            name = os.path.splitext(os.path.basename(path))[0]
            for i, img in enumerate(pages):
                self.app.sess.post(BASE + "/api/slides",
                                   json={"name": "第%d页" % (i + 1), "image": img,
                                         "keywords": [], "ppt_id": name, "ppt_name": name},
                                   timeout=15)
            self.hint.config(text="已导入 %d 页 → %s" % (len(pages), name))
            self._refresh()
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="上传失败: %r" % (exc,), fg=C_DANGER)

    def _upload_image(self):
        path = filedialog.askopenfilename(
            title="选择图片", filetypes=[("图片", "*.png *.jpg *.jpeg *.webp"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                r = self.app.sess.post(BASE + "/api/materials/upload-image",
                                       files={"file": (os.path.basename(path), fh, "image/png")},
                                       timeout=60)
            data = r.json()
            if data.get("code", -1) != 0:
                self.hint.config(text="上传失败: %s" % data.get("msg", ""), fg=C_DANGER)
                return
            img = data.get("data", {}).get("path", "")
            self.app.sess.post(BASE + "/api/slides",
                               json={"name": os.path.splitext(os.path.basename(path))[0],
                                     "image": img, "keywords": [], "ppt_id": "", "ppt_name": ""},
                               timeout=15)
            self.hint.config(text="已添加图片课件")
            self._refresh()
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="上传失败: %r" % (exc,), fg=C_DANGER)

    def _sel(self):
        sel = self.tree.selection()
        if not sel:
            self.hint.config(text="请先在列表选中一行", fg=C_DANGER)
            return None
        return self.slides[int(sel[0])]

    def _rename(self):
        s = self._sel()
        if not s:
            return
        new = simpledialog.askstring("重命名课件", "新名称", initialvalue=s.get("name", ""))
        if not new:
            return
        try:
            r = self.app.sess.put(BASE + "/api/slides/%s" % s.get("id"),
                                  json={"name": new, "keywords": s.get("keywords", [])}, timeout=10)
            if r.json().get("code", -1) != 0:
                self.hint.config(text="重命名失败", fg=C_DANGER)
                return
            self.hint.config(text="已重命名")
            self._refresh()
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="重命名失败: %r" % (exc,), fg=C_DANGER)

    def _delete(self):
        s = self._sel()
        if not s:
            return
        if not messagebox.askyesno("删除课件", "确定删除「%s」？" % s.get("name", "")):
            return
        try:
            r = self.app.sess.delete(BASE + "/api/slides/%s" % s.get("id"), timeout=10)
            if r.json().get("code", -1) != 0:
                self.hint.config(text="删除失败", fg=C_DANGER)
                return
            self.hint.config(text="已删除")
            self._refresh()
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="删除失败: %r" % (exc,), fg=C_DANGER)

    def _clear(self):
        if not messagebox.askyesno("清空课件库", "确定删除全部课件页？"):
            return
        try:
            r = self.app.sess.delete(BASE + "/api/slides", timeout=10)
            self.hint.config(text="已清空 %s 页" % (r.json().get("data", {}).get("deleted", 0)))
            self._refresh()
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="清空失败: %r" % (exc,), fg=C_DANGER)

    def _poll_current(self):
        main = getattr(self.app, "_main", None)
        sid = main.sessionid if main else ""
        if sid:
            try:
                r = self.app.sess.get(BASE + "/api/current_slide",
                                      params={"sessionid": sid}, timeout=5)
                data = r.json().get("data", {})
                slide = data.get("slide")
                if slide:
                    self.cur_label.config(
                        text="第 %s 页 · %s\n（来源：%s）"
                        % (data.get("slide_id", "-"), slide.get("name", ""),
                           slide.get("ppt_name", "") or "单页"))
                else:
                    self.cur_label.config(text="尚未切到课件页")
            except Exception:  # noqa: BLE001
                pass
        self.app.after(3000, self._poll_current)


# ── 提问页（提问式讲课 + 题库 + 记录报告）────────────────────────────
class QuizPage(tk.Frame):
    STAGES = {"asking": "朗读题目中", "awaiting_answer": "等待作答",
              "waiting_slide": "等待翻页", "grading": "判分中",
              "feedback": "反馈中", "teaching": "讲解中", "finished": "已结束"}
    MODES = [("random", "随机选点"), ("sequential", "顺序选点")]

    def __init__(self, parent, app):
        super().__init__(parent, bg=C_BG)
        self.app = app
        self.pairs = []
        tk.Label(self, text="提问", font=(FONT, 20, "bold"), bg=C_BG, fg=C_INK
                 ).pack(anchor="w", padx=S(24), pady=(S(18), S(2)))
        tk.Label(self, text="启动「提问式讲课」：数字人按讲稿讲到提问点停下提问，判分后继续讲；也可先用讲稿生成问答对再启动。",
                 font=(FONT, 12), bg=C_BG, fg=C_MUTED2).pack(anchor="w", padx=S(24))
        body = tk.Frame(self, bg=C_BG)
        body.pack(fill="both", expand=True, padx=S(24), pady=(S(14), S(4)))

        # 左：提问式讲课
        left = tk.Frame(body, bg=C_BG, width=S(460))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        card = Card(left)
        card.pack(fill="y")
        tk.Label(card, text="提问式讲课", font=(FONT, 14, "bold"), bg=C_CARD, fg=C_INK
                 ).pack(anchor="w", padx=S(14), pady=(S(12), S(6)))
        tk.Label(card, text="预设讲稿（可留空：沿用当前会话已投喂的讲稿）", font=(FONT, 11),
                 bg=C_CARD, fg=C_MUTED2).pack(anchor="w", padx=S(14))
        self.text = make_entry(card, height=5, width=44)
        self.text.pack(fill="x", padx=S(14), pady=S(4))
        opts = tk.Frame(card, bg=C_CARD)
        opts.pack(fill="x", padx=S(14), pady=S(4))
        self.count_var = tk.StringVar(value="3")
        self.mode_var = tk.StringVar(value="random")
        self.interval_var = tk.StringVar(value="60")
        tk.Label(opts, text="提问数", font=(FONT, 11), bg=C_CARD, fg=C_MUTED2).pack(side="left")
        make_entry(opts, self.count_var, width=4).pack(side="left", padx=(S(4), S(10)))
        tk.Label(opts, text="模式", font=(FONT, 11), bg=C_CARD, fg=C_MUTED2).pack(side="left")
        RoundedCombo(opts, textvariable=self.mode_var, values=[m[0] for m in self.MODES],
                     font_size=11, width=S(132)).pack(side="left", padx=(S(4), S(10)))
        tk.Label(opts, text="间隔(s)", font=(FONT, 11), bg=C_CARD, fg=C_MUTED2).pack(side="left")
        make_entry(opts, self.interval_var, width=5).pack(side="left", padx=(S(4), S(0)))
        btns = tk.Frame(card, bg=C_CARD)
        btns.pack(fill="x", padx=S(14), pady=(S(8), S(6)))
        make_btn(btns, "生成问答对", self._gen_pairs, sm=True).pack(side="left")
        make_btn(btns, "启动提问", self._start, primary=True, sm=True).pack(side="left", padx=(S(8), S(0)))
        make_btn(btns, "停止提问", self._stop, sm=True).pack(side="left", padx=(S(8), S(0)))
        self.state_label = tk.Label(card, text="未启动（请先连接数字人）", font=(FONT, 12),
                                    bg=C_CARD, fg=C_MUTED2, justify="left",
                                    wraplength=S(430), anchor="w")
        self.state_label.pack(fill="x", padx=S(14), pady=(S(4), S(6)))
        ans = tk.Frame(card, bg=C_CARD)
        ans.pack(fill="x", padx=S(14), pady=(S(0), S(10)))
        self.answer_var = tk.StringVar()
        make_entry(ans, self.answer_var, width=26).pack(side="left")
        make_btn(ans, "提交作答", self._answer, sm=True).pack(side="left", padx=(S(6), S(0)))
        make_btn(ans, "跳过本题", self._skip, sm=True).pack(side="left", padx=(S(6), S(0)))

        # 中：当前题目 / 问答对
        mid = tk.Frame(body, bg=C_BG, width=S(330))
        mid.pack(side="left", fill="y", padx=(S(14), S(0)))
        mid.pack_propagate(False)
        card2 = Card(mid)
        card2.pack(fill="y")
        tk.Label(card2, text="当前题目 / 问答对", font=(FONT, 14, "bold"), bg=C_CARD, fg=C_INK
                 ).pack(anchor="w", padx=S(14), pady=(S(12), S(6)))
        self.q_text = scrolledtext.ScrolledText(card2, height=13, font=(FONT, 11), state="disabled",
                                                wrap="word", relief="flat", bg=C_CARD, fg=C_INK,
                                                highlightthickness=0)
        self.q_text.pack(fill="both", expand=True, padx=S(10), pady=(S(0), S(10)))

        # 右：题库
        right = tk.Frame(body, bg=C_BG, width=S(350))
        right.pack(side="left", fill="y", padx=(S(14), S(0)))
        right.pack_propagate(False)
        card3 = Card(right)
        card3.pack(fill="y")
        head = tk.Frame(card3, bg=C_CARD)
        head.pack(fill="x", padx=S(14), pady=(S(12), S(4)))
        tk.Label(head, text="题库", font=(FONT, 14, "bold"), bg=C_CARD, fg=C_INK).pack(side="left")
        self.bank_count = tk.Label(head, text="0 题", font=(FONT, 11), bg=C_CARD, fg=C_MUTED2)
        self.bank_count.pack(side="right")
        self.bank_list = tk.Listbox(card3, height=7, font=(FONT, 11), relief="flat", bd=0,
                                    bg=C_CARD, fg=C_INK, highlightthickness=0,
                                    selectbackground=C_SEL)
        self.bank_list.pack(fill="x", padx=S(10), pady=(S(0), S(6)))
        add = tk.Frame(card3, bg=C_CARD)
        add.pack(fill="x", padx=S(10), pady=S(2))
        self.q_var = tk.StringVar()
        self.a_var = tk.StringVar()
        make_entry(add, self.q_var, width=30).pack(fill="x")
        make_entry(add, self.a_var, width=30).pack(fill="x", pady=(S(4), S(0)))
        addb = tk.Frame(card3, bg=C_CARD)
        addb.pack(fill="x", padx=S(10), pady=(S(6), S(10)))
        make_btn(addb, "添加题目", self._bank_add, sm=True).pack(side="left")
        make_btn(addb, "删除选中", self._bank_delete, sm=True).pack(side="left", padx=(S(6), S(0)))
        make_btn(addb, "AI 生成 3 题", self._bank_generate, sm=True).pack(side="left", padx=(S(6), S(0)))

        # 底部：记录与报告
        bottom = tk.Frame(self, bg=C_BG)
        bottom.pack(fill="both", expand=True, padx=S(24), pady=(S(6), S(4)))
        card4 = Card(bottom)
        card4.pack(fill="both", expand=True)
        head4 = tk.Frame(card4, bg=C_CARD)
        head4.pack(fill="x", padx=S(14), pady=(S(10), S(4)))
        tk.Label(head4, text="问答记录", font=(FONT, 13, "bold"), bg=C_CARD, fg=C_INK).pack(side="left")
        make_btn(head4, "取本次报告", self._report, sm=True).pack(side="right")
        self.rec_text = scrolledtext.ScrolledText(card4, height=6, font=(FONT, 10), state="disabled",
                                                  wrap="word", relief="flat", bg=C_CARD, fg=C_INK,
                                                  highlightthickness=0)
        self.rec_text.pack(fill="both", expand=True, padx=S(10), pady=(S(0), S(10)))
        self.hint = tk.Label(self, text="", font=(FONT, 11), bg=C_BG, fg=C_GREEN)
        self.hint.pack(anchor="w", padx=S(24), pady=(S(0), S(8)))
        self._refresh_bank()
        self.app.after(1500, self._poll)

    def _sid(self):
        main = getattr(self.app, "_main", None)
        return main.sessionid if main else ""

    def _hint(self, text, ok=True):
        self.hint.config(text=text, fg=C_GREEN if ok else C_DANGER)

    def _start(self):
        sid = self._sid()
        if not sid:
            self._hint("请先在「开播」页连接数字人", False)
            return
        try:
            body = {"sessionid": sid, "count": int(self.count_var.get() or 3),
                    "mode": self.mode_var.get() or "random",
                    "interval": int(self.interval_var.get() or 60)}
            text = self.text.get("1.0", "end").strip()
            if text:
                body["text"] = text
            r = self.app.sess.post(BASE + "/api/quiz/lecture", json=body, timeout=15)
            data = r.json()
            if data.get("code", -1) != 0:
                self._hint("启动失败: %s" % data.get("msg", ""), False)
                return
            self._hint("已启动提问式讲课：共 %s 个提问点" % data.get("data", {}).get("total", 0))
        except Exception as exc:  # noqa: BLE001
            self._hint("启动失败: %r" % (exc,), False)

    def _stop(self):
        sid = self._sid()
        if not sid:
            self._hint("尚未连接会话", False)
            return
        try:
            r = self.app.sess.post(BASE + "/api/quiz/stop",
                                   json={"sessionid": sid}, timeout=10)
            if r.json().get("code", -1) != 0:
                self._hint("停止失败: %s" % r.json().get("msg", ""), False)
                return
            self._hint("已退出提问模式")
        except Exception as exc:  # noqa: BLE001
            self._hint("停止失败: %r" % (exc,), False)

    def _gen_pairs(self):
        text = self.text.get("1.0", "end").strip()
        if not text:
            self._hint("请先在预设讲稿里写内容", False)
            return
        try:
            r = self.app.sess.post(BASE + "/api/quiz/pairs/generate",
                                   json={"text": text, "count": 3}, timeout=60)
            data = r.json()
            if data.get("code", -1) != 0:
                self._hint("生成失败: %s" % data.get("msg", ""), False)
                return
            pairs = data.get("data", {}).get("pairs", [])
            self.pairs = pairs
            self._show_pairs()
            self._hint("已生成 %d 个问答对（启动提问时可直接复用）" % len(pairs))
        except Exception as exc:  # noqa: BLE001
            self._hint("生成失败: %r" % (exc,), False)

    def _show_pairs(self):
        self.q_text.config(state="normal")
        self.q_text.delete("1.0", "end")
        if not self.pairs:
            self.q_text.insert("end", "（暂无内容）")
        else:
            for i, p in enumerate(self.pairs, 1):
                self.q_text.insert("end", "%d. %s\n    参考答案: %s\n\n"
                                   % (i, p.get("question", ""), p.get("reference_answer", "")))
        self.q_text.config(state="disabled")

    def _answer(self):
        sid = self._sid()
        answer = self.answer_var.get().strip()
        if not sid or not answer:
            self._hint("请填写作答内容", False)
            return
        try:
            r = self.app.sess.post(BASE + "/api/quiz/answer",
                                   json={"sessionid": sid, "answer": answer}, timeout=60)
            if r.json().get("code", -1) != 0:
                self._hint("提交失败: %s" % r.json().get("msg", ""), False)
                return
            self.answer_var.set("")
            self._hint("已提交作答，等待判分")
        except Exception as exc:  # noqa: BLE001
            self._hint("提交失败: %r" % (exc,), False)

    def _skip(self):
        sid = self._sid()
        if not sid:
            self._hint("尚未连接会话", False)
            return
        try:
            r = self.app.sess.post(BASE + "/api/quiz/skip",
                                   json={"sessionid": sid}, timeout=10)
            if r.json().get("code", -1) != 0:
                self._hint("跳过失败: %s" % r.json().get("msg", ""), False)
                return
            self._hint("已跳过本题")
        except Exception as exc:  # noqa: BLE001
            self._hint("跳过失败: %r" % (exc,), False)

    def _poll(self):
        sid = self._sid()
        if sid:
            try:
                r = self.app.sess.get(BASE + "/api/quiz/status",
                                      params={"sessionid": sid}, timeout=5)
                st = r.json().get("data", {})
                if st.get("in_quiz"):
                    self.state_label.config(text=self._fmt_status(st), fg=C_INK)
                    q = st.get("question")
                    if q:
                        self.pairs = [{"question": q.get("question", ""),
                                       "reference_answer": q.get("reference_answer", "")}]
                        self._show_pairs()
                else:
                    self.state_label.config(text="未启动提问模式", fg=C_MUTED2)
            except Exception:  # noqa: BLE001
                pass
            try:
                r = self.app.sess.get(BASE + "/api/quiz/records",
                                      params={"sessionid": sid}, timeout=5)
                records = r.json().get("data", {}).get("records", []) or []
                self.rec_text.config(state="normal")
                self.rec_text.delete("1.0", "end")
                if not records:
                    self.rec_text.insert("end", "（暂无问答记录）")
                for i, rec in enumerate(reversed(records), 1):
                    verdict = rec.get("verdict") or ("未作答" if rec.get("unanswered") else "已作答")
                    score = rec.get("verdict") if rec.get("verdict") not in (None, True, False) else ""
                    self.rec_text.insert(
                        "end", "%d. %s | 回答: %s\n   判定: %s%s\n\n"
                        % (i, rec.get("question", "题%s" % rec.get("question_id", "?")),
                           rec.get("answer", "-"), verdict,
                           (" · 反馈: %s" % rec.get("feedback")) if rec.get("feedback") else ""))
                self.rec_text.config(state="disabled")
            except Exception:  # noqa: BLE001
                pass
        self.app.after(1500, self._poll)

    def _fmt_status(self, st):
        stage = self.STAGES.get(st.get("stage"), str(st.get("stage", "?")))
        parts = ["阶段：%s" % stage,
                 "进度：%s / %s" % (st.get("completed", 0), st.get("total", 0))]
        q = st.get("question")
        if q:
            parts.append("当前题目：%s" % q.get("question", ""))
        if st.get("waiting_slide") is not None:
            parts.append("等待翻到第 %s 页（已等 %s 秒）"
                         % (st["waiting_slide"], st.get("waiting_seconds", 0)))
        summary = st.get("summary")
        if summary:
            parts.append("评分：%s" % (summary if isinstance(summary, str) else json.dumps(summary, ensure_ascii=False)))
        return "\n".join(parts)

    # ── 题库 ──
    def _refresh_bank(self):
        try:
            r = self.app.sess.get(BASE + "/api/quiz/bank", timeout=8)
            qs = r.json().get("data", {}).get("questions", []) or []
        except Exception as exc:  # noqa: BLE001
            self._hint("读取题库失败: %r" % (exc,), False)
            return
        self.bank_list.delete(0, "end")
        for item in qs:
            self.bank_list.insert("end", "%s — %s" % (item.get("id", "?"),
                                                      str(item.get("question", ""))[:36]))
        self.bank_count.config(text="%d 题" % len(qs))

    def _bank_add(self):
        q = self.q_var.get().strip()
        a = self.a_var.get().strip()
        if not q or not a:
            self._hint("请填写问题和参考答案", False)
            return
        try:
            r = self.app.sess.post(BASE + "/api/quiz/bank",
                                   json={"question": q, "reference_answer": a,
                                         "rubric": [], "source": "manual"}, timeout=10)
            if r.json().get("code", -1) != 0:
                self._hint("添加失败: %s" % r.json().get("msg", ""), False)
                return
            self.q_var.set("")
            self.a_var.set("")
            self._hint("已加入题库")
            self._refresh_bank()
        except Exception as exc:  # noqa: BLE001
            self._hint("添加失败: %r" % (exc,), False)

    def _bank_delete(self):
        sel = self.bank_list.curselection()
        if not sel:
            self._hint("请先在题库选中一行", False)
            return
        line = self.bank_list.get(sel[0])
        qid = line.split(" — ", 1)[0]
        if not messagebox.askyesno("删除题目", "确定从题库删除这道题？"):
            return
        try:
            r = self.app.sess.delete(BASE + "/api/quiz/bank/%s" % qid, timeout=10)
            if r.json().get("code", -1) != 0:
                self._hint("删除失败", False)
                return
            self._hint("已删除")
            self._refresh_bank()
        except Exception as exc:  # noqa: BLE001
            self._hint("删除失败: %r" % (exc,), False)

    def _bank_generate(self):
        text = self.text.get("1.0", "end").strip()
        if not text:
            self._hint("请先在预设讲稿里写内容（作为出题素材）", False)
            return
        try:
            r = self.app.sess.post(BASE + "/api/quiz/bank/generate",
                                   json={"slide_text": text, "count": 3, "persist": True},
                                   timeout=60)
            data = r.json()
            if data.get("code", -1) != 0:
                self._hint("生成失败: %s" % data.get("msg", ""), False)
                return
            self._hint("已生成 %d 题并写入题库" % len(data.get("data", {}).get("questions", [])))
            self._refresh_bank()
        except Exception as exc:  # noqa: BLE001
            self._hint("生成失败: %r" % (exc,), False)

    def _report(self):
        sid = self._sid()
        if not sid:
            self._hint("尚未连接会话", False)
            return
        try:
            r = self.app.sess.get(BASE + "/api/quiz/report",
                                  params={"sessionid": sid}, timeout=10)
            data = r.json()
            if data.get("code", -1) != 0:
                self._hint("取报告失败: %s" % data.get("msg", ""), False)
                return
            text = data.get("data", {}).get("text") or "（暂无评分数据）"
            self.rec_text.config(state="normal")
            self.rec_text.delete("1.0", "end")
            self.rec_text.insert("end", "── 本次课堂报告 ──\n" + text)
            self.rec_text.config(state="disabled")
            self._hint("已取回报告")
        except Exception as exc:  # noqa: BLE001
            self._hint("取报告失败: %r" % (exc,), False)


# ── 形象生成页（素材 → 生成任务）─────────────────────────────────────
class GenAvatarPage(tk.Frame):
    STATUS = {"pending": "排队中", "running": "进行中", "completed": "已完成", "failed": "失败"}

    def __init__(self, parent, app, embedded=False):
        super().__init__(parent, bg=C_BG)
        self.app = app
        self.file_path = ""
        if not embedded:
            tk.Label(self, text="形象生成", font=(FONT, 20, "bold"), bg=C_BG, fg=C_INK
                     ).pack(anchor="w", padx=S(24), pady=(S(18), S(2)))
            tk.Label(self, text="上传一段人物视频（或图片），后台生成新的数字人形象；任务完成后可在「形象」页选中应用。",
                     font=(FONT, 12), bg=C_BG, fg=C_MUTED2).pack(anchor="w", padx=S(24))
        body = tk.Frame(self, bg=C_BG)
        body.pack(fill="both", expand=True, padx=S(24),
                  pady=(S(12) if embedded else S(14), S(10)))
        left = tk.Frame(body, bg=C_BG, width=S(440))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        card = Card(left)
        card.pack(fill="y")
        tk.Label(card, text="创建生成任务", font=(FONT, 14, "bold"), bg=C_CARD, fg=C_INK
                 ).pack(anchor="w", padx=S(14), pady=(S(12), S(8)))
        self.avatar_var = tk.StringVar(value="my_avatar_01")
        self.model_var = tk.StringVar(value="musetalk_ke")
        for label, var, w in (("形象 ID", self.avatar_var, 18), ("模型", self.model_var, 14)):
            row = tk.Frame(card, bg=C_CARD)
            row.pack(fill="x", padx=S(14), pady=S(4))
            tk.Label(row, text=label, font=(FONT, 12), bg=C_CARD, fg=C_MUTED2,
                     width=9, anchor="w").pack(side="left")
            make_entry(row, var, width=w).pack(side="left", fill="x", expand=True)
        pick = tk.Frame(card, bg=C_CARD)
        pick.pack(fill="x", padx=S(14), pady=(S(8), S(4)))
        make_btn(pick, "选择视频/图片素材", self._pick, sm=True).pack(side="left")
        self.file_label = tk.Label(pick, text="未选择", font=(FONT, 11), bg=C_CARD, fg=C_MUTED2)
        self.file_label.pack(side="left", padx=(S(8), S(0)))
        make_btn(card, "提交生成任务", self._submit, primary=True).pack(padx=S(14), pady=(S(8), S(4)))
        tk.Label(card, text="生成需要几分钟，完成后可在左侧任务列表看到结果；新形象落在 data/avatars 目录。",
                 font=(FONT, 11), bg=C_CARD_ALT, fg=C_MUTED, justify="left",
                 highlightbackground=C_LINE, highlightthickness=1, padx=S(10), pady=S(8)).pack(
            fill="x", padx=S(14), pady=(S(4), S(14)))

        right = tk.Frame(body, bg=C_BG)
        right.pack(side="left", fill="both", expand=True, padx=(S(14), S(0)))
        card2 = Card(right)
        card2.pack(fill="both", expand=True)
        head = tk.Frame(card2, bg=C_CARD)
        head.pack(fill="x", padx=S(14), pady=(S(12), S(6)))
        tk.Label(head, text="生成任务", font=(FONT, 14, "bold"), bg=C_CARD, fg=C_INK).pack(side="left")
        make_btn(head, "刷新", self._refresh, sm=True).pack(side="right")
        self.tree = ttk.Treeview(card2, columns=("id", "status", "progress", "avatar", "cost"),
                                 show="headings", height=10)
        for col, text, w in (("id", "任务ID", 250), ("status", "状态", 90),
                             ("progress", "进度", 90), ("avatar", "形象", 130),
                             ("cost", "耗时(s)", 80)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=S(10), pady=(S(0), S(10)))
        bar = tk.Frame(card2, bg=C_CARD)
        bar.pack(fill="x", padx=S(10), pady=(S(0), S(6)))
        make_btn(bar, "删除选中任务", self._delete, sm=True).pack(side="left")
        make_btn(bar, "预览形象", self._preview_avatar, sm=True).pack(side="left", padx=(S(6), S(0)))
        make_btn(bar, "预览动作", self._preview_gesture, sm=True).pack(side="left", padx=(S(6), S(0)))

        prev = tk.Frame(card2, bg=C_CARD_ALT)
        prev.pack(fill="x", padx=S(10), pady=(S(0), S(6)))
        tk.Label(prev, text="形象与动作预览", font=(FONT, 11, "bold"), bg=C_CARD_ALT,
                 fg=C_INK).pack(anchor="w", padx=S(10), pady=(S(8), S(0)))
        self.preview_img = tk.Label(prev, text="选中已完成的任务，点「预览形象」查看生成效果",
                                    font=(FONT, 11), bg=C_CARD_ALT, fg=C_MUTED2)
        self.preview_img.pack(anchor="w", padx=S(10), pady=S(6))
        self.preview_actions = tk.Frame(prev, bg=C_CARD_ALT)
        self.preview_actions.pack(fill="x", padx=S(10), pady=(S(0), S(8)))

        self.hint = tk.Label(card2, text="", font=(FONT, 11), bg=C_CARD, fg=C_GREEN)
        self.hint.pack(anchor="w", padx=S(10), pady=(S(0), S(8)))
        self._refresh()
        self.app.after(2000, self._poll)

    def _pick(self):
        path = filedialog.askopenfilename(
            title="选择视频或图片",
            filetypes=[("视频/图片", "*.mp4 *.mov *.mkv *.png *.jpg *.jpeg *.webp"),
                       ("视频", "*.mp4 *.mov *.mkv"), ("图片", "*.png *.jpg *.jpeg *.webp")])
        if path:
            self.file_path = path
            self.file_label.config(text=os.path.basename(path))

    def _submit(self):
        if not self.file_path:
            self.hint.config(text="请先选择视频/图片素材", fg=C_DANGER)
            return
        avatar_id = self.avatar_var.get().strip()
        model = self.model_var.get().strip()
        if not avatar_id or not model:
            self.hint.config(text="请填写形象 ID 和模型", fg=C_DANGER)
            return
        try:
            with open(self.file_path, "rb") as fh:
                r = self.app.sess.post(
                    BASE + "/api/avatar/task",
                    data={"model": model, "avatar_id": avatar_id},
                    files={"video_file": (os.path.basename(self.file_path), fh)},
                    timeout=30)
            data = r.json()
            if data.get("code", -1) != 0:
                self.hint.config(text="提交失败: %s" % data.get("msg", ""), fg=C_DANGER)
                return
            self.hint.config(text="任务已提交：%s" % data.get("data", {}).get("task_id", ""))
            self._refresh()
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="提交失败: %r" % (exc,), fg=C_DANGER)

    def _refresh(self):
        try:
            r = self.app.sess.get(BASE + "/api/avatar/tasks", timeout=8)
            tasks = r.json().get("data", {}).get("tasks", []) or []
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="读取任务失败: %r" % (exc,), fg=C_DANGER)
            return
        self.tree.delete(*self.tree.get_children())
        for t in tasks:
            st = self.STATUS.get(t.get("status", ""), t.get("status", "?"))
            self.tree.insert("", "end",
                             values=(t.get("task_id", "?"), st, "%s%%" % t.get("progress", 0),
                                     t.get("avatar_id", "-"),
                                     "%d" % int(t.get("duration") or 0)))

    def _poll(self):
        self._refresh()
        self.app.after(2000, self._poll)

    def _delete(self):
        sel = self.tree.selection()
        if not sel:
            self.hint.config(text="请先选中一个任务", fg=C_DANGER)
            return
        task_id = self.tree.item(sel[0], "values")[0]
        if not messagebox.askyesno("删除任务", "确定删除任务 %s？" % task_id):
            return
        try:
            r = self.app.sess.delete(BASE + "/api/avatar/task/%s" % task_id, timeout=10)
            if r.json().get("code", -1) != 0:
                self.hint.config(text="删除失败", fg=C_DANGER)
                return
            self.hint.config(text="已删除任务")
            self._refresh()
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="删除失败: %r" % (exc,), fg=C_DANGER)

    def _selected_avatar_id(self):
        sel = self.tree.selection()
        if not sel:
            self.hint.config(text="请先在任务列表选中一个任务", fg=C_DANGER)
            return None
        return self.tree.item(sel[0], "values")[3]  # 第 4 列=形象 ID

    def _preview_avatar(self):
        avatar_id = self._selected_avatar_id()
        if not avatar_id:
            return
        try:
            r = self.app.sess.get(BASE + "/api/avatars", timeout=8)
            avatars = r.json().get("data", []) or []
            hit = next((a for a in avatars if a.get("id") == avatar_id), None)
            if not hit or not hit.get("thumbnail"):
                self.hint.config(text="该任务暂无可预览的形象素材", fg=C_DANGER)
                return
            rr = self.app.sess.get(BASE + hit["thumbnail"], timeout=15)
            if rr.status_code != 200:
                self.hint.config(text="缩略图获取失败（%d）" % rr.status_code, fg=C_DANGER)
                return
            from PIL import Image, ImageTk
            import io as _io
            img = Image.open(_io.BytesIO(rr.content)).convert("RGB")
            img.thumbnail((320, 220))
            self._preview_photo = ImageTk.PhotoImage(img)
            self.preview_img.config(image=self._preview_photo, text="", bg=C_CARD)
            self.hint.config(text="已加载形象 %s 预览（效果以最终直播画面为准）" % avatar_id, fg=C_GREEN)
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="预览失败: %r" % (exc,), fg=C_DANGER)

    def _preview_gesture(self):
        avatar_id = self._selected_avatar_id()
        if not avatar_id:
            return
        try:
            r = self.app.sess.get(BASE + "/api/avatars/%s/gestures" % avatar_id, timeout=8)
            gestures = (r.json().get("data") or {}).get("gestures", []) or []
            if not gestures:
                self.hint.config(text="该形象暂没有可预览的动作", fg=C_DANGER)
                return
            win = tk.Toplevel(self)
            win.title("动作预览")
            win.configure(bg=C_BG)
            tk.Label(win, text="形象 %s 的动作（点击播放预览动图）：" % avatar_id,
                     font=(FONT, 12), bg=C_BG, fg=C_INK).pack(padx=S(18), pady=(S(14), S(8)))
            for g in gestures:
                gid = g.get("id") or ""
                label = g.get("label") or gid
                make_btn(win, "预览：%s" % label,
                         lambda gid=gid: self._open_gesture_preview(avatar_id, gid),
                         sm=True).pack(fill="x", padx=S(18), pady=S(3))
            make_btn(win, "关闭", win.destroy, sm=True).pack(pady=(S(10), S(14)))
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="动作读取失败: %r" % (exc,), fg=C_DANGER)

    def _open_gesture_preview(self, avatar_id, gesture_id):
        try:
            import tempfile
            url = BASE + "/api/avatars/%s/gestures/%s/preview" % (avatar_id, gesture_id)
            rr = self.app.sess.get(url, timeout=30)
            if rr.status_code != 200:
                messagebox.showinfo("预览失败", rr.text[:150])
                return
            fd, path = tempfile.mkstemp(suffix=".webp")
            with os.fdopen(fd, "wb") as fh:
                fh.write(rr.content)
            os.startfile(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showinfo("预览失败", "%r" % (exc,))


# ── 通用滚动容器 ────────────────────────────────────────────────
class ScrollFrame(tk.Frame):
    """可纵向滚动的容器：Canvas + 内嵌 Frame + 滚轮绑定。

    tkinter 没有原生滚动容器，而课程列表长度不定（课程下还要展开文件行），
    所以自己包一层。要点是内嵌 Frame 的宽度必须跟着 Canvas 走，否则内容
    会被裁在左边一小条里。
    """

    def __init__(self, parent, bg=None):
        super().__init__(parent, bg=bg or _widget_bg(parent))
        self.canvas = tk.Canvas(self, bg=self["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.bar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.inner = tk.Frame(self.canvas, bg=self["bg"])
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        # 滚轮：鼠标在滚动区内（含子控件）都生效
        for w in (self.canvas, self.inner):
            w.bind("<MouseWheel>", self._wheel)
        self.inner.bind("<Enter>", lambda e: self._bind_children())
        self._bound = False

    def _bind_children(self):
        """滚动内容是在运行时重建的，新控件没法在构造时绑滚轮——
        每次鼠标进入就把子控件递归绑一遍（只做一次，之后不再重复）。"""
        if self._bound:
            return
        self._bound = True

        def walk(w):
            for c in w.winfo_children():
                try:
                    c.bind("<MouseWheel>", self._wheel)
                except Exception:  # noqa: BLE001
                    pass
                walk(c)
        walk(self.inner)

    def _on_inner(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event=None):
        self.canvas.itemconfig(self._win, width=self.canvas.winfo_width())
        self._bound = False          # 宽度变了内容会重排，重新绑一次

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    def clear(self):
        for c in list(self.inner.winfo_children()):
            c.destroy()
        self.canvas.yview_moveto(0.0)


class KnowledgePage(tk.Frame):
    """知识库管理：先是一屏课程卡片，点进去才是这个库里的文档。

    之前是"每个课程一大栏、栏里直接铺文件"的一页式：课程一多要滚很久，而且
    "看看某个库里有什么"得先在一堆栏里找到它。拆成两级之后，列表页只回答
    "有哪些库、每个库多少文档"，详情页才处理预览 / 上传 / 删除。

    文档的解析与切片发生在服务端（上传接口里），这里只负责发请求和看结果。
    """

    STATUS_COLOR = {"indexed": C_GREEN, "error": C_DANGER, "failed": C_DANGER}
    STATUS_TEXT = {"indexed": "已索引", "pending": "待索引", "indexing": "索引中",
                   "error": "失败", "failed": "失败"}

    DOC_COLUMNS = [("title", "文档名称", 200, True, "w"),
                   ("type_text", "类型", 70, False, "center"),
                   ("size_text", "大小", 80, False, "center"),
                   ("chunk_count", "切片", 60, False, "center"),
                   ("status_text", "状态", 80, False, "center")]

    def __init__(self, parent, app):
        super().__init__(parent, bg=C_BG)
        self.app = app
        self.courses = []
        self._current = None        # 详情页正在看的那门课（course_id）
        self._busy = False
        self._grid_cols = 0
        self._cards = []
        self._build()

    # ── 骨架 ──
    def _build(self):
        head = tk.Frame(self, bg=C_BG)
        head.pack(fill="x", padx=S(24), pady=(S(18), S(2)))
        tk.Label(head, text="知识库管理", font=(FONT, 20, "bold"), bg=C_BG, fg=C_INK
                 ).pack(side="left")
        self.state_dot = tk.Label(head, text="●", font=(FONT, 11), bg=C_BG, fg=C_DOT_OFF)
        self.state_dot.pack(side="left", padx=(S(10), S(0)))
        self.state_text = tk.Label(head, text="检测中…", font=(FONT, 11), bg=C_BG, fg=C_MUTED2)
        self.state_text.pack(side="left")
        make_btn(head, "刷新", self._load, sm=True).pack(side="right")
        make_btn(head, "新建知识库", self._new_course, sm=True, primary=True
                 ).pack(side="right", padx=(S(0), S(8)))

        self.desc = tk.Label(self, text="", font=(FONT, 12), bg=C_BG, fg=C_MUTED2,
                             wraplength=S(900), justify="left", anchor="w")
        self.desc.pack(fill="x", padx=S(24))

        self.body = tk.Frame(self, bg=C_BG)
        self.body.pack(fill="both", expand=True, padx=S(24), pady=(S(12), S(4)))

        self.hint = tk.Label(self, text="", font=(FONT, 11), bg=C_BG, fg=C_GREEN,
                             anchor="w", wraplength=S(900), justify="left")
        self.hint.pack(fill="x", padx=S(24), pady=(S(0), S(10)))

    def on_show(self):
        # 每次进页面都重取：在别处（比如讲解页）传进去的文件，切回来要能看到
        self._load()

    # ── 数据 ──
    def _api(self, method, tail, **kw):
        return self.app.sess.request(method, BASE + "/rag/admin/" + tail.lstrip("/"), **kw)

    def _load(self):
        if self._busy:
            return
        self._busy = True
        try:
            r = self.app.sess.get(BASE + "/rag/admin/courses", timeout=10)
            data = r.json()
            courses = (data.get("data") or []) if data.get("code", -1) == 0 else []
            # 接口异常时可能吐出字符串之类的非预期结构，过滤掉——
            # 否则整页渲染会在 .get() 上崩掉，用户只看到一块空白。
            courses = [c for c in courses if isinstance(c, dict)]
            self._set_online(True)
        except Exception as exc:  # noqa: BLE001
            self._set_online(False)
            self.courses = []
            self._render()
            self.hint.config(text="读取知识库失败（服务未运行？）: %r" % (exc,), fg=C_DANGER)
            return
        finally:
            self._busy = False

        self.courses = courses
        self.hint.config(text="", fg=C_GREEN)
        # 详情页只认 course_id：课程被删掉或改名后，按 id 找不到了就退回列表
        if self._current and not self._find(self._current):
            self._current = None
        self._render()
        if self._current is not None:
            self._refresh_docs()

    def _find(self, cid):
        for c in self.courses:
            if c.get("course_id") == cid:
                return c
        return None

    def _set_online(self, ok):
        self.state_dot.config(fg=C_GREEN if ok else C_DANGER)
        self.state_text.config(text="知识库服务已连接" if ok else "知识库服务未连接",
                               fg=C_GREEN if ok else C_DANGER)

    def _docs(self, course):
        cid = course.get("course_id") or ""
        try:
            rd = self.app.sess.get(BASE + "/rag/admin/courses/%s/documents" % cid, timeout=10)
            dd = rd.json()
            docs = (dd.get("data") or []) if dd.get("code", -1) == 0 else []
        except Exception:  # noqa: BLE001 - 单个库取不到不该带崩整页
            docs = []
        docs = [d for d in docs if isinstance(d, dict)]
        course["_docs"] = docs
        return docs

    def _refresh_docs(self):
        course = self._find(self._current)
        if course is None:
            return
        self._docs(course)
        self.table.set_rows([self._doc_row(d) for d in course.get("_docs") or []])
        self._sync_detail_head(course)

    def _status(self, key):
        return self.STATUS_TEXT.get(key or "", key or "未知")

    def _status_fg(self, key):
        return self.STATUS_COLOR.get(key or "", C_MUTED2)

    def _size_text(self, n):
        try:
            n = float(n or 0)
        except (TypeError, ValueError):
            return "-"
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return "%.0f %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
            n /= 1024.0
        return "-"

    # ── 视图切换 ──
    def _render(self):
        for w in self.body.winfo_children():
            w.destroy()
        if self._current is None:
            self._render_grid()
        else:
            self._render_detail()

    def _clear_hint(self):
        self.hint.config(text="", fg=C_GREEN)

    # ── 列表：课程卡片网格 ──
    def _render_grid(self):
        self.desc.config(text="课程文件会切片入库，数字人遇到不会的内容先检索这里。"
                              "点一个知识库，进去看它下面的文档。")
        if not self.courses:
            empty = Card(self.body)
            empty.pack(fill="x", pady=S(4))
            tk.Label(empty, text="还没有知识库，点右上角「新建知识库」开始。",
                     font=(FONT, 12), bg=C_CARD, fg=C_MUTED2).pack(padx=S(16), pady=S(22))
            return
        self._grid = tk.Frame(self.body, bg=C_BG)
        self._grid.pack(fill="both", expand=True)
        self._grid_cols = 0
        self._grid.bind("<Configure>", lambda _e: self._layout_grid())
        self._layout_grid()

    def _layout_grid(self):
        """按宽度决定一行放几张卡。

        Configure 会在拖动过程中连发几十次，所以列数没变就直接返回——
        否则每次都重建卡片，鼠标一拖界面就闪。
        """
        width = max(S(320), self._grid.winfo_width())
        cols = max(2, min(4, int(width // S(230))))
        if cols == self._grid_cols:
            return
        self._grid_cols = cols
        for w in list(self._grid.winfo_children()):
            w.destroy()
        for i, course in enumerate(self.courses):
            cell = self._course_card(self._grid, course)
            cell.grid(row=i // cols, column=i % cols, sticky="nsew",
                      padx=(0, S(14)), pady=(0, S(14)))
        for col in range(cols):
            self._grid.columnconfigure(col, weight=1)

    def _course_card(self, parent, course):
        cid = course.get("course_id") or ""
        card = Card(parent, cursor="hand2")
        inner = tk.Frame(card, bg=C_CARD, cursor="hand2")
        inner.pack(fill="both", expand=True, padx=S(16), pady=S(14))

        tk.Label(inner, text=course.get("title") or cid, font=(FONT, 14, "bold"),
                 bg=C_CARD, fg=C_INK, anchor="w", cursor="hand2",
                 wraplength=S(220), justify="left").pack(fill="x")
        if course.get("description"):
            tk.Label(inner, text=course["description"], font=(FONT, 10), bg=C_CARD,
                     fg=C_MUTED, anchor="w", cursor="hand2", wraplength=S(220),
                     justify="left").pack(fill="x", pady=(S(2), S(0)))

        tk.Label(inner, text="%d 个文档" % int(course.get("document_count") or 0),
                 font=(FONT, 20, "bold"), bg=C_CARD, fg=C_INK, anchor="w",
                 cursor="hand2").pack(fill="x", pady=(S(10), S(2)))

        foot = tk.Frame(inner, bg=C_CARD, cursor="hand2")
        foot.pack(fill="x")
        idx = course.get("index_status") or "pending"
        dot = tk.Label(foot, text="●", font=(FONT, 10), bg=C_CARD,
                       fg=self._status_fg(idx), cursor="hand2")
        dot.pack(side="left")
        tk.Label(foot, text="%s · %s 切片" % (self._status(idx),
                                              course.get("chunk_count", 0)),
                 font=(FONT, 10), bg=C_CARD, fg=C_MUTED2, cursor="hand2"
                 ).pack(side="left", padx=(S(4), S(0)))

        self._bind_click(card, lambda _e, c=course: self._open(c), inner)
        return card

    def _bind_click(self, widget, handler, inner=None):
        """整张卡都可点：光标和点击绑到卡片以及卡里的每个子控件上。

        Tk 的事件不会从父框架"穿透"到子标签——只绑卡片的话，点在文字上没反应，
        用户会以为卡片是坏的。
        """
        widget.configure(cursor="hand2")

        def walk(w):
            try:
                w.configure(cursor="hand2")
            except Exception:  # noqa: BLE001 - 个别控件不支持 cursor
                pass
            w.bind("<Button-1>", handler)
            w.bind("<Enter>", lambda _e: self._hover(widget, inner, True))
            w.bind("<Leave>", lambda _e: self._hover(widget, inner, False))
            for child in w.winfo_children():
                walk(child)

        walk(widget)

    def _hover(self, card, inner, on):
        """悬停换底色，让"这整块能点"看得出来。"""
        fill = C_CARD_ALT if on else C_CARD
        card._fill = fill
        card._last_size = (0, 0)
        card._paint()
        if inner is not None:
            self._recolor(inner, fill)

    @staticmethod
    def _recolor(widget, fill):
        try:
            widget.configure(bg=fill)
        except Exception:  # noqa: BLE001
            pass
        for child in widget.winfo_children():
            KnowledgePage._recolor(child, fill)

    def _open(self, course):
        self._current = course.get("course_id")
        self._clear_hint()
        # 文档列表是进了详情才取的：列表页没必要为每个库都拉一遍
        self._docs(course)
        self._render()

    def _back(self):
        self._current = None
        self._clear_hint()
        self._render()

    # ── 详情：单个知识库的文档 ──
    def _render_detail(self):
        course = self._find(self._current)
        if course is None:
            self._current = None
            self._render_grid()
            return
        self.desc.config(text="")

        top = tk.Frame(self.body, bg=C_BG)
        top.pack(fill="x")
        make_btn(top, "← 返回", self._back, sm=True).pack(side="left")
        self.detail_title = tk.Label(top, text=course.get("title") or self._current,
                                     font=(FONT, 16, "bold"), bg=C_BG, fg=C_INK)
        self.detail_title.pack(side="left", padx=(S(10), S(0)))
        self.detail_meta = tk.Label(top, text="", font=(FONT, 11), bg=C_BG, fg=C_MUTED2)
        self.detail_meta.pack(side="left", padx=(S(10), S(0)))

        card = Card(self.body)
        card.pack(fill="both", expand=True, pady=(S(10), S(0)))

        bar = tk.Frame(card, bg=C_CARD)
        bar.pack(fill="x", padx=S(20), pady=(S(14), S(10)))
        make_btn(bar, "上传文档", lambda: self._upload(course), sm=True,
                 primary=True).pack(side="left")
        make_btn(bar, "重建索引", lambda: self._reindex_course(course), sm=True
                 ).pack(side="left", padx=(S(8), S(0)))
        make_btn(bar, "重命名", lambda: self._rename_course(course), sm=True
                 ).pack(side="left", padx=(S(8), S(0)))
        make_btn(bar, "删除知识库", lambda: self._delete_course(course), sm=True,
                 fg=C_DANGER).pack(side="left", padx=(S(8), S(0)))

        self.table = DataTable(card, self.DOC_COLUMNS,
                               actions=[("预览", self._preview_doc),
                                        ("下载", self._download_doc),
                                        ("重建", self._reindex_doc),
                                        ("删除", self._delete_doc)],
                               page_size=20, height=12,
                               empty_text="还没有文档，点上方「上传文档」添加")
        self.table.pack(fill="both", expand=True, padx=S(20), pady=(0, S(12)))

        self._sync_detail_head(course)
        self.table.set_rows([self._doc_row(d) for d in course.get("_docs") or []])

    def _sync_detail_head(self, course):
        idx = course.get("index_status") or "pending"
        self.detail_meta.config(
            text="%s · %s 个文档 · %s 切片" % (self._status(idx),
                                               course.get("document_count", 0),
                                               course.get("chunk_count", 0)))

    def _doc_row(self, doc):
        status = doc.get("index_status") or "pending"
        return {"id": doc.get("document_id") or "",
                "document_id": doc.get("document_id") or "",
                "title": doc.get("title") or doc.get("file_name") or "未命名",
                "type_text": (doc.get("source_type") or "-").upper(),
                "size_text": self._size_text(doc.get("size")),
                "chunk_count": doc.get("chunk_count", 0),
                "status": status,
                "status_text": self._status(status),
                "file_name": doc.get("file_name") or "",
                "_raw": doc}

    # ── 操作 ──
    def _new_course(self):
        cid = simpledialog.askstring("新建知识库",
                                     "知识库 ID（字母/数字/下划线，如 python-base）：",
                                     parent=self)
        if not cid or not cid.strip():
            return
        cid = cid.strip()
        title = simpledialog.askstring("新建知识库", "知识库名称：", parent=self,
                                       initialvalue=cid) or cid
        try:
            r = self._api("post", "courses",
                          json={"course_id": cid, "title": title.strip() or cid}, timeout=15)
            data = r.json()
            if data.get("code", -1) != 0:
                self.hint.config(text="创建失败: %s" % data.get("msg", ""), fg=C_DANGER)
                return
            self.hint.config(text="知识库「%s」已创建，可以上传文档了" % title, fg=C_GREEN)
            self._load()
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="创建失败: %r" % (exc,), fg=C_DANGER)

    def _upload(self, course):
        cid = course.get("course_id") or ""
        path = filedialog.askopenfilename(
            title="选择要加入知识库的文档",
            filetypes=[("课程文档", "*.pdf *.docx *.doc *.txt *.md *.pptx *.xlsx *.csv"),
                       ("所有文件", "*.*")])
        if not path:
            return
        self.hint.config(text="正在上传并切片「%s」…（大文档可能需要几分钟）" %
                         os.path.basename(path), fg=C_MUTED2)
        self.update_idletasks()

        def work():
            try:
                with open(path, "rb") as fh:
                    files = {"file": (os.path.basename(path), fh)}
                    r = self._api("post", "courses/%s/documents/upload" % cid,
                                  files=files, timeout=600)
                data = r.json()
                ok = data.get("code", -1) == 0
                msg = "已加入知识库，切片完成" if ok else ("失败: %s" % data.get("msg", ""))
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, "上传失败: %r" % (exc,)
            self.app.after(0, lambda: (
                self.hint.config(text=msg, fg=C_GREEN if ok else C_DANGER),
                self._load()))

        self._current = cid
        threading.Thread(target=work, daemon=True).start()

    def _rename_course(self, course):
        cid = course.get("course_id") or ""
        new = simpledialog.askstring("重命名知识库", "新的知识库名称：", parent=self,
                                     initialvalue=course.get("title") or cid)
        if not new or not new.strip():
            return
        try:
            r = self._api("put", "courses/%s" % cid, json={"title": new.strip()}, timeout=15)
            if r.json().get("code", -1) != 0:
                self.hint.config(text="重命名失败", fg=C_DANGER)
                return
            self.hint.config(text="已重命名", fg=C_GREEN)
            self._load()
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="重命名失败: %r" % (exc,), fg=C_DANGER)

    def _delete_course(self, course):
        title = course.get("title") or course.get("course_id")
        if not messagebox.askyesno("删除知识库",
                                   "确定删除知识库「%s」？\n"
                                   "它下面的 %s 个文档和已生成的切片会一起删除，且不可恢复。"
                                   % (title, course.get("document_count", 0))):
            return
        try:
            r = self._api("delete", "courses/%s" % (course.get("course_id") or ""), timeout=30)
            if r.json().get("code", -1) != 0:
                self.hint.config(text="删除失败", fg=C_DANGER)
                return
            self._current = None
            self.hint.config(text="知识库「%s」已删除" % title, fg=C_GREEN)
            self._load()
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="删除失败: %r" % (exc,), fg=C_DANGER)

    def _delete_doc(self, row):
        doc = row.get("_raw") or row
        title = doc.get("title") or doc.get("file_name")
        if not messagebox.askyesno("删除文档", "确定从知识库中删除「%s」？" % title,
                                   parent=self):
            return
        try:
            r = self._api("delete", "documents/%s" % doc.get("document_id"), timeout=30)
            if r.json().get("code", -1) != 0:
                self.hint.config(text="删除失败", fg=C_DANGER)
                return
            self.hint.config(text="已删除「%s」" % title, fg=C_GREEN)
            self._load()
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="删除失败: %r" % (exc,), fg=C_DANGER)

    def _reindex_course(self, course):
        self._fire("post", "courses/%s/reindex" % course.get("course_id"),
                   "正在重建「%s」的索引…" % (course.get("title") or ""))

    def _reindex_doc(self, row):
        doc = row.get("_raw") or row
        self._fire("post", "documents/%s/reindex" % doc.get("document_id"),
                   "正在重建「%s」的索引…" % (doc.get("title") or ""))

    def _fire(self, method, tail, busy_text):
        self.hint.config(text=busy_text, fg=C_MUTED2)

        def work():
            try:
                r = self._api(method, tail, timeout=600)
                ok = r.json().get("code", -1) == 0
                msg = "索引已重建" if ok else "重建失败"
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, "重建失败: %r" % (exc,)
            self.app.after(0, lambda: (
                self.hint.config(text=msg, fg=C_GREEN if ok else C_DANGER),
                self._load()))
        threading.Thread(target=work, daemon=True).start()

    def _download_doc(self, row):
        doc = row.get("_raw") or row
        dest = filedialog.asksaveasfilename(
            title="保存文档", initialfile=doc.get("file_name") or "document")
        if not dest:
            return
        try:
            r = self._api("get", "documents/%s/download" % doc.get("document_id"), timeout=120)
            with open(dest, "wb") as fh:
                fh.write(r.content)
            self.hint.config(text="已保存到 %s" % dest, fg=C_GREEN)
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="下载失败: %r" % (exc,), fg=C_DANGER)

    def _preview_doc(self, row):
        """预览前若干切片，够判断这份文档是否解析正确。"""
        doc = row.get("_raw") or row
        did = doc.get("document_id")
        try:
            r = self._api("get", "documents/%s/chunks" % did, timeout=30)
            data = r.json()
            chunks = (data.get("data") or []) if data.get("code", -1) == 0 else []
        except Exception as exc:  # noqa: BLE001
            chunks, err = [], "预览失败: %r" % (exc,)
        else:
            err = ""

        win = tk.Toplevel(self)
        win.title("预览 · %s" % (doc.get("title") or doc.get("file_name") or ""))
        win.geometry("%dx%d" % (S(720), S(520)))
        win.configure(bg=C_BG)
        tk.Label(win, text=doc.get("title") or "", font=(FONT, 14, "bold"),
                 bg=C_BG, fg=C_INK).pack(anchor="w", padx=S(16), pady=(S(14), S(4)))
        txt = scrolledtext.ScrolledText(win, font=(FONT, 11), wrap="word", relief="flat",
                                        bg=C_CARD, fg=C_INK, highlightthickness=0)
        txt.pack(fill="both", expand=True, padx=S(16), pady=(S(0), S(16)))
        if err:
            txt.insert("end", err)
        else:
            for i, ch in enumerate(chunks[:12]):
                body = ch.get("content") if isinstance(ch, dict) else str(ch)
                txt.insert("end", "── 切片 %d ──\n%s\n\n" % (i + 1, body or ""))
        txt.config(state="disabled")


# ══ 全息舱（展项）页面 ══════════════════════════════════════════════
# 这一组是给"无人值守的数字人展项"用的：唤醒/打断词、菜单库、闲时待机与
# 人体感应、问答记录。共同点是都要能在现场改完立刻生效，所以每张表都是
# 「接口读写 + 列表重绘」，没有本地缓存。

def _kiosk_card(parent, title, hint="", padx=S(24), pady=(S(12), S(0))):
    """带标题与说明的卡片，返回卡片本身（内容往里 pack）。"""
    card = Card(parent)
    card.pack(fill="x", padx=padx, pady=pady)
    tk.Label(card, text=title, font=(FONT, 13, "bold"), bg=C_CARD, fg=C_INK
             ).pack(anchor="w", padx=S(20), pady=(S(14), S(1)))
    if hint:
        tk.Label(card, text=hint, font=(FONT, 10), bg=C_CARD, fg=C_MUTED, wraplength=S(760),
                 justify="left").pack(anchor="w", padx=S(20), pady=(0, S(8)))
    return card


class _KioskBase(tk.Frame):
    """全息舱页面公共骨架：标题栏 + 内容区 + 底部提示行。

    子类自己决定内容区滚不滚动（问答记录的表格自带滚动条，再套一层会
    出现两个滚轮抢事件的问题）。
    """

    TITLE = ""
    DESC = ""
    SCROLL_BODY = True

    def __init__(self, parent, app):
        super().__init__(parent, bg=C_BG)
        self.app = app
        self._busy = False

        head = tk.Frame(self, bg=C_BG)
        head.pack(fill="x", padx=S(24), pady=(S(18), S(2)))
        tk.Label(head, text=self.TITLE, font=(FONT, 20, "bold"), bg=C_BG, fg=C_INK
                 ).pack(side="left")
        make_btn(head, "刷新", self._load, sm=True).pack(side="right")
        if self.DESC:
            tk.Label(self, text=self.DESC, font=(FONT, 12), bg=C_BG, fg=C_MUTED2,
                     wraplength=S(900), justify="left"
                     ).pack(anchor="w", padx=S(24))

        if self.SCROLL_BODY:
            self.body = ScrollFrame(self, bg=C_BG)
        else:
            self.body = tk.Frame(self, bg=C_BG)
        self.body.pack(fill="both", expand=True, padx=S(24), pady=(S(12), S(4)))
        self.inner = self.body.inner if self.SCROLL_BODY else self.body

        self.hint = tk.Label(self, text="", font=(FONT, 11), bg=C_BG, fg=C_GREEN,
                             anchor="w", wraplength=S(900), justify="left")
        self.hint.pack(fill="x", padx=S(24), pady=(S(0), S(10)))

    # ── 接口 ──
    def _api(self, method, tail, timeout=8, **kw):
        """返回 (data, err)。主服务没起来时 err 非空，页面照常渲染。"""
        try:
            resp = self.app.sess.request(method, BASE + tail, timeout=timeout, **kw)
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            return None, "%r" % (exc,)
        if payload.get("code", -1) != 0:
            return None, payload.get("msg") or "接口返回错误"
        return payload.get("data"), ""

    def set_hint(self, text, ok=True):
        self.hint.config(text=text or "", fg=C_GREEN if ok else C_DANGER)

    def _ui(self, fn):
        """把回调投回主线程。窗口已销毁时静默丢弃——后台线程的收尾不该炸界面。"""
        try:
            self.app.after(0, fn)
        except Exception:  # noqa: BLE001
            pass

    def _mutate(self, fn, ok_msg="已保存", reload=True):
        """改数据的操作都走这里：后台线程发请求，完了刷新并提示。

        不用主线程直发，是因为主服务偶尔会卡在模型加载上，直发会把整个界面冻住。
        """
        if self._busy:
            return
        self._busy = True
        self.set_hint("处理中…", True)

        def work():
            try:
                err = fn()
            except Exception as exc:  # noqa: BLE001
                err = "%r" % (exc,)
            self._ui(lambda: self._mutate_done(err, ok_msg, reload))

        threading.Thread(target=work, daemon=True).start()

    def _mutate_done(self, err, ok_msg, reload):
        self._busy = False
        if err:
            self.set_hint("操作失败：%s" % err, False)
        else:
            self.set_hint(ok_msg, True)
        if reload:
            self._load()

    def on_show(self):
        self._load()

    def _load(self):
        raise NotImplementedError


# ── 唤醒与打断页 ─────────────────────────────────────────────────
class WakePage(_KioskBase):
    """唤醒词库 / 打断词库：词条增删改 + 一句话判定测试。"""

    TITLE = "唤醒与打断"
    DESC = ("唤醒词让数字人从待机进入对话态，打断词在它说话时让它立刻停下。"
            "匹配是「包含即命中」并忽略标点，长词优先——配置了「你好小知」和「小知」，"
            "说「你好小知」只会命中前者。")

    KINDS = (("wake", "唤醒词库", "命中后数字人播报下面的应答语，并进入对话态"),
             ("interrupt", "打断词库", "只在数字人正在说话时生效，命中后立刻停止播报"))

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._editing = {"wake": None, "interrupt": None}
        self._lists = {}
        self._forms = {}

        for kind, title, hint in self.KINDS:
            card = _kiosk_card(self.inner, title, hint)
            rows = tk.Frame(card, bg=C_CARD)
            rows.pack(fill="x", padx=S(16), pady=(S(4), S(4)))
            self._lists[kind] = rows
            self._forms[kind] = self._build_form(card, kind)

        test = _kiosk_card(self.inner, "说一句话试试",
                           "只判定不播报，用来确认词表配得对不对。")
        row = tk.Frame(test, bg=C_CARD)
        row.pack(fill="x", padx=S(20), pady=(0, S(10)))
        self.test_var = tk.StringVar()
        make_entry(row, textvariable=self.test_var, width=52).pack(side="left", padx=(0, S(10)))
        make_btn(row, "判定", self._test, sm=True, primary=True).pack(side="left")
        self.test_out = tk.Label(test, text="", font=(FONT, 11), bg=C_CARD, fg=C_MUTED2,
                                 anchor="w", wraplength=S(760), justify="left")
        self.test_out.pack(fill="x", padx=S(20), pady=(0, S(14)))

        self._load()

    def _build_form(self, card, kind):
        """词条表单：新增与编辑共用（编辑时按钮变「保存」）。"""
        form = tk.Frame(card, bg=C_CARD)
        form.pack(fill="x", padx=S(20), pady=(0, S(16)))
        word = tk.StringVar()
        reply = tk.StringVar()
        make_entry(form, textvariable=word, width=20).pack(side="left", padx=(0, S(8)))
        make_entry(form, textvariable=reply, width=38).pack(side="left", padx=(0, S(8)))
        add_btn = make_btn(form, "添加", lambda: self._submit(kind), sm=True, primary=True)
        add_btn.pack(side="left")
        cancel_btn = make_btn(form, "取消", lambda: self._cancel(kind), sm=True)
        cancel_btn.pack(side="left", padx=(S(8), 0))
        cancel_btn.pack_forget()
        tip = tk.Label(card, text="左边填词条，右边填应答语（打断词可留空）",
                       font=(FONT, 10), bg=C_CARD, fg=C_MUTED)
        tip.pack(anchor="w", padx=S(20), pady=(0, S(12)))
        return {"word": word, "reply": reply, "add": add_btn,
                "cancel": cancel_btn, "tip": tip}

    # ── 渲染 ──
    def _load(self):
        data, err = self._api("get", "/api/kiosk/wakewords")
        if err:
            self.set_hint("读取词库失败（主服务未运行？）: %s" % err, False)
            return
        for kind, _title, _hint in self.KINDS:
            self._render(kind, (data or {}).get(kind, []))
        self.set_hint("")

    def _render(self, kind, items):
        box = self._lists[kind]
        for w in list(box.winfo_children()):
            w.destroy()
        if not items:
            tk.Label(box, text="还没有词条，在下面添加一条。", font=(FONT, 11),
                     bg=C_CARD, fg=C_MUTED).pack(anchor="w", pady=S(6))
            return
        for item in items:
            self._row(box, kind, item)

    def _row(self, box, kind, item):
        row = tk.Frame(box, bg=C_CARD)
        row.pack(fill="x", pady=S(3))
        editing = self._editing[kind] == item.get("id")
        tk.Label(row, text=item.get("word") or "（空）",
                 font=(FONT, 12, "bold") if editing else (FONT, 12),
                 bg=C_CARD, fg=C_GREEN if editing else C_INK, width=16, anchor="w"
                 ).pack(side="left")
        reply = (item.get("reply") or "—").strip() or "—"
        if len(reply) > 34:
            reply = reply[:34] + "…"
        tk.Label(row, text=reply, font=(FONT, 11), bg=C_CARD, fg=C_MUTED2,
                 anchor="w").pack(side="left", padx=(S(4), S(10)))

        var = tk.BooleanVar(value=bool(item.get("enabled", True)))
        tk.Label(row, text="启用", font=(FONT, 10), bg=C_CARD, fg=C_MUTED2
                 ).pack(side="right")
        ToggleSwitch(row, var, bg=C_CARD,
                     command=lambda v=var, i=item: self._toggle(kind, i, v)
                     ).pack(side="right", padx=(S(0), S(6)))
        make_btn(row, "删除", lambda i=item: self._delete(kind, i), sm=True,
                 font_size=10).pack(side="right", padx=(0, S(6)))
        make_btn(row, "编辑", lambda i=item: self._edit(kind, i), sm=True,
                 font_size=10).pack(side="right", padx=(0, S(6)))

    # ── 操作 ──
    def _submit(self, kind):
        form = self._forms[kind]
        word = form["word"].get().strip()
        reply = form["reply"].get().strip()
        item_id = self._editing[kind]

        def work():
            if item_id:
                _data, err = self._api("put", "/api/kiosk/wakewords/%s" % item_id,
                                       json={"kind": kind, "word": word, "reply": reply})
                return err
            _data, err = self._api("post", "/api/kiosk/wakewords",
                                   json={"kind": kind, "word": word, "reply": reply})
            return err

        if not word:
            self.set_hint("词条不能为空", False)
            return
        self._mutate(work, "已保存", reload=True)
        self._editing[kind] = None
        form["word"].set("")
        form["reply"].set("")
        form["tip"].config(text="左边填词条，右边填应答语（打断词可留空）")
        form["cancel"].pack_forget()

    def _cancel(self, kind):
        self._editing[kind] = None
        form = self._forms[kind]
        form["word"].set("")
        form["reply"].set("")
        form["tip"].config(text="左边填词条，右边填应答语（打断词可留空）")
        form["cancel"].pack_forget()
        self._load()

    def _edit(self, kind, item):
        self._editing[kind] = item.get("id")
        form = self._forms[kind]
        form["word"].set(item.get("word") or "")
        form["reply"].set(item.get("reply") or "")
        form["tip"].config(text="正在编辑「%s」，改完点「添加」即为保存"
                                % (item.get("word") or "该词条"))
        form["cancel"].pack(side="left", padx=(S(8), 0))
        self._load()

    def _delete(self, kind, item):
        if not messagebox.askyesno("删除词条", "确定删除「%s」？" % (item.get("word") or "该词条")):
            return
        self._mutate(lambda: self._api("delete", "/api/kiosk/wakewords/%s" % item.get("id")
                                       )[1], "已删除")

    def _toggle(self, kind, item, var):
        enabled = bool(var.get())
        item["enabled"] = enabled
        self._mutate(lambda: self._api("put", "/api/kiosk/wakewords/%s" % item.get("id"),
                                       json={"kind": kind, "enabled": enabled})[1],
                     "已更新", reload=False)

    def _test(self):
        text = self.test_var.get().strip()
        if not text:
            self.test_out.config(text="先输入一句话", fg=C_DANGER)
            return

        def work():
            data, err = self._api("post", "/api/kiosk/resolve", json={"text": text})
            self._ui(lambda: self._show_test(data, err))

        threading.Thread(target=work, daemon=True).start()

    def _show_test(self, data, err):
        if err:
            self.test_out.config(text="判定失败：%s" % err, fg=C_DANGER)
            return
        names = {"wake": "唤醒词", "interrupt": "打断词", "menu": "菜单", "rag": "交给知识库"}
        kind = (data or {}).get("kind", "rag")
        parts = ["判定结果：%s" % names.get(kind, kind)]
        if data.get("word"):
            parts.append("命中词条：%s" % data["word"])
        if data.get("reply"):
            parts.append("应答语：%s" % data["reply"])
        if data.get("menu"):
            parts.append("菜单：%s" % (data["menu"].get("title") or ""))
        if kind == "rag":
            parts.append("没有命中任何词条，会去知识库检索。")
        self.test_out.config(text="　|　".join(parts), fg=C_GREEN if kind != "rag" else C_MUTED2)


# ── 菜单库页 ────────────────────────────────────────────────────
def _norm_kw(text):
    """搜索关键词归一化：去掉标点与空白再比较。

    用户在搜索框里打出来的常带空格或标点，直接 == 比较会漏掉肉眼明明一样的那一行。
    """
    return re.sub(r"[\s\W_]+", "", str(text or "")).lower()


# ── 菜单项编辑弹窗 ────────────────────────────────────────────────
class MenuEditDialog(tk.Toplevel):
    """一条菜单项的全部字段。

    做成弹窗而不是表格下面的行表单，是因为字段从 3 个涨到 6 个：
    挤成一行排不下，排两行又会跟表格抢横向空间，还容易看错该填哪一格。
    """

    FIELDS = (("title", "菜单名称", "按钮文字，也是用户可能直接念出来的那个词", True),
              ("order", "菜单排序", "数字越小越靠前；这里填几，就会被挪到第几位", False),
              ("question", "配置问题", "命中后真正去知识库检索的问法（标题太短时检索不准）", False),
              ("answer", "配置答案", "填了就照这句念，不再去查知识库——用来锁标准话术", False),
              ("link", "跳转链接", "可留空；填了则在端侧同时给出跳转入口", False))

    def __init__(self, parent, slides=(), item=None):
        super().__init__(parent)
        self.item = item
        self.title("编辑菜单项" if item else "新增菜单")
        self.transient(parent)
        self.resizable(False, False)
        self.configure(bg=C_BG)
        self.result = None
        self._slides = list(slides or [])

        body = tk.Frame(self, bg=C_BG)
        body.pack(fill="both", expand=True, padx=S(20), pady=(S(16), S(6)))
        self.vars = {}
        for key, label, hint, required in self.FIELDS:
            row = tk.Frame(body, bg=C_BG)
            row.pack(fill="x", pady=(0, S(6)))
            tk.Label(row, text=("* " if required else "") + label, font=(FONT, 11),
                     bg=C_BG, fg=C_MUTED2, width=S(9), anchor="w").pack(side="left")
            var = tk.StringVar()
            make_entry(row, textvariable=var, width=44).pack(side="left", fill="x",
                                                             expand=True)
            self.vars[key] = var
            tk.Label(body, text=hint, font=(FONT, 10), bg=C_BG, fg=C_MUTED,
                     anchor="w", justify="left", wraplength=S(560)
                     ).pack(anchor="w", padx=(S(9) * 7, 0), pady=(0, S(4)))

        # 显示素材：可选一页幻灯片，命中这条菜单时把画面切过去
        row = tk.Frame(body, bg=C_BG)
        row.pack(fill="x", pady=(0, S(6)))
        tk.Label(row, text="显示素材", font=(FONT, 11), bg=C_BG, fg=C_MUTED2,
                 width=S(9), anchor="w").pack(side="left")
        self._material_map = {"（不切换素材）": ""}
        for slide in self._slides:
            sid = str(slide.get("id") or "")
            name = slide.get("name") or ("幻灯片 %s" % sid)
            self._material_map["%s · %s" % (sid, name)] = sid
        self.material_var = tk.StringVar(value="（不切换素材）")
        RoundedCombo(row, textvariable=self.material_var, font_size=11,
                     width=S(360), values=list(self._material_map),
                     state="readonly").pack(side="left")
        tk.Label(body, text="命中这条菜单时，把端侧画面切到选中的那一页素材。",
                 font=(FONT, 10), bg=C_BG, fg=C_MUTED, anchor="w", justify="left",
                 wraplength=S(560)).pack(anchor="w", padx=(S(9) * 7, 0), pady=(0, S(4)))

        row2 = tk.Frame(body, bg=C_BG)
        row2.pack(fill="x", pady=(0, S(4)))
        self.enabled_var = tk.BooleanVar(value=True)
        make_switch(row2, "启用（停用后不在端侧展示）", self.enabled_var,
                    bg=C_BG, hint="").pack(side="left")

        self.err = tk.Label(body, text="", font=(FONT, 10), bg=C_BG, fg=C_DANGER,
                            anchor="w", justify="left")
        self.err.pack(fill="x", pady=(S(2), S(0)))

        foot = tk.Frame(self, bg=C_BG)
        foot.pack(fill="x", padx=S(20), pady=(S(4), S(16)))
        make_btn(foot, "保存", self._save, sm=True, primary=True).pack(side="right")
        make_btn(foot, "取消", self._cancel, sm=True).pack(side="right", padx=(S(0), S(8)))

        if item:
            self._fill(item)
        self.vars["title"].trace_add("write", lambda *_a: self.err.config(text=""))
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.after(120, lambda: self.focus_force())

    def _fill(self, item):
        for key in self.vars:
            self.vars[key].set(str(item.get(key) if item.get(key) is not None else ""))
        self.enabled_var.set(bool(item.get("enabled", True)))
        wanted = str(item.get("material") or "").strip()
        for label, sid in self._material_map.items():
            if sid == wanted:
                self.material_var.set(label)
                break

    def _cancel(self):
        self.result = None
        self.destroy()

    def _save(self):
        title = self.vars["title"].get().strip()
        if not title:
            self.err.config(text="菜单名称不能为空")
            return
        order_raw = self.vars["order"].get().strip()
        order = None
        if order_raw:
            # 排序只接受非负整数：用户可能顺手敲个"第一"，直接报错比默默存歪好
            if not order_raw.isdigit():
                self.err.config(text="菜单排序要填非负整数（如 0、1、2）")
                return
            order = int(order_raw)
        self.result = {
            "title": title,
            "question": self.vars["question"].get().strip(),
            "answer": self.vars["answer"].get().strip(),
            "link": self.vars["link"].get().strip(),
            "material": self._material_map.get(self.material_var.get(), ""),
            "enabled": bool(self.enabled_var.get()),
        }
        if order is not None:
            self.result["order"] = order
        self.destroy()


class MenuPage(_KioskBase):
    """菜单库：菜单名称 / 配置问题 / 配置答案 / 显示素材，后台表格式管理。

    端侧屏幕上的按钮就从这里来。三件容易搞错的事写在明处：

    * **命中之后怎么答** —— 配置答案 → 预置问题去知识库查 → 把标题当介绍语。
      想锁一句标准话术就填「配置答案」，它优先于知识库；
      "配置答案"留空但又不想查知识库时，别填"配置问题"，直接用标题当话术；
    * **显示素材** —— 命中时把画面切到指定的一页幻灯片，讲展品时常用；
    * **顺序** —— 表格从上到下就是端侧按钮从左到右，用工具栏的上移/下移调整。
    """

    TITLE = "菜单库"
    DESC = ("展项屏幕上的按钮就从这里来，表格从上到下 = 按钮从左到右。"
            "命中后的作答顺序：配置答案 → 预置问题查知识库 → 菜单标题。")
    SCROLL_BODY = False

    COLUMNS = [("order", "菜单排序", 62, False, "center"),
               ("title", "菜单名称", 110, False, "w"),
               ("question", "配置问题", 160, True, "w"),
               ("answer", "配置答案", 160, True, "w"),
               ("material_name", "显示素材", 100, False, "w"),
               ("enabled_text", "状态", 70, False, "center")]

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._items = []
        self._slide_names = {}         # slide id -> 名称
        self._kw = tk.StringVar()
        self._build()

    # ── 界面 ──
    def _build(self):
        card = Card(self.inner)
        card.pack(fill="both", expand=True, pady=(S(4), S(0)))
        self._build_toolbar(card)

        self.table = DataTable(card, self.COLUMNS,
                               actions=[("编辑", self._edit), ("删除", self._delete)],
                               page_size=20, height=9, selectable=True,
                               on_select=self._on_select)
        self.table.pack(fill="both", expand=True, padx=S(20), pady=(0, S(12)))

        self.preview_card = Card(self.inner)
        self.preview_card.pack(fill="x", pady=(S(10), S(0)))
        tk.Label(self.preview_card, text="端侧展示效果", font=(FONT, 13, "bold"),
                 bg=C_CARD, fg=C_INK).pack(anchor="w", padx=S(20), pady=(S(12), S(2)))
        tk.Label(self.preview_card, text="停用项不会出现在屏幕上；点一下按钮，数字人会立刻介绍这一项。",
                 font=(FONT, 10), bg=C_CARD, fg=C_MUTED, wraplength=S(760),
                 justify="left").pack(anchor="w", padx=S(20), pady=(0, S(6)))
        self.preview = tk.Frame(self.preview_card, bg=C_CARD)
        self.preview.pack(fill="x", padx=S(20), pady=(0, S(14)))

    def _build_toolbar(self, card):
        bar = tk.Frame(card, bg=C_CARD)
        bar.pack(fill="x", padx=S(20), pady=(S(14), S(10)))
        make_btn(bar, "新增菜单", self._new, sm=True, primary=True).pack(side="left")
        self.up_btn = make_btn(bar, "上移", lambda: self._move(-1), sm=True)
        self.up_btn.pack(side="left", padx=(S(8), S(0)))
        self.down_btn = make_btn(bar, "下移", lambda: self._move(1), sm=True)
        self.down_btn.pack(side="left", padx=(S(8), S(0)))
        make_btn(bar, "删除", self._bulk_delete, sm=True, fg=C_DANGER
                 ).pack(side="left", padx=(S(8), S(0)))

        make_btn(bar, "搜索", self._load, sm=True).pack(side="right")
        self._kw_entry = make_entry(bar, textvariable=self._kw, width=22,
                                    placeholder="输入菜单名称搜索")
        self._kw_entry.pack(side="right", padx=(S(0), S(8)))
        self._kw_entry.bind("<Return>", lambda _e: self._load())
        self._sync_move_buttons(None)

    # ── 数据 ──
    def _load(self):
        data, err = self._api("get", "/api/kiosk/menus", timeout=12)
        slides, _err2 = self._api("get", "/api/slides", timeout=12)
        self._slide_names = {}
        for slide in slides or []:
            sid = str(slide.get("id") or "")
            if sid:
                self._slide_names[sid] = slide.get("name") or ("幻灯片 %s" % sid)
        if err:
            self.set_hint("读取菜单失败（主服务未运行？）: %s" % err, False)
            self.table.set_rows([])
            return
        self._items = (data or {}).get("items", [])
        rows = self._to_rows(self._items)
        kw = _norm_kw(entry_value(self._kw_entry, self._kw))
        if kw:
            rows = [r for r in rows
                    if kw in _norm_kw(r["title"]) or kw in _norm_kw(r["question"])
                    or kw in _norm_kw(r["answer"])]
        self.table.set_rows(rows)
        self._render_preview()
        self.set_hint("共 %d 项" % len(rows))

    def _to_rows(self, items):
        rows = []
        for item in items:
            material = str(item.get("material") or "").strip()
            enabled = bool(item.get("enabled", True))
            rows.append({"id": item.get("id") or "",
                         "order": item.get("order", 0),
                         "title": item.get("title") or "",
                         "question": item.get("question") or "",
                         "answer": item.get("answer") or "",
                         "material": material,
                         "material_name": self._slide_names.get(material, "无") if material else "无",
                         "enabled": enabled,
                         "enabled_text": "已启用" if enabled else "已停用",
                         "link": item.get("link") or ""})
        return rows

    def _render_preview(self):
        for w in list(self.preview.winfo_children()):
            w.destroy()
        shown = [r for r in self.table._rows if r.get("enabled")]
        if not shown:
            tk.Label(self.preview, text="（没有启用的菜单项，端侧不展示按钮）",
                     font=(FONT, 11), bg=C_CARD, fg=C_MUTED).pack(anchor="w")
            return
        for row in shown[:6]:
            make_btn(self.preview, row.get("title") or "",
                     lambda r=row: self._preview_activate(r), sm=True,
                     tooltip=row.get("question") or row.get("answer") or ""
                     ).pack(side="left", padx=(0, S(8)))
        if len(shown) > 6:
            tk.Label(self.preview, text="…还有 %d 项" % (len(shown) - 6),
                     font=(FONT, 10), bg=C_CARD, fg=C_MUTED).pack(side="left")

    # ── 操作 ──
    def _on_select(self, row):
        self._sync_move_buttons(row)

    def _sync_move_buttons(self, row):
        ok = row is not None
        state = "normal" if ok else "disabled"
        if getattr(self, "up_btn", None) is not None:
            self.up_btn.config(state=state)
            self.down_btn.config(state=state)

    def _new(self):
        dialog = MenuEditDialog(self, slides=self._slides())
        self.wait_window(dialog)
        if not dialog.result:
            return
        payload = dict(dialog.result)
        self._mutate(lambda: self._api("post", "/api/kiosk/menus", json=payload)[1],
                     "菜单已添加")

    def _edit(self, row):
        dialog = MenuEditDialog(self, slides=self._slides(), item=row)
        self.wait_window(dialog)
        if not dialog.result:
            return
        payload = dict(dialog.result)
        item_id = row.get("id") or ""
        self._mutate(lambda: self._api("put", "/api/kiosk/menus/%s" % item_id,
                                       json=payload)[1], "已保存")

    def _delete(self, row):
        if not messagebox.askyesno("删除菜单项",
                                   "确定删除「%s」？" % (row.get("title") or "该项"),
                                   parent=self):
            return
        self._mutate(lambda: self._api("delete", "/api/kiosk/menus/%s" % row.get("id"))[1],
                     "已删除")

    def _bulk_delete(self):
        rows = self.table.checked_rows()
        if not rows:
            self.set_hint("先在左侧勾选要删除的菜单项", False)
            return
        names = "、".join(r.get("title") or "该项" for r in rows[:4])
        more = "等 %d 项" % len(rows) if len(rows) > 4 else ""
        if not messagebox.askyesno("批量删除", "确定删除「%s」%s？" % (names, more),
                                   parent=self):
            return

        def work():
            errs = []
            for row in rows:
                _data, err = self._api("delete", "/api/kiosk/menus/%s" % row.get("id"))
                if err:
                    errs.append(err)
            return "；".join(errs[:2])

        self._mutate(work, "已删除 %d 项" % len(rows))

    def _move(self, delta):
        row = self.table.selected_row()
        if row is None:
            self.set_hint("先点选一行，再用上移/下移调整顺序", False)
            return
        self._mutate(lambda: self._api("post", "/api/kiosk/menus/%s/move" % row.get("id"),
                                       json={"delta": delta})[1], "已调整顺序")

    def _slides(self):
        """幻灯片列表（供"显示素材"下拉框使用）。接口不可用时退化为只有「不切换素材」。"""
        try:
            resp = self.app.sess.get(BASE + "/api/slides", timeout=6)
            payload = resp.json()
        except Exception:  # noqa: BLE001 - 主服务没起时只是选不了素材，不该拦住编辑
            return []
        if payload.get("code", -1) != 0:
            return []
        return [s for s in (payload.get("data") or []) if isinstance(s, dict)]

    def _preview_activate(self, row):
        """端侧效果里的按钮也可点击：直接让数字人介绍这一项（配置时即点即听）。"""
        item_id = row.get("id")
        if not item_id:
            return
        self.set_hint("正在介绍：%s …" % (row.get("title") or ""), True)

        def work():
            try:
                resp = self.app.sess.post(
                    BASE + "/api/kiosk/menus/%s/activate" % item_id,
                    json={"sessionid": self._cur_sessionid(), "speak": True}, timeout=90)
                data = resp.json() or {}
                ok = data.get("code", -1) == 0
                answer = (data.get("data") or {}).get("answer", "")
                text = ("%s → %s" % (row.get("title") or "",
                                     (answer[:80] + "…") if len(answer) > 80 else answer)
                        ) if ok else "介绍失败"
            except Exception as exc:  # noqa: BLE001
                text, ok = "请求失败：%r" % (exc,), False
            self._ui(lambda: self.set_hint(text, ok))

        threading.Thread(target=work, daemon=True).start()

    def _cur_sessionid(self):
        main = getattr(self.app, "_main", None)
        return getattr(main, "sessionid", "") or getattr(self.app, "sessionid", "")



# ── 待机与迎宾页 ────────────────────────────────────────────────
class IdlePage(_KioskBase):
    """闲时动作 / 待机提示 / 人体接近检测。

    这一页是"没人站在舱前时它在干什么"的总开关：放哪个动作、打什么提示语、
    靠什么判断有人来了，以及有人走近时先做什么动作。
    """

    TITLE = "待机与迎宾"
    DESC = ("没人时数字人循环播放闲时动作并在屏幕上打待机提示；摄像头检测到有人走近，"
            "就切回实时画面、播一遍触发动作并念迎宾语，人离开后回到闲时。")
    POLL_MS = 1200

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._polling = False
        self._preview_img = None

        self._build_idle_card()
        self._build_sensor_card()
        self._load()

    # ── 闲时动作 / 待机提示 ──
    def _build_idle_card(self):
        card = _kiosk_card(self.inner, "闲时动作与待机提示",
                           "没人时数字人循环播放这个动作，屏幕上叠一行待机提示引导观众开口。"
                           "动作是按形象分开制作的，所以先选数字人再选它的动作。")
        self.idle_on = tk.BooleanVar(value=True)
        make_switch(card, "启用闲时与待机", self.idle_on, bg=C_CARD,
                    hint="关掉后没人时一直停在实时画面"
                    ).pack(anchor="w", padx=S(20), pady=(0, S(8)))

        row = tk.Frame(card, bg=C_CARD)
        row.pack(fill="x", padx=S(20), pady=(0, S(6)))
        tk.Label(row, text="闲时动作", font=(FONT, 12), bg=C_CARD, fg=C_MUTED2,
                 width=10, anchor="w").pack(side="left")
        self.idle_action = ActionPicker(row, self.app, bg=C_CARD)
        self.idle_action.pack(side="left")
        tk.Label(card, text="留空表示不做动作（没人时停在实时画面）；"
                            "动作在「形象与设置 → 形象库」里制作。",
                 font=(FONT, 10), bg=C_CARD, fg=C_MUTED, anchor="w",
                 justify="left").pack(fill="x", padx=S(20), pady=(0, S(8)))

        for label, var, width, hint in (
                ("待机提示", "tip_var", 46, "例如：您可以站在我的前方进行语音交流"),
                ("迎宾语", "welcome_var", 46, "检测到有人走近时播报的整句")):
            line = tk.Frame(card, bg=C_CARD)
            line.pack(fill="x", padx=S(20), pady=(0, S(6)))
            tk.Label(line, text=label, font=(FONT, 12), bg=C_CARD, fg=C_MUTED2,
                     width=10, anchor="w").pack(side="left")
            setattr(self, var, tk.StringVar())
            make_entry(line, textvariable=getattr(self, var), width=width).pack(side="left")
            tk.Label(line, text=hint, font=(FONT, 10), bg=C_CARD, fg=C_MUTED
                     ).pack(side="left", padx=(S(10), 0))

        line = tk.Frame(card, bg=C_CARD)
        line.pack(fill="x", padx=S(20), pady=(0, S(6)))
        tk.Label(line, text="进入闲时", font=(FONT, 12), bg=C_CARD, fg=C_MUTED2,
                 width=10, anchor="w").pack(side="left")
        self.idle_after_var = tk.StringVar(value="30")
        make_entry(line, textvariable=self.idle_after_var, width=6).pack(side="left")
        tk.Label(line, text="秒无人无交互后，切到闲时动作", font=(FONT, 10), bg=C_CARD,
                 fg=C_MUTED).pack(side="left", padx=(S(10), 0))

        tail = tk.Frame(card, bg=C_CARD)
        tail.pack(fill="x", padx=S(20), pady=(S(4), S(14)))
        make_btn(tail, "保存闲时设置", self._save_idle, sm=True, primary=True).pack(side="left")

    # ── 人体接近检测 ──
    def _build_sensor_card(self):
        card = _kiosk_card(self.inner, "人体接近检测（摄像头）",
                           "独立进程读摄像头，只把「有人 / 无人」报给主服务；"
                           "总开关本身就是启停开关，关掉它进程会自己退出。")
        self.prox_on = tk.BooleanVar(value=False)
        make_switch(card, "启用人体接近检测", self.prox_on, bg=C_CARD,
                    command=self._on_prox_toggle,
                    hint="拨开即启动检测进程，拨关即停止"
                    ).pack(anchor="w", padx=S(20), pady=(0, S(8)))

        grid = tk.Frame(card, bg=C_CARD)
        grid.pack(fill="x", padx=S(20), pady=(0, S(8)))
        self.cam_var = tk.StringVar(value="0")
        self.mode_var = tk.StringVar(value="motion")
        self.leave_var = tk.StringVar(value="8")
        self.sens_var = tk.DoubleVar(value=0.5)

        # 摄像头 / 检测方式 / 灵敏度全部用自绘控件：原生 ttk.Combobox 与 tk.Scale
        # 在彩色卡片上像贴上去的，而且 tk.Scale 默认把数值画在滑轨上方，把行顶歪。
        #
        # 控件 MUST 建在**行容器**里（由 `_form_row` 传进来）：直接建在 grid 上，
        # `pack` 就会把它们排到 grid 自己身上，标签留在行里——结果标签一路往下
        # 错开、控件挤在一起，怎么调 padding 都救不回来。
        self.cam_combo = self._form_row(
            grid, "摄像头",
            lambda box: RoundedCombo(box, textvariable=self.cam_var, font_size=11,
                                     width=S(140), values=["0", "1", "2", "3"]))

        self.mode_seg = self._form_row(
            grid, "检测方式",
            lambda box: SegmentedControl(
                box, [("motion", "帧差"), ("hog", "行人检测")],
                variable=self.mode_var, bg=C_CARD, height=S(32), font_size=11,
                min_seg=S(96)))

        self._form_row(grid, "无人判定",
                       lambda box: self._entry_with_unit(box, self.leave_var, 6,
                                                         "秒没动静才算离开"))

        self.sens_slider = self._form_row(
            grid, "灵敏度",
            lambda box: ThemedSlider(box, self.sens_var, from_=0.05, to=1.0, step=0.05,
                                     bg=C_CARD, width=S(320), describe=_sens_word))

        tk.Label(card, text="镜头贴 0 号；行人检测更准但更吃 CPU，"
                            "OpenCV 5 上没有 HOG 时会自动降级成帧差。",
                 font=(FONT, 10), bg=C_CARD, fg=C_MUTED, anchor="w",
                 justify="left").pack(fill="x", padx=S(20), pady=(0, S(6)))

        arow = tk.Frame(card, bg=C_CARD)
        arow.pack(fill="x", padx=S(20), pady=(0, S(6)))
        tk.Label(arow, text="触发动作", font=(FONT, 12), bg=C_CARD, fg=C_MUTED2,
                 width=10, anchor="w").pack(side="left")
        self.prox_action = ActionPicker(arow, self.app, bg=C_CARD)
        self.prox_action.pack(side="left")
        tk.Label(card, text="检测到有人时先播这个动作（播一遍），再念迎宾语；留空只念迎宾语。",
                 font=(FONT, 10), bg=C_CARD, fg=C_MUTED, anchor="w",
                 justify="left").pack(fill="x", padx=S(20), pady=(0, S(8)))

        bar = tk.Frame(card, bg=C_CARD)
        bar.pack(fill="x", padx=S(20), pady=(0, S(10)))
        self.run_btn = make_btn(bar, "启动感应", self._start_sensor, sm=True, primary=True)
        self.run_btn.pack(side="left")
        self.stop_btn = make_btn(bar, "停止感应", self._stop_sensor, sm=True)
        self.stop_btn.pack(side="left", padx=(S(8), 0))
        make_btn(bar, "探测摄像头", self._probe_cameras, sm=True).pack(side="left", padx=(S(8), 0))
        make_btn(bar, "保存感应参数", self._save_prox, sm=True).pack(side="left", padx=(S(8), 0))

        self.state_row = tk.Frame(card, bg=C_CARD)
        self.state_row.pack(fill="x", padx=S(20), pady=(0, S(8)))
        self.state_dot = tk.Label(self.state_row, text="●", font=(FONT, 11), bg=C_CARD,
                                  fg=C_DOT_OFF)
        self.state_dot.pack(side="left")
        self.state_text = tk.Label(self.state_row, text="未启动", font=(FONT, 11),
                                   bg=C_CARD, fg=C_MUTED2)
        self.state_text.pack(side="left", padx=(S(6), S(14)))
        self.presence_text = tk.Label(self.state_row, text="—", font=(FONT, 11),
                                      bg=C_CARD, fg=C_MUTED2)
        self.presence_text.pack(side="left")

        view = tk.Frame(card, bg=C_CARD)
        view.pack(fill="x", padx=S(20), pady=(0, S(14)))
        self.preview_label = tk.Label(view, bg="#1B1B1B", width=40, height=8,
                                      text="检测画面（启动后显示）", fg="#8A8A8A",
                                      font=(FONT, 10), compound="center")
        self.preview_label.pack(side="left")
        tk.Label(view, text="画面由检测进程写到 data/kiosk/preview.jpg，"
                            "这里每秒读一次。框住的区域就是判定范围。",
                 font=(FONT, 10), bg=C_CARD, fg=C_MUTED, wraplength=S(360),
                 justify="left").pack(side="left", padx=(S(14), 0))

    def _form_row(self, parent, label, factory):
        """一行「标签 + 控件」，控件由 factory(row) 建在行里并返回。

        工厂写法是必须的：`pack` 认的是控件**自己的**父容器，控件若建在外面，
        `pack` 就会把它排到外层去，标签单独留在行里 —— 整块表单会呈阶梯状错位。
        """
        row = tk.Frame(parent, bg=C_CARD)
        row.pack(fill="x", pady=S(4))
        tk.Label(row, text=label, font=(FONT, 12), bg=C_CARD, fg=C_MUTED2, width=10,
                 anchor="w").pack(side="left")
        widget = factory(row)
        widget.pack(side="left")
        return widget

    def _entry_with_unit(self, parent, var, width, unit):
        box = tk.Frame(parent, bg=C_CARD)
        make_entry(box, textvariable=var, width=width).pack(side="left")
        tk.Label(box, text=unit, font=(FONT, 10), bg=C_CARD, fg=C_MUTED
                 ).pack(side="left", padx=(S(8), 0))
        return box

    def _on_prox_toggle(self):
        """总开关直接启停检测进程——以前它只改了个变量，得再点保存才落盘。"""
        if self.prox_on.get():
            self._start_sensor()
        else:
            self._stop_sensor()

    # ── 数据 ──
    def _load(self):
        idle, err = self._api("get", "/api/kiosk/idle")
        if err:
            self.set_hint("读取设置失败（主服务未运行？）: %s" % err, False)
            return
        idle = idle or {}
        self.idle_on.set(bool(idle.get("enabled", True)))
        self.tip_var.set(idle.get("tip") or "")
        self.welcome_var.set(idle.get("welcome") or "")
        self.idle_after_var.set(str(idle.get("idle_after_s") or 30))

        prox, perr = self._api("get", "/api/kiosk/proximity")
        if not perr and prox:
            self.prox_on.set(bool(prox.get("enabled", False)))
            self.cam_var.set(str(prox.get("camera_index", 0)))
            self.mode_var.set(prox.get("mode") or "motion")
            self.leave_var.set(str(prox.get("leave_seconds") or 8))
            try:
                self.sens_var.set(float(prox.get("sensitivity")))
            except (TypeError, ValueError):
                self.sens_var.set(0.5)

        # 两个动作选择器都依赖形象列表：先拉形象，再回填已保存的动作
        idle_spec = idle.get("action") or {}
        prox_spec = (prox or {}).get("trigger_action") or {}
        self.idle_action.load(done=lambda: self.idle_action.set(idle_spec))
        self.prox_action.load(done=lambda: self.prox_action.set(prox_spec))

        self._poll_state()
        self.set_hint("")

    def _save_idle(self):
        try:
            after = int(self.idle_after_var.get().strip())
        except ValueError:
            after = 30
        payload = {"enabled": bool(self.idle_on.get()),
                   "action": self.idle_action.get(),
                   "tip": self.tip_var.get().strip(),
                   "welcome": self.welcome_var.get().strip(),
                   "idle_after_s": after}
        self._mutate(lambda: self._api("put", "/api/kiosk/idle", json=payload)[1],
                     "闲时设置已保存")

    def _prox_payload(self, enabled=None):
        """当前界面上的感应参数（也用于启停时顺手落盘）。"""
        try:
            camera = int(self.cam_var.get())
        except (TypeError, ValueError):
            camera = 0
        try:
            leave = int(self.leave_var.get().strip())
        except (TypeError, ValueError):
            leave = 8
        return {"enabled": bool(self.prox_on.get()) if enabled is None else bool(enabled),
                "camera_index": camera, "mode": self.mode_var.get(),
                "leave_seconds": leave, "sensitivity": float(self.sens_var.get()),
                "trigger_action": self.prox_action.get()}

    def _save_prox(self):
        payload = self._prox_payload()
        self._mutate(lambda: self._api("put", "/api/kiosk/proximity", json=payload)[1],
                     "感应参数已保存")

    def _start_sensor(self):
        """启动检测进程：先落盘 enabled=true（脚本自己会读），再拉进程。

        不走 `_mutate`：它带一个全局忙标志，用户来回拨开关时后一次会被直接丢掉，
        而检测进程该不该活着是"最后一次操作说了算"，不能丢。
        """
        self.prox_on.set(True)
        payload = self._prox_payload(True)

        def work():
            err = self._api("put", "/api/kiosk/proximity", json=payload)[1]
            if err:
                self._ui(lambda: self.set_hint("启动失败（参数没写进去）：%s" % err, False))
                return
            try:
                log_dir = os.path.join(ROOT, "logs")
                os.makedirs(log_dir, exist_ok=True)
                log_path = os.path.join(log_dir, "proximity.log")
                log_file = open(log_path, "a", encoding="utf-8", errors="replace")
                # 子进程默认按控制台代码页（cp936）输出，直接写进 utf-8 文件就是
                # 一片乱码（以前的日志全是"��Ӧ"）。强制它按 utf-8 说人话。
                env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
                # 先收掉上一个（用户可能连点两次启动，或上次退出没清干净）
                self.app._kill_sensor(timeout=1.5)
                try:
                    self.app._sensor_proc = subprocess.Popen(
                        [PYTHON, os.path.join(ROOT, "scripts", "proximity_detect.py")],
                        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log_file,
                        stderr=subprocess.STDOUT, env=env,
                        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                finally:
                    log_file.close()      # 句柄已交给子进程，父进程不必再持有
            except Exception as exc:  # noqa: BLE001
                err = "%r" % (exc,)
            self._ui(lambda: self.set_hint(
                "感应进程已启动（日志 logs/proximity.log）" if not err
                else "启动失败：%s" % err, not err))
            self._ui(self._poll_state)

        threading.Thread(target=work, daemon=True).start()

    def _stop_sensor(self):
        """关掉开关 → 脚本下一轮自检时自己退出；同时按句柄/pid 兜底杀一次。"""
        self.prox_on.set(False)
        payload = self._prox_payload(False)

        def work():
            err = self._api("put", "/api/kiosk/proximity", json=payload)[1]
            self.app._kill_sensor(timeout=2.0)
            self._ui(lambda: self.set_hint(
                "已停止感应" if not err else "已停止进程，但参数没写进去：%s" % err,
                not err))
            self._ui(self._poll_state)

        threading.Thread(target=work, daemon=True).start()

    def _probe_cameras(self):
        def work():
            data, err = self._api("get", "/api/kiosk/proximity/cameras", timeout=30)
            self._ui(lambda: self._show_cameras(data, err))
        self.set_hint("正在探测摄像头…")
        threading.Thread(target=work, daemon=True).start()

    def _show_cameras(self, data, err):
        if err:
            self.set_hint("探测失败：%s" % err, False)
            return
        cams = (data or {}).get("cameras") or []
        if not cams:
            self.set_hint("没探测到可用摄像头（被其它程序占用？）", False)
            return
        values = [str(c) for c in cams]
        # 探测是服务侧做的，界面上这个下拉要跟着更新可选值
        self.cam_combo["values"] = values
        self.cam_var.set(values[0])
        self.set_hint("可用摄像头：%s" % "、".join(values))

    # ── 状态轮询 ──
    def on_show(self):
        self._load()
        self._start_poll()

    def on_hide(self):
        self._stop_poll()

    def _start_poll(self):
        if self._polling:
            return
        self._polling = True
        self._poll_loop()

    def _stop_poll(self):
        self._polling = False

    def _poll_loop(self):
        if not self._polling:
            return
        self._poll_state()
        try:
            self.app.after(self.POLL_MS, self._poll_loop)
        except Exception:  # noqa: BLE001 - 窗口已销毁
            self._polling = False

    def _poll_state(self):
        def work():
            data, err = self._api("get", "/api/kiosk/state", timeout=4)
            self._ui(lambda: self._show_state(data, err))
        threading.Thread(target=work, daemon=True).start()

    def _show_state(self, data, err):
        if err or not data:
            self.state_dot.config(fg=C_DOT_OFF)
            self.state_text.config(text="状态未知", fg=C_MUTED2)
            self.presence_text.config(text="")
            return
        running = bool(data.get("detector_running"))
        present = bool(data.get("present"))
        if data.get("detector_error"):
            self.state_dot.config(fg=C_DANGER)
            self.state_text.config(text="检测异常：%s" % data["detector_error"], fg=C_DANGER)
        elif not running:
            self.state_dot.config(fg=C_DOT_OFF)
            self.state_text.config(text="感应未运行", fg=C_MUTED2)
        else:
            self.state_dot.config(fg=C_GREEN)
            self.state_text.config(
                text="感应运行中" + ("（%s）" % data["detector_note"]
                                    if data.get("detector_note") else ""),
                fg=C_GREEN)
        # 动作是否真的发出去了，只有主服务知道（会话不在桌面端）。把原因直接
        # 摆在状态行上：现场"人走近了却没动作"时，不用再去翻 logs。
        gest = data.get("last_gesture") or {}
        gest_txt, gest_ok = "", True
        if gest.get("at"):
            name = gest.get("name") or "未配置"
            if gest.get("ok"):
                gest_txt = "动作「%s」已请求" % name
            else:
                gest_ok = False
                gest_txt = "动作「%s」未播：%s" % (name, gest.get("note") or "原因未知")
        if present:
            text = "检测到有人（置信度 %.2f）" % float(data.get("confidence") or 0)
            if gest_txt:
                text += " · " + gest_txt
            self.presence_text.config(text=text, fg=C_GREEN if gest_ok else C_DANGER)
        else:
            text = "当前无人 → 闲时待机"
            if gest_txt:
                text += " · " + gest_txt
            self.presence_text.config(text=text, fg=C_MUTED2 if gest_ok else C_DANGER)
        self._refresh_preview()

    def _refresh_preview(self):
        path = os.path.join(ROOT, "data", "kiosk", "preview.jpg")
        if not os.path.isfile(path):
            return
        try:
            from PIL import Image, ImageTk
            img = Image.open(path).convert("RGB")
            img.thumbnail((S(320), S(180)))
            photo = ImageTk.PhotoImage(img)
            self.preview_label.configure(image=photo, text="")
            self._preview_img = photo        # 防 GC
        except Exception:  # noqa: BLE001 - 预览图正在被覆写时打不开，跳过这一帧
            pass


# ── 问答记录页 ──────────────────────────────────────────────────
class QaRecordDialog(tk.Toplevel):
    """一条问答的完整内容：表格里挤不下，只看摘要会漏掉答非所问。"""

    def __init__(self, parent, row):
        super().__init__(parent)
        self.title("问答详情")
        self.transient(parent)
        self.resizable(False, False)
        self.configure(bg=C_BG)
        body = tk.Frame(self, bg=C_BG)
        body.pack(fill="both", expand=True, padx=S(20), pady=(S(16), S(8)))
        fields = [("时间", row.get("time") or ""),
                  ("问题", row.get("question") or ""),
                  ("回复内容", row.get("answer") or ""),
                  ("是否匹配", row.get("matched_text") or ""),
                  ("回复类型", row.get("source_text") or ""),
                  ("问题库", row.get("source_name") or ""),
                  ("知识库", row.get("course_name") or ""),
                  ("显示素材", row.get("material_name") or ""),
                  ("设备名称", row.get("device") or ""),
                  ("明细", row.get("detail") or "")]
        for label, value in fields:
            if not value:
                continue
            tk.Label(body, text=label, font=(FONT, 11), bg=C_BG, fg=C_MUTED2,
                     anchor="w").pack(anchor="w")
            tk.Label(body, text=value, font=(FONT, 12), bg=C_BG, fg=C_INK, anchor="w",
                     justify="left", wraplength=S(620)).pack(anchor="w", pady=(0, S(8)))
        foot = tk.Frame(self, bg=C_BG)
        foot.pack(fill="x", padx=S(20), pady=(S(4), S(16)))
        make_btn(foot, "关闭", self.destroy, sm=True, primary=True).pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(120, lambda: self.focus_force())


class QaLogPage(_KioskBase):
    """问答记录：按回复类型 / 问题库 / 知识库 / 设备四个维度筛，找知识库覆盖的缺口。

    这四个维度都是"这条答案是从哪儿来的"。只有提问那一刻把来源记下来，事后才
    筛得出"某台舱 / 某个知识库的命中率如何"——靠答案文本反推是推不出来的。
    """

    TITLE = "问答记录"
    DESC = ("展项上每一句话都会记一条。「未命中」是最该看的一栏——"
            "那些就是资料里没有、需要补内容的问题。")
    SCROLL_BODY = False

    SOURCE_TEXT = {"wake": "唤醒词", "interrupt": "打断词", "menu": "菜单",
                   "rag": "知识库", "none": "未命中"}

    FILTERS = (("source", "回复类型"), ("source_name", "问题库"),
               ("course", "知识库"), ("device", "设备"))

    COLUMNS = [("question", "问题", 140, True, "w"),
               ("answer_short", "回复内容", 180, True, "w"),
               ("matched_text", "是否匹配", 74, False, "center"),
               ("material_name", "显示素材", 96, False, "w"),
               ("source_text", "回复类型", 84, False, "center"),
               ("source_name", "问题库", 100, False, "w"),
               ("course_name", "知识库", 100, False, "w"),
               ("device", "设备名称", 104, False, "w")]

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._rows = []
        self._slide_names = {}      # 素材 id -> 名称
        self._course_names = {}     # 课程 id -> 标题
        self._source_labels = {}    # 下拉显示文案 -> 接口用的原始值
        self._course_labels = {}
        self.filter_vars = {key: tk.StringVar(value="全部") for key, _label in self.FILTERS}
        self._combos = {}
        self._build()

    # ── 界面 ──
    def _build(self):
        card = Card(self.inner)
        card.pack(fill="both", expand=True, pady=(S(4), S(0)))

        self.stat_text = tk.Label(card, text="—", font=(FONT, 12), bg=C_CARD, fg=C_INK)
        self.stat_text.pack(anchor="w", padx=S(20), pady=(S(14), S(2)))

        bar = tk.Frame(card, bg=C_CARD)
        bar.pack(fill="x", padx=S(20), pady=(S(6), S(10)))
        for key, label in self.FILTERS:
            tk.Label(bar, text=label, font=(FONT, 11), bg=C_CARD, fg=C_MUTED2
                     ).pack(side="left")
            combo = RoundedCombo(bar, textvariable=self.filter_vars[key], font_size=11,
                                 width=S(130), values=["全部"], state="readonly")
            combo.pack(side="left", padx=(S(6), S(14)))
            self._combos[key] = combo
        make_btn(bar, "查询", self._load, sm=True).pack(side="left")
        make_btn(bar, "清空记录", self._clear, sm=True, fg=C_DANGER).pack(side="right")
        make_btn(bar, "导出 CSV", self._export, sm=True).pack(side="right",
                                                              padx=(S(0), S(8)))

        self.table = DataTable(card, self.COLUMNS,
                               actions=[("详情", self._detail), ("删除", self._delete)],
                               page_size=20, height=13)
        self.table.pack(fill="both", expand=True, padx=S(20), pady=(0, S(12)))

    # ── 数据 ──
    def _query(self):
        params = []
        for key, _label in self.FILTERS:
            value = self._filter_value(key)
            if value:
                params.append("%s=%s" % (key, urllib.parse.quote(value)))
        return "/api/kiosk/qa_log?limit=500" + ("&" + "&".join(params) if params else "")

    def _filter_value(self, key):
        """下拉显示的是中文，接口要的是原始值（课程显示标题、传的是 course_id）。"""
        label = self.filter_vars[key].get()
        if not label or label == "全部":
            return ""
        if key == "source":
            return self._source_labels.get(label, "")
        if key == "course":
            return self._course_labels.get(label, "")
        return label

    def _load(self):
        data, err = self._api("get", self._query(), timeout=15)
        facets, _fe = self._api("get", "/api/kiosk/qa_log/facets", timeout=10)
        slides, _se = self._api("get", "/api/slides", timeout=10)
        courses = self._courses()
        if err:
            self.set_hint("读取记录失败（主服务未运行？）: %s" % err, False)
            self.table.set_rows([])
            return

        self._slide_names = {str(s.get("id") or ""): (s.get("name") or "")
                             for s in slides or []}
        self._course_names = {str(c.get("course_id") or ""): (c.get("title") or "")
                              for c in courses}
        self._sync_filters(facets or {}, courses)

        data = data or {}
        stats = data.get("stats") or {}
        self.stat_text.config(
            text="共 %d 条　已匹配 %d　未匹配 %d　命中率 %.1f%%"
                 % (stats.get("total", 0), stats.get("matched", 0),
                    stats.get("unmatched", 0), float(stats.get("hit_rate", 0)) * 100))
        self._rows = [self._to_row(r) for r in (data.get("rows") or [])]
        self.table.set_rows(self._rows)
        self.set_hint("筛出 %d 条" % len(self._rows))

    def _courses(self):
        """课程列表；取不到就返回空——知识库服务没起时其余维度照常可用。"""
        try:
            resp = self.app.sess.get(BASE + "/rag/admin/courses", timeout=8)
            payload = resp.json()
        except Exception:  # noqa: BLE001
            return []
        if payload.get("code", -1) != 0:
            return []
        return [c for c in (payload.get("data") or []) if isinstance(c, dict)]

    def _sync_filters(self, facets, courses):
        """下拉候选值取自接口（全量去重），不是界面上这一页——否则永远只有几条。"""
        source_values = [v for v in (facets.get("source") or []) if v]
        self._source_labels = {self.SOURCE_TEXT.get(v, v): v for v in source_values}
        self._set_combo("source", ["全部"] + sorted(self._source_labels))
        self._set_combo("source_name",
                        ["全部"] + [v for v in (facets.get("source_name") or []) if v])
        course_ids = [v for v in (facets.get("course") or []) if v]
        self._course_labels = {self._course_names.get(v, v): v for v in course_ids}
        self._set_combo("course", ["全部"] + sorted(self._course_labels))
        self._set_combo("device", ["全部"] + [v for v in (facets.get("device") or []) if v])

    def _set_combo(self, key, values):
        combo = self._combos.get(key)
        if combo is None or not values:
            return
        current = self.filter_vars[key].get()
        combo.config(values=values)
        if current not in values:      # 记录被清空后原选项会失效，别留一个筛不出东西的值
            combo.set(values[0])

    def _to_row(self, record):
        answer = str(record.get("answer") or "").replace("\n", " ")
        material = str(record.get("material") or "").strip()
        course = str(record.get("course") or "").strip()
        source = record.get("source") or ""
        matched = bool(record.get("matched"))
        return {"id": record.get("id") or "",
                "time": record.get("time") or "",
                "question": str(record.get("question") or "").replace("\n", " "),
                "answer": answer,
                "answer_short": (answer[:38] + "…") if len(answer) > 39 else answer,
                "matched": matched,
                "matched_text": "已匹配" if matched else "未匹配",
                "source": source,
                "source_text": self.SOURCE_TEXT.get(source, source or "—"),
                "source_name": record.get("source_name") or "—",
                "course": course,
                "course_name": self._course_names.get(course, course or "—"),
                "material": material,
                "material_name": self._slide_names.get(material, material or "—"),
                "device": record.get("device") or "—",
                "detail": record.get("detail") or ""}

    # ── 操作 ──
    def _detail(self, row):
        QaRecordDialog(self, row)

    def _delete(self, row):
        if not messagebox.askyesno("删除记录", "确定删除这条问答记录？该操作不可撤销。",
                                   parent=self):
            return
        self._mutate(lambda: self._api("delete", "/api/kiosk/qa_log/%s" % row.get("id"))[1],
                     "已删除")

    def _export(self):
        def work():
            data, err = self._api("post", "/api/kiosk/qa_log/export", json={}, timeout=30)
            self._ui(lambda: self.set_hint(
                "已导出 %d 条 → %s" % ((data or {}).get("total", 0), (data or {}).get("path", ""))
                if not err else "导出失败：%s" % err, not err))
        threading.Thread(target=work, daemon=True).start()

    def _clear(self):
        if not messagebox.askyesno("清空记录", "确定清空全部问答记录？该操作不可撤销。",
                                   parent=self):
            return
        self._mutate(lambda: self._api("delete", "/api/kiosk/qa_log")[1], "已清空")


# ── 背景设置页（抠像后换背景）────────────────────────────────────
# 虚拟投影（接地阴影）的滑块元数据：键 / 标签 / 范围 / 步长 / 定性词。
# 与 utils/avatar_background.py 的 SHADOW_RANGES 对应。桌面端刻意不 import 那边
# （会连带拉起 cv2/numpy，而它只依赖标准库），越界值由主服务的 normalize_shadow
# 夹取，所以两处即便有偏差也不会写出非法配置。
SHADOW_FIELDS = (
    ("strength", "浓度", 0.0, 1.0, 0.02, "strength"),
    ("spread", "展开", 0.3, 3.0, 0.05, None),
    ("flatten", "扁平", 0.05, 1.0, 0.02, None),
    ("softness", "柔和", 0.1, 1.0, 0.05, "softness"),
)
SHADOW_DEFAULTS = {"enabled": True, "strength": 0.62, "spread": 1.25,
                   "flatten": 0.26, "softness": 0.80}


def _shadow_word(kind, value):
    """把数值翻成人话，跟在滑块右侧——「0.62」看不出是浓还是淡。"""
    if kind == "strength":
        if value < 0.25:
            return "淡"
        if value < 0.50:
            return "适中"
        if value < 0.75:
            return "明显"
        return "很重"
    if value < 0.35:
        return "偏硬"
    if value < 0.70:
        return "自然"
    return "很散"


class BackgroundPage(_KioskBase):
    """画面背景：把抠好的人像合成到纯色或图片背景上。

    原来挤在开播页左栏底部，得先想起开播才找得到。背景是舱体外观的一部分，
    和"今天连哪个形象"是同一类配置，所以独立成页，并把下拉框换成色板 / 缩略图——
    选背景本质是选"看起来什么样"，看图比读名字快得多。
    """

    TITLE = "背景设置"
    DESC = ("纯色或上传图片作为数字人身后画面。合成发生在取帧这一层，"
            "所以换完背景要重新连接数字人才看得到效果。")

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._config = {"type": "none", "value": ""}
        self._cells = {}            # key -> {canvas, kind, value, label, w, h}
        self._photos = []           # 防 GC：色板/缩略图/预览图
        self._shadow_on = tk.BooleanVar(value=SHADOW_DEFAULTS["enabled"])
        self._shadow_vars = {}      # key -> DoubleVar
        self._save_job = None       # 阴影参数保存的防抖定时器
        self._loading = False       # 回填配置期间不要触发保存

        self._build_state_card()
        self._build_color_card()
        self._build_image_card()
        self._build_shadow_card()
        self._build_preview_card()

    # ── 卡片 ──
    def _build_state_card(self):
        card = _kiosk_card(self.inner, "生效状态", "")
        row = tk.Frame(card, bg=C_CARD)
        row.pack(fill="x", padx=S(20), pady=(S(0), S(12)))
        self.state_dot = tk.Label(row, text="●", font=(FONT, 11), bg=C_CARD, fg=C_DOT_OFF)
        self.state_dot.pack(side="left")
        self.state_text = tk.Label(row, text="读取中…", font=(FONT, 11), bg=C_CARD,
                                   fg=C_MUTED2, justify="left", wraplength=S(720))
        self.state_text.pack(side="left", padx=(S(6), S(0)))

    def _build_color_card(self):
        card = _kiosk_card(self.inner, "纯色背景",
                           "「不换背景」= 保留形象素材自带的原背景。")
        self.color_row = tk.Frame(card, bg=C_CARD)
        self.color_row.pack(fill="x", padx=S(20), pady=(S(0), S(14)))

    def _build_image_card(self):
        card = _kiosk_card(self.inner, "图片背景",
                           "把图片放进 data/backgrounds 或用下面的按钮上传；"
                           "按 cover 方式铺满，不会把人拉变形。")
        bar = tk.Frame(card, bg=C_CARD)
        bar.pack(fill="x", padx=S(20), pady=(S(0), S(10)))
        make_btn(bar, "上传图片", self._upload, sm=True, primary=True).pack(side="left")
        self.img_hint = tk.Label(bar, text="", font=(FONT, 10), bg=C_CARD, fg=C_MUTED)
        self.img_hint.pack(side="left", padx=(S(12), S(0)))
        self.img_row = tk.Frame(card, bg=C_CARD)
        self.img_row.pack(fill="x", padx=S(20), pady=(S(0), S(14)))

    def _build_shadow_card(self):
        card = _kiosk_card(self.inner, "虚拟投影",
                           "抠像把原素材脚下的阴影一起丢了，人像看着像浮在背景上。"
                           "这里按每帧自己的脚部位置，在新背景上重新画一片接地阴影；"
                           "只压暗背景，不会糊到鞋面上。改完下一帧就生效，不用重连。")
        row = tk.Frame(card, bg=C_CARD)
        row.pack(fill="x", padx=S(20), pady=(S(0), S(12)))
        make_switch(row, "启用接地阴影", self._shadow_on,
                    command=self._queue_shadow_save, bg=C_CARD).pack(side="left")
        self.shadow_state = tk.Label(row, text="", font=(FONT, 10), bg=C_CARD, fg=C_MUTED)
        self.shadow_state.pack(side="left", padx=(S(12), S(0)))

        for i, (key, label, lo, hi, step, kind) in enumerate(SHADOW_FIELDS):
            last = i == len(SHADOW_FIELDS) - 1
            self._shadow_row(card, key, label, lo, hi, step, kind, last)

    def _shadow_row(self, parent, key, label, lo, hi, step, kind, last=False):
        row = tk.Frame(parent, bg=C_CARD)
        row.pack(fill="x", padx=S(20), pady=(S(0), S(14) if last else S(4)))
        tk.Label(row, text=label, font=(FONT, 11), bg=C_CARD, fg=C_MUTED2,
                 width=6, anchor="w").pack(side="left")
        var = tk.DoubleVar(value=SHADOW_DEFAULTS[key])
        self._shadow_vars[key] = var
        ThemedSlider(row, var, from_=lo, to=hi, step=step, bg=C_CARD, width=S(380),
                     describe=(lambda v, k=kind: _shadow_word(k, v)) if kind else None,
                     fmt="%.2f").pack(side="left", padx=(S(6), S(0)))
        var.trace_add("write", lambda *a: self._queue_shadow_save())

    def _build_preview_card(self):
        card = _kiosk_card(self.inner, "效果预览",
                           "用当前背景合成一帧，确认人像边缘与背景是否协调。")
        bar = tk.Frame(card, bg=C_CARD)
        bar.pack(fill="x", padx=S(20), pady=(S(0), S(10)))
        make_btn(bar, "生成预览", self._preview, sm=True).pack(side="left")
        self.preview_state = tk.Label(bar, text="", font=(FONT, 10), bg=C_CARD, fg=C_MUTED)
        self.preview_state.pack(side="left", padx=(S(12), S(0)))
        self.preview_label = tk.Label(card, bg=C_CARD, fg=C_MUTED2, font=(FONT, 11),
                                      text="点「生成预览」看合成效果")
        self.preview_label.pack(anchor="w", padx=S(20), pady=(S(0), S(14)))

    # ── 数据 ──
    def _load(self):
        self.set_hint("")
        threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self):
        data, err = self._api("get", "/api/avatar/background", timeout=12)
        self._ui(lambda: self._apply(data, err))

    def _apply(self, data, err):
        if err or not data:
            self.state_dot.config(fg=C_DANGER)
            self.state_text.config(text="读取背景配置失败（主服务未运行？）：%s" % (err or ""),
                                   fg=C_DANGER)
            return
        self._config = data.get("config") or {"type": "none", "value": ""}
        avatar = data.get("avatar") or ""
        missing = [str(g) for g in (data.get("missing_gestures") or [])]
        if data.get("matte_ready") and missing:
            # 有素材但不全：播到缺的那个动作会保持原背景，必须提前说明白，
            # 否则现场只会看到"有的动作换背景、有的不换"，以为是随机的。
            self.state_dot.config(fg=C_WARN)
            self.state_text.config(
                text="形象 %s 已有抠像，但这些动作还没有：%s（播到时保持原背景）"
                     "　补一次：python scripts/avatar_matte.py --avatar %s --missing"
                     % (avatar or "-", "、".join(missing), avatar or "<形象 id>"),
                fg=C_WARN)
        elif data.get("matte_ready"):
            self.state_dot.config(fg=C_GREEN)
            self.state_text.config(
                text="形象 %s 已抠像，背景可正常生效。换背景后重新连接数字人即可看到效果。"
                     % (avatar or "-"), fg=C_GREEN)
        else:
            self.state_dot.config(fg=C_DANGER)
            self.state_text.config(
                text="没有可用抠像素材：换背景不会生效。先跑 "
                     "python scripts/avatar_matte.py --avatar %s" % (avatar or "<形象 id>"),
                fg=C_DANGER)
        self._render_colors(data.get("presets") or [])
        self._render_images(data.get("backgrounds") or [])
        self._fill_shadow(data.get("shadow") or {})

    # ── 虚拟投影 ──
    def _fill_shadow(self, cfg):
        self._loading = True                    # 回填会触发 trace，别把它当成用户改动
        try:
            self._shadow_on.set(bool(cfg.get("enabled", SHADOW_DEFAULTS["enabled"])))
            for key, var in self._shadow_vars.items():
                try:
                    var.set(float(cfg.get(key, SHADOW_DEFAULTS[key])))
                except (TypeError, ValueError):
                    pass
        finally:
            self._loading = False

    def _queue_shadow_save(self):
        """滑块是连续拖动，按住不放会刷出几十次请求——攒 0.4s 只发最后一次。"""
        if self._loading:
            return
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(400, self._save_shadow)

    def _save_shadow(self):
        self._save_job = None
        payload = {"enabled": bool(self._shadow_on.get())}
        for key, var in self._shadow_vars.items():
            payload[key] = float(var.get())

        def work():
            _data, err = self._api("put", "/api/avatar/shadow", json=payload, timeout=10)
            return err

        # reload=False：刷新会把页面重新拉一遍，滑块位置被服务端值覆写，
        # 正在拖的那一下会被拽回去。
        self._mutate(work, "虚拟投影已保存，下一帧生效", reload=False)

    # ── 色板 ──
    def _render_colors(self, presets):
        for child in self.color_row.winfo_children():
            child.destroy()
        self._cells = {k: v for k, v in self._cells.items() if v["kind"] != "color"}
        self._cells.pop("none", None)
        self._make_cell(self.color_row, "none", "none", "", "不换背景")
        for item in presets:
            name = str(item.get("name") or "")
            value = str(item.get("value") or "")
            self._make_cell(self.color_row, "color:%s" % value, "color", value, name)

    def _make_cell(self, parent, key, kind, value, label):
        w, h = S(112), S(96)
        block_h = h - S(20)
        holder = tk.Frame(parent, bg=C_CARD, width=w, height=h)
        holder.pack(side="left", padx=(S(0), S(10)))
        holder.pack_propagate(False)
        canvas = tk.Canvas(holder, width=w, height=h, bg=C_CARD,
                           highlightthickness=0, bd=0, cursor="hand2")
        canvas.pack(fill="both", expand=True)
        canvas.bind("<Button-1>", lambda e, k=key: self._pick(k))
        self._cells[key] = {"canvas": canvas, "kind": kind, "value": value,
                            "label": label, "w": w, "h": h, "block_h": block_h}
        self._paint_cell(key)

    def _paint_cell(self, key):
        cell = self._cells.get(key)
        if cell is None:
            return
        canvas, w, h, block_h = cell["canvas"], cell["w"], cell["h"], cell["block_h"]
        on = self._is_current(cell["kind"], cell["value"])
        canvas.delete("all")
        if cell["kind"] == "color":
            photo = rounded_photo(w, block_h, S(10), cell["value"],
                                  C_GREEN if on else C_LINE_SOFT, 2 if on else 1)
        else:
            photo = rounded_photo(w, block_h, S(10), C_CARD_ALT,
                                  C_GREEN if on else C_LINE_SOFT, 2 if on else 1)
        self._photos.append(photo)
        canvas.create_image(w / 2, block_h / 2, image=photo)
        if cell["kind"] == "none":
            canvas.create_text(w / 2, block_h / 2, text="不换", font=(FONT, 11, "bold"),
                               fill=C_MUTED2)
        canvas.create_text(w / 2, block_h + S(10), text=cell["label"],
                           font=(FONT, 10, "bold" if on else "normal"),
                           fill=C_GREEN if on else C_MUTED2)
        if on:
            canvas.create_oval(w - S(19), S(7), w - S(7), S(19), fill=C_GREEN, outline="")

    def _is_current(self, kind, value):
        return (kind == self._config.get("type")
                and str(value) == str(self._config.get("value") or ""))

    # ── 图片卡片 ──
    def _render_images(self, backgrounds):
        for child in self.img_row.winfo_children():
            child.destroy()
        self._cells = {k: v for k, v in self._cells.items() if v["kind"] != "image"}
        if not backgrounds:
            tk.Label(self.img_row, text="还没有上传图片背景", font=(FONT, 11),
                     bg=C_CARD, fg=C_MUTED).pack(side="left")
            return
        for item in backgrounds:
            path = str(item.get("path") or "")
            label = str(item.get("label") or os.path.splitext(str(item.get("name") or ""))[0])
            self._make_image_cell(self.img_row, path, label)

    def _make_image_cell(self, parent, path, label):
        w, h = S(150), S(112)
        block_h = h - S(20)
        holder = tk.Frame(parent, bg=C_CARD, width=w, height=h)
        holder.pack(side="left", padx=(S(0), S(10)))
        holder.pack_propagate(False)
        canvas = tk.Canvas(holder, width=w, height=h, bg=C_CARD,
                           highlightthickness=0, bd=0, cursor="hand2")
        canvas.pack(fill="both", expand=True)
        key = "image:%s" % path
        canvas.bind("<Button-1>", lambda e, k=key: self._pick(k))
        self._cells[key] = {"canvas": canvas, "kind": "image", "value": path,
                            "label": label, "w": w, "h": h, "block_h": block_h}
        self._paint_cell(key)
        self._paint_thumb(key, path)

    def _paint_thumb(self, key, path):
        """把缩略图贴到色板底上（读本地文件：桌面端与主服务同机）。"""
        cell = self._cells.get(key)
        if cell is None:
            return
        full = path if os.path.isabs(path) else os.path.join(
            ROOT, str(path).replace("/", os.sep))
        if not os.path.isfile(full):
            return
        try:
            from PIL import Image, ImageTk
            img = Image.open(full).convert("RGB")
            box_w, box_h = cell["w"] - S(8), cell["block_h"] - S(8)
            ratio = max(box_w / float(img.width), box_h / float(img.height))
            img = img.resize((max(1, int(img.width * ratio)),
                              max(1, int(img.height * ratio))), Image.LANCZOS)
            left = (img.width - box_w) // 2
            top = (img.height - box_h) // 2
            img = img.crop((left, top, left + box_w, top + box_h))
            photo = ImageTk.PhotoImage(img)
            self._photos.append(photo)
            cell["canvas"].create_image(cell["w"] / 2, cell["block_h"] / 2, image=photo)
        except Exception:  # noqa: BLE001 - 图片损坏就只显示色板底，不炸页面
            pass

    # ── 操作 ──
    def _pick(self, key):
        cell = self._cells.get(key)
        if cell is None or self._busy:
            return
        payload = {"type": cell["kind"], "value": cell["value"]}

        def work():
            try:
                resp = self.app.sess.put(BASE + "/api/avatar/background",
                                         json=payload, timeout=12)
                body = resp.json()
            except Exception as exc:  # noqa: BLE001
                return "%r" % (exc,)
            if body.get("code", -1) != 0:
                return body.get("msg") or "接口返回错误"
            return ""

        self._mutate(work, "已切换到「%s」，重新连接后生效" % cell["label"])

    def _upload(self):
        path = filedialog.askopenfilename(
            title="选择背景图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if not path:
            return
        if self._busy:
            return
        self._busy = True
        self.set_hint("上传中…", True)

        def work():
            err = ""
            try:
                with open(path, "rb") as fh:
                    resp = self.app.sess.post(
                        BASE + "/api/avatar/background/upload",
                        files={"file": (os.path.basename(path), fh)}, timeout=60)
                body = resp.json()
                if body.get("code", -1) != 0:
                    err = body.get("msg") or "上传失败"
            except Exception as exc:  # noqa: BLE001
                err = "%r" % (exc,)
            self._ui(lambda: self._upload_done(err))

        threading.Thread(target=work, daemon=True).start()

    def _upload_done(self, err):
        self._busy = False
        if err:
            self.set_hint("上传失败：%s" % err, False)
            return
        self.set_hint("已上传；在「图片背景」里点一下它即可生效", True)
        self._load()

    def _preview(self):
        if self._busy:
            return
        self._busy = True
        self.preview_state.config(text="正在合成…", fg=C_MUTED2)

        def work():
            img = b""
            err = ""
            try:
                resp = self.app.sess.get(BASE + "/api/avatar/background/preview",
                                         timeout=60)
                if resp.status_code == 200:
                    img = resp.content or b""
                else:
                    body = resp.json()
                    err = body.get("msg") or "预览生成失败"
            except Exception as exc:  # noqa: BLE001
                err = "%r" % (exc,)
            self._ui(lambda: self._preview_done(img, err))

        threading.Thread(target=work, daemon=True).start()

    def _preview_done(self, blob, err):
        self._busy = False
        if err or not blob:
            self.preview_state.config(text="预览失败：%s" % (err or "空响应"), fg=C_DANGER)
            return
        try:
            import io

            from PIL import Image, ImageTk
            img = Image.open(io.BytesIO(blob)).convert("RGB")
            max_w, max_h = S(360), S(520)
            ratio = min(max_w / float(img.width), max_h / float(img.height))
            if ratio < 1:
                img = img.resize((max(1, int(img.width * ratio)),
                                  max(1, int(img.height * ratio))), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._photos.append(photo)
            self.preview_label.configure(image=photo, text="")
            self.preview_state.config(text="已按当前背景合成", fg=C_GREEN)
        except Exception as exc:  # noqa: BLE001
            self.preview_state.config(text="预览图打不开：%r" % (exc,), fg=C_DANGER)


# ── 管理后台页（会话 + 配置 + 密码）──────────────────────────────────
class AdminPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=C_BG)
        self.app = app
        tk.Label(self, text="管理后台", font=(FONT, 20, "bold"), bg=C_BG, fg=C_INK
                 ).pack(anchor="w", padx=S(24), pady=(S(18), S(2)))
        tk.Label(self, text="查看主服务启动参数与在线会话，可踢出异常会话或修改登录密码。",
                 font=(FONT, 12), bg=C_BG, fg=C_MUTED2).pack(anchor="w", padx=S(24))
        body = tk.Frame(self, bg=C_BG)
        body.pack(fill="both", expand=True, padx=S(24), pady=(S(14), S(10)))
        left = tk.Frame(body, bg=C_BG)
        left.pack(side="left", fill="both", expand=True)
        card = Card(left)
        card.pack(fill="both", expand=True)
        head = tk.Frame(card, bg=C_CARD)
        head.pack(fill="x", padx=S(14), pady=(S(12), S(6)))
        tk.Label(head, text="在线会话", font=(FONT, 14, "bold"), bg=C_CARD, fg=C_INK).pack(side="left")
        self.sess_count = tk.Label(head, text="", font=(FONT, 11), bg=C_CARD, fg=C_MUTED2)
        self.sess_count.pack(side="right")
        self.tree = ttk.Treeview(card, columns=("sid", "model", "avatar", "speaking", "recording"),
                                 show="headings", height=11)
        for col, text, w in (("sid", "会话ID", 180), ("model", "模型", 110),
                             ("avatar", "形象", 120), ("speaking", "朗读中", 70),
                             ("recording", "录制", 70)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w)
        self.tree.pack(fill="both", expand=True, padx=S(10), pady=(S(0), S(10)))
        bar = tk.Frame(card, bg=C_CARD)
        bar.pack(fill="x", padx=S(10), pady=(S(0), S(10)))
        make_btn(bar, "刷新会话", self._refresh_sessions, sm=True).pack(side="left")
        make_btn(bar, "踢出选中会话", self._kick, sm=True).pack(side="left", padx=(S(8), S(0)))
        self.hint = tk.Label(card, text="", font=(FONT, 11), bg=C_CARD, fg=C_GREEN)
        self.hint.pack(anchor="w", padx=S(10), pady=(S(0), S(8)))
        right = tk.Frame(body, bg=C_BG, width=S(330))
        right.pack(side="left", fill="y", padx=(S(14), S(0)))
        cfg = Card(right)
        cfg.pack(fill="x")
        tk.Label(cfg, text="服务配置", font=(FONT, 13, "bold"), bg=C_CARD, fg=C_INK
                 ).pack(anchor="w", padx=S(12), pady=(S(12), S(4)))
        self.cfg_text = tk.Text(cfg, height=7, font=("Consolas", 9), bg=C_CARD, fg=C_INK,
                                relief="flat", highlightthickness=0, state="disabled")
        self.cfg_text.pack(fill="x", padx=S(10), pady=(S(0), S(8)))
        pw = Card(right)
        pw.pack(fill="x", pady=(S(12), S(0)))
        tk.Label(pw, text="修改密码", font=(FONT, 13, "bold"), bg=C_CARD, fg=C_INK
                 ).pack(anchor="w", padx=S(12), pady=(S(12), S(4)))
        self.old_var = tk.StringVar()
        self.new_var = tk.StringVar()
        make_entry(pw, self.old_var, width=18, show="*").pack(anchor="w", padx=S(12), pady=S(2))
        make_entry(pw, self.new_var, width=18, show="*").pack(anchor="w", padx=S(12), pady=S(2))
        make_btn(pw, "修改密码", self._change_pwd, sm=True).pack(anchor="w", padx=S(12), pady=(S(4), S(12)))
        self._refresh_sessions()
        self._refresh_config()

    def _refresh_sessions(self):
        try:
            r = self.app.sess.get(BASE + "/api/admin/sessions", timeout=8)
            sessions = r.json().get("data", {}).get("sessions", []) or []
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="读取会话失败: %r" % (exc,), fg=C_DANGER)
            return
        self.tree.delete(*self.tree.get_children())
        for s in sessions:
            self.tree.insert("", "end",
                             values=(s.get("sessionid", "?"), s.get("model", ""),
                                     s.get("avatar_id", ""),
                                     "是" if s.get("speaking") else "否",
                                     "是" if s.get("recording") else "否"))
        self.sess_count.config(text="%d 个会话" % len(sessions))

    def _kick(self):
        sel = self.tree.selection()
        if not sel:
            self.hint.config(text="请先选中一个会话", fg=C_DANGER)
            return
        sid = self.tree.item(sel[0], "values")[0]
        if not messagebox.askyesno("踢出会话", "确定踢出会话 %s？将关闭其 WebRTC 连接。" % sid):
            return
        try:
            r = self.app.sess.delete(BASE + "/api/admin/sessions/%s" % sid, timeout=10)
            data = r.json()
            if data.get("code", -1) != 0:
                self.hint.config(text="踢出失败: %s" % data.get("msg", ""), fg=C_DANGER)
                return
            self.hint.config(text="已踢出会话 %s" % sid)
            self._refresh_sessions()
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="踢出失败: %r" % (exc,), fg=C_DANGER)

    def _refresh_config(self):
        try:
            r = self.app.sess.get(BASE + "/api/admin/config", timeout=8)
            cfg = r.json().get("data", {}).get("config", {}) or {}
        except Exception:  # noqa: BLE001
            cfg = {}
        self.cfg_text.config(state="normal")
        self.cfg_text.delete("1.0", "end")
        for k, v in sorted(cfg.items()):
            if k in ("password", "api_key", "secret", "token"):
                v = "***"
            self.cfg_text.insert("end", "%s = %s\n" % (k, v))
        self.cfg_text.config(state="disabled")

    def _change_pwd(self):
        old = self.old_var.get()
        new = self.new_var.get()
        if not old or not new:
            self.hint.config(text="请填写旧密码和新密码", fg=C_DANGER)
            return
        try:
            r = self.app.sess.post(BASE + "/api/auth/password",
                                   json={"old_password": old, "new_password": new}, timeout=10)
            data = r.json()
            if data.get("code", -1) != 0:
                self.hint.config(text="修改失败: %s" % data.get("msg", ""), fg=C_DANGER)
                return
            self.old_var.set("")
            self.new_var.set("")
            self.hint.config(text="密码已修改，其他设备需要重新登录")
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="修改失败: %r" % (exc,), fg=C_DANGER)



# ── 形象管理页 ───────────────────────────────────────────────────────
class _GestureClip:
    """在 Label 上循环播放一组 PIL 帧——动作预览的“小视频”。"""

    def __init__(self, label, frames, interval=90):
        self.label = label
        self.frames = frames
        self.interval = interval
        self.photos = []
        self.i = 0
        self.job = None
        self.alive = True

    def start(self):
        from PIL import ImageTk
        self.photos = [ImageTk.PhotoImage(f) for f in self.frames]
        if not self.photos:
            return
        self.label.config(image=self.photos[0], text="")
        self.job = self.label.after(self.interval, self._tick)

    def _tick(self):
        if not self.alive or not self.photos:
            return
        self.i = (self.i + 1) % len(self.photos)
        try:
            self.label.config(image=self.photos[self.i])
        except Exception:  # noqa: BLE001 - 控件已销毁
            return
        self.job = self.label.after(self.interval, self._tick)

    def stop(self):
        self.alive = False
        if self.job:
            try:
                self.label.after_cancel(self.job)
            except Exception:  # noqa: BLE001
                pass
            self.job = None


def decode_gesture_frames(blob, box=None, max_frames=10):
    """把动作预览 WebP 抽帧并缩到缩略图尺寸。

    只按步长解码需要的帧——预览动图常有上百帧，全解一遍既慢又占内存。
    透明通道合成到面板底色上，避免缩略图变成黑框。
    """
    import io as _io
    from PIL import Image
    box = box or (S(108), S(216))
    im = Image.open(_io.BytesIO(blob))
    total = getattr(im, "n_frames", 1) or 1
    step = max(1, total // max_frames)
    frames = []
    for idx in range(0, total, step)[:max_frames]:
        im.seek(idx)
        frame = im.convert("RGBA")
        canvas = Image.new("RGBA", frame.size, (255, 253, 248, 255))
        frame = Image.alpha_composite(canvas, frame).convert("RGB")
        frame.thumbnail(box, Image.BILINEAR)
        frames.append(frame)
    return frames


class AvatarMgrPage(tk.Frame):
    def __init__(self, parent, app, embedded=False):
        super().__init__(parent, bg=C_BG)
        self.app = app
        self._avatars = {}
        self._clips = []       # 正在循环播放的动作预览
        self._tiles = []       # 动作缩略图容器
        self._gblobs = {}      # 动作 id -> 预览 webp 原始字节（放大时复用，不再请求）
        self._gq = queue.Queue()
        self._gtoken = 0
        self._last_gestures = []
        self._strip_h = 0
        self._resize_job = None

        if not embedded:
            tk.Label(self, text="形象", font=(FONT, 20, "bold"), bg=C_BG, fg=C_INK
                     ).pack(anchor="w", padx=S(24), pady=(S(18), S(2)))
            tk.Label(self, text="选择本机已有的数字人形象；下方逐个预览动作，应用后对新建会话生效。",
                     font=(FONT, 12), bg=C_BG, fg=C_MUTED2).pack(anchor="w", padx=S(24))
        card = Card(self)
        card.pack(fill="x", padx=S(24), pady=(S(12) if embedded else S(14), S(8)))
        self.listbox = tk.Listbox(card, height=3, font=(FONT, 13), relief="flat", bd=0,
                                  bg=C_CARD, fg=C_INK, highlightthickness=0,
                                  selectbackground=C_SEL, selectforeground=C_INK)
        self.listbox.pack(fill="x", padx=S(8), pady=S(6))
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._preview_selected())
        bar = tk.Frame(self, bg=C_BG)
        bar.pack(fill="x", padx=S(24))
        make_btn(bar, "应用形象", self._apply, primary=True).pack(side="left")
        make_btn(bar, "制作新动作", self._make_gesture, sm=True
                 ).pack(side="left", padx=(S(8), S(0)))
        make_btn(bar, "重命名", self._rename, sm=True).pack(side="left", padx=(S(6), S(0)))
        make_btn(bar, "删除", self._delete, sm=True, fg=C_DANGER
                 ).pack(side="left", padx=(S(6), S(0)))
        make_btn(bar, "刷新形象", self._reload, sm=True).pack(side="left", padx=(S(6), S(0)))
        self.hint = tk.Label(bar, text="", font=(FONT, 11), bg=C_BG, fg=C_GREEN)
        self.hint.pack(side="left", padx=(S(14), S(0)))

        pv = Card(self)
        pv.pack(fill="both", expand=True, padx=S(24), pady=(S(6), S(16)))
        tk.Label(pv, text="数字人预览", font=(FONT, 13, "bold"), bg=C_CARD, fg=C_INK
                 ).pack(anchor="w", padx=S(16), pady=(S(14), S(2)))
        top = tk.Frame(pv, bg=C_CARD)
        top.pack(fill="x", padx=S(16), pady=(S(2), S(2)))
        self.preview_img = tk.Label(top, text="选中形象后显示缩略图（含说话效果）",
                                    font=(FONT, 11), bg=C_CARD, fg=C_MUTED2)
        self.preview_img.pack(side="left", anchor="n")
        self.preview_meta = tk.Label(top, text="", font=(FONT, 11), bg=C_CARD, fg=C_MUTED2,
                                     justify="left", anchor="w", wraplength=S(520))
        self.preview_meta.pack(side="left", anchor="n", padx=(S(16), S(0)))

        tk.Label(pv, text="动作预览 · 自动循环播放，点任一张放大", font=(FONT, 11),
                 bg=C_CARD, fg=C_MUTED).pack(anchor="w", padx=S(16), pady=(S(8), S(0)))
        strip_wrap = tk.Frame(pv, bg=C_CARD)
        strip_wrap.pack(fill="both", expand=True, padx=S(12), pady=(S(2), S(8)))
        self.strip = tk.Canvas(strip_wrap, bg=C_CARD, highlightthickness=0, height=S(210))
        sb = ttk.Scrollbar(strip_wrap, orient="horizontal", command=self.strip.xview)
        sb.pack(side="bottom", fill="x")      # 先占位，画布才拿得到真实剩余高度
        self.strip.pack(side="top", fill="both", expand=True)
        self.strip_inner = tk.Frame(self.strip, bg=C_CARD)
        self.strip.create_window((0, 0), window=self.strip_inner, anchor="nw")
        self.strip.configure(xscrollcommand=sb.set)
        self.strip_inner.bind("<Configure>", self._sync_strip)
        self.strip.bind("<Configure>", self._on_strip_configure)
        self._gmsg = tk.Label(self.strip_inner, text="选中形象后显示动作预览", font=(FONT, 11),
                              bg=C_CARD, fg=C_MUTED2)
        self._gmsg.pack(side="left", padx=S(10), pady=S(12))
        self._scan()

    # ── 列表与缩略图 ───────────────────────────────────────────────
    def _sync_strip(self, event=None):
        self.strip.configure(scrollregion=self.strip.bbox("all"))

    def _on_strip_configure(self, event=None):
        """预览条高度变化时按新尺寸重排缩略图——避免名称行被压掉。"""
        h = self.strip.winfo_height()
        if not self._tiles or abs(h - self._strip_h) < 24:
            self._strip_h = h
            return
        if self._resize_job:
            try:
                self.after_cancel(self._resize_job)
            except Exception:  # noqa: BLE001
                pass
        self._resize_job = self.after(280, self._rebuild_tiles)

    def _rebuild_tiles(self):
        self._resize_job = None
        if not self._last_gestures or not self._gblobs:
            return
        try:
            self.update_idletasks()      # 先把新尺寸算实，再按它重排
        except Exception:  # noqa: BLE001
            pass
        self._clear_tiles()
        self._render_gestures(self._last_gestures, self._gblobs, None)

    def _scan(self):
        self._load_avatars_meta()
        names = sorted(getattr(self, "_avatars", {}).keys())
        if not names:                       # 接口不可用时退回扫目录
            base = os.path.join(ROOT, "data", "avatars")
            try:
                names = sorted(n for n in os.listdir(base)
                               if os.path.isdir(os.path.join(base, n)))
            except Exception:  # noqa: BLE001
                names = []
        if not names:
            names = ["musetalk_ke"]
        # 素材不完整的形象也列出来，但标清楚，选了会明确提示不能应用
        self._list_ids = {}
        for idx, a in enumerate(names):
            meta = getattr(self, "_avatars", {}).get(a) or {}
            label = a if meta.get("ready", True) else "%s（素材不完整）" % a
            self._list_ids[label] = a
            self.listbox.insert("end", label)
            if not meta.get("ready", True):
                self.listbox.itemconfig(idx, foreground=C_MUTED)
        if names:
            ready = [i for i, a in enumerate(names)
                     if (getattr(self, "_avatars", {}).get(a) or {}).get("ready", True)]
            self.listbox.selection_set((ready or [0])[0])
        self._preview_selected()

    def _selected_avatar(self):
        sel = self.listbox.curselection()
        if not sel:
            return ""
        return getattr(self, "_list_ids", {}).get(self.listbox.get(sel[0]),
                                                  self.listbox.get(sel[0]))

    def _make_gesture(self):
        current = self._selected_avatar()
        if not current:
            return
        GestureMakeDialog(self.winfo_toplevel(), self.app, current,
                          on_done=lambda: self._load_gestures(current))

    def _rename(self):
        current = self._selected_avatar()
        if not current:
            return
        new = ask_text(self.winfo_toplevel(), "重命名形象", "形象名称",
                       current, hint="只能包含字母、数字、下划线和短横线；改名后素材一起跟过去。")
        if not new or new == current:
            return
        try:
            r = self.app.sess.put(BASE + "/api/avatars/%s" % current,
                                  json={"new_id": new}, timeout=15)
            data = r.json()
            ok = data.get("code", -1) == 0
            self.hint.config(text="已改名为 %s" % new if ok
                             else (data.get("msg") or "改名失败"),
                             fg=C_GREEN if ok else C_DANGER)
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="改名失败：%r" % (exc,), fg=C_DANGER)
            return
        if ok:
            self._reload()

    def _delete(self):
        current = self._selected_avatar()
        if not current:
            return
        if not ask_confirm(self.winfo_toplevel(), "删除形象",
                           "确定删除形象「%s」？\n\n素材目录会整个删掉，含该形象的动作素材，"
                           "不可恢复。" % current, ok_text="删除", danger=True):
            return
        try:
            r = self.app.sess.delete(BASE + "/api/avatars/%s" % current, timeout=15)
            data = r.json()
            ok = data.get("code", -1) == 0
            self.hint.config(text="已删除 %s" % current if ok
                             else (data.get("msg") or "删除失败"),
                             fg=C_GREEN if ok else C_DANGER)
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="删除失败：%r" % (exc,), fg=C_DANGER)
            return
        if ok:
            self._reload()

    def _apply(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        avatar = self._selected_avatar()
        meta = getattr(self, "_avatars", {}).get(avatar) or {}
        if not meta.get("ready", True):
            self.hint.config(text="%s 素材不完整，无法应用（缺 latents.pt / coords.pkl）" % avatar,
                             fg=C_DANGER)
            return
        try:
            r = self.app.sess.put(BASE + "/api/settings",
                                  json={"avatar": {"id": avatar}}, timeout=8)
            if r.json().get("code", -1) == 0:
                self.hint.config(text="已应用 %s（新建会话生效）" % avatar, fg=C_GREEN)
                self.app._main.avatar_page.f_avatar.set(avatar)
                self.app._main.avatar_page._refresh_avatar_hint()
            else:
                self.hint.config(text="应用失败: %s" % r.json().get("msg", ""), fg=C_DANGER)
        except Exception as exc:  # noqa: BLE001
            self.hint.config(text="应用失败: %r" % (exc,), fg=C_DANGER)

    def _reload(self):
        self.listbox.delete(0, "end")
        self._avatars = {}
        self._scan()

    def _load_avatars_meta(self):
        try:
            r = self.app.sess.get(BASE + "/api/avatars", timeout=8)
            data = r.json().get("data", []) or []
            self._avatars = {a["id"]: a for a in data if isinstance(a, dict)}
        except Exception:  # noqa: BLE001
            self._avatars = {}

    def _preview_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        avatar = self._selected_avatar()
        meta = getattr(self, "_avatars", {}).get(avatar)
        if meta and not meta.get("ready", True):
            self.preview_meta.config(text="形象 %s\n素材不完整（缺 latents.pt / coords.pkl），无法开播；\n"
                                          "可在「形象生成」页重新生成。" % avatar, fg=C_DANGER)
        else:
            self.preview_meta.config(text="形象 %s\n缩略图为该形象的说话效果预览；\n下方动作可逐个点开查看。" % avatar,
                                     fg=C_MUTED2)
        self._load_gestures(avatar)
        if not meta or not meta.get("thumbnail"):
            self.preview_img.config(image="", text="该形象暂无缩略图", fg=C_MUTED2)
            return
        try:
            rr = self.app.sess.get(BASE + meta["thumbnail"], timeout=15)
            if rr.status_code != 200:
                self.preview_img.config(text="缩略图获取失败（%d）" % rr.status_code, fg=C_DANGER)
                return
            from PIL import Image, ImageTk
            import io as _io
            img = Image.open(_io.BytesIO(rr.content)).convert("RGB")
            img.thumbnail((S(160), S(120)))
            self._preview_photo = ImageTk.PhotoImage(img)
            self.preview_img.config(image=self._preview_photo, text="", bg=C_CARD)
        except Exception as exc:  # noqa: BLE001
            self.preview_img.config(text="预览失败: %r" % (exc,), fg=C_DANGER)

    # ── 动作预览条（下方循环播放的“小视频”）────────────────────────
    def _stop_clips(self):
        for clip in self._clips:
            clip.stop()
        self._clips = []

    def _clear_tiles(self):
        self._stop_clips()
        for w in self._tiles:
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._tiles = []

    def _load_gestures(self, avatar):
        """拉动作列表 + 预览动图，逐张铺到预览条里。

        取图放到后台线程（首次生成动图可能要几秒），主线程只轮询结果，
        避免界面卡住；换形象时用 token 丢弃过期结果。
        """
        self._clear_tiles()
        self._gblobs = {}
        self._gmsg.config(text="正在生成动作预览…", fg=C_MUTED2)
        if not self._gmsg.winfo_ismapped():
            self._gmsg.pack(side="left", padx=S(10), pady=S(12))
        token = self._gtoken = self._gtoken + 1
        threading.Thread(target=self._fetch_gestures, args=(avatar, token),
                         daemon=True).start()
        self._poll_gestures(token)

    def _fetch_gestures(self, avatar, token):
        try:
            r = self.app.sess.get(BASE + "/api/avatars/%s/gestures" % avatar, timeout=10)
            gestures = (r.json().get("data") or {}).get("gestures", []) or []
            blobs = {}
            for g in gestures:
                gid = g.get("id") or ""
                try:
                    rr = self.app.sess.get(
                        BASE + "/api/avatars/%s/gestures/%s/preview" % (avatar, gid), timeout=90)
                    blobs[gid] = rr.content if rr.status_code == 200 else None
                except Exception:  # noqa: BLE001
                    blobs[gid] = None
            self._gq.put((token, gestures, blobs, None))
        except Exception as exc:  # noqa: BLE001
            self._gq.put((token, [], {}, exc))

    def _poll_gestures(self, token):
        if token != self._gtoken:
            return
        try:
            got = self._gq.get_nowait()
        except queue.Empty:
            self.after(150, lambda: self._poll_gestures(token))
            return
        if got[0] != token:                      # 过期结果，继续等本轮
            self.after(60, lambda: self._poll_gestures(token))
            return
        self._render_gestures(got[1], got[2], got[3])

    def _render_gestures(self, gestures, blobs, err):
        if err is not None:
            self._gmsg.config(text="动作读取失败：%r" % (err,), fg=C_DANGER)
            return
        if not gestures:
            self._gmsg.config(text="该形象暂无动作素材", fg=C_MUTED2)
            return
        self._gblobs = blobs or {}
        self._last_gestures = gestures
        self._gmsg.pack_forget()
        # 缩略图尺寸按预览条实际可用高度算，保证名称行始终完整可见
        avail = self.strip.winfo_height()
        self._strip_h = avail
        if avail < 140:
            avail = 210
        img_h = int(avail) - S(58)
        img_h = max(S(92), min(S(176), img_h))
        box = (max(S(52), int(img_h * 0.5)), img_h)
        for g in gestures:
            self._tiles.append(self._make_tile(g, box))
        self._sync_strip()

    def _make_tile(self, g, box):
        gid = g.get("id") or ""
        label_text = g.get("label") or gid or "动作"
        blob = self._gblobs.get(gid)
        frames = []
        if blob:
            try:
                frames = decode_gesture_frames(blob, box=box)
            except Exception:  # noqa: BLE001
                frames = []
        holder = tk.Frame(self.strip_inner, bg=C_CARD_ALT,
                          highlightbackground=C_LINE_SOFT, highlightthickness=1)
        holder.pack(side="left", padx=(S(0), S(10)), pady=S(8))
        img = tk.Label(holder, bg=C_CARD_ALT, bd=0, cursor="hand2")
        img.pack(padx=S(6), pady=(S(6), S(3)))
        if frames:
            clip = _GestureClip(img, frames)
            clip.start()
            self._clips.append(clip)
        else:
            img.config(text="预览不可用", font=(FONT, 10), fg=C_MUTED, width=12, height=7)
        name = tk.Frame(holder, bg=C_CARD_ALT)
        name.pack(fill="x", padx=S(6), pady=(S(0), S(6)))
        tk.Label(name, text=label_text, font=(FONT, 10), bg=C_CARD_ALT,
                 fg=C_INK).pack(side="left")
        tk.Label(name, text="%d 帧" % int(g.get("frames") or 0), font=(FONT, 9),
                 bg=C_CARD_ALT, fg=C_MUTED).pack(side="right")
        for w in (holder, img, name):
            w.bind("<Button-1>", lambda e, g=g: self._open_clip(g))
        return holder

    def _open_clip(self, g):
        """点缩略图放大播放：复用已下载的动图，不再请求接口。"""
        gid = g.get("id") or ""
        label_text = g.get("label") or gid
        blob = self._gblobs.get(gid)
        frames = []
        if blob:
            try:
                frames = decode_gesture_frames(blob, box=(330, 660), max_frames=24)
            except Exception:  # noqa: BLE001
                frames = []
        if not frames:
            messagebox.showinfo("动作预览", "该动作暂无可用预览")
            return
        win = tk.Toplevel(self)
        win.title("动作预览 · %s" % label_text)
        win.configure(bg=C_BG)
        tk.Label(win, text="%s（%d 帧循环）" % (label_text, int(g.get("frames") or 0)),
                 font=(FONT, 12), bg=C_BG, fg=C_INK).pack(padx=S(18), pady=(S(14), S(6)))
        lbl = tk.Label(win, bg=C_CARD_ALT, bd=0)
        lbl.pack(padx=S(18))
        clip = _GestureClip(lbl, frames, interval=80)
        clip.start()
        win._clip = clip                                   # 挂住动画，窗口在播就不回收
        win.bind("<Destroy>", lambda e: clip.stop())
        make_btn(win, "关闭", win.destroy, sm=True).pack(pady=(S(10), S(14)))
        win.focus_set()


# ── 音色与设置页 ──────────────────────────────────────────────────────
class SettingsPage(tk.Frame):
    def __init__(self, parent, app, embedded=False):
        super().__init__(parent, bg=C_BG)
        self.app = app
        self._voice_values = {}
        self._load_voices()

        if not embedded:
            tk.Label(self, text="素材与设置", font=(FONT, 20, "bold"), bg=C_BG, fg=C_INK
                     ).pack(anchor="w", padx=S(24), pady=(S(18), S(2)))
            tk.Label(self, text="语音服务、形象与音色都在这里配置；保存后对新建会话生效。",
                     font=(FONT, 12), bg=C_BG, fg=C_MUTED2).pack(anchor="w", padx=S(24))

        # 内容和窗口一样高会放不下（参考音色改成卡片后更高了），套一层滚动；
        # 右侧留白代替原来的固定列宽，表单不会被拉成超宽一行。
        body = ScrollFrame(self, bg=C_BG)
        body.pack(fill="both", expand=True, padx=S(24),
                  pady=(S(12) if embedded else S(0), S(0)))
        col = tk.Frame(body.inner, bg=C_BG)
        col.pack(fill="x", padx=(S(0), S(120)), pady=(S(0), S(10)))

        # ── 语音服务 ──
        voice_card = Card(col)
        voice_card.pack(fill="x", pady=(S(14), S(10)))
        self._card_title(voice_card, "语音服务",
                         "在线音色不占显存；本地克隆音色需要先启动对应的 TTS 服务。")
        row = tk.Frame(voice_card, bg=C_CARD)
        row.pack(fill="x", padx=S(20), pady=(S(2), S(4)))
        self.tts_var = tk.StringVar(value=self.app.tts_choice)
        SegmentedControl(row, [("none", "在线音色"), ("xtts", "XTTS 克隆"),
                               ("fish", "Fish 克隆")],
                         variable=self.tts_var, bg=C_CARD, min_seg=S(104),
                         height=S(32), font_size=11).pack(side="left")
        act = tk.Frame(voice_card, bg=C_CARD)
        act.pack(fill="x", padx=S(20), pady=(S(4), S(16)))
        make_btn(act, "应用并重启主服务", self._apply_tts_server, sm=True).pack(side="left")
        tk.Label(act, text="重启后生效，期间数字人会短暂断开", font=(FONT, 10),
                 bg=C_CARD, fg=C_MUTED).pack(side="left", padx=S(10))

        # ── 形象与音色 ──
        form = Card(col)
        form.pack(fill="x", pady=(S(0), S(10)))
        self._card_title(form, "形象与音色", "留空的项沿用主服务启动参数。")
        self.fields = {}

        row7 = tk.Frame(form, bg=C_CARD)
        row7.pack(fill="x", padx=S(20), pady=S(6))
        tk.Label(row7, text="数字人形象", width=10, anchor="w", bg=C_CARD,
                 fg=C_MUTED2, font=(FONT, 11)).pack(side="left")
        self.fields["avatar"] = tk.StringVar()
        make_entry(row7, self.fields["avatar"], width=20).pack(side="left")
        tk.Label(row7, text="如 musetalk_ke", font=(FONT, 10), fg=C_MUTED,
                 bg=C_CARD).pack(side="left", padx=S(10))

        advbar = tk.Frame(form, bg=C_CARD)
        advbar.pack(fill="x", padx=S(20), pady=(S(6), S(0)))
        self.adv_show = tk.BooleanVar(value=False)
        make_switch(advbar, "高级设置（引擎 / 语速 / 参考音色）", self.adv_show,
                    command=self._toggle_advanced, bg=C_CARD).pack(side="left")
        self.adv_frame = tk.Frame(form, bg=C_CARD)

        refbox = tk.Frame(self.adv_frame, bg=C_CARD)
        refbox.pack(fill="x", padx=S(20), pady=S(6))
        tk.Label(refbox, text="参考音色", anchor="w", bg=C_CARD, fg=C_MUTED2,
                 font=(FONT, 11)).pack(anchor="w")
        self.fields["ref_file"] = tk.StringVar()
        self.voice_picker = VoicePicker(refbox, variable=self.fields["ref_file"],
                                        on_change=self._on_voice_pick, bg=C_CARD, cols=3)
        self.voice_picker.pack(anchor="w", pady=(S(6), S(2)))
        custom = tk.Frame(refbox, bg=C_CARD)
        custom.pack(fill="x", pady=(S(2), S(2)))
        tk.Label(custom, text="自定义复刻音色 ID", bg=C_CARD, fg=C_MUTED2,
                 font=(FONT, 10)).pack(side="left")
        make_entry(custom, self.fields["ref_file"], width=24).pack(side="left", padx=S(8))
        make_btn(custom, "刷新列表", self._refresh_voices, sm=True).pack(side="left")

        row2 = tk.Frame(self.adv_frame, bg=C_CARD)
        row2.pack(fill="x", padx=S(20), pady=S(6))
        tk.Label(row2, text="TTS 引擎", width=10, anchor="w", bg=C_CARD,
                 fg=C_MUTED2, font=(FONT, 11)).pack(side="left")
        self.fields["engine"] = tk.StringVar()
        RoundedCombo(row2, textvariable=self.fields["engine"], font_size=11, width=S(200),
                     values=list(TTS_ENGINES),
                     state="readonly").pack(side="left")
        tk.Label(row2, text="备用引擎", bg=C_CARD, fg=C_MUTED2, font=(FONT, 11)
                 ).pack(side="left", padx=(S(20), S(8)))
        self.fields["fallback"] = tk.StringVar()
        RoundedCombo(row2, textvariable=self.fields["fallback"], font_size=11, width=S(150),
                     values=[""] + list(TTS_ENGINES),
                     state="readonly").pack(side="left")

        row4 = tk.Frame(self.adv_frame, bg=C_CARD)
        row4.pack(fill="x", padx=S(20), pady=S(6))
        tk.Label(row4, text="语速", width=10, anchor="w", bg=C_CARD,
                 fg=C_MUTED2, font=(FONT, 11)).pack(side="left")
        self.fields["rate"] = tk.StringVar()
        make_entry(row4, self.fields["rate"], width=10).pack(side="left")
        tk.Label(row4, text="如 +0% / +20% / -10%", font=(FONT, 10), fg=C_MUTED,
                 bg=C_CARD).pack(side="left", padx=S(10))

        row6 = tk.Frame(self.adv_frame, bg=C_CARD)
        row6.pack(fill="x", padx=S(20), pady=(S(6), S(10)))
        tk.Label(row6, text="参考文本", width=10, anchor="w", bg=C_CARD,
                 fg=C_MUTED2, font=(FONT, 11)).pack(side="left")
        self.fields["ref_text"] = tk.StringVar()
        make_entry(row6, self.fields["ref_text"], width=44).pack(side="left")

        foot = tk.Frame(form, bg=C_CARD)
        foot.pack(fill="x", padx=S(20), pady=(S(8), S(16)))
        make_btn(foot, "保存设置", self._save, primary=True, sm=True).pack(side="left")
        self.save_label = tk.Label(foot, text="", fg=C_GREEN, bg=C_CARD, font=(FONT, 11))
        self.save_label.pack(side="left", padx=S(12))

        # ── 音频输出 ──
        # 真机"没声音"的第二大原因：系统默认输出指向显示器/HDMI 而不是音箱。
        # 这里给出设备下拉框 + 一键测试播放，让排查有据可依。
        acard = Card(col)
        acard.pack(fill="x", pady=(S(0), S(10)))
        self._card_title(acard, "音频输出",
                         "多屏或接 HDMI 时，系统默认输出常是显示器而不是音箱——"
                         "选错设备就会「画面在动却没声音」。")

        arow = tk.Frame(acard, bg=C_CARD)
        arow.pack(fill="x", padx=S(20), pady=S(6))
        tk.Label(arow, text="输出设备", width=10, anchor="w", bg=C_CARD,
                 fg=C_MUTED2, font=(FONT, 11)).pack(side="left")
        self.fields["output_device"] = tk.StringVar()
        self.device_combo = RoundedCombo(arow, textvariable=self.fields["output_device"],
                                         font_size=11, width=S(420), state="readonly")
        self.device_combo.pack(side="left")
        make_btn(arow, "刷新", self._refresh_devices, sm=True).pack(side="left", padx=S(8))
        self.device_list = []

        arow2 = tk.Frame(acard, bg=C_CARD)
        arow2.pack(fill="x", padx=S(20), pady=(S(2), S(4)))
        make_btn(arow2, "测试播放", self._test_audio, sm=True).pack(side="left")
        self.audio_label = tk.Label(arow2, text="", fg=C_MUTED2, bg=C_CARD,
                                    font=(FONT, 11), wraplength=S(560), justify="left")
        self.audio_label.pack(side="left", padx=S(12))

        arow3 = tk.Frame(acard, bg=C_CARD)
        arow3.pack(fill="x", padx=S(20), pady=(S(0), S(16)))
        self.fields["test_tone"] = tk.BooleanVar(value=True)
        make_switch(arow3, "连接建立后自动播放测试提示音", self.fields["test_tone"],
                    bg=C_CARD).pack(side="left")
        make_btn(arow3, "保存音频设置", self._save_audio, sm=True).pack(side="right")

        # ── 声音克隆 ──
        self.up = Card(col)
        self.up.pack(fill="x", pady=(S(0), S(16)))
        self._card_title(self.up, "声音克隆",
                         "上传一段 3~10 秒的人声，数字人就能用这个人的音色朗读讲稿。")

        st = tk.Frame(self.up, bg=C_CARD)
        st.pack(fill="x", padx=S(20), pady=(S(2), S(4)))
        self.clone_dot = tk.Label(st, text="●", font=(FONT, 11), bg=C_CARD, fg=C_DOT_OFF)
        self.clone_dot.pack(side="left")
        self.clone_state = tk.Label(st, text="检测中…", font=(FONT, 11), bg=C_CARD,
                                    fg=C_MUTED2)
        self.clone_state.pack(side="left", padx=(S(4), S(0)))
        make_btn(st, "启动服务", self._start_clone_service, sm=True, primary=True
                 ).pack(side="right")
        make_btn(st, "刷新", self._refresh_clone, sm=True).pack(side="right",
                                                               padx=(S(0), S(8)))

        uprow = tk.Frame(self.up, bg=C_CARD)
        uprow.pack(fill="x", padx=S(20), pady=(S(4), S(2)))
        make_btn(uprow, "选择音频并克隆", self._clone_upload, sm=True, primary=True
                 ).pack(side="left")
        self.up_label = tk.Label(uprow, text="", fg=C_MUTED2, bg=C_CARD, font=(FONT, 11),
                                 wraplength=S(560), justify="left")
        self.up_label.pack(side="left", padx=S(12))

        self.clone_list = tk.Frame(self.up, bg=C_CARD)
        self.clone_list.pack(fill="x", padx=S(20), pady=(S(6), S(14)))
        tk.Label(self.up, text="用克隆音色朗读有两条路：\n"
                               "① 本地 XTTS —— 引擎选 xtts，参考音色选下面列表里的名字；"
                               "离线可用，但首次加载约 48 秒、合成较慢。\n"
                               "② 云端复刻（推荐，速度与普通在线音色一致）—— 先运行 "
                               "scripts/register_voice.py 训练音色，再把引擎选 doubao、"
                               "参考音色填 S_ 开头的音色 ID。",
                 font=(FONT, 10), bg=C_CARD, fg=C_MUTED, justify="left",
                 wraplength=S(820)).pack(anchor="w", padx=S(20), pady=(S(0), S(14)))

        self.app.after(800, self._refresh_clone)
        self.app.after(600, self._refresh_devices)

        self.app.after(300, self._load_settings)

    @staticmethod
    def _card_title(card, title, hint=""):
        tk.Label(card, text=title, font=(FONT, 13, "bold"), bg=C_CARD, fg=C_INK
                 ).pack(anchor="w", padx=S(20), pady=(S(16), S(1)))
        if hint:
            tk.Label(card, text=hint, font=(FONT, 10), bg=C_CARD, fg=C_MUTED
                     ).pack(anchor="w", padx=S(20), pady=(0, S(8)))

    def _toggle_advanced(self):
        if self.adv_show.get():
            self.adv_frame.pack(fill="x", pady=(S(4), S(0)))
        else:
            self.adv_frame.pack_forget()

    def _apply_pending_device(self):
        """把设置里的设备 id 还原成下拉框标签（需设备列表已就绪）。"""
        pending = getattr(self, "_pending_device_id", "")
        if pending in ("", None):
            return
        try:
            target = int(pending)
        except (TypeError, ValueError):
            return
        for dev_id, name in self.device_list:
            if dev_id == target:
                self.fields["output_device"].set(name)
                return

    def _refresh_devices(self):
        """刷新可用音频输出设备列表。"""
        try:
            from utils.audio_playback import list_output_devices
            devices = list_output_devices()
        except Exception as exc:  # noqa: BLE001
            devices = [(None, "（系统默认）")]
            self.audio_label.config(text="读取设备失败: %r" % (exc,))
            return
        self.device_list = devices
        labels = [name for _, name in devices]
        try:
            self.device_combo.combo.configure(values=labels)
        except Exception:  # noqa: BLE001
            pass
        self._apply_pending_device()
        if not labels:
            self.audio_label.config(text="未找到音频输出设备")
        elif len(labels) == 1:
            self.audio_label.config(text="未检测到其它输出设备，将使用系统默认")

    def _selected_device(self):
        """把下拉框当前选中的标签映射回设备 id（None = 系统默认）。"""
        label = self.fields["output_device"].get()
        for dev_id, name in self.device_list:
            if name == label:
                return dev_id
        return None

    def _test_audio(self):
        """用当前选中的设备播一声提示音，验证本机扬声器通路。"""
        device = self._selected_device()
        player = AudioPlayer(device=device)
        player.start()
        try:
            if player.play_test_tone():
                # 提示音是异步播放的，等一小会儿让线程把声音写出去
                self.after(900, player.stop)
                self.audio_label.config(text="已发送测试音：若听不到，请换一个输出设备")
            else:
                self.audio_label.config(text="测试音播放失败，详见日志")
        except Exception as exc:  # noqa: BLE001
            self.audio_label.config(text="测试失败: %r" % (exc,))

    def _save_audio(self):
        """保存音频输出设置（走 /api/settings，与其它设置同源）。"""
        device = self._selected_device()
        payload = {
            "audio": {
                "output_device": "" if device is None else str(device),
                "test_tone": bool(self.fields["test_tone"].get()),
            },
        }
        try:
            r = self.app.sess.put(BASE + "/api/settings", json=payload, timeout=8)
            data = r.json()
            if data.get("code", -1) == 0:
                self.audio_label.config(text="已保存音频设置")
            else:
                self.audio_label.config(text="保存失败：" + str(data.get("msg", "")))
        except Exception as exc:  # noqa: BLE001
            self.audio_label.config(text="保存失败: %r" % (exc,))

    def _load_settings(self):
        try:
            r = self.app.sess.get(BASE + "/api/settings", timeout=6)
            data = r.json().get("data", {})
            tts = data.get("tts", {})
            avatar = data.get("avatar", {})
            self.fields["engine"].set(str(setting_value(tts.get("engine"), "edgetts")))
            self.fields["fallback"].set(str(setting_value(tts.get("fallback"), "")))
            self.fields["rate"].set(str(setting_value(tts.get("rate"), "+0%")))
            self._show_ref_label(str(setting_value(tts.get("ref_file"), "")))
            self.fields["ref_text"].set(str(setting_value(tts.get("ref_text"), "")))
            self.fields["avatar"].set(str(setting_value(avatar.get("id"), "")))
            audio = data.get("audio", {})
            self.fields["test_tone"].set(bool(setting_value(audio.get("test_tone"), True)))
            # 输出设备要等设备列表加载后才能还原成标签，先记下 id
            self._pending_device_id = setting_value(audio.get("output_device"), "")
            self._apply_pending_device()
        except Exception as exc:  # noqa: BLE001
            self.save_label.config(text="读取设置失败: %r" % (exc,))

    def _save(self):
        # /api/settings 现在按「分组对象」收，不再是 tts.engine 这种带点的扁平键
        payload = {
            "tts": {
                "engine": self.fields["engine"].get(),
                "fallback": self.fields["fallback"].get(),
                "rate": self.fields["rate"].get(),
                "ref_file": self._ref_value(),
                "ref_text": self.fields["ref_text"].get(),
            },
            "avatar": {"id": self.fields["avatar"].get()},
            "audio": {
                "output_device": ("" if self._selected_device() is None
                                  else str(self._selected_device())),
                "test_tone": bool(self.fields["test_tone"].get()),
            },
        }
        try:
            r = self.app.sess.put(BASE + "/api/settings", json=payload, timeout=8)
            data = r.json()
            if data.get("code", -1) == 0:
                self.save_label.config(text="已保存：%s" % ",".join(data.get("data", {}).get("changed", [])))
            else:
                self.save_label.config(text="保存失败：" + str(data.get("msg", "")))
        except Exception as exc:  # noqa: BLE001
            self.save_label.config(text="保存失败: %r" % (exc,))

    def _apply_tts_server(self):
        choice = self.tts_var.get()
        save_tts_choice(choice)
        preset = TTS_PRESETS.get(choice, TTS_PRESETS["xtts"])
        if preset["engine"]:
            # 同步把引擎设到设置里（对新建会话生效）
            try:
                self.app.sess.put(BASE + "/api/settings",
                                  json={"tts": {"engine": preset["engine"]}}, timeout=8)
            except Exception:  # noqa: BLE001
                pass
        self.save_label.config(text="已选择 %s（%s）。重启主服务后生效：请到「系统状态」页点「重启主服务」"
                               % (preset["name"], preset["server"] or "无本地服务"))

    def _refresh_voices(self):
        self._load_voices(refresh=True)

    def _load_voices(self, refresh=False):
        def work():
            system, uploaded = [], []
            try:
                r = self.app.sess.get(BASE + "/api/voices/system", timeout=12)
                data = r.json().get("data", {}) or {}
                if isinstance(data, dict):
                    system = (data.get("edge") or []) + (data.get("sapi") or [])
                else:
                    system = data if isinstance(data, list) else []
            except Exception:  # noqa: BLE001
                pass
            try:
                r = self.app.sess.get(BASE + "/api/voices", timeout=6)
                uploaded = r.json().get("data", [])
            except Exception:  # noqa: BLE001
                pass
            # 接口异常时可能吐 dict（错误体被当成 data），切片会直接抛 TypeError
            if not isinstance(uploaded, list):
                uploaded = []
            self.app.ui_q.put(("voices", (system, uploaded)))
        threading.Thread(target=work, daemon=True).start()

    def handle_voices(self, item):
        system, uploaded = item
        # 系统音色只保留经典那几把嗓子，其余不再往界面上堆；克隆音色取前 3 个。
        options = []
        seen = set()
        for row in system:
            if not isinstance(row, dict):
                continue
            _, value = voice_option(row)
            for cval, cname, ctrait in VOICE_CLASSICS:
                if value == cval and cval not in seen:
                    options.append((cval, cname, ctrait))
                    seen.add(cval)
        for idx, v in enumerate(uploaded[:3], 1):
            if isinstance(v, dict):
                name = str(v.get("name") or "") or os.path.splitext(str(v.get("path") or ""))[0]
                path = v.get("path") or name
            else:
                name = os.path.splitext(str(v))[0]
                path = str(v)
            label = name if _CJK_RE.search(name) else ("克隆音色 %d" % idx)
            options.append((path, label, "克隆音色"))
        self.voice_picker.set_options(options)
        self._show_ref_label(self.fields["ref_file"].get())

    def _on_voice_pick(self, value):
        """点了音色卡片：把自定义输入框同步成真实取值。"""
        self.fields["ref_file"].set(value)

    def _ref_value(self):
        label = self.fields["ref_file"].get().strip()
        if not label:
            return ""
        mapped = getattr(self, "_voice_values", {}).get(label)
        if mapped:
            return mapped
        # 手填的复刻音色 ID（S_xxxxxxxx）没有中文标签，必须原样下发
        return "" if "  ·  " in label else label

    def _show_ref_label(self, value):
        for label, val in getattr(self, "_voice_values", {}).items():
            if val == value:
                self.fields["ref_file"].set(label)
                return
        self.fields["ref_file"].set(value)

    # ── 声音克隆 ──
    def _set_clone_state(self, kind, text):
        color = {"ok": C_GREEN, "bad": C_DANGER, "busy": C_WARN}.get(kind, C_DOT_OFF)
        self.clone_dot.config(fg=color)
        self.clone_state.config(text=text, fg=color if kind != "unknown" else C_MUTED2)

    def _refresh_clone(self):
        """查克隆服务状态，顺带刷音色列表。"""
        try:
            r = self.app.sess.get(BASE + "/api/clone/status", timeout=4)
            data = r.json().get("data", {}) or {}
        except Exception:  # noqa: BLE001
            data = {}
        if not data.get("running"):
            self._set_clone_state("unknown", "服务未启动")
        elif data.get("loading"):
            self._set_clone_state("busy", "模型加载中…")
        elif data.get("ready"):
            self._set_clone_state("ok", "就绪（%s）" % (data.get("device") or "cpu"))
        else:
            self._set_clone_state("bad", "模型未就绪：%s" % (data.get("error") or "未知"))
        self._load_cloned()
        return data

    def _start_clone_service(self):
        """拉起克隆服务进程。模型没下载时先提示下载，别让用户干等一个必然失败的服务。"""
        model_dir = os.path.join(ROOT, "data", "models", "xtts_v2")
        if not os.path.isfile(os.path.join(model_dir, "model.pth")):
            if not messagebox.askyesno(
                    "声音克隆",
                    "还没下载克隆模型（XTTS-v2，约 1.9GB）。\n\n"
                    "现在开始下载吗？下载完才能启动服务。"):
                return
            self._set_clone_state("busy", "正在下载模型（约 1.9GB）…")
            threading.Thread(target=self._fetch_model, daemon=True).start()
            return
        self._set_clone_state("busy", "正在启动服务…")
        threading.Thread(target=self._spawn_clone_service, daemon=True).start()

    def _spawn_clone_service(self):
        log_dir = os.path.join(ROOT, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = open(os.path.join(log_dir, "voice_clone.log"), "a",
                        encoding="utf-8", errors="replace")
        try:
            subprocess.Popen(
                [PYTHON, os.path.join(ROOT, "scripts", "voice_clone_server.py")],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        except Exception as exc:  # noqa: BLE001
            self.app.after(0, lambda: self._set_clone_state("bad", "启动失败: %r" % (exc,)))
            return
        # 等服务就绪（模型加载要几十秒），期间持续回报状态
        for _ in range(90):
            time.sleep(2)
            data = self._refresh_clone()
            if data.get("ready") or not data.get("running"):
                return
        self.app.after(0, lambda: self._set_clone_state(
            "bad", "启动超时，请看 logs/voice_clone.log"))

    def _fetch_model(self):
        proc = subprocess.run(
            [PYTHON, os.path.join(ROOT, "scripts", "fetch_voice_clone_model.py")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok = proc.returncode == 0
        tail = (proc.stdout or proc.stderr or "").strip().split("\n")[-1:]
        self.app.after(0, lambda: (
            self._set_clone_state("ok" if ok else "bad",
                                  "模型下载完成，可以启动服务了" if ok
                                  else "下载失败：%s" % (tail[0] if tail else "见日志")),
            self.up_label.config(text="", fg=C_MUTED2)))

    def _clone_upload(self):
        path = filedialog.askopenfilename(
            title="选择一段人声（3~10 秒效果最好）",
            filetypes=[("音频", "*.wav *.mp3 *.ogg *.m4a *.flac *.webm"),
                       ("所有文件", "*.*")])
        if not path:
            return
        self.up_label.config(text="正在克隆…", fg=C_MUTED2)
        threading.Thread(target=self._clone_worker, args=(path,), daemon=True).start()

    def _clone_worker(self, path):
        try:
            with open(path, "rb") as fh:
                r = self.app.sess.post(BASE + "/api/clone/speaker",
                                       files={"wav_file": (os.path.basename(path), fh)},
                                       timeout=300)
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            self.app.after(0, lambda: (
                self.up_label.config(text="克隆失败：服务未启动或不可用（%r）" % (exc,),
                                     fg=C_DANGER),
                self._refresh_clone()))
            return
        if data.get("speaker_id"):
            sid = data["speaker_id"]
            self.app.after(0, lambda: (
                self.up_label.config(text="克隆完成：%s，可在参考音色里选用" % sid,
                                     fg=C_GREEN),
                self._load_voices(refresh=True),
                self._load_cloned()))
        else:
            err = data.get("msg") or data.get("error") or "未知错误"
            self.app.after(0, lambda: self.up_label.config(
                text="克隆失败：%s" % err, fg=C_DANGER))

    def _load_cloned(self):
        """列出已克隆的音色（一栏一个：名称 / 是否已算好 / 试听 / 删除）。"""
        for w in list(self.clone_list.winfo_children()):
            w.destroy()
        try:
            r = self.app.sess.get(BASE + "/api/clone/speakers", timeout=5)
            items = (r.json().get("speakers") or []) if r.json().get("code", 1) == 0 \
                else (r.json().get("speakers") or [])
        except Exception:  # noqa: BLE001
            return
        # 只显示真正做过克隆的（有 latent 缓存的），避免把随便上传的参考音频也列进来
        items = [it for it in items if isinstance(it, dict) and it.get("cached")]
        if not items:
            tk.Label(self.clone_list, text="还没有克隆音色", font=(FONT, 11),
                     bg=C_CARD, fg=C_MUTED2).pack(anchor="w", pady=S(2))
            return
        for it in items[:8]:
            row = tk.Frame(self.clone_list, bg=C_CARD_ALT,
                           highlightbackground=C_LINE_SOFT, highlightthickness=1)
            row.pack(fill="x", pady=S(3))
            inner = tk.Frame(row, bg=C_CARD_ALT)
            inner.pack(fill="x", padx=S(10), pady=S(6))
            tk.Label(inner, text=it.get("name", ""), font=(FONT, 12), bg=C_CARD_ALT,
                     fg=C_INK).pack(side="left")
            tk.Label(inner, text="%.1f MB" % (it.get("size", 0) / 1048576.0),
                     font=(FONT, 10), bg=C_CARD_ALT, fg=C_MUTED2
                     ).pack(side="left", padx=(S(8), S(0)))
            make_btn(inner, "删除", lambda s=it.get("name"): self._del_clone(s),
                     sm=True, fg=C_DANGER).pack(side="right")
            make_btn(inner, "试听", lambda s=it.get("name"): self._preview_clone(s),
                     sm=True).pack(side="right", padx=(S(0), S(6)))

    def _preview_clone(self, sid):
        self.up_label.config(text="正在合成试听…", fg=C_MUTED2)
        threading.Thread(target=self._preview_worker, args=(sid,), daemon=True).start()

    def _preview_worker(self, sid):
        try:
            r = self.app.sess.post(BASE + "/api/clone/preview",
                                   json={"speaker_id": sid,
                                         "text": "这是用这段声音克隆出来的音色，你觉得像吗？"},
                                   timeout=180)
            if r.status_code != 200 or len(r.content) < 100:
                self.app.after(0, lambda: self.up_label.config(
                    text="试听失败：%s" % (r.text[:80] if r.text else "服务无响应"),
                    fg=C_DANGER))
                return
            import wave as _wave
            from io import BytesIO
            with _wave.open(BytesIO(r.content)) as wf:
                sr = wf.getframerate()
                arr = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            self.app.audio_player.play(arr, sr)
            self.app.after(0, lambda: self.up_label.config(text="正在播放试听…", fg=C_GREEN))
        except Exception as exc:  # noqa: BLE001
            self.app.after(0, lambda: self.up_label.config(
                text="试听失败：%r" % (exc,), fg=C_DANGER))

    def _del_clone(self, sid):
        if not messagebox.askyesno("删除音色", "确定删除克隆音色「%s」？" % sid):
            return
        try:
            self.app.sess.delete(BASE + "/api/clone/speaker/%s" % sid, timeout=10)
        except Exception as exc:  # noqa: BLE001
            self.up_label.config(text="删除失败：%r" % (exc,), fg=C_DANGER)
            return
        self.up_label.config(text="已删除", fg=C_GREEN)
        self._load_voices(refresh=True)
        self._load_cloned()


# ── 系统状态页 ────────────────────────────────────────────────────────
class Meter(tk.Canvas):
    """资源占用条：标题 + 右侧数值 + 底部轨道。

    用 Canvas 自绘是因为 ttk.Progressbar 配色受系统主题限制，在这套米白
    卡片里会露出灰蓝底；自绘的圆角轨道才能跟卡片融在一起。
    """

    def __init__(self, parent, title, width=S(230), bar_h=S(8)):
        # 注意：别碰 `self._w`——那是 tkinter 存 widget 名的内部属性，
        # 覆盖后 pack() 会拿到一个数字当窗口名而报 "bad argument"。
        self._h = S(62)
        super().__init__(parent, width=width, height=self._h,
                         bg=_widget_bg(parent), highlightthickness=0, bd=0)
        self._width = width
        self._bar_h = bar_h
        self._title = title
        self._percent = 0.0
        self._value = "—"
        self._color = C_GREEN
        self.bind("<Configure>", lambda e: self._paint())

    def set(self, percent, value_text, color=None):
        self._percent = max(0.0, min(100.0, float(percent or 0)))
        self._value = value_text or "—"
        self._color = color or (C_DANGER if self._percent >= 88
                                else C_WARN if self._percent >= 70 else C_GREEN)
        self._paint()

    def _paint(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 8 or h < 8:
            return
        self.delete("all")
        pad = S(12)
        self.create_text(pad, S(12), anchor="nw", text=self._title,
                         font=(FONT, 11), fill=C_MUTED2)
        self.create_text(w - pad, S(12), anchor="ne", text=self._value,
                         font=(FONT, 11, "bold"), fill=C_INK)
        y0 = h - self._bar_h - S(12)
        r = self._bar_h / 2.0
        x0, x1 = pad, w - pad
        # 轨道
        self.create_rectangle(x0, y0, x1, y0 + self._bar_h, fill=C_LINE_SOFT, outline="")
        # 填充（至少留一个圆角，不然 0% 时是一条细线，看不出是进度条）
        fill_w = max(self._bar_h, (x1 - x0) * self._percent / 100.0)
        self.create_oval(x0, y0, x0 + self._bar_h, y0 + self._bar_h,
                         fill=self._color, outline="")
        self.create_rectangle(x0 + r, y0, x0 + fill_w, y0 + self._bar_h,
                              fill=self._color, outline="")
        if fill_w > self._bar_h:
            self.create_oval(x0 + fill_w - self._bar_h, y0, x0 + fill_w, y0 + self._bar_h,
                             fill=self._color, outline="")


class StatusPage(tk.Frame):
    """系统状态：由主界面的单一后台轮询提供状态快照。"""

    def __init__(self, parent, app):
        super().__init__(parent, bg=C_BG)
        self.app = app
        self.data = {}

        head = tk.Frame(self, bg=C_BG)
        head.pack(fill="x", padx=S(24), pady=(S(18), S(2)))
        tk.Label(head, text="系统状态", font=(FONT, 20, "bold"), bg=C_BG, fg=C_INK
                 ).pack(side="left")
        make_btn(head, "刷新", self._refresh, sm=True).pack(side="right")
        make_btn(head, "重启主服务", self._restart_service, sm=True, primary=True
                 ).pack(side="right", padx=(S(0), S(8)))

        self.body = ScrollFrame(self, bg=C_BG)
        self.body.pack(fill="both", expand=True, padx=S(24), pady=(S(12), S(4)))

        # ── 总状态卡 ──
        self.top_card = Card(self.body.inner)
        self.top_card.pack(fill="x", pady=(S(0), S(10)))
        self.top_row = tk.Frame(self.top_card, bg=C_CARD)
        self.top_row.pack(fill="x", padx=S(16), pady=S(14))
        self.big_dot = tk.Label(self.top_row, text="●", font=(FONT, 20), bg=C_CARD,
                                fg=C_GREEN)
        self.big_dot.pack(side="left")
        self.big_text = tk.Label(self.top_row, text="主服务运行中", font=(FONT, 16, "bold"),
                                 bg=C_CARD, fg=C_INK)
        self.big_text.pack(side="left", padx=(S(10), S(0)))
        self.uptime_text = tk.Label(self.top_row, text="", font=(FONT, 11), bg=C_CARD,
                                    fg=C_MUTED2)
        self.uptime_text.pack(side="right")

        # ── 资源条 ──
        res = tk.Frame(self.body.inner, bg=C_BG)
        res.pack(fill="x", pady=(S(0), S(10)))
        self.m_cpu = Meter(res, "CPU 使用率")
        self.m_cpu.pack(side="left", fill="x", expand=True, padx=(S(0), S(10)))
        self.m_ram = Meter(res, "内存")
        self.m_ram.pack(side="left", fill="x", expand=True, padx=(S(0), S(10)))
        self.m_gpu = Meter(res, "显存")
        self.m_gpu.pack(side="left", fill="x", expand=True)

        # ── 组件清单 ──
        card = Card(self.body.inner)
        card.pack(fill="x", pady=(S(0), S(10)))
        tk.Label(card, text="组件", font=(FONT, 14, "bold"), bg=C_CARD, fg=C_INK
                 ).pack(anchor="w", padx=S(16), pady=(S(12), S(6)))
        self.rows = {}
        for key in ("engine", "tts", "rag", "llm", "sessions"):
            self.rows[key] = self._mk_row(card)
        tk.Frame(card, bg=C_CARD, height=S(6)).pack()

        self.rag_var = tk.BooleanVar(value=True)
        make_switch(self, "启动时启用本地 RAG 知识库", self.rag_var,
                    command=self._toggle_rag, bg=C_BG,
                    hint="内存紧张可关闭，重启主服务生效"
                    ).pack(anchor="w", padx=S(24), pady=(S(0), S(10)))

        self._load_rag_flag()
        self._refresh()

    def _mk_row(self, parent):
        row = tk.Frame(parent, bg=C_CARD)
        row.pack(fill="x", padx=S(16), pady=S(4))
        dot = tk.Label(row, text="●", font=(FONT, 10), bg=C_CARD, fg=C_DOT_OFF)
        dot.pack(side="left")
        name = tk.Label(row, text="", font=(FONT, 12), bg=C_CARD, fg=C_INK, width=12,
                        anchor="w")
        name.pack(side="left", padx=(S(8), S(0)))
        val = tk.Label(row, text="—", font=(FONT, 12), bg=C_CARD, fg=C_MUTED2, anchor="w")
        val.pack(side="left", fill="x", expand=True)
        return {"dot": dot, "name": name, "val": val}

    def _set_row(self, key, ok, name, value, unknown=False):
        r = self.rows[key]
        r["dot"].config(fg=C_DOT_OFF if unknown else (C_GREEN if ok else C_DANGER))
        r["name"].config(text=name)
        r["val"].config(text=value, fg=C_MUTED2 if ok else C_DANGER)

    # ── 数据 ──
    def _refresh(self):
        main = getattr(self.app, "_main", None)
        if main is not None:
            main.refresh_status()

    def _fmt_uptime(self, sec):
        try:
            sec = int(float(sec or 0))
        except (TypeError, ValueError):
            return ""
        if sec < 60:
            return "已运行 %d 秒" % sec
        if sec < 3600:
            return "已运行 %d 分钟" % (sec // 60)
        return "已运行 %d 小时 %d 分" % (sec // 3600, (sec % 3600) // 60)

    @staticmethod
    def _gb(mb):
        try:
            return float(mb) / 1024.0
        except (TypeError, ValueError):
            return 0.0

    def update_status(self, data):
        online = bool(data)
        self.big_dot.config(fg=C_GREEN if online else C_DANGER)
        self.big_text.config(text="主服务运行中" if online else "主服务未连接",
                             fg=C_INK if online else C_DANGER)
        self.uptime_text.config(text=self._fmt_uptime(data.get("uptime_s")) if online else "")

        sysinfo = data.get("system") or {}
        self.m_cpu.set(sysinfo.get("cpu_percent", 0),
                       "%.0f%%" % sysinfo.get("cpu_percent", 0) if sysinfo else "—")
        if sysinfo.get("ram_total_mb"):
            self.m_ram.set(sysinfo.get("ram_percent", 0),
                           "%.1f / %.1f GB" % (self._gb(sysinfo.get("ram_used_mb")),
                                               self._gb(sysinfo.get("ram_total_mb"))))
        else:
            self.m_ram.set(0, "—")

        gpu = data.get("gpu")
        if gpu and gpu.get("total_mb"):
            self.m_gpu.set(gpu.get("used_percent", 0),
                           "%.1f / %.1f GB" % (self._gb(gpu.get("used_mb")),
                                               self._gb(gpu.get("total_mb"))))
        else:
            self.m_gpu.set(0, "未检测到 NVIDIA 显卡")

        model = data.get("model") or ""
        self._set_row("engine", bool(model), "数字人引擎",
                      (model or "未加载") + (" · %s fps" % data.get("fps")
                                            if data.get("fps") else ""))
        voice = data.get("voice") or ""
        self._set_row("tts", bool(voice), "语音合成",
                      "%s · %s" % (data.get("tts_engine") or TTS_PRESETS.get(
                          self.app.tts_choice, {}).get("name", "-"),
                                   _match_voice_zh(voice) or voice or "未配置"))
        self._set_row("rag", bool(data.get("rag_available")), "知识库",
                      "已连接" if data.get("rag_available") else "未运行")
        self._set_row("llm", bool(data.get("llm_configured")), "大模型",
                      "已配置" if data.get("llm_configured") else "未配置（.env 里填 DEEPSEEK_API_KEY）")
        n = data.get("sessions", 0)
        self._set_row("sessions", True, "活跃会话", "%d 个" % n)

    # ── 生命周期 ──
    def on_show(self):
        self._refresh()

    def on_hide(self):
        pass

    # ── 操作 ──
    def _load_rag_flag(self):
        try:
            r = self.app.sess.get(BASE + "/api/settings", timeout=6)
            data = r.json().get("data", {}) or {}
            rag = data.get("rag", {}) or {}
            self.rag_var.set(setting_bool(rag.get("RAG_ENABLED"), True))
        except Exception:  # noqa: BLE001
            pass

    def _toggle_rag(self):
        try:
            r = self.app.sess.put(BASE + "/api/settings",
                                  json={"rag": {"RAG_ENABLED": bool(self.rag_var.get())}},
                                  timeout=8)
            data = r.json()
            if data.get("code", -1) == 0:
                messagebox.showinfo("RAG 设置", "已保存。重启主服务后生效（可点右上角「重启主服务」）。")
            else:
                self.rag_var.set(not self.rag_var.get())
                messagebox.showwarning("RAG 设置", "保存失败: %s" % data.get("msg", ""))
        except Exception as exc:  # noqa: BLE001
            self.rag_var.set(not self.rag_var.get())
            messagebox.showwarning("RAG 设置", "保存失败: %r" % (exc,))

    def _restart_service(self):
        if messagebox.askyesno("重启主服务", "确定重启主服务进程吗？重启约需 1-2 分钟。"):
            self.app.status_var.set("正在重启主服务…")
            threading.Thread(target=self._restart_worker, daemon=True).start()

    def _restart_worker(self):
        if self.app.launcher is not None:
            self.app.launcher.stop()
        elif service_alive():
            # 用户手工启动主服务时也要先关掉该实例，否则会让新进程撞上 8010 端口。
            try:
                requests.post(BASE + "/api/system/shutdown", timeout=5)
            except Exception:  # noqa: BLE001 - 已停止同样可以继续拉起
                pass
        time.sleep(3)
        launcher = ServiceLauncher(
            self.app.tts_choice,
            ready_cb=lambda: self.app.ui_q.put(("ready", True)),
            fail_cb=lambda m: self.app.ui_q.put(("fail", m)),
            progress_cb=lambda m: self.app.ui_q.put(("busy", m)),
        )
        self.app.launcher = launcher
        launcher.start()


def main():
    try:
        import PIL  # noqa: F401
    except ImportError:
        subprocess.run([PYTHON, "-m", "pip", "install", "pillow",
                        "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple"],
                       check=False)
    app = DesktopApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    # 登录页由事件泵在「服务就绪 + 鉴权结果」后统一触发，不再用一次性 after，
    # 否则服务未就绪时 _pending_login 还是 None，登录页永远不弹（白屏）。
    app.mainloop()


if __name__ == "__main__":
    main()
