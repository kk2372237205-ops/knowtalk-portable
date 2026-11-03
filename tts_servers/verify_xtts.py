# -*- coding: utf-8 -*-
"""XTTS 本地服务端到端验证：克隆音色 -> 流式合成 -> 音频有效性检查。"""
import requests
import numpy as np
import wave
import time

BASE = "http://127.0.0.1:50000"
REF = r"D:\models\tts\ref_voice.wav"
OUT = r"D:\models\tts\xtts_test.wav"

# 1) 上传参考音频克隆音色
t0 = time.time()
with open(REF, "rb") as f:
    r = requests.post(BASE + "/clone_speaker", files={"wav_file": ("ref.wav", f, "audio/wav")}, timeout=30)
print("clone_speaker:", r.status_code, r.json())
assert r.status_code == 200, r.text
speaker = r.json()
print("clone 耗时 %.1fs" % (time.time() - t0))

# 2) 流式合成
t0 = time.time()
payload = {
    "text": "你好，欢迎来到知课课堂。今天我们来学习一条重要的数学知识。",
    "language": "zh-cn",
    "speaker_wav": speaker["speaker_wav"],
    "stream_chunk_size": "20",
}
resp = requests.post(BASE + "/tts_stream", json=payload, timeout=180, stream=True)
print("tts_stream status:", resp.status_code)
assert resp.status_code == 200, resp.text[:500]
chunks, first_t = [], None
for c in resp.iter_content(chunk_size=None):
    if first_t is None:
        first_t = time.time()
    chunks.append(c)
print("首块耗时 %.1fs, 总块数 %d, 总字节 %d" % (first_t - t0, len(chunks), sum(len(c) for c in chunks)))

# 3) 检查音频有效性
audio = b"".join(chunks)
pcm = np.frombuffer(audio, dtype=np.int16)
print("采样数 %d, 时长 %.2fs @24kHz, 峰值 %.4f, RMS %.4f" % (
    len(pcm), len(pcm) / 24000, np.abs(pcm).max() / 32767,
    np.sqrt((pcm.astype(float) ** 2).mean()) / 32767))
if len(pcm) > 0 and np.abs(pcm).max() > 0:
    with wave.open(OUT, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(audio)
    print("OK 已保存", OUT)
else:
    print("警告: 输出为空或全静音")
