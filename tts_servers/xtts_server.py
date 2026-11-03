# -*- coding: utf-8 -*-
"""XTTS-v2 本地语音服务（兼容项目 tts/xtts.py 客户端接口，运行时完全离线）。

接口（与项目 tts/xtts.py 的调用方式一一对应）：
- ``POST /clone_speaker``   multipart 上传 wav（字段名 ``wav_file``）→ ``{"speaker_wav": "<wav 绝对路径>"}``
- ``POST /tts_stream``      json ``{text, language, speaker_wav, stream_chunk_size}``
                            → 流式返回 24kHz int16 PCM 块（项目端 np.frombuffer 直接消费）
- ``GET  /health``          服务与模型是否就绪

用法：:
    python tts_servers/xtts_server.py --model D:\\models\\tts\\XTTS-v2 --port 50000

然后项目侧：``python app.py --tts xtts --TTS_SERVER http://127.0.0.1:50000 --REF_FILE data/voices/参考.wav``
（XTTS 用参考音频克隆音色，REF_FILE 必须是本机 wav 路径，不能填 Edge 语音名。）
"""

import argparse
import os
import tempfile
import threading
import time

import numpy as np
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

app = FastAPI(title="XTTS 本地语音服务")

_STATE = {
    "tts": None,
    "device": "cuda",
    "ready": False,
    "error": "",
    "lock": threading.Lock(),
}
_REF_DIR = os.path.join(tempfile.gettempdir(), "xtts-refs")
os.makedirs(_REF_DIR, exist_ok=True)


def _patch_load_audio():
    """把 XTTS 的 load_audio 从 torchaudio(torchcodec) 换成 soundfile。

    torchaudio 2.11 的音频后端全部依赖 torchcodec，而 torchcodec 在 Windows 上
    需要系统 FFmpeg full-shared DLL（本机没有）。soundfile 是纯 Python 绑定
    （libsndfile），零外部依赖，读 wav 更稳。
    """
    import torch
    import torchaudio
    import TTS.tts.models.xtts as xtts_mod

    def patched(audiopath, sampling_rate):
        import soundfile as sf
        data, lsr = sf.read(audiopath, dtype="float32", always_2d=True)
        if data.shape[1] > 1:
            data = data.mean(axis=1, keepdims=True)
        audio = torch.from_numpy(data.T)  # [1, N]
        if lsr != sampling_rate:
            audio = torchaudio.functional.resample(audio, lsr, sampling_rate)
        audio.clip_(-1.0, 1.0)
        # fp16 推理时模型权重为 Half，输入也必须转 Half，否则报类型不匹配
        if _STATE["device"] == "cuda" and _STATE["fp16"]:
            audio = audio.half()
        return audio

    xtts_mod.load_audio = patched

    # fp16 下 mel 谱输出为 fp32，会与 half 权重冲突，转 half
    if _STATE["fp16"]:
        _orig_mel = xtts_mod.wav_to_mel_cloning

        def _patched_mel(*a, **k):
            mel = _orig_mel(*a, **k)
            return mel.half()

        xtts_mod.wav_to_mel_cloning = _patched_mel

    # transformers 4.46 的 isin_mps_friendly 要求 elements 为 tensor，
    # 而 TTS 0.22 stream_generator 传入 int 型 eos_token_id 触发崩溃，此处兜底
    import transformers.pytorch_utils as _tpu
    import transformers.generation.utils as _tgu

    _orig_isin = _tpu.isin_mps_friendly

    def _safe_isin(elements, test_elements):
        if isinstance(elements, int):
            elements = torch.tensor([elements])
        return _orig_isin(elements, test_elements)

    _tpu.isin_mps_friendly = _safe_isin
    _tgu.isin_mps_friendly = _safe_isin

    # TTS 0.22 的流式生成跳过了 transformers 的准备阶段，导致
    # generation_config._eos_token_tensor 未赋值（transformers 4.4x/4.5x 的
    # _get_logits_processor 会访问该属性）。此处 patch StreamGenerationConfig.__init__
    # 在配置构造时补齐属性；同时覆盖 PreTrainedModel.generate_stream（它是
    # init_stream_support() 对 NewGenerationMixin.generate 的旧快照，改类属性不生效）。
    import TTS.tts.layers.xtts.stream_generator as _sg

    _orig_sg_init = _sg.StreamGenerationConfig.__init__

    def _patched_sg_init(self, **kwargs):
        _orig_sg_init(self, **kwargs)
        # 注意：保持 int/list 原样（不转 tensor）。transformers 的
        # to_json_string 会遍历实例属性，tensor 无法序列化；而 list 是
        # transformers 显式支持的类型（utils.py 有 isinstance(list) 分支）。
        eos = getattr(self, "eos_token_id", None)
        if eos is None:
            eos = getattr(self, "eos_token_ids", None)
        if isinstance(eos, int):
            eos = [eos]
        self._eos_token_tensor = eos

    _sg.StreamGenerationConfig.__init__ = _patched_sg_init

    def _patched_generate(self, inputs=None, generation_config=None, **kwargs):
        cfg = generation_config or getattr(self, "generation_config", None)
        if cfg is not None and not hasattr(cfg, "_eos_token_tensor"):
            eos = getattr(cfg, "eos_token_id", None)
            if eos is None:
                eos = getattr(cfg, "eos_token_ids", None)
            if isinstance(eos, int):
                eos = [eos]
            cfg._eos_token_tensor = eos
        return _orig_generate(self, inputs=inputs, generation_config=generation_config, **kwargs)

    from transformers import PreTrainedModel as _PTM

    _orig_generate = _sg.NewGenerationMixin.generate
    _sg.NewGenerationMixin.generate = _patched_generate
    # generate_stream 是 init_stream_support() 对旧方法对象的快照，需直接覆盖
    _PTM.generate_stream = _patched_generate

    # transformers 4.4x+ 把 temperature/top_k/top_p 合并进 _get_logits_processor，
    # 已不存在 _get_logits_warper；补空实现避免 AttributeError 且不会重复采样。
    # 注意：GPT2InferenceModel 的 MRO 里没有 NewGenerationMixin，必须 patch
    # transformers.generation.utils.GenerationMixin 才能被实例解析到。
    def _patched_warper(self, generation_config, **kwargs):
        from transformers.generation import LogitsProcessorList
        return LogitsProcessorList()

    import transformers.generation.utils as _tgu2

    _tgu2.GenerationMixin._get_logits_warper = _patched_warper

    # transformers 4.4x+ 的 _update_model_kwargs_for_generation 需要 model_kwargs
    # 里的 cache_position 键，TTS 0.22 的旧流程不维护它；首次调用前注入。
    _orig_update = _tgu2.GenerationMixin._update_model_kwargs_for_generation

    def _patched_update(self, outputs, model_kwargs, is_encoder_decoder=False, num_new_tokens=1):
        if "cache_position" not in model_kwargs:
            am = model_kwargs.get("attention_mask")
            if am is not None:
                model_kwargs["cache_position"] = torch.arange(am.shape[-1], device=am.device)
            else:
                model_kwargs["cache_position"] = torch.tensor([0], device="cpu")
        return _orig_update(self, outputs, model_kwargs, is_encoder_decoder=is_encoder_decoder,
                            num_new_tokens=num_new_tokens)

    _tgu2.GenerationMixin._update_model_kwargs_for_generation = _patched_update

    print("[xtts-server] 已替换 load_audio 为 soundfile 实现（绕开 torchcodec/FFmpeg）", flush=True)


def _load_model(model_dir, device, fp16):
    """加载 XTTS-v2 模型（首次较慢，预加载在后台线程完成）。

    使用底层 ``TTS.tts.models.xtts.XTTS`` 类直接加载，避免 TTS.api 的重型
    导入链（spacy/tensorboard/umap 等），降低启动时的内存与 CPU 压力。

    fp16=True 时在 CUDA 上以半精度推理，显存占用约减半（RTX 4060 8G 与数字人主程序共存所需）。
    """
    try:
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts, XttsArgs, XttsAudioConfig

        _patch_load_audio()

        # torch 2.6+ 默认 weights_only=True 会拦截 XTTS 官方 checkpoint 内 pickle 的
        # TTS 配置类（XttsConfig / BaseDatasetConfig 等）。checkpoint 来自官方
        # coqui/XTTS-v2（本机可信），此处放行完整反序列化：
        import torch
        torch.serialization.add_safe_globals([XttsConfig, XttsAudioConfig, XttsArgs, Xtts])
        _orig_load = torch.load
        torch.load = lambda *a, **k: _orig_load(*a, **{**k, "weights_only": False})

        config = XttsConfig()
        config.load_json(os.path.join(model_dir, "config.json"))
        model = Xtts.init_from_config(config)
        model.load_checkpoint(config, checkpoint_dir=model_dir, eval=True)
        if device == "cuda":
            model.cuda()
        if device == "cuda" and fp16:
            model.half()
            # XTTS 0.22 的 GroupNorm32（latent_encoder.py）forward 会把输入转 fp32
            # 计算（super().forward(x.float()).type(x.dtype)），权重若仍是 Half 会报
            # mixed dtype。按类型把全部 GroupNorm32 的权重回退 fp32 即可兼容
            # （其 forward 本身就在 fp32 下计算，输出再转回 half）。
            from TTS.tts.layers.xtts.latent_encoder import GroupNorm32
            _fixed = 0
            for _mod in model.modules():
                if isinstance(_mod, GroupNorm32):
                    _mod.float()
                    _fixed += 1
            print("[xtts-server] 已启用 fp16 半精度推理（GroupNorm32 回退 fp32 x%d）"
                  % _fixed, flush=True)
        _STATE["tts"] = model
        _STATE["config"] = config
        _STATE["ready"] = True
        _STATE["error"] = ""
        print("[xtts-server] 模型就绪: %s (device=%s)" % (model_dir, device), flush=True)
    except Exception as exc:  # noqa: BLE001 - 服务仍可启动，/health 会报告错误
        _STATE["ready"] = False
        _STATE["error"] = "%r" % (exc,)
        print("[xtts-server] 模型加载失败: %r" % (exc,), flush=True)


@app.get("/health")
def health():
    return {"ready": _STATE["ready"], "error": _STATE["error"], "device": _STATE["device"]}


@app.post("/clone_speaker")
async def clone_speaker(wav_file: UploadFile = File(...)):
    """接收参考音频，保存到服务私有临时目录，返回绝对路径供 /tts_stream 使用。"""
    data = await wav_file.read()
    if not data:
        return JSONResponse({"error": "上传的音频为空"}, status_code=400)
    path = os.path.join(_REF_DIR, "ref_%d.wav" % int(time.time() * 1000))
    with open(path, "wb") as fh:
        fh.write(data)
    return JSONResponse({"speaker_wav": path})


def _synth_blocks(text, speaker_wav, language, chunk_ms):
    """流式合成：yield 24kHz int16 PCM 字节块（TTS 0.22 的 inference_stream API）。"""
    model = _STATE["tts"]
    gpt_cond_len = getattr(model.config, "gpt_cond_len", 6)
    max_ref_len = getattr(model.config, "max_ref_len", 30)
    gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(
        audio_path=[speaker_wav],
        gpt_cond_len=gpt_cond_len,
        max_ref_length=max_ref_len,
        sound_norm_refs=False,
    )
    chunks = model.inference_stream(
        text,
        language,
        gpt_cond_latent,
        speaker_embedding,
        stream_chunk_size=chunk_ms,
        overlap_wav_len=1024,
        enable_text_splitting=True,
    )
    for wav_chunk in chunks:
        arr = (wav_chunk.cpu().numpy() * 32767.0).clip(-32768, 32767).astype(np.int16)
        yield arr.tobytes()


@app.post("/tts_stream")
async def tts_stream(body: dict):
    text = str(body.get("text") or "").strip()
    speaker_wav = str(body.get("speaker_wav") or body.get("speaker") or "")
    language = str(body.get("language") or "zh-cn")
    try:
        chunk_ms = int(body.get("stream_chunk_size") or 20)
    except (TypeError, ValueError):
        chunk_ms = 20
    chunk_ms = max(10, min(chunk_ms, 200))

    if not text:
        return JSONResponse({"error": "text 为空"}, status_code=400)
    if not speaker_wav:
        return JSONResponse({"error": "缺少 speaker_wav（请先 POST /clone_speaker 上传参考音频）"},
                            status_code=400)
    if not os.path.isfile(speaker_wav):
        return JSONResponse({"error": "参考音频不存在: %s" % speaker_wav}, status_code=400)
    if not _STATE["ready"]:
        return JSONResponse({"error": "模型未就绪: %s" % _STATE["error"]}, status_code=503)

    def stream():
        # XTTS 非线程安全：整段合成持有锁，串行化（数字人场景本就是串行播报）
        with _STATE["lock"]:
            try:
                yield from _synth_blocks(text, speaker_wav, language, chunk_ms)
            except Exception as exc:  # noqa: BLE001
                import traceback
                print("[xtts-server] 合成失败:\n%s" % traceback.format_exc(), flush=True)
                yield b""

    return StreamingResponse(stream(), media_type="application/octet-stream")


def main():
    parser = argparse.ArgumentParser(description="XTTS-v2 本地语音服务")
    parser.add_argument("--model", default=r"D:\models\tts\XTTS-v2", help="XTTS-v2 模型目录")
    parser.add_argument("--port", type=int, default=50000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--device", default="cuda", help="cuda / cpu")
    parser.add_argument("--fp16", action="store_true",
                        help="CUDA 上半精度推理（显存/内存约减半；TTS 0.22 在 Windows 上 fp16 与部分 norm 层不兼容，默认关闭 fp32 稳定运行）")
    args = parser.parse_args()

    if not os.path.isdir(args.model):
        print("[xtts-server] 模型目录不存在: %s（先下载 coqui/XTTS-v2）" % args.model)
        raise SystemExit(1)

    _STATE["device"] = args.device
    fp16 = args.fp16 and args.device == "cuda"
    _STATE["fp16"] = fp16
    threading.Thread(target=_load_model, args=(args.model, args.device, fp16), daemon=True).start()
    print("[xtts-server] 服务启动于 http://%s:%d，模型后台加载中…" % (args.host, args.port), flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
