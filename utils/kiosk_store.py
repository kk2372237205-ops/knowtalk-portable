# -*- coding: utf-8 -*-
"""全息舱（数字人展项）配置与记录存储。

数据落在 ``data/kiosk/`` 下，一个功能一个 JSON 文件：

- ``wakewords.json`` 唤醒词库 / 打断词库（含词条增删改）
- ``menus.json``     菜单库（菜单项 + 预置问题 / 跳转链接）
- ``idle.json``      闲时视频与待机提示
- ``proximity.json`` 人体接近检测（摄像头）参数
- ``qa_log.json``    问答记录（按是否匹配查询）

设计上刻意不用数据库：这些是运营人员要在界面上一条条改的小表，
JSON 便于直接备份、拷到另一台舱、以及人工修补。写入统一走
``_write_json``（先写临时文件再原子替换），中途断电不会留下半个文件。
"""

import json
import os
import platform
import re
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIOSK_DIR = os.path.join(ROOT, "data", "kiosk")

WAKE_FILE = "wakewords.json"
MENU_FILE = "menus.json"
IDLE_FILE = "idle.json"
PROX_FILE = "proximity.json"
QA_FILE = "qa_log.json"

WAKE_KINDS = ("wake", "interrupt")
KIND_LABEL = {"wake": "唤醒词", "interrupt": "打断词"}

# 问答记录上限：超过后丢最旧的。展项一天几百条，2000 条够回溯一个月，
# 还能避免文件无限膨胀拖慢界面读取。
QA_LOG_MAX = 2000

_LOCK = threading.RLock()

_PUNCT_RE = re.compile(r"[\s，。！？、；：,.!?;:\"'“”‘’()（）\[\]【】\-—_/\\]+")


# ─── 通用读写 ────────────────────────────────────────────────────

def _path(name):
    return os.path.join(KIOSK_DIR, name)


def _read_json(name, default):
    """读一个 JSON 文件；文件不存在或损坏时回落默认值。

    ``default`` 为 None 时表示"没有默认值"，此时不做类型校验——
    否则 ``type(None)`` 会把任何读到的内容判为类型不符，写进去的东西
    下次全读不出来（这个坑踩过一次：加了词条、刷新就没了）。
    """
    path = _path(name)
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001 - 文件坏了就回落默认值，不能让整个页面打不开
        return default
    if default is None:
        return data
    return data if isinstance(data, type(default)) else default


def _write_json(name, payload):
    """原子写：临时文件 → os.replace。"""
    os.makedirs(KIOSK_DIR, exist_ok=True)
    path = _path(name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _new_id(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:10])


def normalize(text):
    """归一化用于匹配：去标点、去空白、转小写。

    麦克风转写出来的文本带标点、带口语填充（"嗯，你好小知啊"），
    直接做相等比较几乎永远匹配不上，所以统一压成"纯字串"再做包含判断。
    """
    if not text:
        return ""
    return _PUNCT_RE.sub("", str(text)).lower()


# ─── 默认值 ──────────────────────────────────────────────────────

def _default_wakewords():
    """首次使用时给一套能直接跑的词表，省得用户面对空列表不知所措。"""
    def mk(kind, word, reply):
        return {"id": _new_id(kind), "word": word, "reply": reply,
                "enabled": True, "created": time.time()}

    return {
        "wake": [
            mk("wake", "你好小知", "您好，我是知课数字人，有什么可以帮您？"),
            mk("wake", "小知同学", "在呢，请讲。"),
            mk("wake", "你好数字人", "您好，欢迎咨询。"),
        ],
        "interrupt": [
            mk("interrupt", "停", ""),
            mk("interrupt", "停止", ""),
            mk("interrupt", "别说了", ""),
            mk("interrupt", "安静", ""),
        ],
    }


def _default_menus():
    """首次运行的示例菜单：按课程知识库（Python 基础篇）主线排。

    提示词是给 RAG 检索用的，问法都在真实链路上验证过能命中对应章节——
    这里别写成"请介绍一下这门课程"这种空泛问法，知识库里没有"课程介绍"
    这种段落，只会得到"课程资料中未涉及该内容"。
    """
    def mk(title, question, link):
        return {"id": _new_id("menu"), "title": title, "question": question,
                "link": link, "enabled": True, "order": 0, "created": time.time()}

    items = [
        mk("课程导览", "为什么学习 Python", ""),
        mk("语法基础", "Python 有哪些基本数据类型", ""),
        mk("流程控制", "if 语句的作用是什么，elif 和 else 子句有什么区别", ""),
        mk("函数与类", "Python 的函数怎么定义和调用，类和对象是什么关系", ""),
        mk("实战项目", "校园信息管理系统项目是做什么的", ""),
    ]
    for i, item in enumerate(items):
        item["order"] = i
    return {"items": items}


def _default_action():
    """「某个形象的某个动作」。

    动作素材是按形象分目录存的（`data/avatars/<id>/gestures/<gid>`），
    所以一条动作配置必须同时带形象与动作两个标识，缺一不可。
    """
    return {"avatar": "", "gesture": ""}


def _norm_action(value):
    """把外部传进来的动作配置收成干净的两字段字典。"""
    if not isinstance(value, dict):
        return _default_action()
    return {"avatar": str(value.get("avatar") or "").strip(),
            "gesture": str(value.get("gesture") or "").strip()}


def _default_idle():
    return {
        "enabled": True,
        "video": "",                       # 历史字段：闲时视频路径，已无消费方
        "action": _default_action(),       # 闲时循环播放的数字人动作
        "tip": "您可以站在我的前方进行语音交流",
        "idle_after_s": 30,                # 无人/无交互多久进入闲时
        "welcome": "您好，欢迎来到知课数字人展区，请问有什么可以帮您？",
        "tip_font_size": 28,
    }


def _default_proximity():
    return {
        "enabled": False,
        "camera_index": 0,
        "mode": "motion",                  # motion=帧差（快） / hog=行人检测（准）
        "fps": 5,                          # 检测帧率：5MP 全帧跑不动，降采样后 5fps 足够
        "width": 640,                      # 检测用分辨率（只影响检测，不影响显示）
        "height": 480,
        "sensitivity": 0.5,                # 0~1，越大越灵敏
        "enter_frames": 3,                 # 连续命中多少帧才算"有人"
        "leave_seconds": 8,                # 连续多久没人才算"离开"
        "min_area_ratio": 0.02,            # 运动区域占画面比例下限（滤掉噪点）
        "roi": "",                         # 检测区域 x,y,w,h（像素，相对检测分辨率）；空=整幅
        "preview": True,                   # 是否在桌面端显示检测画面
        "auto_welcome": True,              # 检测到人自动播迎宾语
        "trigger_action": _default_action(),  # 检测到人时播哪个形象的哪个动作
    }


# ─── 唤醒词 / 打断词 ─────────────────────────────────────────────

def load_wakewords():
    with _LOCK:
        data = _read_json(WAKE_FILE, None)
        if not isinstance(data, dict) or not data:
            data = _default_wakewords()
            _write_json(WAKE_FILE, data)
        for kind in WAKE_KINDS:
            items = data.get(kind)
            if not isinstance(items, list):
                data[kind] = []
            # 老数据可能缺字段，补齐后再返回，免得界面 .get() 到处判空
            for item in data[kind]:
                if not isinstance(item, dict):
                    continue
                item.setdefault("id", _new_id(kind))
                item.setdefault("word", "")
                item.setdefault("reply", "")
                item.setdefault("enabled", True)
        return data


def _save_wakewords(data):
    _write_json(WAKE_FILE, data)


def add_wakeword(kind, word, reply="", enabled=True):
    if kind not in WAKE_KINDS:
        raise ValueError("未知词库: %s" % kind)
    word = (word or "").strip()
    if not word:
        raise ValueError("词条不能为空")
    with _LOCK:
        data = load_wakewords()
        # 同一个词库里重复词条没有意义，直接判重
        for item in data[kind]:
            if normalize(item.get("word")) == normalize(word):
                raise ValueError("%s「%s」已存在" % (KIND_LABEL[kind], word))
        item = {"id": _new_id(kind), "word": word, "reply": (reply or "").strip(),
                "enabled": bool(enabled), "created": time.time()}
        data[kind].append(item)
        _save_wakewords(data)
        return item


def update_wakeword(kind, item_id, **fields):
    with _LOCK:
        data = load_wakewords()
        for item in data[kind]:
            if item.get("id") == item_id:
                for key in ("word", "reply", "enabled"):
                    if key in fields and fields[key] is not None:
                        item[key] = fields[key]
                if "word" in fields:
                    item["word"] = (item["word"] or "").strip()
                    if not item["word"]:
                        raise ValueError("词条不能为空")
                item["updated"] = time.time()
                _save_wakewords(data)
                return item
    return None


def delete_wakeword(kind, item_id):
    with _LOCK:
        data = load_wakewords()
        before = len(data[kind])
        data[kind] = [i for i in data[kind] if i.get("id") != item_id]
        if len(data[kind]) == before:
            return False
        _save_wakewords(data)
        return True


def match_wakeword(kind, text):
    """在指定词库里匹配一句话，返回命中的词条（没命中返回 None）。

    长词优先：配置了「你好小知」和「小知」时，"你好小知" 应该命中前者，
    否则回复语会错配。
    """
    target = normalize(text)
    if not target:
        return None
    with _LOCK:
        data = load_wakewords()
    best = None
    for item in data.get(kind, []):
        if not item.get("enabled", True):
            continue
        word = normalize(item.get("word"))
        if not word or word not in target:
            continue
        if best is None or len(word) > len(normalize(best.get("word"))):
            best = item
    return best


# ─── 菜单库 ──────────────────────────────────────────────────────

def load_menus():
    with _LOCK:
        data = _read_json(MENU_FILE, None)
        if not isinstance(data, dict) or "items" not in data:
            data = _default_menus()
            _write_json(MENU_FILE, data)
        items = [i for i in data.get("items", []) if isinstance(i, dict)]
        for i, item in enumerate(items):
            item.setdefault("id", _new_id("menu"))
            item.setdefault("title", "")
            item.setdefault("question", "")
            item.setdefault("link", "")
            item.setdefault("answer", "")
            item.setdefault("material", "")
            item.setdefault("enabled", True)
            item.setdefault("order", i)
        items.sort(key=lambda x: x.get("order", 0))
        data["items"] = items
        return data


def _save_menus(data):
    _write_json(MENU_FILE, data)


def add_menu(title, question="", link="", enabled=True, answer="", material=""):
    """新增菜单项。

    ``answer`` 是**固定答复**：配了它，命中该菜单就照原句念，不再去问知识库
    （运营只想给一句标准话术时，不必为它准备课程文件）。

    ``material`` 是**显示素材**：命中时把端侧画面切到这一页幻灯片。
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("菜单标题不能为空")
    with _LOCK:
        data = load_menus()
        for item in data["items"]:
            if normalize(item.get("title")) == normalize(title):
                raise ValueError("菜单项「%s」已存在" % title)
        order = max([i.get("order", 0) for i in data["items"]] + [-1]) + 1
        item = {"id": _new_id("menu"), "title": title,
                "question": (question or "").strip(), "link": (link or "").strip(),
                "answer": (answer or "").strip(), "material": (material or "").strip(),
                "enabled": bool(enabled), "order": order, "created": time.time()}
        data["items"].append(item)
        _save_menus(data)
        return item


def update_menu(item_id, **fields):
    with _LOCK:
        data = load_menus()
        for item in data["items"]:
            if item.get("id") == item_id:
                for key in ("title", "question", "link", "answer", "material",
                            "enabled", "order"):
                    if key in fields and fields[key] is not None:
                        item[key] = fields[key]
                for key in ("question", "link", "answer", "material"):
                    item[key] = (item.get(key) or "").strip()
                if "title" in fields:
                    item["title"] = (item["title"] or "").strip()
                    if not item["title"]:
                        raise ValueError("菜单标题不能为空")
                item["updated"] = time.time()
                _save_menus(data)
                return item
    return None


def delete_menu(item_id):
    with _LOCK:
        data = load_menus()
        before = len(data["items"])
        data["items"] = [i for i in data["items"] if i.get("id") != item_id]
        if len(data["items"]) == before:
            return False
        for i, item in enumerate(sorted(data["items"], key=lambda x: x.get("order", 0))):
            item["order"] = i
        _save_menus(data)
        return True


def move_menu(item_id, delta):
    """上移/下移：delta 为 -1 / +1。"""
    with _LOCK:
        data = load_menus()
        items = sorted(data["items"], key=lambda x: x.get("order", 0))
        idx = next((i for i, it in enumerate(items) if it.get("id") == item_id), None)
        if idx is None:
            return False
        new_idx = min(max(idx + delta, 0), len(items) - 1)
        if new_idx == idx:
            return False
        items.insert(new_idx, items.pop(idx))
        for i, item in enumerate(items):
            item["order"] = i
        data["items"] = items
        _save_menus(data)
        return True


def _overlap_ratio(short, long_text):
    """短串的字符有多少出现在长串里（0~1）。

    中文口语几乎不会照着菜单一字不差地念：「课程介绍」会被说成
    "请介绍一下课程"。只做包含匹配的话这种说法一条都命中不了，
    所以补一个字面覆盖率兜底。用集合而非计数，避免"介介绍绍"刷分。
    """
    keys = set(short)
    if not keys or not long_text:
        return 0.0
    return len(keys & set(long_text)) / float(len(keys))


MENU_FUZZY_MIN = 0.6


def match_menu(text):
    """按菜单标题或预置问题匹配一句话。

    两档判据：
    1. 包含匹配 —— 标题/预置问题是用户这句话的子串（最可靠）；
    2. 字面覆盖率 ≥ 0.6 —— 口语改写过的说法（"请介绍一下课程" → 「课程介绍」）。

    两档都命中时优先取第一档，其次取覆盖率高的。
    """
    target = normalize(text)
    if not target:
        return None
    with _LOCK:
        items = load_menus()["items"]

    exact = None
    exact_len = -1
    fuzzy = None
    fuzzy_ratio = 0.0
    for item in items:
        if not item.get("enabled", True):
            continue
        for key in ("title", "question"):
            cand = normalize(item.get(key))
            if not cand:
                continue
            if cand in target:
                if len(cand) > exact_len:
                    exact, exact_len = item, len(cand)
            elif len(cand) >= 2:
                ratio = _overlap_ratio(cand, target)
                if ratio >= MENU_FUZZY_MIN and ratio > fuzzy_ratio:
                    fuzzy, fuzzy_ratio = item, ratio
    return exact or fuzzy


def enabled_menus():
    """端侧展示用的菜单（只给启用的，按 order 排好）。"""
    with _LOCK:
        items = load_menus()["items"]
    return [i for i in items if i.get("enabled", True)]


def get_menu(item_id):
    """按 id 取单个菜单项；没有返回 None。端侧按钮点击直达用。"""
    if not item_id:
        return None
    with _LOCK:
        items = load_menus()["items"]
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


# ─── 闲时视频 / 待机提示 ─────────────────────────────────────────

def get_idle():
    with _LOCK:
        data = _read_json(IDLE_FILE, None)
        if not isinstance(data, dict) or not data:
            data = _default_idle()
            _write_json(IDLE_FILE, data)
        base = _default_idle()
        base.update(data)
        base["action"] = _norm_action(base.get("action"))
        return base


def set_idle(patch):
    with _LOCK:
        data = get_idle()
        for key in ("enabled", "video", "tip", "idle_after_s",
                    "welcome", "tip_font_size"):
            if key in patch and patch[key] is not None:
                data[key] = patch[key]
        if "action" in patch and patch["action"] is not None:
            data["action"] = _norm_action(patch["action"])
        data["enabled"] = bool(data.get("enabled", True))
        try:
            data["idle_after_s"] = max(5, int(data.get("idle_after_s", 30)))
        except (TypeError, ValueError):
            data["idle_after_s"] = 30
        try:
            data["tip_font_size"] = min(72, max(12, int(data.get("tip_font_size", 28))))
        except (TypeError, ValueError):
            data["tip_font_size"] = 28
        _write_json(IDLE_FILE, data)
        return data


# ─── 人体接近检测 ────────────────────────────────────────────────

def get_proximity():
    with _LOCK:
        data = _read_json(PROX_FILE, None)
        if not isinstance(data, dict) or not data:
            data = _default_proximity()
            _write_json(PROX_FILE, data)
        base = _default_proximity()
        base.update(data)
        base["trigger_action"] = _norm_action(base.get("trigger_action"))
        return base


def set_proximity(patch):
    with _LOCK:
        data = get_proximity()
        for key in _default_proximity():
            if key == "trigger_action":
                continue
            if key in patch and patch[key] is not None:
                data[key] = patch[key]
        if "trigger_action" in patch and patch["trigger_action"] is not None:
            data["trigger_action"] = _norm_action(patch["trigger_action"])
        data["enabled"] = bool(data.get("enabled", False))
        try:
            data["camera_index"] = max(0, int(data.get("camera_index", 0)))
        except (TypeError, ValueError):
            data["camera_index"] = 0
        try:
            data["fps"] = min(15, max(1, int(data.get("fps", 5))))
        except (TypeError, ValueError):
            data["fps"] = 5
        try:
            data["sensitivity"] = min(1.0, max(0.05, float(data.get("sensitivity", 0.5))))
        except (TypeError, ValueError):
            data["sensitivity"] = 0.5
        try:
            data["leave_seconds"] = min(600, max(1, int(data.get("leave_seconds", 8))))
        except (TypeError, ValueError):
            data["leave_seconds"] = 8
        if data.get("mode") not in ("motion", "hog"):
            data["mode"] = "motion"
        _write_json(PROX_FILE, data)
        return data


# ─── 问答记录 ────────────────────────────────────────────────────

def load_qa_log():
    with _LOCK:
        data = _read_json(QA_FILE, [])
        return [r for r in data if isinstance(r, dict)]


def device_name():
    """这台全息舱的名字，写进每条问答记录。

    默认取主机名（多台机器导出的记录合在一起时才看得出是哪台），
    可以用环境变量 ``KIOSK_DEVICE_NAME`` 改成"一号舱"这类好认的名字。
    """
    custom = (os.environ.get("KIOSK_DEVICE_NAME") or "").strip()
    if custom:
        return custom
    try:
        return platform.node() or "本机"
    except Exception:  # noqa: BLE001 - 主机名取不到不影响记录功能
        return "本机"


def append_qa(question, matched, source="", answer="", detail="",
              source_name="", course="", material="", device=""):
    """写一条问答记录。

    ``matched`` 是运营最关心的字段：false 说明知识库没覆盖到这句话，
    正是需要补内容的缺口。source 记命中来源（wake/interrupt/menu/rag/none）。

    后面四个可选字段是「这条答案是从哪儿来的」，对应界面上的问题库 / 知识库 /
    显示素材 / 设备名称四列——只有当时就记下来，事后才筛得出"某个知识库的
    命中率如何"，靠答案文本反推是推不出来的。
    """
    record = {
        "id": _new_id("qa"),
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "question": (question or "").strip(),
        "matched": bool(matched),
        "source": source or ("rag" if matched else "none"),
        "answer": (answer or "")[:500],
        "detail": (detail or "")[:300],
        "source_name": (source_name or "").strip(),
        "course": (course or "").strip(),
        "material": (material or "").strip(),
        "device": (device or "").strip() or device_name(),
    }
    with _LOCK:
        data = load_qa_log()
        data.append(record)
        if len(data) > QA_LOG_MAX:
            data = data[-QA_LOG_MAX:]
        _write_json(QA_FILE, data)
    return record


def query_qa(matched=None, keyword="", source="", limit=200, offset=0,
             source_name="", course="", device=""):
    """按条件查记录。matched=None 表示不过滤；返回 (总数, 当页记录)。"""
    with _LOCK:
        data = load_qa_log()
    kw = normalize(keyword)
    rows = []
    for r in data:
        if matched is not None and bool(r.get("matched")) != bool(matched):
            continue
        if source and r.get("source") != source:
            continue
        if source_name and r.get("source_name") != source_name:
            continue
        if course and r.get("course") != course:
            continue
        if device and r.get("device") != device:
            continue
        if kw and kw not in normalize(r.get("question")) \
                and kw not in normalize(r.get("answer")):
            continue
        rows.append(r)
    # 界面从新到旧看
    rows.sort(key=lambda x: x.get("ts", 0), reverse=True)
    total = len(rows)
    return total, rows[offset:offset + limit]


def qa_stats():
    with _LOCK:
        data = load_qa_log()
    total = len(data)
    hit = sum(1 for r in data if r.get("matched"))
    miss = total - hit
    by_source = {}
    for r in data:
        key = r.get("source") or "none"
        by_source[key] = by_source.get(key, 0) + 1
    return {
        "total": total,
        "matched": hit,
        "unmatched": miss,
        "hit_rate": round(hit / total, 4) if total else 0.0,
        "by_source": by_source,
    }


def clear_qa():
    with _LOCK:
        _write_json(QA_FILE, [])
    return True


def delete_qa(item_id):
    """删掉单条记录。

    记录里偶尔会混进调试时随口问的一句，运营要挑出来删掉——只能整表清空
    的话，删一条就得连带丢掉当天的全部记录，没人敢点。
    """
    if not item_id:
        return False
    with _LOCK:
        data = load_qa_log()
        left = [r for r in data if r.get("id") != item_id]
        if len(left) == len(data):
            return False
        _write_json(QA_FILE, left)
    return True


def export_qa_path():
    """导出 CSV 的落盘位置（data/kiosk/qa_log.csv）。"""
    return _path("qa_log.csv")


def export_qa_csv():
    import csv
    total, rows = query_qa(limit=QA_LOG_MAX)
    path = export_qa_path()
    os.makedirs(KIOSK_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["时间", "是否匹配", "回复类型", "问题", "回复内容",
                         "显示素材", "问题库", "知识库", "设备名称", "明细"])
        for r in rows:
            writer.writerow([r.get("time", ""), "是" if r.get("matched") else "否",
                             r.get("source", ""), r.get("question", ""),
                             r.get("answer", ""), r.get("material", ""),
                             r.get("source_name", ""), r.get("course", ""),
                             r.get("device", ""), r.get("detail", "")])
    return path, total
