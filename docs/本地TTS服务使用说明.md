# 本地 TTS 服务使用说明（XTTS-v2 + Fish Speech）

知课的本地语音服务已部署两个可离线运行的克隆音色引擎，替代在线 Edge TTS（已被微软 403 拒绝）与本机 SAPI 兜底：

| 引擎 | 模型 | 服务端口 | 接入参数 | 特点 |
| --- | --- | --- | --- | --- |
| XTTS-v2 | `D:\models\tts\XTTS-v2`（1.8GB） | 50000 | `--tts xtts` | 流式 24kHz，首包约 1 秒，音色克隆自然 |
| Fish Speech 1.5 | `D:\models\tts\fish-speech-1.5`（1.5GB）+ 内置 decoder | 50002（官方）/ 50001（桥接） | `--tts fishtts` | 44100Hz 高质量合成，首包约 10 秒 |

两者均为**本地推理、运行时完全离线**：参考音频（你的声音样本）→ 本地模型合成，不调用任何云服务。

---

## 一、一键启动

双击 `start_local_tts.bat`（或命令行执行），会同时拉起三个服务进程：

1. XTTS-v2 服务 → `http://127.0.0.1:50000`
2. Fish Speech 官方 api_server → `http://127.0.0.1:50002`
3. Fish Speech 桥接服务（兼容项目客户端契约）→ `http://127.0.0.1:50001`

日志：
- `D:\models\tts\xtts_server.log`（XTTS，模型加载约 2 分钟）
- `D:\models\tts\fish_server.log`（Fish，模型加载约 1 分钟）
- `D:\models\tts\fish_bridge.log`（桥接）

## 二、接入项目

在主程序启动参数中指定引擎与对应服务地址：

```
# 使用 XTTS
python app.py --tts xtts --TTS_SERVER http://127.0.0.1:50000 --REF_FILE D:\models\tts\ref_voice.wav

# 使用 Fish Speech
python app.py --tts fishtts --TTS_SERVER http://127.0.0.1:50001 --REF_FILE D:\models\tts\ref_voice.wav
```

- `REF_FILE` 必须是本机 wav 路径（克隆用的参考人声），不能填 Edge 语音名。
- 参考音频建议：10–30 秒、单人、清晰、无背景噪音的 wav（48000/44100/22050Hz 均可，服务自动重采样）。
- 桌面版（desktop_app.py）同样通过这两个地址使用对应引擎。

## 三、故障排查

| 现象 | 处理 |
| --- | --- |
| 启动后 /health 未 ready | 看对应日志（模型加载中），XTTS 约 2 分钟、Fish 约 1 分钟 |
| 显存不足（RTX 4060 8G 与主程序共存） | Fish 占用约 1.9GB、XTTS 约 2GB+；若 OOM 请临时关闭主程序，或 XTTS 加 `--fp16`（半精度，音质略降） |
| 端口被占用 | 先杀旧进程再启动：`Get-NetTCPConnection -LocalPort 50000 -State Listen` 找 OwningProcess 后 Stop-Process |
| Fish 参考音频读不了 | 确认 wav 文件存在且是标准 PCM/float wav；服务端用 soundfile 读取 |
| 想换参考音色 | 换 `--REF_FILE` 指向另一个 wav，重启主程序即可 |

## 四、环境说明（已落地）

- 解释器：`C:\Users\24306\.conda\envs\livetalking\python.exe`
- 依赖：TTS 0.22.0、transformers 4.45.2、numpy 1.22.0、fish-speech（源码安装 v1.5.0，`D:\models\tts\fish-speech-1.5.0`）
- 兼容性补丁（在服务脚本/源码中，已实测）：
  - XTTS：`tts_servers\xtts_server.py` 内——soundfile 读音频（绕开 torchcodec/FFmpeg）、torch.load 放行、`isin_mps_friendly`/`_eos_token_tensor`/`_get_logits_warper`/`cache_position` 四个 transformers 兼容补丁。
  - Fish：`tools\inference_engine\reference_loader.py` 内——音频加载改 soundfile、移除已废弃的 `list_audio_backends`。

## 五、验证结果（2026-10-29 实测）

- XTTS：文本“你好，欢迎来到知课课堂。”→ 7.06 秒 @24kHz，峰值 0.769 / RMS 0.127，落盘 `D:\models\tts\xtts_test.wav`。
- Fish：同文本 → 4.41 秒 @44.1kHz，峰值 0.774 / RMS 0.119，落盘 `D:\models\tts\fish_test.wav`。
- 全量项目单元测试（~981 用例）在环境共存调整后回归通过。
