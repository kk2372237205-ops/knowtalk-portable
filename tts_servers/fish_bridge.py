# -*- coding: utf-8 -*-
"""Fish Speech 桥接服务（兼容项目 tts/fish.py 客户端契约，运行时完全离线）。

项目客户端（tts/fish.py）的契约：
    POST {server}/v1/tts  json {text, reference_id(本机 wav 路径), format:'wav',
                                 streaming:True, use_memory_cache:'on'}
    → 流式返回 44100Hz int16 PCM（客户端再 resample 到项目采样率）

而官方 fish api_server 的 reference_id 是 fish.audio 的引用 ID（服务端缓存），
不接受本地路径；官方 streaming 输出带 44 字节 RIFF 头。本桥接层负责：
  1. 把 reference_id（本地 wav 路径）读入并转 base64，以 references 字段转发上游；
  2. 剥掉上游返回的 WAV 头，输出纯 PCM 块。

用法：:
    # 上游：官方 api_server（默认 127.0.0.1:50002）
    # 本桥接：
    python tts_servers/fish_bridge.py --port 50001 [--upstream http://127.0.0.1:50002]
然后项目侧：--tts fishtts --TTS_SERVER http://127.0.0.1:50001
（REF_FILE 传本机 wav 路径，如 data/voices/参考.wav）
"""

import argparse
import base64
import os

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

app = FastAPI(title="Fish Speech 桥接服务")

_UPSTREAM = os.environ.get("FISH_UPSTREAM", "http://127.0.0.1:50002")
_WAV_HEADER = 44  # 官方 streaming 输出的 RIFF WAV 头长度


@app.post("/v1/tts")
async def tts(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求体不是合法 JSON"}, status_code=400)

    text = str(body.get("text") or "").strip()
    ref_file = str(body.get("reference_id") or "")
    if not text:
        return JSONResponse({"error": "text 为空"}, status_code=400)

    # 参考音频：项目把 reference_id 当本机 wav 路径传
    references = []
    if ref_file and os.path.isfile(ref_file):
        try:
            with open(ref_file, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            references.append({"audio": b64, "text": ""})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": "读取参考音频失败: %r" % (exc,)}, status_code=400)

    payload = {
        "text": text,
        "references": references,
        "format": "wav",
        "streaming": True,
        "use_memory_cache": "off",
        "normalize": True,
        "chunk_length": 200,
    }
    try:
        up = requests.post(_UPSTREAM + "/v1/tts", json=payload,
                           stream=True, timeout=(30, 600))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "上游连接失败: %r" % (exc,)}, status_code=502)

    if up.status_code != 200:
        try:
            err = up.text[:300]
        except Exception:  # noqa: BLE001
            err = "不可读"
        return JSONResponse({"error": "上游错误 %s: %s" % (up.status_code, err)},
                            status_code=502)

    def gen():
        header_left = _WAV_HEADER
        try:
            for chunk in up.iter_content(chunk_size=4096):
                if not chunk:
                    continue
                if header_left > 0:
                    skip = min(len(chunk), header_left)
                    chunk = chunk[skip:]
                    header_left -= skip
                if chunk:
                    yield chunk
        except Exception as exc:  # noqa: BLE001 - 客户端断开等，记录后结束
            print("[fish-bridge] 转发中断: %r" % (exc,), flush=True)

    return StreamingResponse(gen(), media_type="application/octet-stream")


@app.get("/health")
async def health():
    return {"ready": True, "upstream": _UPSTREAM}


def main():
    parser = argparse.ArgumentParser(description="Fish Speech 桥接服务")
    parser.add_argument("--port", type=int, default=50001)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--upstream", default="http://127.0.0.1:50002")
    args = parser.parse_args()
    global _UPSTREAM
    _UPSTREAM = args.upstream
    print("[fish-bridge] 桥接服务启动于 http://%s:%d，上游 %s" % (args.host, args.port, args.upstream),
          flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
