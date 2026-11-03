# -*- coding: utf-8 -*-
"""语音转写接口与语音作答接口（课堂面，免口令）。

分工：
- `POST /api/stt/transcribe` —— 通用转写，返回识别文本；
- `POST /api/quiz/answer/audio` —— 语音作答：先转写，再走**与文字作答完全相同**的
  提交路径（同一个编排器方法），因此判分、记录、继续讲课的逻辑只有一份。

上传处理是流式的：超限时立即中断，MUST NOT 把整个音频读进内存。
转写失败必须保留等待作答状态，允许学员重录。
"""

import os
import tempfile
import uuid

from server import quiz_routes
from server.blocking import run_blocking
from server.quiz_routes import (
    _require_object,  # noqa: F401  (与提问接口共用同一套请求校验)
    _stage_value,
    json_error,
    json_ok,
)
from utils.logger import logger
from utils.speech_to_text import (
    SpeechToTextError,
    get_config,
    transcribe_audio,
    validate_audio_name,
    validate_size,
)

UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "knowtalk-stt")
TEMP_PREFIX = "stt-"
READ_CHUNK = 256 * 1024


def _error_response(exc, prefix=""):
    """把 SpeechToTextError 的 message 与 hint 一起返回，便于前端直接展示。"""
    message = str(exc)
    hint = getattr(exc, "hint", "")
    if prefix:
        message = "%s%s" % (prefix, message)
    if hint:
        message = "%s（%s）" % (message, hint)
    return json_error(message)


async def _save_upload(request, config):
    """把 multipart 里的音频流式落盘。返回临时文件路径。

    超过体积上限时立即中断，不继续读取剩余内容。
    """
    # 先显式校验 Content-Type。不校验的话 aiohttp 的 `request.multipart()` 会直接抛
    # `KeyError: 'Content-Type'`，被兜底分支原样拼进业务错误返回给调用方
    # （实测客户端收到的是 `语音转写失败: 'Content-Type'`），既不可读也不该外泄。
    try:
        content_type = request.content_type or ""
    except Exception:  # noqa: BLE001 - 头部畸形时按"不是 multipart"处理
        content_type = ""
    if "multipart/form-data" not in content_type.lower():
        raise SpeechToTextError(
            "请求必须以 multipart/form-data 提交音频",
            hint="请把 Content-Type 设为 multipart/form-data，并把音频放在带文件名的分片里")

    reader = await request.multipart()
    part = await reader.next()
    while part is not None and not getattr(part, "filename", None):
        # 跳过没有文件名的分片（例如同请求里的普通字段）
        part = await reader.next()
    if part is None:
        raise SpeechToTextError("请求里没有找到音频文件", hint="请以 multipart 上传 audio 字段")

    suffix = validate_audio_name(part.filename)
    max_bytes = config["max_bytes"]

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    path = os.path.join(UPLOAD_DIR, "%s%s%s" % (TEMP_PREFIX, uuid.uuid4().hex[:8], suffix))
    written = 0
    try:
        with open(path, "wb") as handle:
            while True:
                chunk = await part.read_chunk(READ_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise SpeechToTextError(
                        "音频体积超过上限 %d 字节（约 %.0f MB）"
                        % (max_bytes, max_bytes / 1024.0 / 1024.0),
                        hint="请缩短录音时长后重试",
                    )
                handle.write(chunk)
        if written == 0:
            raise SpeechToTextError("上传的音频是空文件", hint="请重新录制后上传")
    except BaseException:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                logger.warning("[语音] 清理临时文件失败: %s", path)
        raise
    return path


def _remove_quietly(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            logger.warning("[语音] 清理临时文件失败: %s", path)


async def stt_transcribe(request):
    """通用转写接口：受理音频，返回文本。"""
    config = get_config()
    path = None
    try:
        path = await _save_upload(request, config)
        text = await run_blocking(transcribe_audio, path)
        return json_ok(data={"text": text, "language": config["language"]})
    except SpeechToTextError as exc:
        logger.warning("[语音] 转写失败: %s", exc)
        return _error_response(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[语音] 转写接口异常")
        return json_error("语音转写失败: %s" % exc)
    finally:
        _remove_quietly(path)


async def quiz_answer_audio(request):
    """语音作答：转写后走与文字作答完全相同的提交路径。"""
    sessionid = str(request.query.get("sessionid") or "").strip()
    if not sessionid:
        return json_error("缺少 sessionid")

    orchestrator = quiz_routes.registry.get(sessionid)
    if orchestrator is None:
        return json_error("该会话未启用提问模式: %s" % sessionid)

    config = get_config()
    path = None
    try:
        path = await _save_upload(request, config)
        # 转写是 CPU 密集的同步调用（faster-whisper）。直接跑在协程里会把事件循环
        # 冻住数秒：数字人停流、状态轮询排队，学员以为"卡死了"。
        text = await run_blocking(transcribe_audio, path)
    except SpeechToTextError as exc:
        # 转写失败（含格式不合法、超限、识别不到语音）：保留等待作答状态，允许重录
        logger.warning("[提问] 语音作答转写失败 session=%s: %s", sessionid, exc)
        return _error_response(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[提问] 语音作答转写异常")
        return json_error("语音作答失败: %s" % exc)
    finally:
        _remove_quietly(path)

    # 与文字作答同一条路径；判分同样是阻塞调用，一并卸载。
    if not await run_blocking(orchestrator.submit_answer, text):
        return json_error("当前不处于等待作答状态，或识别结果为空")
    return json_ok(data={
        "sessionid": sessionid,
        "text": text,
        "stage": _stage_value(orchestrator.stage),
    })


def setup_stt_routes(app):
    """注册语音转写路由。必须在 `add_static('/')` 之前调用。"""
    app.router.add_post("/api/stt/transcribe", stt_transcribe)
    app.router.add_post("/api/quiz/answer/audio", quiz_answer_audio)
