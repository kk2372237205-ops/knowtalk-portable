"""把参考音频注册成火山引擎（豆包语音）的复刻音色，产出 speaker_id。

为什么需要它：EdgeTTS 只认微软音色名，协议上不接受音频文件；要让数字人用
「某个人的声音」说话，必须先把这个人的音频在云端训练成一个音色 ID，之后每次
合成只传这个 ID —— 相当于把音色一次性「拷」到云端，合成时不再需要参考音频，
所以速度与普通在线音色一致（首包通常 300ms 级）。

用法::

    # 注册新音色（自动生成 speaker_id）
    python scripts/register_voice.py --audio data/voices/讲解员.wav --text "录音里说的那句话"

    # 查训练状态
    python scripts/register_voice.py --status S_abc12345

    # 用已有 speaker_id 重新训练（覆盖）
    python scripts/register_voice.py --audio data/voices/讲解员.wav --speaker-id S_abc12345

前置条件：环境变量 ``DOUBAO_APPID`` 与 ``DOUBAO_TOKEN``（火山引擎控制台
- 豆包语音 - 应用管理 里获取），且已开通「声音复刻」服务。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import string
import sys
import tempfile
import time
import urllib.error
import urllib.request

UPLOAD_URL = "https://openspeech.bytedance.com/api/v1/mega_tts/audio/upload"
STATUS_URL = "https://openspeech.bytedance.com/api/v1/mega_tts/status"
RESOURCE_ID = "volc.megatts.voiceclone"

# 官方建议 10-30 秒；过短会导致相似度不足
MIN_SECONDS = 10.0
MAX_SECONDS = 30.0
MAX_BYTES = 3 * 1024 * 1024

STATUS_TEXT = {
    0: "未找到该音色",
    1: "训练中",
    2: "训练成功",
    3: "训练失败",
    4: "已激活",
}


def _auth_headers() -> dict:
    token = os.getenv("DOUBAO_TOKEN")
    if not token:
        raise SystemExit(
            "缺少环境变量 DOUBAO_TOKEN。\n"
            "请在火山引擎控制台 - 豆包语音 - 应用管理获取 Access Token，\n"
            "写入 .env：DOUBAO_APPID=xxx  DOUBAO_TOKEN=xxx"
        )
    # 火山的鉴权格式是 "Bearer; <token>"，中间是分号而不是空格
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer; {token}",
        "Resource-Id": RESOURCE_ID,
    }


def _post(url: str, payload: dict, timeout: int = 60) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_auth_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body[:400]}
    except urllib.error.URLError as e:
        raise SystemExit(f"连接火山引擎失败：{e.reason}") from e


def _inspect_audio(path: str) -> tuple[float, int, str, int]:
    """返回 (时长, 采样率, 声道名, 大小)，并对不符合要求的情况给出提示。"""
    import soundfile as sf  # 延迟导入，--status 模式无需音频库

    if not os.path.isfile(path):
        raise SystemExit(f"找不到音频文件：{path}")
    info = sf.info(path)
    size = os.path.getsize(path)
    return info.duration, info.samplerate, info.channels, size


def _prepare_audio(path: str) -> tuple[str, str]:
    """确保上传的是火山接受的 wav（单声道、24k、PCM16、≤3MB）。

    已经是合规 wav 就原样返回；否则转码到临时文件，不动用户的原件。
    返回 (可用文件路径, 需要清理的临时路径或空串)。
    """
    import numpy as np
    import soundfile as sf

    duration, sr, channels, size = _inspect_audio(path)
    print(f"音频：{os.path.basename(path)}  {duration:.2f}s  {sr}Hz  {channels}ch  {size / 1024:.0f}KB")

    if duration < 5.0:
        print(f"  [警告] 只有 {duration:.1f}s，远低于官方建议的 10-30s，复刻相似度会很差。")
    elif duration < MIN_SECONDS:
        print(f"  [提示] {duration:.1f}s 略短于建议的 {MIN_SECONDS:.0f}s，相似度可能不足，建议补录。")
    elif duration > MAX_SECONDS:
        print(f"  [提示] {duration:.1f}s 超过建议的 {MAX_SECONDS:.0f}s，云端会自动截断，可能截到瑕疵片段。")

    need_convert = (
        not path.lower().endswith(".wav")
        or channels != 1
        or sr != 24000
        or size > MAX_BYTES
    )
    if not need_convert:
        print("  格式合规，直接上传。")
        return path, ""

    data, sr_in = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)  # 多声道直接混成单声道
    if sr_in != 24000:
        try:
            import resampy

            mono = resampy.resample(mono, sr_orig=sr_in, sr_new=24000)
        except Exception:  # noqa: BLE001 - 缺 resampy 时退回线性插值，音色训练足够
            idx = np.linspace(0, len(mono) - 1, int(len(mono) * 24000 / sr_in))
            mono = np.interp(idx, np.arange(len(mono)), mono)

    tmp_dir = tempfile.mkdtemp(prefix="voice_reg_")
    tmp_path = os.path.join(tmp_dir, "voice_24k_mono.wav")
    sf.write(tmp_path, mono.astype(np.float32), 24000, subtype="PCM_16")
    print(f"  已转码为 24kHz 单声道：{tmp_path}")
    return tmp_path, tmp_path


def _new_speaker_id() -> str:
    """火山要求自定义 speaker_id 以 S_ 开头，后接 8 位字母数字。"""
    body = "".join(random.choices(string.ascii_letters + string.digits, k=8))
    return f"S_{body}"


def upload(audio_path: str, speaker_id: str, text: str) -> None:
    appid = os.getenv("DOUBAO_APPID")
    if not appid:
        raise SystemExit("缺少环境变量 DOUBAO_APPID（火山控制台 - 豆包语音 - 应用管理）。")

    usable, _tmp = _prepare_audio(audio_path)
    with open(usable, "rb") as fh:
        encoded = base64.b64encode(fh.read()).decode("utf-8")

    audio_meta = {"audio_bytes": encoded, "audio_format": "wav"}
    if text:
        # 带上对应文本能明显提升复刻质量，云端会比对音频与文本的差异
        audio_meta["text"] = text

    payload = {
        "appid": appid,
        "speaker_id": speaker_id,
        "audios": [audio_meta],
        "source": 2,
        "language": 0,  # 0=中文
        "model_type": 1,  # 1=声音复刻 ICL V1（兼容性最好）
    }
    print(f"上传训练中（speaker_id={speaker_id}）...")
    code, body = _post(UPLOAD_URL, payload)
    print(f"  HTTP {code}  {json.dumps(body, ensure_ascii=False)[:300]}")
    if code != 200:
        raise SystemExit("上传失败，请对照返回的 message 检查（常见：音色未购买/已训练 15 次/音频不合规）。")
    if body.get("BaseResp", {}).get("StatusCode") not in (0, None):
        raise SystemExit(f"训练请求被拒绝：{body.get('BaseResp')}")


def status(speaker_id: str) -> int:
    appid = os.getenv("DOUBAO_APPID")
    if not appid:
        raise SystemExit("缺少环境变量 DOUBAO_APPID。")
    code, body = _post(STATUS_URL, {"appid": appid, "speaker_id": speaker_id})
    if code != 200:
        print(f"查询失败 HTTP {code}  {json.dumps(body, ensure_ascii=False)[:300]}")
        return code
    st = body.get("status", 0)
    print(f"音色 {speaker_id}: status={st} ({STATUS_TEXT.get(st, '未知')})")
    if body.get("available_training_times") is not None:
        print(f"  剩余训练次数：{body['available_training_times']}")
    if body.get("demo_audio"):
        print(f"  试听链接（1 小时有效）：{body['demo_audio']}")
    return st


def wait_ready(speaker_id: str, timeout: int = 300, interval: int = 5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        appid = os.getenv("DOUBAO_APPID")
        code, body = _post(STATUS_URL, {"appid": appid, "speaker_id": speaker_id})
        st = body.get("status", 0) if code == 200 else -1
        print(f"  状态={st} ({STATUS_TEXT.get(st, '查询失败')})")
        if st in (2, 4):
            if body.get("demo_audio"):
                print(f"  试听：{body['demo_audio']}")
            return True
        if st == 3:
            print(f"  训练失败：{json.dumps(body, ensure_ascii=False)[:300]}")
            return False
        time.sleep(interval)
    print("  等待超时，稍后可用 --status 再查。")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="注册火山引擎复刻音色")
    ap.add_argument("--audio", help="参考音频路径（wav 最佳，建议 10-30 秒）")
    ap.add_argument("--text", default="", help="音频里说的原文，能提升复刻质量")
    ap.add_argument("--speaker-id", default="", help="指定已有音色 ID 重新训练")
    ap.add_argument("--status", default="", help="只查询该音色状态")
    ap.add_argument("--no-wait", action="store_true", help="上传后立即返回，不轮询训练状态")
    args = ap.parse_args()

    if args.status:
        return 0 if status(args.status) in (2, 4) else 1

    if not args.audio:
        ap.error("需要 --audio（注册）或 --status（查询）")

    speaker_id = args.speaker_id or _new_speaker_id()
    upload(args.audio, speaker_id, args.text)

    if args.no_wait:
        print(f"\n已提交训练。稍后用 --status {speaker_id} 查询。")
        return 0

    print("等待训练完成（通常 1-3 分钟）...")
    if not wait_ready(speaker_id):
        return 1

    print("\n" + "=" * 60)
    print("音色已就绪。在应用里这样用：")
    print("  1) 设置 - TTS 引擎 选 doubao")
    print(f"  2) 参考音色 填：{speaker_id}")
    print("  或直接改 data/settings/settings.json:")
    print(json.dumps({"tts": {"engine": "doubao", "ref_file": speaker_id}},
                     ensure_ascii=False, indent=2))
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
