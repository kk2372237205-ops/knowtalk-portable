###############################################################################
#  配置解析 — CLI 参数 + YAML 配置
###############################################################################

import argparse
import json
import os

from utils.platform_support import DEFAULT_TTS_FALLBACK


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="KnowTalk Digital Human Server")

    # ─── 音频 ──────────────────────────────────────────────────────────
    parser.add_argument('--fps', type=int, default=25, help="video fps, must be 25")
    parser.add_argument('-l', type=int, default=10)
    parser.add_argument('-m', type=int, default=8)
    parser.add_argument('-r', type=int, default=10)

    # ─── 画面 ──────────────────────────────────────────────────────────
    # parser.add_argument('--W', type=int, default=450, help="GUI width")
    # parser.add_argument('--H', type=int, default=450, help="GUI height")

    # ─── 数字人模型 ────────────────────────────────────────────────────
    parser.add_argument('--model', type=str, default='musetalk',
                        help="avatar model: musetalk/wav2lip/ultralight"
                             "（默认 musetalk：扩散式口型，1080p 小脸下嘴型也明显好于"
                             " wav2lip；需要回退时用 --model wav2lip）")
    parser.add_argument('--avatar_id', type=str, default='musetalk_ke',
                        help="avatar id in data/avatars（默认 musetalk_ke：MuseTalk 素材形态；"
                             "切回 wav2lip 时请一并指定 wav2lip 素材的形象，如 --avatar_id ke01）")
    parser.add_argument('--batch_size', type=int, default=8,
               help="infer batch（默认 8：8G 显存下更稳，16 会抬高 UNet 推理峰值）")
    parser.add_argument('--modelres', type=int, default=192)
    parser.add_argument('--modelfile', type=str, default='')

    # ─── 自定义动作和多形象 ────────────────────────────────────────────
    parser.add_argument('--customvideo_config', type=str, default='',
                        help="custom action json")

    # ─── TTS ───────────────────────────────────────────────────────────
    parser.add_argument('--tts', type=str, default='edgetts',
                        help="tts plugin: edgetts/sapi/gpt-sovits/cosyvoice/fishtts/tencent/doubao/azuretts/qwentts"
                             "（sapi = Windows 系统语音，完全离线，音质较机械）")
    parser.add_argument('--REF_FILE', type=str, default="zh-CN-YunjianNeural",
                        help="参考文件名或语音模型ID")
    parser.add_argument('--REF_TEXT', type=str, default=None)
    parser.add_argument('--TTS_RATE', type=str, default='+0%',
                        help="语速: 如 -30%/-20%/+0%/+20% (负数=慢，正数=快)；SAPI 引擎按每 10% 约 1 级近似换算")
    parser.add_argument('--TTS_FALLBACK', type=str, default=DEFAULT_TTS_FALLBACK,
                        help="备用 TTS 引擎：主引擎合成失败时用它重试一次。"
                             "默认值在本平台提供离线语音能力时为 sapi，否则为空（不回退）；"
                             "显式传空串可关闭回退")
    parser.add_argument('--TTS_SERVER', type=str, default='http://127.0.0.1:50000')

    # ─── 口型控制 ─────────────────────────────────────────────────────
    parser.add_argument('--mouth_alpha', type=float, default=0.96,
                        help='口型幅度: 0.0=不动嘴, 1.0=原始幅度, 越小幅度越收敛')
    parser.add_argument('--mouth_offset_y', type=float, default=-0.15,
                        help='嘴部垂直微调: 负值=上移, 正值=下移, 单位为人脸占比')
    parser.add_argument('--mouth_offset_x', type=float, default=0.15,
                        help='嘴部水平微调: 负值=左移, 正值=右移, 单位为人脸占比')
    parser.add_argument('--pad_scale', type=float, default=0.35,
                        help='人脸裁剪填充比例，越大嘴部在画面中占比越小')

    # ─── 传输 ─────────────────────────────────────────────────────────
    parser.add_argument('--transport', type=str, default='webrtc',
                        help="output: rtcpush/webrtc/rtmp/virtualcam")
    parser.add_argument('--push_url', type=str,
                        default='http://localhost:1985/rtc/v1/whip/?app=live&stream=livestream')
    parser.add_argument('--max_session', type=int, default=1)
    parser.add_argument('--listenport', type=int, default=8010,
                        help="web listen port")

    opt = parser.parse_args()

    # ─── 后处理 ────────────────────────────────────────────────────────
    opt.customopt = []
    if opt.customvideo_config:
        with open(opt.customvideo_config, 'r') as f:
            opt.customopt = json.load(f)

    return opt
