# -*- coding: utf-8 -*-
"""Fish Speech 桥接端到端验证：按项目客户端契约调用桥接服务，检查并落盘音频。"""
import time
import numpy as np
import requests
import soundfile as sf

SRV = "http://127.0.0.1:50001"
REF = r"D:\models\tts\ref_voice.wav"

t0 = time.perf_counter()
r = requests.post(
    SRV + "/v1/tts",
    json={"text": "你好，欢迎来到知课课堂。",
          "reference_id": REF, "format": "wav",
          "streaming": True, "use_memory_cache": "on"},
    timeout=(30, 600),
    stream=True,
)
print("status:", r.status_code)
if r.status_code != 200:
    print("body:", r.text[:500])
    raise SystemExit(1)

chunks = []
first_t = None
for c in r.iter_content(chunk_size=4096):
    if not c:
        continue
    if first_t is None:
        first_t = time.perf_counter()
    chunks.append(c)

print("首块耗时 %.1fs, 总块数 %d, 总字节 %d" % (first_t - t0, len(chunks), sum(len(c) for c in chunks)))
data = b"".join(chunks)
if len(data) < 17640:
    print("数据过短，疑似失败")
    raise SystemExit(1)
samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32767.0
print("采样数 %d, 时长 %.2fs @44.1kHz, 峰值 %.4f, RMS %.4f" % (
    samples.shape[0], samples.shape[0] / 44100.0,
    float(np.abs(samples).max()), float(np.sqrt(np.mean(samples ** 2)))))
sf.write(r"D:\models\tts\fish_test.wav", samples, 44100)
print("OK 已保存 D:\\models\\tts\\fish_test.wav")
