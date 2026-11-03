# -*- coding: utf-8 -*-
"""全息舱（展项）运行接口。

覆盖运营要在界面上自己维护的四张表 + 一个感应链路：

- ``/api/kiosk/wakewords``  唤醒词库 / 打断词库（增改删）
- ``/api/kiosk/menus``      菜单库（菜单项 + 预置问题 / 跳转链接）
- ``/api/kiosk/idle``       闲时视频与待机提示
- ``/api/kiosk/proximity``  人体接近检测参数与事件
- ``/api/kiosk/ask``        一句话进来的统一处置（打断 → 菜单 → 唤醒 → 知识库）
- ``/api/kiosk/qa_log``     问答记录（按是否匹配查询 / 导出 / 清空）

注意：本模块必须在 ``server/routes.py`` 的 ``add_static('/')`` 之前注册。
"""

import asyncio
import os
import time

import aiohttp
from aiohttp import web

from server.routes import json_error, json_ok
from server.session_manager import session_manager
from utils import kiosk_store as store
from utils.logger import logger

RAG_TIMEOUT = float(os.getenv("KIOSK_RAG_TIMEOUT", "60"))
# 检测器心跳超过这个秒数没到，就认为它已经不在了（界面显示"未运行"）。
BEAT_TIMEOUT = 15.0

# 运行期状态：只活在进程内存里，重启即回到待机。
# 放这里而不是写文件，是因为它描述的是"此刻有没有人站在舱前"，
# 落盘没有任何意义，反而会在重启后骗过一次迎宾。
_STATE = {
    "present": False,      # 有人
    "awake": False,        # 已被唤醒（说过唤醒词或已迎宾）
    "speaking": False,     # 数字人正在说话
    "present_since": 0.0,
    "last_event_ts": 0.0,
    "last_beat_ts": 0.0,
    "detector_error": "",
    "detector_note": "",
    "confidence": 0.0,
    "last_question": "",
    "last_source": "",
    # 最近一次动作请求的结果：现场最怕「配了动作却没播」，光看日志不够，
    # 得让桌面端把「为什么没播」直接显示出来（没会话 / 形象不匹配 / 加载失败）。
    "last_gesture": "",
    "last_gesture_ok": False,
    "last_gesture_note": "",
    "last_gesture_ts": 0.0,
}


# ─── 工具 ────────────────────────────────────────────────────────

async def _json(request):
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


def _pick_session(sessionid=""):
    """取一个可用会话。

    展项场景下调用方常常不传 sessionid（感应器是独立进程，不知道 sid），
    这时取任意一个活着的会话即可——全息舱本来就只有一个。
    """
    if sessionid:
        session = session_manager.get_session(str(sessionid))
        if session is not None:
            return session
    for _sid, session in session_manager.sessions.items():
        if session is not None:
            return session
    return None


def _speak(session, text):
    """让数字人朗读一段文本，并支持讲稿同款的动作标签。

    唤醒词、菜单固定答复原先直接送给 TTS，因此 ``[GESTURE:wave_one]``
    会被当作普通字符，无法触发动作。这里复用讲稿解析器：标签写在文字前时
    边说边做，写在一句末尾时则在音频真正结束后再做。
    """
    if session is None or not text:
        return False
    try:
        from server.routes import _parse_segments, partition_gestures

        segments = _parse_segments(str(text), include_gesture_timing=True)
        requested = [gesture for _text, _slide, gesture, _timing in segments if gesture]
        if requested:
            avatar_id = _avatar_of(session)
            usable, missing, available_ids = partition_gestures(avatar_id, requested)
            if missing:
                logger.warning(
                    '[全息舱] 回复中的动作不可用：%s；当前形象 %s 可用：%s',
                    '、'.join(missing), avatar_id or '未知', '、'.join(available_ids) or '无')
                segments = [
                    (seg_text, slide_id, None if gesture in missing else gesture, timing)
                    for seg_text, slide_id, gesture, timing in segments
                ]
            preheat = getattr(session, 'preheat_gestures', None)
            if usable and callable(preheat):
                preheat(list(dict.fromkeys(usable)))

        for seg_text, _slide_id, gesture, timing in segments:
            datainfo = {}
            if gesture:
                datainfo['gesture_after' if timing == 'after' else 'gesture'] = gesture
            session.put_msg_txt(seg_text, datainfo)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("[全息舱] 朗读失败")
        return False


def _interrupt(session):
    if session is None:
        return False
    try:
        session.flush_talk()
        return True
    except Exception:  # noqa: BLE001
        logger.exception("[全息舱] 打断失败")
        return False


def _is_speaking(session):
    if session is None:
        return False
    try:
        return bool(session.is_speaking())
    except Exception:  # noqa: BLE001
        return False


def _avatar_of(session):
    """会话当前用的形象 id（拿不到就当空）。"""
    return str(getattr(getattr(session, "opt", None), "avatar_id", "") or "")


def _note_gesture(gesture, ok, note=""):
    """记下最近一次动作请求的结果，供 /api/kiosk/state 回报给桌面端。"""
    _STATE["last_gesture"] = gesture or ""
    _STATE["last_gesture_ok"] = bool(ok)
    _STATE["last_gesture_note"] = note or ""
    _STATE["last_gesture_ts"] = time.time()


def _play_gesture(session, spec, repeat=False):
    """按「形象 + 动作」配置在会话上请求一个动作，返回是否真的发出去了。

    动作素材按形象分目录存放（`data/avatars/<id>/gestures/<gid>`），帧尺寸与
    人脸框都是针对该形象归一化的。配置的形象与当前会话不一致时硬播只会糊成一团，
    所以这里跳过并留一条日志 —— 迎宾语该照念，不能因为动作配错就整段静音。

    **每一个提前返回都写一次 `_note_gesture`**：现场"人走近了却没有动作"，
    光靠翻日志是查不动的，桌面端要能直接把原因显示出来。
    """
    if not isinstance(spec, dict):
        _note_gesture("", False, "未配置触发动作")
        return False
    gesture = str(spec.get("gesture") or "").strip()
    if not gesture:
        _note_gesture("", False, "未配置触发动作")
        return False
    if session is None:
        _note_gesture(gesture, False, "数字人还没连接（没有可用会话）")
        logger.info("[全息舱] 跳过动作 %s：当前没有可用会话", gesture)
        return False
    want = str(spec.get("avatar") or "").strip()
    have = _avatar_of(session)
    if want and have and want != have:
        _note_gesture(gesture, False, "形象不匹配：配置 %s，当前 %s" % (want, have))
        logger.info("[全息舱] 跳过动作 %s：配置形象 %s，当前会话形象 %s", gesture, want, have)
        return False
    try:
        session.request_gesture(gesture, repeat=repeat)
    except TypeError:
        # 老签名的会话对象（没有 repeat 参数）退化成播放一遍
        try:
            session.request_gesture(gesture)
        except Exception as exc:  # noqa: BLE001
            _note_gesture(gesture, False, "请求失败：%s" % exc)
            logger.exception("[全息舱] 请求动作失败")
            return False
    except Exception as exc:  # noqa: BLE001
        _note_gesture(gesture, False, "请求失败：%s" % exc)
        logger.exception("[全息舱] 请求动作失败")
        return False
    _note_gesture(gesture, True, "已请求（%s）" % (have or want or "未知形象"))
    logger.info("[全息舱] 已请求动作 %s（形象 %s，循环=%s）", gesture, have or want, repeat)
    return True


async def _rag_answer(question, course_id=None, top_k=None):
    """问知识库。返回 {"matched", "answer", "detail", "course"}。

    "命中"的判据是 RAG 给了引用片段且回答不是兜底话术——这正是运营
    想从问答记录里看出来的东西：没命中的问句就是要补进课程文件的缺口。

    ``course`` 是实际检索的课程 id（RAG 未显式指定时会自己挑一门），写进
    问答记录，这样界面上才能按"知识库"这一列筛出某门课的命中情况。
    """
    base_url = os.getenv("RAG_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
    payload = {"question": question}
    if course_id:
        payload["course_id"] = course_id
    if top_k:
        payload["top_k"] = top_k
    timeout = aiohttp.ClientTimeout(total=RAG_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post("%s/rag/ask" % base_url, json=payload) as resp:
                if resp.status != 200:
                    return {"matched": False, "answer": "", "course": str(course_id or ""),
                            "detail": "知识库返回 %d" % resp.status}
                body = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[全息舱] 知识库不可用: %r", exc)
        return {"matched": False, "answer": "", "course": str(course_id or ""),
                "detail": "知识库服务不可用：%r" % (exc,)}

    data = (body or {}).get("data") or {}
    answer = (data.get("speak_text") or data.get("answer") or "").strip()
    citations = data.get("citations") or []
    course = ((data.get("retrieval") or {}).get("course_id") or course_id or "")
    if not answer or "未涉及该内容" in answer or not citations:
        return {"matched": False, "course": str(course),
                "answer": answer or "课程资料中未涉及该内容。", "detail": "知识库无命中"}
    return {"matched": True, "answer": answer, "course": str(course),
            "detail": "命中 %d 个片段" % len(citations)}


def _show_material(session, material):
    """把端侧画面切到菜单指定的那一页幻灯片。

    菜单里的「显示素材」存的是幻灯片 id。这里只负责"切过去"，
    素材自身内容的匹配（讲稿讲到哪一页）不受影响。
    """
    if session is None or not material:
        return False
    try:
        from utils import material_manager as mat_mgr
        slide_id = int(str(material).strip())
        mat_mgr.set_current_slide(getattr(session, "sessionid", "") or "", slide_id)
        return True
    except (TypeError, ValueError):
        logger.warning("[全息舱] 显示素材不是合法幻灯片 ID：%s", material)
        return False
    except Exception:  # noqa: BLE001
        logger.exception("[全息舱] 切换显示素材失败")
        return False


async def _answer_menu(menu, session, course_id=None, top_k=None, speak=True):
    """命中菜单后怎么答：固定答复 → 预置问题查知识库 → 标题当介绍语。

    三档优先级是刻意排的：运营配了「配置答案」就说明这句话要一字不改地念，
    再去查知识库反而可能念出别的内容；没配答案但有预置问题才走知识库检索；
    两者都没有时把菜单标题当介绍语念出来（毕竟用户是照着标题点的）。
    """
    title = menu.get("title") or ""
    matched = True
    course = ""
    material = (menu.get("material") or "").strip()
    answer = (menu.get("answer") or "").strip()
    detail = "命中菜单：%s" % title
    if answer:
        detail = "%s；菜单固定答复" % detail
    elif (menu.get("question") or "").strip():
        result = await _rag_answer(menu["question"], course_id, top_k)
        matched = result["matched"]
        answer = result["answer"]
        course = result["course"]
        detail = "%s；%s" % (detail, result["detail"])
        if not matched:
            answer = answer or "课程资料中未涉及该内容。"
    else:
        answer = title
    _show_material(session, material)
    spoken = _speak(session, answer) if (speak and answer) else False
    return {"matched": matched, "answer": answer, "detail": detail,
            "course": course, "material": material, "spoken": spoken}


# ─── 唤醒词 / 打断词 ─────────────────────────────────────────────

async def get_wakewords(request):
    return json_ok(data=store.load_wakewords())


async def add_wakeword(request):
    payload = await _json(request)
    if payload is None:
        return json_error("请求体必须是合法 JSON")
    kind = (payload.get("kind") or "").strip()
    if kind not in store.WAKE_KINDS:
        return json_error("kind 必须是 wake 或 interrupt")
    try:
        item = store.add_wakeword(kind, payload.get("word"), payload.get("reply"),
                                  payload.get("enabled", True))
    except ValueError as exc:
        return json_error(str(exc))
    return json_ok(data=item)


async def update_wakeword(request):
    item_id = request.match_info.get("item_id", "")
    payload = await _json(request) or {}
    fields = {k: payload.get(k) for k in ("word", "reply", "enabled")
              if k in payload}
    if "enabled" in fields:
        fields["enabled"] = bool(fields["enabled"])
    try:
        item = store.update_wakeword(payload.get("kind") or "wake", item_id, **fields)
    except ValueError as exc:
        return json_error(str(exc))
    if item is None:
        return json_error("没找到该词条")
    return json_ok(data=item)


async def delete_wakeword(request):
    item_id = request.match_info.get("item_id", "")
    kind = request.query.get("kind") or "wake"
    # id 自带库前缀（wake_/interrupt_），没传 kind 时按 id 判断更可靠
    if item_id.startswith("interrupt_"):
        kind = "interrupt"
    elif item_id.startswith("wake_"):
        kind = "wake"
    if not store.delete_wakeword(kind, item_id):
        return json_error("没找到该词条")
    return json_ok()


# ─── 菜单库 ──────────────────────────────────────────────────────

async def get_menus(request):
    return json_ok(data=store.load_menus())


async def add_menu(request):
    payload = await _json(request)
    if payload is None:
        return json_error("请求体必须是合法 JSON")
    try:
        item = store.add_menu(payload.get("title"), payload.get("question"),
                              payload.get("link"), payload.get("enabled", True),
                              answer=payload.get("answer") or "",
                              material=payload.get("material") or "")
    except ValueError as exc:
        return json_error(str(exc))
    return json_ok(data=item)


async def update_menu(request):
    item_id = request.match_info.get("item_id", "")
    payload = await _json(request) or {}
    fields = {k: payload.get(k) for k in ("title", "question", "link", "answer",
                                          "material", "enabled")
              if k in payload}
    if "enabled" in fields:
        fields["enabled"] = bool(fields["enabled"])
    try:
        item = store.update_menu(item_id, **fields)
    except ValueError as exc:
        return json_error(str(exc))
    if item is None:
        return json_error("没找到该菜单项")
    return json_ok(data=item)


async def delete_menu(request):
    item_id = request.match_info.get("item_id", "")
    if not store.delete_menu(item_id):
        return json_error("没找到该菜单项")
    return json_ok()


async def list_enabled_menus(request):
    """端侧展示用的启用菜单（按 order 排好）。"""
    return json_ok(data={"items": store.enabled_menus()})


async def activate_menu(request):
    """端侧按钮点击直达：跳过文本匹配，按 id 直接取菜单 → 检索 → 数字人开口。

    和 ``kiosk_ask`` 的区别：端侧按钮是"确定的选择"而不是"一句话"，
    没必要再去做唤醒/打断/菜单的模糊匹配，直接拿这条菜单去知识库问即可，
    也避免两个标题相近的菜单互相串台。
    """
    item_id = request.match_info.get("item_id", "")
    menu = store.get_menu(item_id)
    if menu is None:
        return json_error("没找到该菜单项")
    payload = await _json(request) or {}
    session = _pick_session(payload.get("sessionid"))

    # 展项快捷按钮代表“切换到这个主题”，不能把新回答排在上一段朗读之后。
    # 先清掉旧 TTS / WebRTC 缓冲，再做可能较慢的 RAG 查询；这样用户点下新按钮
    # 时旧语音会立即停止，查询完成后只播当前选择的内容。
    if payload.get("interrupt", True):
        _interrupt(session)

    answered = await _answer_menu(menu, session, payload.get("course_id"),
                                  payload.get("top_k"), speak=True)
    answer = answered["answer"]
    record = store.append_qa(menu.get("title") or "", answered["matched"], "menu",
                             answer, answered["detail"],
                             source_name=menu.get("title") or "",
                             course=answered["course"],
                             material=answered["material"])
    return json_ok(data={"kind": "menu", "matched": answered["matched"],
                         "answer": answer, "link": menu.get("link") or "",
                         "menu": menu, "spoken": answered["spoken"],
                         "record": record})


async def move_menu(request):
    item_id = request.match_info.get("item_id", "")
    payload = await _json(request) or {}
    try:
        delta = int(payload.get("delta", 0))
    except (TypeError, ValueError):
        return json_error("delta 必须是整数")
    if not store.move_menu(item_id, delta):
        return json_error("没找到该菜单项，或已在边界")
    return json_ok()


# ─── 闲时视频 / 待机提示 ─────────────────────────────────────────

async def get_idle(request):
    return json_ok(data=store.get_idle())


async def put_idle(request):
    payload = await _json(request)
    if payload is None:
        return json_error("请求体必须是合法 JSON")
    return json_ok(data=store.set_idle(payload))


# ─── 人体接近检测 ────────────────────────────────────────────────

async def get_proximity(request):
    return json_ok(data=store.get_proximity())


async def put_proximity(request):
    payload = await _json(request)
    if payload is None:
        return json_error("请求体必须是合法 JSON")
    return json_ok(data=store.set_proximity(payload))


def _probe_cameras(max_index=5):
    """探测可用的摄像头下标。没装 OpenCV 时返回空列表，不报错。"""
    found = []
    try:
        import cv2
    except Exception:  # noqa: BLE001
        return found, "未安装 OpenCV，无法探测摄像头"
    for index in range(max_index):
        cap = None
        try:
            cap = cv2.VideoCapture(index)
            if cap is not None and cap.isOpened():
                ok, _frame = cap.read()
                if ok:
                    found.append(index)
        except Exception:  # noqa: BLE001
            continue
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:  # noqa: BLE001
                    pass
    return found, ""


async def list_cameras(request):
    loop = asyncio.get_event_loop()
    found, err = await loop.run_in_executor(None, _probe_cameras)
    return json_ok(data={"cameras": found, "error": err})


async def proximity_event(request):
    """感应器上报：有人 / 无人 / 心跳。

    只有**状态翻转**才播报：人站着不动会一直上报 present=True，
    每次都迎宾就成了复读机。
    """
    payload = await _json(request) or {}
    now = time.time()
    _STATE["last_beat_ts"] = now
    _STATE["last_event_ts"] = now
    _STATE["detector_error"] = payload.get("error") or ""
    _STATE["detector_note"] = payload.get("note") or ""
    _STATE["confidence"] = float(payload.get("confidence") or 0.0)

    present = bool(payload.get("present"))
    changed = present != _STATE["present"]
    _STATE["present"] = present
    if present:
        if not _STATE["present_since"]:
            _STATE["present_since"] = now
    else:
        _STATE["present_since"] = 0.0

    action = "none"
    spoken = False
    gestured = False
    if changed and present:
        _STATE["awake"] = True
        cfg = store.get_idle()
        prox = store.get_proximity()
        session = _pick_session(payload.get("sessionid"))
        # 先起动作再念话：动作的首帧要赶在开口之前，否则观众先听到声音、
        # 半秒后手臂才抬起来，观感上是"画面跟不上人"。
        gestured = _play_gesture(session, prox.get("trigger_action"))
        want_welcome = bool(prox.get("auto_welcome", True))
        if want_welcome:
            # 没有会话（数字人还没连）时也要把意图报出来，否则现场看到的
            # 永远是 "none"，排查不出是感应没触发还是数字人没连上。
            spoken = _speak(session, cfg.get("welcome", ""))
        action = "welcome" if want_welcome else ("gesture" if gestured else "none")
    elif changed and not present:
        # 人走了：停掉没说完的话，回到闲时（闲时动作循环播，没人时也不定格）
        session = _pick_session(payload.get("sessionid"))
        _interrupt(session)
        _STATE["awake"] = False
        cfg = store.get_idle()
        if cfg.get("enabled", True):
            gestured = _play_gesture(session, cfg.get("action"), repeat=True)
        action = "idle"

    return json_ok(data={"present": present, "action": action, "spoken": spoken,
                         "gesture": gestured})


async def kiosk_state(request):
    cfg = store.get_idle()
    prox = store.get_proximity()
    running = (time.time() - _STATE["last_beat_ts"]) < BEAT_TIMEOUT
    state = "idle"
    if _STATE["present"]:
        state = "welcome"
    elif _STATE["awake"]:
        state = "active"
    return json_ok(data={
        "state": state,
        "present": _STATE["present"],
        "awake": _STATE["awake"],
        "confidence": round(_STATE["confidence"], 3),
        "detector_running": running,
        "detector_error": _STATE["detector_error"],
        "detector_note": _STATE["detector_note"],
        "last_event_ts": _STATE["last_event_ts"],
        "last_gesture": {
            "name": _STATE["last_gesture"],
            "ok": _STATE["last_gesture_ok"],
            "note": _STATE["last_gesture_note"],
            "at": _STATE["last_gesture_ts"],
        },
        "idle": cfg,
        "proximity": prox,
        "menus": store.enabled_menus(),
    })


# ─── 一句话进来的统一处置 ────────────────────────────────────────

def _resolve(text, speaking=False):
    """判定一句话该怎么处置，不产生副作用。返回 dict。

    顺序是刻意的：
    1. 正在说话时先判打断词 —— "停"必须立刻生效，否则用户会被迫听完；
    2. 菜单 —— 展项里菜单是最高频的操作，比唤醒词更具体；
    3. 唤醒词 —— 命中就播预设应答语；
    4. 都没有 → 交给知识库。
    """
    result = {"kind": "rag", "word": "", "reply": "", "menu": None}
    if speaking:
        hit = store.match_wakeword("interrupt", text)
        if hit:
            result.update(kind="interrupt", word=hit.get("word", ""),
                          reply=hit.get("reply", ""))
            return result
    menu = store.match_menu(text)
    if menu:
        result.update(kind="menu", menu=menu)
        return result
    hit = store.match_wakeword("wake", text)
    if hit:
        result.update(kind="wake", word=hit.get("word", ""),
                      reply=hit.get("reply", ""))
        return result
    return result


async def resolve_text(request):
    payload = await _json(request) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return json_error("text 不能为空")
    session = _pick_session(payload.get("sessionid"))
    return json_ok(data=_resolve(text, speaking=_is_speaking(session)))


async def kiosk_ask(request):
    """语音转写后的总入口：判定 → 处置 → 记一条问答记录。"""
    payload = await _json(request)
    if payload is None:
        return json_error("请求体必须是合法 JSON")
    text = (payload.get("text") or "").strip()
    if not text:
        return json_error("text 不能为空")

    session = _pick_session(payload.get("sessionid"))
    speak = payload.get("speak", True)
    verdict = _resolve(text, speaking=_is_speaking(session))
    kind = verdict["kind"]

    # 浏览器语音入口传入 interrupt=true 时，最新一句覆盖旧回答；“打断词”
    # 本身仍走下面的专用分支，避免重复清理和重复记录。
    if payload.get("interrupt") and kind != "interrupt":
        _interrupt(session)

    _STATE["last_question"] = text
    _STATE["last_source"] = kind

    if kind == "interrupt":
        _interrupt(session)
        _STATE["awake"] = False
        record = store.append_qa(text, True, "interrupt",
                                 verdict.get("reply") or "（已打断）",
                                 "命中打断词：%s" % verdict.get("word"),
                                 source_name=verdict.get("word") or "")
        return json_ok(data={"kind": "interrupt", "matched": True,
                             "answer": "", "record": record})

    if kind == "menu":
        menu = verdict["menu"]
        answered = await _answer_menu(menu, session, payload.get("course_id"),
                                      payload.get("top_k"), speak=speak)
        answer = answered["answer"]
        record = store.append_qa(text, answered["matched"], "menu", answer,
                                 answered["detail"],
                                 source_name=menu.get("title") or "",
                                 course=answered["course"],
                                 material=answered["material"])
        return json_ok(data={"kind": "menu", "matched": answered["matched"],
                             "answer": answer, "link": menu.get("link") or "",
                             "menu": menu, "record": record})

    if kind == "wake":
        reply = verdict.get("reply") or "您好，请问有什么可以帮您？"
        _STATE["awake"] = True
        if speak:
            _speak(session, reply)
        record = store.append_qa(text, True, "wake", reply,
                                 "命中唤醒词：%s" % verdict.get("word"),
                                 source_name=verdict.get("word") or "")
        return json_ok(data={"kind": "wake", "matched": True, "answer": reply,
                             "record": record})

    result = await _rag_answer(text, payload.get("course_id"), payload.get("top_k"))
    answer = result["answer"]
    if speak and answer:
        _speak(session, answer)
    record = store.append_qa(text, result["matched"],
                             "rag" if result["matched"] else "none",
                             answer, result["detail"], course=result["course"])
    return json_ok(data={"kind": "rag", "matched": result["matched"],
                         "answer": answer, "record": record})


# ─── 问答记录 ────────────────────────────────────────────────────

async def get_qa_log(request):
    q = request.query
    matched = None
    raw = (q.get("matched") or "").strip().lower()
    if raw in ("1", "true", "yes"):
        matched = True
    elif raw in ("0", "false", "no"):
        matched = False
    try:
        limit = min(1000, max(1, int(q.get("limit") or 200)))
    except ValueError:
        limit = 200
    try:
        offset = max(0, int(q.get("offset") or 0))
    except ValueError:
        offset = 0
    total, rows = store.query_qa(matched=matched, keyword=q.get("kw") or "",
                                 source=q.get("source") or "",
                                 source_name=q.get("source_name") or "",
                                 course=q.get("course") or "",
                                 device=q.get("device") or "",
                                 limit=limit, offset=offset)
    return json_ok(data={"total": total, "rows": rows,
                         "stats": store.qa_stats()})


def qa_log_facets(request):
    """GET /api/kiosk/qa_log/facets — 筛选下拉框的候选值。

    直接按已有记录去重，而不是让界面自己攒：界面上只有当页几十条，去重出来
    必然不全，用户会以为"某个知识库从来没命中过"。
    """
    rows = store.load_qa_log()
    def uniq(key):
        seen = []
        for r in rows:
            value = (r.get(key) or "").strip()
            if value and value not in seen:
                seen.append(value)
        return sorted(seen)
    return json_ok(data={"source": uniq("source"), "source_name": uniq("source_name"),
                         "course": uniq("course"), "device": uniq("device")})


async def get_qa_stats(request):
    return json_ok(data=store.qa_stats())


async def clear_qa_log(request):
    store.clear_qa()
    return json_ok()


async def delete_qa_log(request):
    """DELETE /api/kiosk/qa_log/{id} — 删单条记录。"""
    ok = store.delete_qa(request.match_info.get("id") or "")
    return json_ok() if ok else json_error("记录不存在或已被删除")


async def export_qa_log(request):
    loop = asyncio.get_event_loop()
    path, total = await loop.run_in_executor(None, store.export_qa_csv)
    return json_ok(data={"path": path, "total": total})


async def append_qa_log(request):
    """给其它链路（例如端侧直接提问）手动补一条记录。"""
    payload = await _json(request)
    if payload is None:
        return json_error("请求体必须是合法 JSON")
    text = (payload.get("question") or "").strip()
    if not text:
        return json_error("question 不能为空")
    record = store.append_qa(text, payload.get("matched", False),
                             payload.get("source") or "", payload.get("answer") or "",
                             payload.get("detail") or "",
                             source_name=payload.get("source_name") or "",
                             course=payload.get("course") or "",
                             material=payload.get("material") or "",
                             device=payload.get("device") or "")
    return json_ok(data=record)


# ─── 注册 ────────────────────────────────────────────────────────

def setup_kiosk_routes(app):
    """注册全息舱路由。必须在 ``add_static('/')`` 之前调用。"""
    app.router.add_get("/api/kiosk/wakewords", get_wakewords)
    app.router.add_post("/api/kiosk/wakewords", add_wakeword)
    app.router.add_put("/api/kiosk/wakewords/{item_id}", update_wakeword)
    app.router.add_delete("/api/kiosk/wakewords/{item_id}", delete_wakeword)

    app.router.add_get("/api/kiosk/menus", get_menus)
    app.router.add_post("/api/kiosk/menus", add_menu)
    app.router.add_put("/api/kiosk/menus/{item_id}", update_menu)
    app.router.add_delete("/api/kiosk/menus/{item_id}", delete_menu)
    app.router.add_post("/api/kiosk/menus/{item_id}/move", move_menu)
    app.router.add_get("/api/kiosk/menus/enabled", list_enabled_menus)
    app.router.add_post("/api/kiosk/menus/{item_id}/activate", activate_menu)

    app.router.add_get("/api/kiosk/idle", get_idle)
    app.router.add_put("/api/kiosk/idle", put_idle)

    app.router.add_get("/api/kiosk/proximity", get_proximity)
    app.router.add_put("/api/kiosk/proximity", put_proximity)
    app.router.add_get("/api/kiosk/proximity/cameras", list_cameras)
    app.router.add_post("/api/kiosk/proximity/event", proximity_event)

    app.router.add_get("/api/kiosk/state", kiosk_state)
    app.router.add_post("/api/kiosk/resolve", resolve_text)
    app.router.add_post("/api/kiosk/ask", kiosk_ask)

    app.router.add_get("/api/kiosk/qa_log/facets", qa_log_facets)
    app.router.add_get("/api/kiosk/qa_log", get_qa_log)
    app.router.add_post("/api/kiosk/qa_log", append_qa_log)
    app.router.add_delete("/api/kiosk/qa_log", clear_qa_log)
    app.router.add_delete("/api/kiosk/qa_log/{id}", delete_qa_log)
    app.router.add_get("/api/kiosk/qa_stats", get_qa_stats)
    app.router.add_post("/api/kiosk/qa_log/export", export_qa_log)
