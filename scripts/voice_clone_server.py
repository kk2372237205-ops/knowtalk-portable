# -*- coding: utf-8 -*-
"""声音克隆服务（独立进程）：上传一段音频 → 得到一个可以朗读的音色。

技术方案
--------
引擎用 **XTTS-v2**（Coqui TTS，环境里已装 `TTS==0.22.0`）。选它的理由：

- 零样本克隆：给它一段 3~10 秒的人声，就能用那个人的音色读任意文本，
  不需要训练、不需要微调——这正是"通过一段声音克隆声音"要的能力。
- 中文开箱可用（language="zh-cn"），不用再挂中文前端或分词器。
- 显存占用约 2~3GB，本项目 RTX 4060 Laptop（8GB）在数字人跑着的时候仍放得下。
- 项目里已有 `tts/xtts.py` 客户端，协议对上就能直接复用，主服务一行都不用改。

为什么是独立进程而不是塞进主服务：模型加载要几十秒到几分钟，且常驻显存。
放独立进程后，不用克隆功能时它根本不启动，不抢数字人的显存。

协议（与 tts/xtts.py 已对齐，勿随意改动）
------------------------------------------
- POST /clone_speaker  multipart: wav_file   → {"speaker_id": "..."}
  客户端把这个 JSON 原样塞进下一次请求，所以这里只回 speaker_id 就够了
  （官方实现回的是整段 latent 数组，几百 KB，没必要往返搬）。
- POST /tts_stream     json: speaker_id/text/language/stream_chunk_size
                                            → 流式 int16 PCM @ 24000Hz
- GET  /health, GET /speakers, DELETE /speakers/<id>, POST /preview（试听）

启动：python scripts/voice_clone_server.py [--port 50000] [--device cuda|cpu]
"""
import argparse
import asyncio
import functools
import hashlib
import io
import json
import os
import sys
import threading
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aiohttp import web  # noqa: E402

MODEL_DIR = os.path.join(ROOT, "data", "models", "xtts_v2")
# 克隆出来的音色（原始音频）与 latent 缓存都放在主服务同一目录树下，
# 这样 GET /api/voices 能直接把它们列进"参考音色"下拉。
VOICES_DIR = os.path.join(ROOT, "data", "voices")
CLONE_DIR = os.path.join(VOICES_DIR, ".clone")      # latent 缓存
LOG_PATH = os.path.join(ROOT, "logs", "voice_clone.log")

ALLOWED_EXTS = (".wav", ".mp3", ".ogg", ".m4a", ".flac", ".webm")

_state = {
    "model": None,
    "config": None,
    "device": "cuda",
    "ready": False,
    "error": "",
    "loading": False,
}
_lock = threading.Lock()


def _patch_audio_decode():
    """把 torchaudio.load 换成 soundfile 实现。

    torchaudio 2.11 用 torchcodec 解码，而 torchcodec 要系统里有 FFmpeg 的
    DLL——Windows 上常常没有，结果 XTTS 一读 wav 就抛
    "Failed to create AudioDecoder ... Could not load libtorchcodec"。
    装 FFmpeg 太重，而 soundfile 走 libsndfile（本来就是依赖之一），
    读 wav/flac/ogg 完全够用，所以这里直接换掉解码这一步。
    """
    import torch
    import torchaudio
    import soundfile as sf

    def _load(path, *args, **kwargs):
        data, sr = sf.read(path, dtype="float32", always_2d=True)
        return torch.from_numpy(np.ascontiguousarray(data.T)), sr

    torchaudio.load = _load


def _decode_upload(data, filename):
    """上传的音频统一成 24000Hz 单声道 float32。

    各种来源的音频（手机录音、视频里扒出来的、mp3…）采样率和声道都不统一，
    在这里就归一掉，后面 XTTS 取音色特征时不会因为格式问题翻车。
    """
    import soundfile as sf
    import resampy
    ext = os.path.splitext(filename or "")[1].lower()
    try:
        arr, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("读不了这个音频（%s）：%s\n建议先转成 wav 再上传。"
                         % (ext or "未知格式", exc))
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    if sr != 24000:
        arr = resampy.resample(np.asarray(arr, dtype=np.float32), sr, 24000)
    if arr.size < 2400:                       # 不足 0.1 秒，克隆不出东西
        raise ValueError("音频太短了（不足 0.1 秒）")
    return np.asarray(arr, dtype=np.float32)


def _load_model(device):
    """加载模型。耗时较长（几十秒起），所以放在后台线程里跑。"""
    try:
        _state["loading"] = True
        import torch
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts
        from TTS.config.shared_configs import BaseDatasetConfig
        from TTS.tts.models.xtts import XttsArgs, XttsAudioConfig

        # torch 2.6+ 起 torch.load 默认 weights_only=True，而 XTTS 的 checkpoint
        # 里存的是它自己的配置类，会被拦下来报 UnpicklingError。这里显式放行。
        try:
            torch.serialization.add_safe_globals(
                [XttsArgs, XttsAudioConfig, BaseDatasetConfig])
        except Exception:  # noqa: BLE001
            pass
        if not getattr(torch.load, "_vc_patched", False):
            _orig_load = torch.load

            def _load(*a, **kw):
                kw.setdefault("weights_only", False)
                return _orig_load(*a, **kw)
            _load._vc_patched = True
            torch.load = _load

        use_gpu = device == "cuda" and torch.cuda.is_available()
        _state["device"] = "cuda" if use_gpu else "cpu"

        try:
            _patch_audio_decode()
        except Exception as exc:  # noqa: BLE001
            print("[克隆] 音频解码替换失败（可能读不了音频）: %r" % (exc,), flush=True)

        config = XttsConfig()
        config.load_json(os.path.join(MODEL_DIR, "config.json"))
        model = Xtts.init_from_config(config)
        print("[克隆] 正在加载 XTTS-v2 权重…", flush=True)
        model.load_checkpoint(config, checkpoint_dir=MODEL_DIR, eval=True)
        if use_gpu:
            model.cuda()
        else:
            model.cpu()
        _state["model"], _state["config"] = model, config
        _state["ready"] = True
        print("[克隆] 模型就绪（device=%s）" % _state["device"], flush=True)
    except Exception as exc:  # noqa: BLE001
        _state["error"] = "%r" % (exc,)
        print("[克隆] 加载失败: %r" % (exc,), flush=True)
        import traceback
        traceback.print_exc()
    finally:
        _state["loading"] = False


# ── 音色缓存 ─────────────────────────────────────────────────────────
def _clean_stem(name):
    stem = os.path.splitext(os.path.basename(name or ""))[0][:24]
    stem = "".join(c for c in stem if c not in "\\/:*?\"<>|").strip()
    return stem


def _index_path():
    return os.path.join(CLONE_DIR, "index.json")


def _load_index():
    try:
        with open(_index_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_index(idx):
    os.makedirs(CLONE_DIR, exist_ok=True)
    with open(_index_path(), "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=1)


def _speaker_id(arr, name=""):
    """按**音频内容**定 id：同一段声音重复上传只保留一个音色。

    早先是按「文件名字 + 原始字节」算的，结果同一段音频换了个文件名上传
    就会多出一个音色（列表里出现两个大小一模一样的条目）。改成对归一化后的
    波形取 hash——格式、文件名怎么变，只要声音一样就是同一个音色。
    返回 (sid, is_new)。
    """
    digest = hashlib.sha1(np.ascontiguousarray(arr).tobytes()).hexdigest()[:12]
    idx = _load_index()
    hit = idx.get(digest)
    if hit and os.path.isfile(os.path.join(VOICES_DIR, hit + ".wav")):
        return hit, False
    stem = _clean_stem(name)
    sid = "%s-%s" % (stem, digest) if stem else "vc-" + digest
    idx[digest] = sid
    _save_index(idx)
    return sid, True


def _latent_path(sid):
    return os.path.join(CLONE_DIR, sid + ".npz")


def _get_latents(sid):
    """取音色条件；没有就现算并落盘缓存（算一次约 1~3 秒）。"""
    model = _state["model"]
    path = _latent_path(sid)
    if os.path.isfile(path):
        try:
            z = np.load(path, allow_pickle=False)
            import torch
            g = torch.from_numpy(z["gpt"]).to(_state["device"])
            s = torch.from_numpy(z["speaker"]).to(_state["device"])
            return g, s
        except Exception:  # noqa: BLE001
            pass
    wav = None
    for ext in ALLOWED_EXTS:
        cand = os.path.join(VOICES_DIR, sid + ext)
        if os.path.isfile(cand):
            wav = cand
            break
    if wav is None:
        raise FileNotFoundError("找不到音色音频: %s" % sid)
    g, s = model.get_conditioning_latents(audio_path=[wav])
    os.makedirs(CLONE_DIR, exist_ok=True)
    np.savez(path, gpt=g.cpu().numpy(), speaker=s.cpu().numpy())
    return g, s


def _list_speakers():
    out = []
    if not os.path.isdir(VOICES_DIR):
        return out
    for fname in sorted(os.listdir(VOICES_DIR)):
        if not fname.lower().endswith(ALLOWED_EXTS):
            continue
        path = os.path.join(VOICES_DIR, fname)
        out.append({"name": os.path.splitext(fname)[0],
                    "file": fname,
                    "size": os.path.getsize(path),
                    "cached": os.path.isfile(
                        _latent_path(os.path.splitext(fname)[0]))})
    return out


# ── 接口 ─────────────────────────────────────────────────────────────
async def health(request):
    return web.json_response({
        "status": "ok",
        "ready": _state["ready"],
        "loading": _state["loading"],
        "device": _state["device"],
        "error": _state["error"],
        "model_dir": MODEL_DIR,
        "model_present": os.path.isfile(os.path.join(MODEL_DIR, "model.pth")),
    })


async def clone_speaker(request):
    """上传一段人声 → 登记成音色。返回 speaker_id，客户端会原样带回。"""
    if not _state["ready"]:
        return web.json_response({"error": "模型尚未就绪"}, status=503)
    try:
        reader = await request.multipart()
        field = None
        filename = ""
        async for part in reader:
            if part.name in ("wav_file", "file"):
                filename = part.filename or ""
                field = await part.read()
        if not field:
            return web.json_response({"error": "没有收到音频"}, status=400)

        os.makedirs(VOICES_DIR, exist_ok=True)
        try:
            arr = _decode_upload(field, filename)
        except ValueError as exc:
            # 音频本身有问题：告诉用户原因，不留一个用不了的"音色"在列表里
            return web.json_response({"error": str(exc)}, status=400)

        import soundfile as sf
        sid, is_new = _speaker_id(arr, filename)
        out = os.path.join(VOICES_DIR, sid + ".wav")
        if is_new or not os.path.isfile(out):
            sf.write(out, arr, 24000, subtype="PCM_16")

        # 立刻算一次 latent：既是校验（音频不可用会在这里报错），
        # 也让第一次朗读不用再等这几秒。
        try:
            _get_latents(sid)
        except Exception as exc:  # noqa: BLE001
            # 保留文件（用户可能只是想存一段音频），但明确报失败
            return web.json_response(
                {"error": "音色特征提取失败：%r" % (exc,)}, status=500)
        return web.json_response({
            "speaker_id": sid,
            "file": os.path.join("data", "voices", sid + ".wav"),
            "seconds": round(len(arr) / 24000.0, 2),
            "reused": not is_new,      # 同一段声音已经克隆过，直接复用
            "conditioned": True,
        })
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"error": repr(exc)}, status=500)


def _split_text(text, limit=180):
    """长文本切句，逐句流式返回，避免等整段合成完才能出声。"""
    import re
    parts = re.split(r"(?<=[。！？；!?;])", text or "")
    buf, out = "", []
    for p in parts:
        if len(buf) + len(p) > limit and buf:
            out.append(buf)
            buf = p
        else:
            buf += p
    if buf:
        out.append(buf)
    return [p for p in (x.strip() for x in out) if p]


async def tts_stream(request):
    """流式朗读：返回 int16 PCM @ 24000Hz（与 tts/xtts.py 的解析一致）。

    注意客户端把 /clone_speaker 的返回**平铺**进这个 body，
    所以 speaker_id 在顶层，不在嵌套对象里。
    """
    if not _state["ready"]:
        return web.json_response({"error": "模型尚未就绪"}, status=503)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "请求体不是 JSON"}, status=400)

    sid = body.get("speaker_id") or ""
    text = (body.get("text") or "").strip()
    lang = body.get("language") or "zh-cn"
    try:
        chunk_size = int(body.get("stream_chunk_size") or 20)
    except (TypeError, ValueError):
        chunk_size = 20
    if not text:
        return web.json_response({"error": "文本为空"}, status=400)

    try:
        gpt_cond, spk_emb = _get_latents(sid)
    except FileNotFoundError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"error": repr(exc)}, status=500)

    resp = web.StreamResponse(status=200, headers={"Content-Type": "application/octet-stream"})
    await resp.prepare(request)
    model = _state["model"]
    loop = asyncio.get_event_loop()

    # 这里没用 model.inference_stream：Coqui 0.22 的流式生成走到
    # transformers 的 _prepare_attention_mask_for_generation 时会拿 int 当
    # tensor 用，抛 AttributeError（TTS 0.22 与 transformers 4.45 不兼容）。
    # 改成「按句合成 + 按 20ms 分块推送」：出来的数据格式与真流式完全一致
    # （int16 PCM 24kHz），客户端无感；代价只是每句多等一两秒。
    step = max(240, int(24000 * chunk_size / 1000.0))   # chunk_size=20 → 20ms
    try:
        for piece in _split_text(text):
            wav = await loop.run_in_executor(
                None, functools.partial(
                    model.inference, piece, lang, gpt_cond, spk_emb,
                    temperature=0.75, repetition_penalty=5.0,
                    enable_text_splitting=False))
            arr = (wav or {}).get("wav")
            if arr is None:
                continue
            if hasattr(arr, "cpu"):
                arr = arr.cpu().numpy()
            pcm = np.clip(np.asarray(arr, dtype=np.float32).squeeze(), -1.0, 1.0)
            for i in range(0, len(pcm), step):
                await resp.write((pcm[i:i + step] * 32767.0).astype(np.int16).tobytes())
    except Exception as exc:  # noqa: BLE001
        print("[克隆] 合成中断: %r" % (exc,), flush=True)
    await resp.write_eof()
    return resp


async def preview(request):
    """试听：合成一段整段 wav（非流式），界面点一下就能听效果。"""
    if not _state["ready"]:
        return web.json_response({"error": "模型尚未就绪"}, status=503)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    sid = body.get("speaker_id") or ""
    text = (body.get("text") or "这是一段用你的声音朗读的测试语音。").strip()
    try:
        gpt_cond, spk_emb = _get_latents(sid)
    except FileNotFoundError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"error": repr(exc)}, status=500)
    try:
        out = _state["model"].inference(
            text, body.get("language") or "zh-cn", gpt_cond, spk_emb,
            temperature=0.75, repetition_penalty=5.0, enable_text_splitting=False)
        wav = out.get("wav")
        if wav is None:
            return web.json_response({"error": "合成失败"}, status=500)
        if hasattr(wav, "cpu"):
            wav = wav.cpu().numpy()
        pcm = np.clip(np.asarray(wav, dtype=np.float32).squeeze(), -1.0, 1.0)
        return web.Response(
            body=(pcm * 32767.0).astype(np.int16).tobytes(),
            content_type="application/octet-stream")
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"error": repr(exc)}, status=500)


async def speakers(request):
    return web.json_response({"speakers": _list_speakers()})


async def delete_speaker(request):
    sid = request.match_info.get("sid", "")
    removed = []
    for ext in ALLOWED_EXTS:
        p = os.path.join(VOICES_DIR, sid + ext)
        if os.path.isfile(p):
            os.remove(p)
            removed.append(os.path.basename(p))
    p = _latent_path(sid)
    if os.path.isfile(p):
        os.remove(p)
        removed.append(os.path.basename(p))
    # 索引里也删掉，否则同名音频再上传会被判成"已存在"却找不到文件
    idx = _load_index()
    for digest, name in list(idx.items()):
        if name == sid:
            idx.pop(digest, None)
    _save_index(idx)
    return web.json_response({"removed": removed})


def build_app():
    app = web.Application(client_max_size=256 * 1024 * 1024)
    app.router.add_get("/health", health)
    app.router.add_post("/clone_speaker", clone_speaker)
    app.router.add_post("/tts_stream", tts_stream)
    app.router.add_post("/preview", preview)
    app.router.add_get("/speakers", speakers)
    app.router.add_delete("/speakers/{sid}", delete_speaker)
    return app


def main():
    ap = argparse.ArgumentParser(description="XTTS-v2 声音克隆服务")
    ap.add_argument("--port", type=int, default=50000)
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    os.makedirs(VOICES_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    if not os.path.isfile(os.path.join(MODEL_DIR, "model.pth")):
        print("[克隆] 未找到模型：%s" % MODEL_DIR, flush=True)
        print("[克隆] 请先运行 scripts/fetch_voice_clone_model.py 下载 XTTS-v2", flush=True)
        return 2

    # 模型加载放到后台线程：HTTP 端口立刻起来，/health 能回报进度，
    # 界面上就能显示"正在加载"而不是一直转圈猜。
    threading.Thread(target=_load_model, args=(args.device,), daemon=True).start()
    print("[克隆] 服务监听 http://%s:%d" % (args.host, args.port), flush=True)
    web.run_app(build_app(), host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
