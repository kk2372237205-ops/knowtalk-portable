# -*- coding: utf-8 -*-
"""数字人背景：把抠好的人像合成到自定义背景上。

素材来源：
    data/avatars/<id>/matte/{base,gestures/<gid>}/<帧号>.png   由 scripts/avatar_matte.py 生成
    data/backgrounds/*                                          用户上传的背景图
    data/settings/background.json                               当前选中的背景

设计要点：
- 合成发生在**取帧**这一层（base_avatar._frame_at），拿到的帧 = 源帧 + 人脸替换，
  和源帧的轮廓一致，所以能直接套同一套 alpha；不必再改推理或推流逻辑。
- 没配背景时 Compositor 根本不建，热路径零开销。
- 背景图按 cover 方式铺满（等比放大 + 居中裁切），不会把人拉变形，也不留白边。
- 抠像把原素材脚下的接触阴影一起丢了（alpha 低于外推下限），换背景后人像是"浮"着的，
  所以合成时按该帧自己的 alpha 重新画一片接地阴影（虚拟投影），见 `_foot_band` / `_shadow_for`。
"""
import json
import os
import threading
import time

import cv2
import numpy as np

from utils.logger import logger

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
AVATAR_ROOT = os.path.join(DATA_DIR, "avatars")
BACKGROUND_DIR = os.path.join(DATA_DIR, "backgrounds")
CONFIG_PATH = os.path.join(DATA_DIR, "settings", "background.json")
SHADOW_CONFIG_PATH = os.path.join(DATA_DIR, "settings", "shadow.json")
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

_LOCK = threading.Lock()

# alpha ≥ 这个值算「实心人体」，前景色从这里往外推
SOLID = 250
# 低于这个值的不算人体边缘（脚下接触阴影那类），不参与外推
EDGE_MIN = 25
K3 = np.ones((3, 3), np.uint8)

# 内置纯色背景（前端下拉直接选，不用上传图片）
PRESET_COLORS = [
    ("studio_navy", "深蓝棚拍", "#101F33"),
    ("studio_gray", "浅灰影棚", "#D9DDE2"),
    ("studio_warm", "暖米色", "#EFE6D8"),
    ("studio_green", "墨绿", "#152820"),
]

# ── 虚拟投影（接地阴影）默认参数 ────────────────────────────────────
# 抠像把原素材脚下的接触阴影当背景丢了，人像贴到新背景上脚下是一条齐刷刷的切口，
# 近看就是"浮"着的。这里在**新背景**上重新画一片椭圆软阴影补回落地感。
DEFAULT_SHADOW = {
    "enabled": True,
    "strength": 0.62,   # 最暗处压掉多少亮度（0=没有，1=压到底）
    "spread": 1.25,     # 阴影半宽相对「脚宽」的倍数
    "flatten": 0.26,    # 纵向压扁比（半高 / 半宽）——地面视角看到的投影是扁的
    "softness": 0.80,   # 边缘过渡宽度：越大越散越淡，越小越实越硬
    "offset_y": 0.0,    # 纵向偏移，按画面高的比例（正值=往下挪）
    "shade": 0.28,      # 阴影最暗处保留的背景亮度比（0=纯黑，1=没压暗）
}
# 各字段的取值范围，界面滑块与文件读入共用，别两处各写一份
SHADOW_RANGES = (
    ("strength", 0.0, 1.0),
    ("spread", 0.3, 3.0),
    ("flatten", 0.05, 1.0),
    ("softness", 0.1, 1.0),
    ("offset_y", -0.1, 0.1),
    ("shade", 0.0, 1.0),
)
# 算「实心脚部」的 alpha 下限：比外推用的 SOLID(250) 低一些，
# 鞋子和裤脚边缘抠完本来就偏软，卡 250 会把脚切没。
FOOT_MIN = 200
# 接触带高度（占画面高的比例）：只在最低行上方这一小段里求水平跨度，
# 取太厚会把张开的双臂算进脚宽，阴影跟着变成一大片。
FOOT_BAND = 0.035
# 带内实心像素少于这个数就认为「脚不可靠」（被下边缘裁掉、或只剩一条边），不画阴影
FOOT_MIN_PX = 60
# 阴影缓存条目上限：键里带参数签名，拖滑块会不断产生新键，满了就整表清掉
_SHADOW_CACHE_MAX = 512


# 文件名 → 中文显示名（下拉里给用户看的是这个；文件名保持 ASCII，避免编码问题）
BG_LABELS = {
    "training_medical": "医疗实训室走廊",
    "training_dome": "科技环形大厅",
    "training_stage": "环形实训台",
    "training_classroom": "明亮实训室",
    "studio_gray": "浅灰影棚",
    "warm_office": "暖色办公室",
    "tech_dark": "深蓝科技",
}


def bg_label(filename):
    """给背景图一个中文显示名；没有映射就退回文件名（去扩展名）。"""
    stem = os.path.splitext(os.path.basename(filename))[0]
    return BG_LABELS.get(stem, stem)


# ── 配置读写 ─────────────────────────────────────────────────────────
def load_config():
    """返回 {"type": "none"|"color"|"image", "value": "..."}；读不到就当没配。"""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {"type": "none", "value": ""}
        kind = data.get("type") or "none"
        if kind not in ("none", "color", "image"):
            kind = "none"
        return {"type": kind, "value": str(data.get("value") or "")}
    except Exception:  # noqa: BLE001
        return {"type": "none", "value": ""}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    payload = {"type": cfg.get("type") or "none", "value": str(cfg.get("value") or "")}
    with _LOCK:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    return payload


def normalize_shadow(raw):
    """把外部传入/文件读出的阴影配置收敛成合法值。

    界面滑块、HTTP 请求、旧版本文件三种来源都可能给出越界值或非数字，
    统一在这里夹一遍，下游就不用到处写 `if 0 <= x <= 1`。
    """
    out = dict(DEFAULT_SHADOW)
    if not isinstance(raw, dict):
        return out
    out["enabled"] = bool(raw.get("enabled", out["enabled"]))
    for key, lo, hi in SHADOW_RANGES:
        try:
            value = float(raw.get(key))
        except (TypeError, ValueError):
            continue
        if value != value:                       # NaN 直接跳过，别把整份配置带坏
            continue
        out[key] = min(hi, max(lo, value))
    return out


def load_shadow_config():
    """返回接地阴影配置；读不到就用默认值（默认开启）。"""
    try:
        with open(SHADOW_CONFIG_PATH, encoding="utf-8") as fh:
            return normalize_shadow(json.load(fh))
    except Exception:  # noqa: BLE001
        return dict(DEFAULT_SHADOW)


def save_shadow_config(cfg):
    os.makedirs(os.path.dirname(SHADOW_CONFIG_PATH), exist_ok=True)
    payload = normalize_shadow(cfg)
    with _LOCK:
        with open(SHADOW_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    return payload


def list_backgrounds():
    """已上传的背景图列表。"""
    os.makedirs(BACKGROUND_DIR, exist_ok=True)
    items = []
    for name in sorted(os.listdir(BACKGROUND_DIR)):
        if not name.lower().endswith(IMG_EXTS):
            continue
        path = os.path.join(BACKGROUND_DIR, name)
        items.append({"name": name, "label": bg_label(name),
                      "path": rel_path(path), "size": os.path.getsize(path)})
    return items


def rel_path(path):
    """统一成 data/... 这种相对路径，方便跨端传递。"""
    try:
        return os.path.relpath(path, ROOT).replace("\\", "/")
    except Exception:  # noqa: BLE001
        return path


def abs_path(path):
    if not path:
        return ""
    return path if os.path.isabs(path) else os.path.join(ROOT, path.replace("/", os.sep))


def resolve_color(value):
    """'#RRGGBB' → (B, G, R)。"""
    text = (value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) != 6:
        return None
    try:
        r, g, b = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None
    return (b, g, r)


def load_background_image(cfg):
    """把当前配置解析成一张背景图（BGR）；纯色会生成一张 1x1 后再铺。"""
    if not cfg or cfg.get("type") in (None, "none"):
        return None
    kind, value = cfg.get("type"), cfg.get("value")
    if kind == "color":
        color = resolve_color(value)
        if color is None:
            return None
        return np.full((8, 8, 3), color, dtype=np.uint8)
    if kind == "image":
        path = abs_path(value)
        if not path or not os.path.isfile(path):
            return None
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        return img
    return None


# ── 合成器 ───────────────────────────────────────────────────────────
class AvatarBackground:
    """把单帧人像合成到背景上；alpha 按「帧源 + 帧号」查表。"""

    def __init__(self, avatar_id, cfg=None, cache_size=48, shadow=None):
        self.avatar_id = avatar_id
        self.cfg = cfg if cfg is not None else load_config()
        self.shadow = normalize_shadow(shadow) if shadow is not None else load_shadow_config()
        self.enabled = False
        self._shadow_cache = {}      # (source, stem, w, h, 参数签名) -> 阴影图块或 None
        self._bg_cache = {}          # (w, h) -> uint8 BGR
        self._alpha_cache = {}       # key -> uint8 灰度
        self._alpha_order = []
        self._a3_cache = {}          # key -> uint8 三通道 alpha（省掉每帧的 cvtColor）
        self._cache_size = cache_size
        self._sources = {}           # "base"/"gestures.x" -> [stem, ...]
        self._warned = False
        self._missing = set()        # 已警告过「缺动作 alpha」的帧源，别每帧刷日志
        self._out_cache = {}         # (source, stem, w, h) -> 合成好的帧（同一帧会反复用）
        self._out_order = []

        self._cfg_mtime = self._stamp()
        self._next_check = 0.0
        self._bgimg = load_background_image(self.cfg)
        if self._bgimg is None:
            return
        self._scan_mattes()
        if not self._sources.get("base"):
            logger.warning("[背景] 形象 %s 还没有抠像素材，先跑 scripts/avatar_matte.py",
                           avatar_id)
            return
        self.enabled = True
        logger.info("[背景] 已启用：%s（形象 %s）", self.cfg, avatar_id)

    # ── 配置热重载 ──
    @staticmethod
    def _file_stamp(path):
        """「修改时间 + 大小」，变了就说明用户改过这个文件。"""
        try:
            st = os.stat(path)
            return (st.st_mtime, st.st_size)
        except Exception:  # noqa: BLE001
            return None

    def _stamp(self):
        """背景和阴影两个配置一起盯：拖了强度滑块也要立刻生效。"""
        return (self._file_stamp(CONFIG_PATH),
                self._file_stamp(SHADOW_CONFIG_PATH))

    def _maybe_reload(self):
        """用户在界面里换了背景或阴影参数后，不必重启服务——下一帧自动切换。

        每帧都 stat 太浪费，热路径上按 0.5s 节流。
        """
        now = time.time()
        if now < self._next_check:
            return
        self._next_check = now + 0.5
        stamp = self._stamp()
        if stamp == self._cfg_mtime:
            return
        self._cfg_mtime = stamp
        try:
            new_cfg = load_config()
            new_shadow = load_shadow_config()
            shadow_changed = new_shadow != self.shadow
            self.shadow = new_shadow
            new_img = load_background_image(new_cfg)
            if shadow_changed:
                # 阴影图的缓存键里带参数签名，改了参数本来就不会命中旧键；
                # 但合成结果缓存（`_out_cache`）不带签名，必须一起作废
                self._shadow_cache.clear()
                self._out_cache.clear()
                self._out_order = []
                logger.info("[背景] 虚拟投影参数已更新：%s", self.shadow)
            if new_img is None:                      # 换成了「原背景」→ 关掉合成
                self.cfg, self._bgimg, self.enabled = new_cfg, None, False
                self._reset_caches()
                logger.info("[背景] 已关闭（恢复原背景）")
                return
            self.cfg, self._bgimg = new_cfg, new_img
            self._sources = {}
            self._scan_mattes()
            self._reset_caches()
            self.enabled = bool(self._sources.get("base"))
            logger.info("[背景] 已切换：%s（enabled=%s）", self.cfg, self.enabled)
        except Exception:  # noqa: BLE001
            logger.exception("[背景] 热切换失败，保持原设置")

    def _reset_caches(self):
        self._bg_cache.clear()
        self._alpha_cache.clear()
        self._alpha_order = []
        self._a3_cache.clear()
        self._shadow_cache.clear()
        self._out_cache.clear()
        self._out_order = []

    # ── 素材索引 ──
    def _matte_root(self):
        return os.path.join(AVATAR_ROOT, self.avatar_id, "matte")

    def _scan_mattes(self):
        root = self._matte_root()
        base_dir = os.path.join(root, "base")
        if os.path.isdir(base_dir):
            self._sources["base"] = sorted(
                os.path.splitext(n)[0] for n in os.listdir(base_dir)
                if n.lower().endswith(".png"))
        gestures_dir = os.path.join(root, "gestures")
        if os.path.isdir(gestures_dir):
            for gid in os.listdir(gestures_dir):
                folder = os.path.join(gestures_dir, gid)
                if not os.path.isdir(folder):
                    continue
                stems = sorted(os.path.splitext(n)[0] for n in os.listdir(folder)
                               if n.lower().endswith(".png"))
                if stems:
                    self._sources["gestures.%s" % gid] = stems

    # ── 背景准备 ──
    def _background_for(self, width, height):
        """铺满画面的背景（uint8 BGR，连续内存——cv2 定点运算要求连续）。"""
        cached = self._bg_cache.get((width, height))
        if cached is not None:
            return cached
        src = self._bgimg
        if src is None:
            return None
        sh, sw = src.shape[:2]
        if sw == 1 and sh == 1:                      # 纯色
            bg = np.empty((height, width, 3), np.uint8)
            bg[:, :] = src[0, 0]
        else:
            scale = max(width / float(sw), height / float(sh))   # cover：铺满不留边
            nw, nh = max(width, int(round(sw * scale))), max(height, int(round(sh * scale)))
            resized = cv2.resize(src, (nw, nh), interpolation=cv2.INTER_AREA
                                 if scale < 1 else cv2.INTER_LINEAR)
            x0 = (nw - width) // 2
            y0 = (nh - height) // 2
            bg = np.ascontiguousarray(resized[y0:y0 + height, x0:x0 + width])
        self._bg_cache[(width, height)] = bg
        return bg

    # ── alpha 读取 ──
    def _alpha_dir(self, source):
        if source == "base" or not source.startswith("gestures."):
            return os.path.join(self._matte_root(), "base")
        return os.path.join(self._matte_root(), "gestures", source.split(".", 1)[1])

    def _alpha_for(self, source, idx, shape):
        """读该帧源自己的 alpha。

        **不再回退 base**：base 是站姿、动作是抬手/前伸，两套轮廓完全对不上，
        套错 alpha 会把动作帧里的白背景当成人体保留下来，深色底上就是一大块白影；
        而且 base 只有一帧，回退后所有动作帧的缓存键会撞在一起，画面整段冻住。
        缺素材就返回 None，由 `compose` 决定「这一帧不换背景」。
        """
        stems = self._sources.get(source)
        if not stems:
            return None
        stem = stems[int(idx or 0) % len(stems)]
        key = "%s|%s" % (source, stem)
        cached = self._alpha_cache.get(key)
        if cached is not None:
            return cached
        path = os.path.join(self._alpha_dir(source), stem + ".png")
        alpha = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if alpha is None:
            if not self._warned:
                self._warned = True
                logger.warning("[背景] alpha 读不到：%s（这一帧保持原背景）", path)
            return None
        if alpha.shape[:2] != shape[:2]:
            alpha = cv2.resize(alpha, (shape[1], shape[0]), interpolation=cv2.INTER_LINEAR)
        self._alpha_cache[key] = alpha
        self._alpha_order.append(key)
        if len(self._alpha_order) > self._cache_size:
            old = self._alpha_order.pop(0)
            self._alpha_cache.pop(old, None)
            self._a3_cache.pop(old, None)
        return alpha

    def _alpha3_for(self, key, alpha):
        """三通道化并缓存的 alpha / 反 alpha（cv2 的定点乘法要求通道数一致）。"""
        cached = self._a3_cache.get(key)
        if cached is not None:
            return cached
        a3 = cv2.cvtColor(alpha, cv2.COLOR_GRAY2BGR)
        # 合成要用 a 和 (255-a) 两张，一起缓存，省掉每帧一次 bitwise_not
        self._a3_cache[key] = (a3, cv2.bitwise_not(a3))
        return self._a3_cache[key]

    # ── 虚拟投影 ──
    @staticmethod
    def _foot_band(alpha):
        """从该帧自己的 alpha 求脚部接触带：{"y", "x0", "x1", "cx", "w"}。

        触底 y 取实心像素的最低行；接触宽度在它**上方一小段**里求——取太厚会把
        张开的双臂算进脚宽，阴影就跟着变成横贯画面的一大片。

        各帧独立算：动作里有抬腿、侧身、重心转移，接触点会左右移动，
        用一套固定的画面底部椭圆会在人动起来时脱脚。

        人像被画面下边缘裁掉、或带内像素太少时返回 None，由 `_shadow_for` 跳过阴影。
        """
        mask = alpha >= FOOT_MIN
        if not mask.any():
            return None
        rows = np.nonzero(mask.any(axis=1))[0]
        y_bot = int(rows[-1])
        h = alpha.shape[0]
        band_h = max(3, int(round(h * FOOT_BAND)))
        band = mask[max(0, y_bot - band_h + 1):y_bot + 1]
        cols = np.nonzero(band.any(axis=0))[0]
        if cols.size == 0:
            return None
        x0, x1 = int(cols[0]), int(cols[-1])
        if int(band[:, x0:x1 + 1].sum()) < FOOT_MIN_PX:
            return None
        return {"y": y_bot, "x0": x0, "x1": x1,
                "cx": (x0 + x1) / 2.0, "w": float(x1 - x0 + 1)}

    def _shadow_for(self, key, alpha):
        """该帧的接地阴影：返回一个待乘的系数块 (y0, y1, x0, x1, k3)。

        k3 是三通道 uint8，取值 255 = 背景原样、0 = 压到最暗；热路径上只做一次
        小范围乘法。形状由该帧的脚部接触带决定，与背景无关，所以能按帧缓存。

        **只算包围盒**：阴影只占画面底部一条，全图算网格是几十倍的浪费。
        """
        cfg = self.shadow
        sig = tuple(cfg[k] for k, _lo, _hi in SHADOW_RANGES) + (cfg["offset_y"],)
        cache_key = key + (sig,)
        # 缓存值可能是 None（这一帧没有可靠脚部），所以按「键在不在」判断，
        # 不能写成 `.get(...) is not None` —— 那会让没脚的帧每次都重算一遍
        if cache_key in self._shadow_cache:
            return self._shadow_cache[cache_key]
        band = self._foot_band(alpha)
        if band is None:
            self._store_shadow(cache_key, None)
            return None
        h, w = alpha.shape[:2]
        half_w = max(4.0, band["w"] * 0.5 * cfg["spread"])
        half_h = max(2.0, half_w * cfg["flatten"])
        cx = band["cx"]
        cy = min(h - 1.0, max(0.0, band["y"] + cfg["offset_y"] * h))
        # 包围盒略微放宽，装下椭圆外的软边
        y0 = max(0, int(cy - half_h * 1.05) - 1)
        y1 = min(h, int(cy + half_h * 1.05) + 2)
        x0 = max(0, int(cx - half_w * 1.05) - 1)
        x1 = min(w, int(cx + half_w * 1.05) + 2)
        if y1 <= y0 or x1 <= x0:
            self._store_shadow(cache_key, None)
            return None
        yy = np.arange(y0, y1, dtype=np.float32)[:, None]
        xx = np.arange(x0, x1, dtype=np.float32)[None, :]
        dist = np.sqrt(((xx - cx) / half_w) ** 2 + ((yy - cy) / half_h) ** 2)
        # softness 控制过渡带的宽度：越小则中间大片饱和成实心、只在靠边处化开
        t = np.clip((1.0 - dist) / max(0.05, cfg["softness"]), 0.0, 1.0)
        fall = t * t * (3.0 - 2.0 * t)                     # smoothstep
        # 阴影 = 遮挡环境光：按背景自身亮度等比压暗，纯色底和实景底都成立
        k = 1.0 - fall * cfg["strength"] * (1.0 - cfg["shade"])
        k8 = (np.clip(k, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
        item = (y0, y1, x0, x1, cv2.cvtColor(k8, cv2.COLOR_GRAY2BGR))
        self._store_shadow(cache_key, item)
        return item

    def _store_shadow(self, key, item):
        if len(self._shadow_cache) >= _SHADOW_CACHE_MAX:
            self._shadow_cache.clear()
        self._shadow_cache[key] = item

    def _layered_background(self, bg, key, alpha):
        """把接地阴影压到背景上，返回一张新的图；**不改动 bg 本身**。

        `bg` 是按尺寸缓存、被所有帧共用的，就地写会让阴影一帧一帧累积，
        几秒内把脚下压成一块黑——所以这里必须复制。
        """
        if not (self.shadow.get("enabled") and self.shadow.get("strength", 0) > 0):
            return bg
        item = self._shadow_for(key, alpha)
        if item is None:
            return bg
        y0, y1, x0, x1, k3 = item
        out = bg.copy()
        out[y0:y1, x0:x1] = cv2.multiply(bg[y0:y1, x0:x1], k3,
                                         scale=1.0 / 255.0, dtype=cv2.CV_8U)
        return out

    # ── 前景去污染 ──
    @staticmethod
    def _foreground(frame, alpha, rounds=6):
        """把人体实心区的颜色外推到半透明边缘，得到「不含背景色」的前景。

        抠像为了不吃到白底，做了腐蚀 + 羽化，代价是轮廓内侧 1~3px 的 alpha 只有
        0.2~0.8 —— 但它们**本来就是实心人体**（实测：边缘带像素 100% 更接近"不透明
        前景"而非"半透明混合"，误差 0.0 对 93.5）。拿这样的 alpha 直接混合，等于往
        西装和头发里掺进源背景（白）：深色底上是一圈灰白、浅色底上是一圈暗边，
        正是现场看到的「黑白重影」。

        这里先把颜色洗干净：边缘像素取邻近实心像素的颜色，再走 `a·F + (1-a)·bg`。
        混合比例一个像素都没改，改的只是**混进去的东西是纯前景色而不是脏色**。

        **轮数必须盖住整条软边**：AI 抠像输出的过渡带有 3~5px 宽，只推 2 圈的话
        外圈那 30% 像素（约 3000~4000 个/帧）仍带着源背景的白，合成后就是手部和
        身体轮廓上那层去不掉的白雾——手部动作（挥手、胸前的手势）尤其明显。
        实测过冲（合成结果超出「人体色↔背景色」区间的量）随轮数变化：

            r2  均值 5.7~9.7   雾像素 1200~1900   6ms
            r4  均值 1.7~2.4   雾像素  400~ 600   9ms
            r6  均值 0.5~0.8   雾像素   90~ 175  12ms
            r8  均值 0.2~0.5   雾像素   27~ 107  15ms

        r6 之后收益很小而耗时继续线性增长，取 6。环带为空时循环自己会退出，
        所以软边本来就窄的帧不会白付代价。

        只在边缘环带上算（约轮廓周长 ×3 个像素，占全图 1.5%），
        不做全图距离变换——后者 58ms/帧，实时预算里放不下。
        """
        solid = alpha >= SOLID
        if not solid.any():
            return frame
        h, w = alpha.shape[:2]
        fore = frame.copy()
        mask = solid
        grown = None
        for _ in range(rounds):
            # ring 只收 alpha 已经不算低的那圈（真正的人体边缘）。脚下接触阴影那类
            # 大片低 alpha 区域不填——它本来就该留成背景，填了既慢又会在新背景上
            # 糊出一片人体色。
            grown = cv2.dilate(mask.view(np.uint8), K3).view(bool)
            grown &= ~mask
            grown &= alpha > EDGE_MIN
            if not grown.any():
                break
            ys, xs = np.nonzero(grown)
            acc = np.zeros((ys.size, 3), np.float32)
            cnt = np.zeros(ys.size, np.float32)
            for dy in (-1, 0, 1):
                ny = ys + dy
                np.clip(ny, 0, h - 1, out=ny)
                for dx in (-1, 0, 1):
                    nx = xs + dx
                    np.clip(nx, 0, w - 1, out=nx)
                    hit = mask[ny, nx]
                    if not hit.any():
                        continue
                    acc += fore[ny, nx] * hit[:, None]
                    cnt += hit
            ok = cnt > 0
            if not ok.any():
                break
            fore[ys[ok], xs[ok]] = (acc[ok] / cnt[ok][:, None]).astype(np.uint8)
            mask = grown | mask
        return fore

    # ── 对外 ──
    def compose(self, frame, gesture=None, idx=0):
        """frame: BGR uint8；gesture: 当前动作名或 None；idx: 该帧源内的帧号。

        合成走最朴素的 alpha 混合，但**参与混合的前景必须先洗干净**：

            out = a·F + (1-a)·bg

        其中 F 是「去背景 + 边缘外推」后的前景（见 `_foreground`）。

        为什么不继续用 `out = src + (1-a)·(bg - bg_src)`：那个式子假设整幅源帧都能
        写成 `a·F + (1-a)·bg_src`，于是减掉 bg_src 再加 bg 就完事。可素材里 **a=0 的
        大片区域并不是标准背景色**——脚下有投影、身体周围有打光反光、影棚本身上亮
        下暗。那一式在这些地方会原样保留 `src - bg_src` 的差，实测每帧有 5000~10000
        个像素偏离新背景 15 以上，看起来就是人像旁边拖着一团黑影/白影。
        新式子 a=0 处严格等于 bg，这一项直接归零；边缘则用纯前景色参与混合，
        不会再往西装和头发里掺白。
        """
        if frame is None:
            return frame
        self._maybe_reload()
        if not self.enabled:
            return frame
        h, w = frame.shape[:2]
        source = "gestures.%s" % gesture if gesture else "base"
        stems = self._sources.get(source)
        if not stems:
            # 该动作还没有抠像素材：保持原背景，别拿 base 的 alpha 硬套
            if source not in self._missing:
                self._missing.add(source)
                logger.warning(
                    "[背景] 动作 %s 还没有抠像素材（matte/%s），这一段保持原背景；"
                    "补一次即可：python scripts/avatar_matte.py --avatar %s --gesture %s",
                    gesture, "gestures/" + str(gesture), self.avatar_id, gesture)
            return frame
        n = int(idx or 0) % len(stems)
        # 缓存键带上真实帧文件名：同一个 idx 在不同尺寸/不同素材下不会互相污染
        key = (source, stems[n], w, h)
        hit = self._out_cache.get(key)
        if hit is not None:
            return hit
        alpha = self._alpha_for(source, n, frame.shape)
        if alpha is None:
            return frame
        bg = self._background_for(w, h)
        if bg is None:
            return frame
        a3, inv = self._alpha3_for("%s|%s" % (source, stems[n]), alpha)
        fore = self._foreground(frame, alpha)
        # 先给背景铺上虚拟投影（脚下接地阴影），再跟前景混合：
        # 阴影只压暗背景，人体实心区被 a·F 整块覆盖，鞋面裤脚不会糊上灰
        layered = self._layered_background(bg, (source, stems[n], w, h), alpha)
        out = cv2.add(cv2.multiply(fore, a3, scale=1.0 / 255.0, dtype=cv2.CV_8U),
                      cv2.multiply(layered, inv, scale=1.0 / 255.0, dtype=cv2.CV_8U))
        self._out_cache[key] = out
        self._out_order.append(key)
        if len(self._out_order) > 6:
            old = self._out_order.pop(0)
            self._out_cache.pop(old, None)
        return out


# ── 单帧预览（供接口/脚本复用）───────────────────────────────────────
def render_preview(avatar_id, cfg=None, gesture=None, idx=0):
    """用形象的某一帧生成一张合成预览 PNG（bytes）；失败返回 None。"""
    cfg = cfg if cfg is not None else load_config()
    comp = AvatarBackground(avatar_id, cfg)
    if not comp.enabled:
        return None
    source_dir = os.path.join(AVATAR_ROOT, avatar_id, "full_imgs")
    if gesture:
        source_dir = os.path.join(AVATAR_ROOT, avatar_id, "gestures", gesture, "full_imgs")
    if not os.path.isdir(source_dir):
        return None
    names = sorted(n for n in os.listdir(source_dir) if n.lower().endswith(IMG_EXTS))
    if not names:
        return None
    frame = cv2.imread(os.path.join(source_dir, names[int(idx) % len(names)]),
                       cv2.IMREAD_COLOR)
    if frame is None:
        return None
    out = comp.compose(frame, gesture, idx)
    ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    return buf.tobytes() if ok else None
