###############################################################################
#  服务器路由 — 统一异常处理的 API 路由
###############################################################################

import json
import os
import asyncio
import re
from aiohttp import web
import aiohttp

from utils import gestures as gesture_utils
from utils import path_guard
from utils.logger import logger


# ─── 路由工具函数 ──────────────────────────────────────────────────────────

def json_ok(data=None):
    """返回成功 JSON 响应"""
    body = {"code": 0, "msg": "ok"}
    if data is not None:
        body["data"] = data
    return web.Response(
        content_type="application/json",
        text=json.dumps(body),
    )


def json_error(msg: str, code: int = -1):
    """返回错误 JSON 响应"""
    return web.Response(
        content_type="application/json",
        text=json.dumps({"code": code, "msg": str(msg)}),
    )


def _parse_ppt_tags(text: str):
    """解析 [PPT:N] / [slide:N] 标签，返回 (清洗后文本, 最后匹配的slide_id)"""
    pattern = r'\[(?:PPT|slide):(\d+)\]'
    matches = re.findall(pattern, text)
    cleaned = re.sub(pattern, '', text).strip()
    slide_id = int(matches[-1]) if matches else None
    return cleaned, slide_id


# 段落边界标签：幻灯片切换与手势触发在同一层解析。
# 标签名大小写不敏感；手势标识本身保留大小写（它是目录名，有其自身含义）。
_SEGMENT_TAG_RE = re.compile(
    r'\[(?:PPT|slide):(?P<slide>\d+)\]'
    r'|\[GESTURE:(?P<gesture>[A-Za-z0-9_.-]+)\]',
    re.IGNORECASE,
)


def _parse_segments(text: str):
    """
    按段落边界标签分段，返回 [(text, slide_id, gesture), ...]

    两类标签都作为段落边界，标签在文本中的先后顺序就是播放顺序：
      [PPT:N] / [slide:N]  → 该段开始朗读时应显示的幻灯片
      [GESTURE:<id>]       → 该段开始朗读时应触发的手势

    语义要点：
      - slide_id 会一直沿用到下一个幻灯片标签（与改动前一致）；
      - gesture **不继承**：段内没有手势标签，该段的 gesture 就是 None；
      - 一段内出现多个手势标签时取第一个，其余一并从朗读文本移除。
      - 动作标签可写在所作用文字的前面；若动作标签位于整段末尾，则兼容地
        绑定到紧邻的前一段文字，方便使用“文字[GESTURE:动作ID]”的写法。

    示例：
      "[PPT:1] 欢迎。[GESTURE:wave] 大家好。[PPT:2] 今天介绍新产品。"
      → [("欢迎。", 1, None), ("大家好。", 1, "wave"), ("今天介绍新产品。", 2, None)]
    """
    segments = []
    current_slide = None
    pending_gesture = None
    cursor = 0
    for match in _SEGMENT_TAG_RE.finditer(text):
        seg_text = text[cursor:match.start()].strip()
        if seg_text:
            segments.append((seg_text, current_slide, pending_gesture))
            pending_gesture = None
        slide = match.group('slide')
        if slide is not None:
            current_slide = int(slide)
        elif pending_gesture is None:
            pending_gesture = match.group('gesture')
        cursor = match.end()
    tail = text[cursor:].strip()
    if tail:
        segments.append((tail, current_slide, pending_gesture))
    elif pending_gesture is not None and segments:
        # 兼容句末动作标签，例如“大家好，我是陈老师[GESTURE:wave_one]”。
        # 常规语义仍是标签作用于后文；只有标签后没有可朗读文本时，才把它
        # 作用到紧邻前一段。前一段已有动作时保留先出现的动作，避免覆盖。
        previous_text, previous_slide, previous_gesture = segments[-1]
        if previous_gesture is None:
            segments[-1] = (previous_text, previous_slide, pending_gesture)
    # 如果没有标签，返回整段文本
    if not segments:
        segments = [(text.strip(), None, None)]

    # 防止整套演讲稿被重复粘贴后播放两遍。只有当页码重新回到起始页，
    # 且后半段仍按升序构成一套不短于前半段的稿件时，才视为重复。
    tagged = [(index, slide_id) for index, (_, slide_id, _gesture) in enumerate(segments)
              if slide_id is not None]
    if tagged:
        first_slide = tagged[0][1]
        for tagged_index in range(len(tagged) - 1, 0, -1):
            segment_index, slide_id = tagged[tagged_index]
            if slide_id != first_slide:
                continue
            prefix_ids = [item[1] for item in tagged[:tagged_index]]
            suffix_ids = [item[1] for item in tagged[tagged_index:]]
            suffix_ascending = all(a <= b for a, b in zip(suffix_ids, suffix_ids[1:]))
            if len(set(prefix_ids)) >= 2 and len(suffix_ids) >= len(prefix_ids) and suffix_ascending:
                logger.warning(
                    f"[PPT分段] 检测到演讲稿从第 {first_slide} 页重复开始，"
                    f"忽略前 {segment_index} 段，只播放最后一套"
                )
                segments = segments[segment_index:]
                break
    logger.info(f"[PPT分段] 共 {len(segments)} 段，顺序: {[(s[1], s[2], s[0][:30]) for s in segments]}")
    return segments


def _parse_ppt_segments(text: str):
    """只取 (文本, 幻灯片) 两个字段的薄包装，保留给既有调用点。"""
    return [(seg_text, slide_id) for seg_text, slide_id, _gesture in _parse_segments(text)]


def _resolve_slide_id(value: int):
    """兼容标签使用实际 slide_id 或 1-based 页面索引。"""
    from utils import material_manager as mat_mgr
    if mat_mgr.get_slide(value) is not None:
        return value
    slides = mat_mgr.list_slides()
    if 1 <= value <= len(slides):
        return slides[value - 1]['id']
    return value


def _active_model():
    """当前运行的模型配置（app.py 启动时写入 avatar_routes.ACTIVE_MODEL）。

    单独包一个函数是为了避免在模块顶部引入 avatar_routes，形成循环依赖。
    """
    from server import avatar_routes as _ar
    return _ar


def partition_gestures(avatar_id: str, names):
    """把讲稿引用到的手势分成「该形象可用」与「不可用」两组。

    返回 (usable, missing, available_ids)：
    - usable：可用、应当继续下发给渲染层的手势名（保持讲稿里的出现顺序，去重）
    - missing：该形象不可用的手势名
    - available_ids：该形象可用手势的排序列表（不可用时用来告诉用户能选什么）
    """
    names = list(dict.fromkeys(name for name in names if name))
    if not names:
        return [], [], []
    available = gesture_utils.list_gestures(avatar_id)
    # MuseTalk 形象还必须具备 musetalk/ 下的逐帧资产才能播动作；缺资产的那些
    # 按「不可用」处理（摘除 + warning），而不是到渲染层才静默降级。
    require_muse = getattr(_active_model(), 'ACTIVE_MODEL', None) == 'musetalk'
    if require_muse:
        known = {item['id'] for item in available if item.get('muse_ready')}
    else:
        known = {item['id'] for item in available}
    usable = [name for name in names if name in known]
    missing = [name for name in names if name not in known]
    return usable, missing, sorted(known)


def gesture_unavailable_message(avatar_id: str, missing, available_ids) -> str:
    """生成「讲稿引用了不可用手势」的提示文案。

    要说清三件事：缺了哪些手势、这个形象有哪些可选、朗读不受影响。
    """
    if not available_ids:
        return (f"形象 {avatar_id} 没有任何可用手势（讲稿里用了 {'、'.join(missing)}）；"
                f"请先在 data/avatars/{avatar_id}/gestures/ 下登记手势素材。"
                "这些动作本次不会播放，朗读照常进行。")
    return (f"手势 {'、'.join(missing)} 在形象 {avatar_id} 上不可用；"
            f"该形象的可用手势：{'、'.join(available_ids)}。"
            "这些动作本次不会播放，朗读照常进行。")


from server.session_manager import session_manager
from server.avatar_routes import setup_avatar_routes

def get_session(request, sessionid: str):
    """从 app 中获取 session 实例"""
    return session_manager.get_session(sessionid)


# ─── 路由处理函数 ──────────────────────────────────────────────────────────

async def human(request):
    """文本输入（echo/chat 模式），支持 voice/emotion 参数 及 [PPT:N] 幻灯片标签"""
    try:
        params: dict = await request.json()

        sessionid: str = params.get('sessionid', '')
        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")

        # 清理当前合成和队列中尚未播放的文本，避免预设内容重复排队播放。
        if params.get('interrupt'):
            avatar_session.flush_talk()
        request_id = avatar_session.speech_request_id

        datainfo = {}
        if params.get('tts'):  # tts 参数透传（voice, emotion 等）
            datainfo['tts'] = params.get('tts')
        datainfo['_speech_request_id'] = request_id

        # 受理阶段产生的提示（例如讲稿引用了当前形象没有的手势）。
        # 必须随响应回给调用方，否则降级就成了静默降级。
        warnings = []

        if params['type'] == 'echo':
            text = params['text']
            # 讲稿留底：展示页「连接后播放预设」是把整篇带 [PPT:N] 的讲稿一次性
            # 投到这里的，而启动提问式讲课会 flush_talk() 清空队列。先记一份，
            # 编排器拿不到 text 时才有稿可讲（否则就是"0 段、一开课就提问"）。
            if 'PPT:' in text:
                try:
                    from utils import material_manager as mat_mgr
                    mat_mgr.remember_lecture(sessionid, text)
                except Exception:  # noqa: BLE001 - 留底失败不影响本次朗读
                    logger.exception("[提问] 讲稿留底失败（不影响朗读）")
            # 按段落边界标签分段（[PPT:N] / [slide:N] / [GESTURE:<id>]），
            # 每段携带 slide_id 与 gesture；两者都只在该段真正开始朗读时生效。
            segments = _parse_segments(text)
            # 手势在受理阶段校验：**不可用的手势只摘除该段的手势指令**，不拒绝整篇
            # 讲稿。换形象时讲稿里的手势标签对不上是常态（不同形象的手势集合可能
            # 互不相交），此时整篇报错等于数字人彻底不出声，用户只看到"连上了却不
            # 说话"。摘除后该段按默认素材渲染，文字照常朗读。
            # 同时记警告并把警告回给调用方——降级但不静默。
            requested_gestures = [gesture for _text, _slide, gesture in segments if gesture]
            warnings = []
            if requested_gestures:
                avatar_id = getattr(getattr(avatar_session, 'opt', None), 'avatar_id', None)
                usable, missing, available_ids = partition_gestures(
                    avatar_id, requested_gestures)
                if missing:
                    message = gesture_unavailable_message(avatar_id, missing, available_ids)
                    logger.warning('[手势标签] %s', message)
                    warnings.append({
                        'code': 'GESTURE_UNAVAILABLE',
                        'message': message,
                        'gestures': missing,
                        'available': available_ids,
                    })
                    # 摘除不可用手势：这些段按默认素材渲染，其余段的手势照常生效。
                    segments = [
                        (seg_text, slide_id, None if gesture in missing else gesture)
                        for (seg_text, slide_id, gesture) in segments
                    ]
                if usable:
                    try:
                        avatar_session.preheat_gestures(list(dict.fromkeys(usable)))
                    except Exception:  # noqa: BLE001
                        # 预热只是让首次触发赶上段首；失败不影响本次朗读
                        logger.exception('[手势标签] 后台预热失败（不影响本次朗读）')
            for seg_text, slide_id, gesture in segments:
                # 新请求到达后，旧请求即使仍在当前线程中，也不得继续入队。
                if request_id != avatar_session.speech_request_id:
                    break
                seg_datainfo = datainfo.copy()
                if slide_id is not None:
                    resolved_slide_id = _resolve_slide_id(slide_id)
                    seg_datainfo['slide_id'] = resolved_slide_id
                    logger.info(f"[PPT标签] echo 分段: slide={resolved_slide_id}, text={seg_text[:30]}")
                if gesture is not None:
                    seg_datainfo['gesture'] = gesture
                    logger.info(f"[手势标签] echo 分段: gesture={gesture}, text={seg_text[:30]}")
                avatar_session.put_msg_txt(seg_text, seg_datainfo)
        elif params['type'] == 'chat':
            text = params['text']
            # chat 模式下也解析标签：设幻灯片 + 清洗文本后给 LLM
            clean_text, slide_id = _parse_ppt_tags(text)
            if slide_id is not None:
                from utils import material_manager as mat_mgr
                resolved_slide_id = _resolve_slide_id(slide_id)
                mat_mgr.set_current_slide(sessionid, resolved_slide_id)
                logger.info(f"[PPT标签] chat 模式切换幻灯片 → id={resolved_slide_id}")
            llm_response = request.app.get("llm_response")
            if llm_response:
                asyncio.get_event_loop().run_in_executor(
                    None, llm_response, clean_text, avatar_session, datainfo
                )

        return json_ok({'warnings': warnings} if warnings else None)
    except Exception as e:
        logger.exception('human route exception:')
        return json_error(str(e))


async def rag_ask(request):
    """RAG 旁路服务代理，不改变原有 /human 协议。"""
    try:
        params = await request.json()
        base_url = os.getenv("RAG_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
        timeout_seconds = float(os.getenv("RAG_PROXY_TIMEOUT", "120"))
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(f"{base_url}/rag/ask", json=params) as response:
                body = await response.text()
                return web.Response(
                    status=response.status,
                    content_type="application/json",
                    text=body,
                )
    except Exception as e:
        logger.exception("rag_ask proxy exception:")
        return web.Response(
            status=503,
            content_type="application/json",
            text=json.dumps({"code": 503, "msg": f"RAG 服务不可用: {e}"}, ensure_ascii=False),
        )


async def rag_admin_proxy(request):
    """转发知识库管理 API 和原文件下载请求。"""
    try:
        base_url = os.getenv("RAG_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
        tail = request.match_info.get("tail", "")
        target = f"{base_url}/rag/admin/{tail}"
        if request.query_string:
            target += "?" + request.query_string
        headers = {}
        for name in ("Content-Type", "Authorization"):
            if request.headers.get(name):
                headers[name] = request.headers[name]
        body = await request.read() if request.method not in {"GET", "HEAD"} else None
        # 文档上传会包含 PDF 解析、分片、Ollama Embedding 和索引写入，
        # 不能使用普通问答的短超时。可通过 RAG_PROXY_TIMEOUT 覆盖，单位为秒。
        proxy_timeout = float(os.getenv("RAG_PROXY_TIMEOUT", "600"))
        timeout = aiohttp.ClientTimeout(
            total=proxy_timeout,
            connect=min(20, proxy_timeout),
            sock_read=proxy_timeout,
        )
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.request(request.method, target, headers=headers, data=body) as response:
                response_headers = {}
                for name in ("Content-Type", "Content-Disposition", "Content-Length"):
                    if response.headers.get(name):
                        response_headers[name] = response.headers[name]
                return web.Response(status=response.status, body=await response.read(), headers=response_headers)
    except asyncio.TimeoutError:
        logger.error("rag_admin_proxy timeout after %ss", os.getenv("RAG_PROXY_TIMEOUT", "600"))
        return web.Response(
            status=504,
            content_type="application/json",
            text=json.dumps({
                "code": 504,
                "msg": "知识库处理超时，索引任务可能仍在后台执行，请稍后刷新状态。",
            }, ensure_ascii=False),
        )
    except Exception as e:
        logger.exception("rag_admin_proxy exception:")
        return web.Response(
            status=503,
            content_type="application/json",
            text=json.dumps({"code": 503, "msg": f"RAG 管理服务不可用: {e}"}, ensure_ascii=False),
        )


async def rag_config_proxy(request):
    """代理 ``GET /rag/config``：把知识库技术配置从 sidecar 透传回来。

    sidecar 不可用时返回 502，让前端明确知道「配置查不到」而不是拿到空壳。
    """
    try:
        base_url = os.getenv("RAG_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
        timeout = aiohttp.ClientTimeout(total=20, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.get(f"{base_url}/rag/config") as response:
                body = await response.text()
                return web.Response(
                    status=response.status,
                    content_type="application/json",
                    text=body,
                )
    except Exception as e:
        logger.exception("rag_config_proxy exception:")
        return web.Response(
            status=502,
            content_type="application/json",
            text=json.dumps({"code": 502, "msg": f"RAG 服务不可用: {e}"}, ensure_ascii=False),
        )


async def interrupt_talk(request):
    """打断当前说话"""
    try:
        params = await request.json()
        sessionid = params.get('sessionid', '')
        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")
        avatar_session.flush_talk()
        return json_ok()
    except Exception as e:
        logger.exception('interrupt_talk exception:')
        return json_error(str(e))


async def playback_control(request):
    """切换会话的播放暂停态。

    暂停期间让画面回到基础素材帧（闭嘴静止），避免定格在张嘴的推理帧上；
    不清队列、不改朗读进度，恢复后从原处继续。
    """
    try:
        params = await request.json()
    except Exception:
        return json_error("invalid json body", code=400)

    sessionid = params.get('sessionid', '')
    avatar_session = get_session(request, sessionid)
    if avatar_session is None:
        return json_error("session not found", code=404)

    paused = params.get('paused', None)
    if not isinstance(paused, bool):
        return json_error("paused must be a boolean", code=400)

    avatar_session.playback_paused = paused
    logger.info("[播放] 会话 %s 暂停态 -> %s", sessionid, paused)
    return json_ok(data={'paused': paused})


async def humanaudio(request):
    """上传音频文件"""
    try:
        form = await request.post()
        sessionid = str(form.get('sessionid', ''))
        fileobj = form["file"]
        filebytes = fileobj.file.read()

        datainfo = {}

        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")
        avatar_session.put_audio_file(filebytes, datainfo)
        return json_ok()
    except Exception as e:
        logger.exception('humanaudio exception:')
        return json_error(str(e))


async def set_audiotype(request):
    """设置自定义状态（动作编排）"""
    try:
        params = await request.json()
        sessionid = params.get('sessionid', '')
        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")
        avatar_session.set_custom_state(params['audiotype'])
        return json_ok()
    except Exception as e:
        logger.exception('set_audiotype exception:')
        return json_error(str(e))


async def record(request):
    """录制控制"""
    try:
        params = await request.json()
        sessionid = params.get('sessionid', '')
        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")
        if params['type'] == 'start_record':
            avatar_session.start_recording()
        elif params['type'] == 'end_record':
            avatar_session.stop_recording()
        return json_ok()
    except Exception as e:
        logger.exception('record exception:')
        return json_error(str(e))


async def is_speaking(request):
    """查询是否正在说话"""
    params = await request.json()
    sessionid = params.get('sessionid', '')
    avatar_session = get_session(request, sessionid)
    if avatar_session is None:
        return json_error("session not found")
    return json_ok(data=avatar_session.is_speaking())


def bubble_snapshot(avatar_session, speaking=False):
    """屏幕上那块对话气泡该显示谁说的话。

    两条链路都会让数字人开口：
    - LLM 问答：`llm_text` 边生成边累加（逐句念），`llm_done` 表示本轮写完；
    - 直接朗读：`say_text` 是一次性整句（欢迎语、菜单介绍、固定应答）。
    谁**后开始**念谁就是当前这句，用两个时间戳比大小判断，别用"哪个非空"——
    上一轮的回答会一直留在 llm_text 里，会把新来的欢迎语压掉。

    `bubble_key` 一变就是新的一轮，客户端据此清空气泡而不是接着往下贴。
    """
    llm_at = float(getattr(avatar_session, 'llm_started_at', 0) or 0)
    say_at = float(getattr(avatar_session, 'say_at', 0) or 0)
    if not llm_at and not say_at:
        # 还没开过口：别把 "done=False" 报出去，客户端会显示"正在想…"
        return {
            'text': '', 'done': True, 'question': '', 'speaking': speaking,
            'request_id': getattr(avatar_session, 'speech_request_id', 0),
            'started_at': 0.0, 'bubble_key': '',
        }
    if llm_at >= say_at:
        return {
            'text': getattr(avatar_session, 'llm_text', '') or '',
            'done': bool(getattr(avatar_session, 'llm_done', False)),
            'question': getattr(avatar_session, 'last_question', '') or '',
            'speaking': speaking,
            'request_id': getattr(avatar_session, 'speech_request_id', 0),
            'started_at': llm_at,
            'bubble_key': 'llm:%.3f' % llm_at if llm_at else '',
        }
    return {
        'text': getattr(avatar_session, 'say_text', '') or '',
        'done': True,                    # 整句朗读：发出时文本就已完整
        'question': '',
        'speaking': speaking,
        'request_id': getattr(avatar_session, 'speech_request_id', 0),
        'started_at': say_at,
        'bubble_key': 'say:%.3f' % say_at if say_at else '',
    }


async def llm_status(request):
    """查询当前会话要说的话，用于同步问答气泡 / 桌面端对话浮层。

    返回的是"这一轮"的完整快照（不是增量），客户端自己做 diff 即可。
    """
    params = await request.json()
    sessionid = params.get('sessionid', '')
    avatar_session = get_session(request, sessionid)
    if avatar_session is None:
        return json_error("session not found")
    try:
        speaking = bool(avatar_session.is_speaking())
    except Exception:  # noqa: BLE001
        speaking = False

    return json_ok(data=bubble_snapshot(avatar_session, speaking))


async def admin_config(request):
    """Admin: 获取全局配置参数"""
    try:
        opt = request.app.get("opt")
        if opt:
            return json_ok(data={"config": vars(opt)})
        return json_error("Config not found")
    except Exception as e:
        logger.exception('admin_config exception:')
        return json_error(str(e))


async def admin_sessions(request):
    """Admin: 获取活跃的会话及其配置"""
    try:
        sessions_info = []
        for sid, avatar_session in session_manager.sessions.items():
            if avatar_session:
                s_opt = getattr(avatar_session, 'opt', None)
                s_data = {
                    "sessionid": sid,
                    "speaking": avatar_session.is_speaking() if hasattr(avatar_session, 'is_speaking') else False,
                    "recording": getattr(avatar_session, 'recording', False),
                }
                if s_opt:
                    s_data.update({
                        "model": getattr(s_opt, "model", ""),
                        "avatar_id": getattr(s_opt, "avatar_id", ""),
                        "REF_FILE": getattr(s_opt, "REF_FILE", ""),
                        "transport": getattr(s_opt, "transport", ""),
                        "batch_size": getattr(s_opt, "batch_size", 0),
                        "customopt": getattr(s_opt, "customopt", []),
                    })
                sessions_info.append(s_data)
        return json_ok(data={"sessions": sessions_info})
    except Exception as e:
        logger.exception('admin_sessions exception:')
        return json_error(str(e))


# ─── 会话踢出 API ──────────────────────────────────────────────────────────

async def admin_session_delete(request):
    """Admin: 踢出指定会话并回收其资源（含对应的 WebRTC 连接）。"""
    try:
        sessionid = request.match_info.get('sessionid', '')
        if not session_manager.has_session(sessionid):
            return json_error("session not found", code=404)

        rtc_manager = request.app.get("rtc_manager")
        closed = False
        if rtc_manager is not None and hasattr(rtc_manager, 'close_session'):
            closed = await rtc_manager.close_session(sessionid)
        session_manager.remove_session(sessionid)
        logger.info("[会话] 已踢出会话 %s（关闭 WebRTC 连接=%s）", sessionid, closed)
        return json_ok(data={"sessionid": sessionid, "peer_connection_closed": closed})
    except Exception as e:
        logger.exception('admin_session_delete exception:')
        return json_error(str(e))


# ─── 声音克隆 API ──────────────────────────────────────────────────────────

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICES_DIR = os.path.join(PROJECT_ROOT, 'data', 'voices')

# 与 GET /api/voices 认可的音频后缀保持同一份定义，避免两处漂移。
ALLOWED_VOICE_EXTS = ('.wav', '.mp3', '.ogg', '.m4a', '.flac')


async def voice_upload(request):
    """上传声音样本：POST /api/voice/upload (multipart/form-data)"""
    try:
        os.makedirs(VOICES_DIR, exist_ok=True)
        form = await request.post()
        fileobj = form.get('file')
        voice_name = (form.get('name') or '未命名').strip() or '未命名'

        if not fileobj:
            return json_error("请上传音频文件")

        # 名称与后缀都会直接决定落盘路径，必须先过校验（见 utils/path_guard.py）。
        try:
            safe_name = path_guard.validate_filename(voice_name)
        except path_guard.PathGuardError as exc:
            logger.warning("[声音克隆] 拒绝非法名称 %r: %s", voice_name, exc)
            return json_error("声音名称不合法：{0}".format(exc))

        ext = os.path.splitext(fileobj.filename or '')[1].lower()
        if ext not in ALLOWED_VOICE_EXTS:
            logger.warning("[声音克隆] 拒绝非法后缀 %r", ext)
            return json_error("音频格式不支持：{0}".format(ext or '(无后缀)'))

        save_name = "{0}{1}".format(safe_name, ext)
        try:
            # 兜底：即使前面的校验将来被改动，落盘路径仍必须落在声音目录内。
            save_path = path_guard.resolve_within(
                VOICES_DIR, os.path.join(VOICES_DIR, save_name))
        except path_guard.PathGuardError:
            logger.warning("[声音克隆] 拒绝越界落盘: %r", save_name)
            return json_error("声音名称不合法")

        # 读取并保存
        content = fileobj.file.read()
        with open(save_path, 'wb') as f:
            f.write(content)

        # 返回相对路径，供前端展示和后端使用
        relative_path = os.path.join('data', 'voices', save_name)
        logger.info(f"[声音克隆] 上传成功: {relative_path} ({len(content)} bytes)")

        return json_ok(data={
            "path": relative_path,
            "name": safe_name,
            "size": len(content),
        })
    except Exception as e:
        logger.exception('voice_upload exception:')
        return json_error(str(e))


async def voice_list(request):
    """列出已上传的声音：GET /api/voices"""
    try:
        voices = []
        if os.path.exists(VOICES_DIR):
            for fname in sorted(os.listdir(VOICES_DIR)):
                if fname.lower().endswith(ALLOWED_VOICE_EXTS):
                    fpath = os.path.join(VOICES_DIR, fname)
                    voices.append({
                        "name": os.path.splitext(fname)[0],
                        "path": os.path.join('data', 'voices', fname),
                        "size": os.path.getsize(fpath),
                    })
        return json_ok(data=voices)
    except Exception as e:
        logger.exception('voice_list exception:')
        return json_error(str(e))


async def voice_system_list(request):
    """列出系统音色（Edge 在线神经语音 + Windows 本机 SAPI 语音）：GET /api/voices/system

    枚举可能涉及网络往返（Edge 列表）或 COM 调用（SAPI），放进线程池执行，
    避免阻塞 aiohttp 事件循环。
    """
    try:
        from utils import system_voices
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, system_voices.all_system_voices)
        return json_ok(data=data)
    except Exception as e:
        logger.exception('voice_system_list exception:')
        return json_error(str(e))


async def voice_apply(request):
    """应用音色到当前会话：POST /api/voice/apply"""
    try:
        params = await request.json()
        sessionid = params.get('sessionid', '')
        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")

        ref_file = params.get('ref_file', '')
        ref_text = params.get('ref_text', '')
        engine = params.get('engine', '')

        if not ref_file:
            return json_error("请指定声音文件")

        # 更新 session 的 TTS 配置
        if hasattr(avatar_session, 'opt'):
            avatar_session.opt.REF_FILE = ref_file
            if ref_text:
                avatar_session.opt.REF_TEXT = ref_text

        logger.info(f"[声音克隆] 应用音色: ref_file={ref_file}, ref_text={ref_text}, engine={engine}, sessionid={sessionid}")

        return json_ok(data={
            "ref_file": ref_file,
            "ref_text": ref_text,
            "engine": engine,
        })
    except Exception as e:
        logger.exception('voice_apply exception:')
        return json_error(str(e))


async def voice_delete(request):
    """删除声音样本：DELETE /api/voice/delete"""
    try:
        params = await request.json()
        file_path = params.get('path', '')
        if not file_path:
            return json_error("请指定文件路径")

        # 相对路径按项目根解析（既有语义），随后必须落在声音目录内。
        candidate = file_path
        if not os.path.isabs(candidate):
            candidate = os.path.join(PROJECT_ROOT, candidate)
        try:
            full_path = path_guard.resolve_within(VOICES_DIR, candidate)
        except path_guard.PathGuardError:
            logger.warning("[声音克隆] 拒绝越界删除: %r", file_path)
            return json_error("只能删除 data/voices 目录下的声音文件")

        if os.path.exists(full_path):
            os.remove(full_path)
            logger.info(f"[声音克隆] 删除: {file_path}")
            return json_ok()
        return json_error("文件不存在")
    except Exception as e:
        logger.exception('voice_delete exception:')
        return json_error(str(e))


# ─── 声音克隆服务代理 ──────────────────────────────────────────────────────
# 克隆服务是独立进程（scripts/voice_clone_server.py），默认 50000 端口。
# 桌面端只跟主服务打交道，所以这里做一层薄代理——和 /rag/admin 一样的划法。
CLONE_BASE = os.getenv("CLONE_SERVER_URL", "http://127.0.0.1:50000").rstrip("/")
CLONE_TIMEOUT = float(os.getenv("CLONE_PROXY_TIMEOUT", "300"))


async def _clone_proxy(request, method, tail, timeout=None):
    try:
        target = "{0}/{1}".format(CLONE_BASE, tail.lstrip("/"))
        if request.query_string:
            target += "?" + request.query_string
        headers = {}
        for name in ("Content-Type",):
            if request.headers.get(name):
                headers[name] = request.headers[name]
        body = await request.read() if method not in ("GET", "HEAD", "DELETE") else (
            None if method == "DELETE" else None)
        if method == "GET":
            body = None
        timeout = aiohttp.ClientTimeout(total=timeout or CLONE_TIMEOUT,
                                        connect=min(10, timeout or CLONE_TIMEOUT))
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.request(method, target, headers=headers, data=body) as resp:
                payload = await resp.read()
                return web.Response(status=resp.status, body=payload,
                                    content_type="application/json")
    except (aiohttp.ClientConnectorError, asyncio.TimeoutError, OSError):
        return web.Response(status=503, content_type="application/json",
                            text=json.dumps({"code": 503, "ready": False,
                                             "msg": "声音克隆服务未启动"},
                                            ensure_ascii=False))
    except Exception as e:
        logger.exception('clone_proxy exception:')
        return web.Response(status=502, content_type="application/json",
                            text=json.dumps({"code": 502, "msg": str(e)},
                                            ensure_ascii=False))


async def clone_status(request):
    """GET /api/clone/status：声音克隆服务是否就绪。"""
    resp = await _clone_proxy(request, "GET", "health", timeout=3)
    if resp.status != 200:
        return json_ok(data={"ready": False, "running": False,
                             "msg": "声音克隆服务未启动"})
    try:
        info = json.loads(resp.body)
    except Exception:  # noqa: BLE001
        info = {}
    return json_ok(data={"running": True, "ready": bool(info.get("ready")),
                         "loading": bool(info.get("loading")),
                         "device": info.get("device", ""),
                         "error": info.get("error", ""),
                         "model_present": bool(info.get("model_present"))})


async def clone_speaker(request):
    """POST /api/clone/speaker：上传一段人声 → 登记成音色（multipart: file）。"""
    return await _clone_proxy(request, "POST", "clone_speaker")


async def clone_speakers(request):
    """GET /api/clone/speakers：已克隆的音色列表。"""
    return await _clone_proxy(request, "GET", "speakers")


async def clone_preview(request):
    """POST /api/clone/preview：用克隆音色合成一句试听音频（PCM 24k int16）。"""
    body = await _clone_proxy(request, "POST", "preview")
    if body.status != 200:
        return body
    # 试听音频是二进制 PCM，包一层 wav 头，前端才能直接播
    import struct
    pcm = body.body
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " \
        + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16) \
        + b"data" + struct.pack("<I", len(pcm))
    return web.Response(status=200, body=header + pcm, content_type="audio/wav")


async def clone_delete(request):
    """DELETE /api/clone/speaker：删除一个克隆音色。"""
    sid = request.match_info.get("sid", "")
    return await _clone_proxy(request, "DELETE", "speakers/%s" % sid)


# ─── 状态检查 API ──────────────────────────────────────────────────────────

async def api_status(request):
    """GET /api/status：给「系统状态」页的一份快照。

    除了原有的 RAG/LLM/会话数，还带上引擎配置与机器资源（CPU/内存/显存），
    采集逻辑在 server/status_info.py。
    """
    try:
        from server import status_info
        return json_ok(data=status_info.collect(session_manager))
    except Exception as e:
        logger.exception('api_status exception:')
        return json_error(str(e))


# ─── 路由注册 ──────────────────────────────────────────────────────────────

async def system_shutdown(request):
    """POST /api/system/shutdown — 让主服务自己退出。

    桌面版关窗时先调这个，而不是直接 `TerminateProcess`：硬杀不会跑
    `on_shutdown`，所以 RAG sidecar（主服务拉起的独立进程）会变成孤儿继续
    占着端口和内存，所有 PeerConnection 也不会走 close。优雅退出多花一两秒，
    换来的是"下次启动干净"。

    只放行本机回环（见 `server/auth_mw.py` 的 LOCAL_PROCESS_PATHS）：
    能访问回环的进程本来就有能力直接杀这个进程，不算额外放大权限。
    """
    loop = asyncio.get_event_loop()
    logger.info("[系统] 收到退出请求，主服务将在 0.3s 后优雅退出")
    # 先让响应写出去，再停循环；否则客户端只会看到连接被重置
    loop.call_later(0.3, loop.stop)
    return json_ok(data={"bye": True})


def setup_routes(app):
    """注册所有路由到 aiohttp app"""

    app.router.add_post("/human", human)
    app.router.add_post("/rag/ask", rag_ask)
    app.router.add_get("/rag/config", rag_config_proxy)
    # 不使用 '*' 全方法路由，aiohttp_cors 无法为该动态资源注册预检处理器。
    # 管理页面实际使用以下四种方法，显式注册也能避免主服务启动时报错。
    for method in ("GET", "POST", "PUT", "DELETE"):
        app.router.add_route(method, "/rag/admin/{tail:.*}", rag_admin_proxy)
    app.router.add_post("/humanaudio", humanaudio)
    app.router.add_post("/set_audiotype", set_audiotype)
    app.router.add_post("/record", record)
    app.router.add_post("/interrupt_talk", interrupt_talk)
    app.router.add_post("/api/playback", playback_control)
    app.router.add_post("/is_speaking", is_speaking)
    app.router.add_post("/llm_status", llm_status)
    app.router.add_get("/api/admin/config", admin_config)
    app.router.add_get("/api/admin/sessions", admin_sessions)
    app.router.add_delete("/api/admin/sessions/{sessionid}", admin_session_delete)

    # 声音克隆 API
    app.router.add_post("/api/voice/upload", voice_upload)
    app.router.add_get("/api/voices", voice_list)
    app.router.add_get("/api/voices/system", voice_system_list)
    app.router.add_post("/api/voice/apply", voice_apply)
    app.router.add_post("/api/voice/delete", voice_delete)

    # 声音克隆服务代理（独立进程，默认 50000）
    app.router.add_get("/api/clone/status", clone_status)
    app.router.add_post("/api/clone/speaker", clone_speaker)
    app.router.add_get("/api/clone/speakers", clone_speakers)
    app.router.add_post("/api/clone/preview", clone_preview)
    app.router.add_delete("/api/clone/speaker/{sid}", clone_delete)

    # 状态检查 API（RAG/LLM/会话数）
    app.router.add_get("/api/status", api_status)

    # 让主服务自己退出（桌面版关窗时调用，见 ServiceLauncher.stop）
    app.router.add_post("/api/system/shutdown", system_shutdown)

    # 注册 avatar 生成相关的路由
    setup_avatar_routes(app)

    # 网页版已移除（项目只保留桌面版）：不再挂载 web/ 静态目录。
