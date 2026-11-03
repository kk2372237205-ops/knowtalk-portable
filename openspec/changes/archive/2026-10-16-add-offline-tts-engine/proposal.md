# Proposal

## Why

项目默认的 TTS 引擎 `edgetts` **已经不可用**。实测连续两次失败：

```
WSServerHandshakeError 403  wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1
```

`edge-tts` 本机版本是 `7.2.8`，而**这已是 PyPI 上的最新版**，没有升级路径。它在 `constants.py` 里硬编码 `CHROMIUM_FULL_VERSION = "143.0.3650.75"`，微软侧收紧了对该客户端版本的校验。

雪上加霜的是**这个失败是静默的**：`tts/edge.py:106` 的 `except` 只调用 `logger.exception`，合成产出为空时在第 52 行记一条 `edgetts err!!!!!` 然后直接 `return`。课堂上的表现是"数字人突然不说话了"，而使用者看不到任何原因。

本会话早前 edgetts 还能用，说明这是一次**外部服务收紧导致的能力丧失**，不是本项目的代码退化。但项目没有任何离线兜底，也没有把失败暴露出来，因此一旦外部服务不可用，整堂课的语音播报就整体失效。

Windows 系统自带的 SAPI 语音是**离线可用**的，本机已实测存在中文语音 `Microsoft Huihui Desktop`，且能成功合成中文音频。

## What Changes

- 新增一个**不依赖外部网络**的 TTS 引擎插件，使用操作系统自带的语音合成能力（Windows SAPI）。
- **让合成失败可见**：合成产出为空时不得只写日志就返回，必须产生可被上层察觉的失败信号，并给出可操作的排查提示（例如"该引擎依赖外部服务，当前不可用，请检查网络或改用离线引擎"）。
- 新增**可配置的回退引擎**：当被选引擎合成失败时，若配置了回退引擎且其可用，则自动改用回退引擎重试一次，并明确报告发生了回退；回退不可用或同样失败时，必须明确报告而不是静默。
- 回退链**不得循环**（回退引擎本身失败时不得再回退到原引擎）。
- 把新引擎纳入 `--tts` 与运行期设置的引擎枚举，使界面可切换。
- 修正既有 `edgetts` 引擎的失败处理：把"静默返回"改为可察觉的失败。
- 合成产出的采样率与分块约定保持一致（16 kHz 单声道、20 ms 帧），不得因换引擎破坏下游。

## Capabilities

### New Capabilities

- `speech-synthesis`: 语音合成引擎的统一契约——引擎可从统一入口选择、必须提供离线可用的引擎、合成失败必须可见、回退行为与回退链不得循环、产出采样率与分块必须一致、以及系统语音的可用性判定与报告。

### Modified Capabilities

无。

逐个核对过：`digital-human-api` 描述的是 HTTP 接口契约，TTS 引擎内部如何合成不属于它；`runtime-configuration` 拥有的是设置机制与「各配置项的生效档位归属」，新增一个引擎**取值**不改变 `tts.engine` 的生效档位，因此其要求文本不变。按 `llm-configuration`、`rag-sidecar-control` 的既有先例，新能力自己声明自己的接口与边界。

## Impact

- **新增依赖**：`comtypes`（用于驱动 Windows SAPI 的 COM 接口）。选它而不是 `pyttsx3`，因为后者在 Windows 上还会连带引入 `pywin32` 等，而本项目只需要「把文本合成到一个 wav 文件」这一件事。
- **新增文件**：`tts/sapi.py`（新引擎）、相应单元测试。
- **修改文件**：`tts/edge.py`（失败可见）、`tts/base_tts.py`（回退编排的落点）、`config.py`（`--tts` 帮助文本与新参数）、`utils/settings_store.py`（引擎枚举）、`README.md`（离线引擎与回退说明）。
- **平台约束**：新引擎仅 Windows 可用。在非 Windows 上选择它必须明确报错并说明原因，而不是抛未捕获异常。其它平台仍可继续使用原有引擎。
- **不受影响**：`/human`、`/humanaudio` 等接口的请求与响应结构；`BaseTTS` 的帧产出契约；下游 `put_audio_frame` 的消费方式。
- **已知取舍**：SAPI 语音的音质明显不如 `edgetts` 的神经网络音色。这是"离线可用与更好音质不可兼得"的取舍，必须在文档中如实说明，并保留切回云端引擎的能力。
