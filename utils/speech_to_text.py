# -*- coding: utf-8 -*-
"""语音转写（faster-whisper，本地离线）。

实测结论（见 `openspec/changes/add-quiz-mode/design.md` 的可行性一节）：
- 本机只有 CPU 可用：GPU 路径因缺少 `cublas64_12.dll` 无法运行（torch 自带的
  CUDA 运行库在 `torch/lib` 下，CTranslate2 不去那里找）。
- CPU + int8 的 RTF 约 0.29（3.4 倍实时），课堂问答够用。
- **必须传简体 `initial_prompt`**：不传时模型会输出**繁体**，对中文课堂是错的。

并发上限为 1：CPU 上并发识别只会让所有请求一起变慢，超出时立即拒绝，
比排队到超时体验更好。超时只表示"不再等待"，实际工作仍占用并发额度，
从而不会真的并发执行。
"""

import os
import threading

from utils.logger import logger

# 简体偏置：实测不传这句时模型输出繁体
SIMPLIFIED_PROMPT = "以下是普通话的句子。"

DEFAULT_MODEL = "small"
DEFAULT_LANGUAGE = "zh"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_MB = 25
BEAM_SIZE = 5

SUPPORTED_SUFFIXES = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".aac", ".mp4")

_ENV_MODEL = "STT_MODEL"
_ENV_DEVICE = "STT_DEVICE"
_ENV_COMPUTE_TYPE = "STT_COMPUTE_TYPE"
_ENV_TIMEOUT = "STT_TIMEOUT"
_ENV_MAX_MB = "STT_MAX_MB"
_ENV_LANGUAGE = "STT_LANGUAGE"

_MODEL_CACHE = {}
_CACHE_LOCK = threading.Lock()
_BUSY_LOCK = threading.Lock()


class SpeechToTextError(RuntimeError):
    """转写失败。消息面向调用方，可直接回显；`hint` 给出排查建议。"""

    def __init__(self, message, hint="", busy=False):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.busy = busy


def _env_float(name, default):
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("[语音] 环境变量 %s=%r 不是数字，改用默认值 %s", name, raw, default)
        return default
    return value if value > 0 else default


def get_config():
    """从环境变量读取转写配置；缺失时回退内置默认值。

    `STT_MODEL` 既可以是模型规格名（如 `small`），也可以是**本地模型目录**，
    后者用于离线部署（本机已有 `D:\\models\\faster-whisper-small`）。
    """
    model = (os.getenv(_ENV_MODEL) or "").strip() or DEFAULT_MODEL
    device = (os.getenv(_ENV_DEVICE) or "").strip() or "cpu"
    compute_type = (os.getenv(_ENV_COMPUTE_TYPE) or "").strip()
    if not compute_type:
        compute_type = "float16" if device == "cuda" else "int8"
    timeout = _env_float(_ENV_TIMEOUT, DEFAULT_TIMEOUT)
    max_mb = _env_float(_ENV_MAX_MB, DEFAULT_MAX_MB)
    language = (os.getenv(_ENV_LANGUAGE) or "").strip() or DEFAULT_LANGUAGE
    return {
        "model": model,
        "device": device,
        "compute_type": compute_type,
        "timeout": timeout,
        "max_mb": max_mb,
        "max_bytes": int(max_mb * 1024 * 1024),
        "language": language,
    }


def validate_audio_name(name):
    """校验扩展名；不支持时把支持的格式一并写在消息里。"""
    suffix = os.path.splitext(str(name or ""))[1].lower()
    supported = "、".join(item.lstrip(".") for item in SUPPORTED_SUFFIXES)
    if suffix in SUPPORTED_SUFFIXES:
        return suffix
    raise SpeechToTextError(
        "不支持的音频格式: %s；支持的格式: %s" % (suffix or "（无扩展名）", supported),
        hint="请把录音转为上述任一格式后重试",
    )


def validate_size(size, max_bytes):
    """校验体积；超限时说明上限，且调用方必须在读取内容之前调用它。"""
    if size is None:
        return
    if size > max_bytes:
        raise SpeechToTextError(
            "音频体积 %d 字节超过上限 %d 字节（约 %.0f MB）"
            % (size, max_bytes, max_bytes / 1024.0 / 1024.0),
            hint="请缩短录音时长后重试",
        )


def _load_model(model_ref, device, compute_type):
    """真正加载模型。单独抽出来便于测试替换。"""
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # noqa: BLE001
        raise SpeechToTextError(
            "缺少 faster-whisper 依赖: %s" % exc,
            hint="执行 pip install faster-whisper 后重启服务",
        )
    try:
        return WhisperModel(model_ref, device=device, compute_type=compute_type)
    except Exception as exc:  # noqa: BLE001
        raise SpeechToTextError(
            "加载语音模型失败（model=%s device=%s compute=%s）：%s"
            % (model_ref, device, compute_type, exc),
            hint="本地离线部署请把 STT_MODEL 指向已下载的模型目录",
        )


def get_model(model_ref=None, device=None, compute_type=None):
    """按需加载并复用模型；同一规格只加载一次。"""
    config = get_config()
    model_ref = model_ref or config["model"]
    device = device or config["device"]
    compute_type = compute_type or config["compute_type"]
    key = (model_ref, device, compute_type)

    with _CACHE_LOCK:
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            return cached
    # 加载放在锁外：加载很慢，不应阻塞其它规格的查询
    model = _load_model(model_ref, device, compute_type)
    with _CACHE_LOCK:
        _MODEL_CACHE[key] = model
    logger.info("[语音] 模型已加载: %s (%s/%s)", model_ref, device, compute_type)
    return model


def clear_model_cache():
    """清空模型缓存（测试与显存回收用）。"""
    with _CACHE_LOCK:
        _MODEL_CACHE.clear()


def is_busy():
    return _BUSY_LOCK.locked()


def _transcribe_once(path, config, model_ref, device, compute_type, language):
    model = get_model(model_ref, device, compute_type)
    try:
        segments, _info = model.transcribe(
            path,
            language=language or config["language"],
            beam_size=BEAM_SIZE,
            initial_prompt=SIMPLIFIED_PROMPT,
        )
        text = "".join(segment.text for segment in segments).strip()
    except SpeechToTextError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SpeechToTextError(
            "音频无法解析或识别失败: %s" % exc,
            hint="确认文件是受支持的音频格式且未损坏",
        )
    if not text:
        raise SpeechToTextError(
            "未识别到语音内容",
            hint="确认录音有声音、不是静音，并检查麦克风是否正常",
        )
    return text


def transcribe_audio(path, model_ref=None, device=None, compute_type=None,
                     language=None, timeout=None):
    """把音频转写为文本。失败抛出 `SpeechToTextError`。"""
    config = get_config()
    timeout = timeout if timeout and timeout > 0 else config["timeout"]

    if not path or not os.path.isfile(path):
        raise SpeechToTextError("音频文件不存在: %s" % path, hint="确认上传是否成功")

    if not _BUSY_LOCK.acquire(blocking=False):
        # 立即拒绝，而不是排队——CPU 上排队只会让所有请求一起变慢
        raise SpeechToTextError(
            "正在处理另一段语音，请稍后重试",
            hint="同一时刻只处理一个转写请求",
            busy=True,
        )

    box = {}
    done = threading.Event()

    def worker():
        try:
            box["text"] = _transcribe_once(
                path, config, model_ref, device, compute_type, language
            )
        except BaseException as exc:  # noqa: BLE001 - 原样交给调用方
            box["error"] = exc
        finally:
            done.set()
            # 只有真正结束时才释放，因此超时不会造成真并发
            _BUSY_LOCK.release()

    thread = threading.Thread(target=worker, daemon=True, name="stt-transcribe")
    thread.start()

    if not done.wait(timeout):
        raise SpeechToTextError(
            "语音识别超时（%.0f 秒）" % timeout,
            hint="可缩短录音时长，或调大 STT_TIMEOUT；CPU 环境下识别较慢属正常",
        )

    error = box.get("error")
    if error is not None:
        raise error
    return box.get("text", "")
