# Fix: 桌面端 WebRTC 音频帧声道判定错误导致永久静音

## Why
用户反馈"数字人播报没声音"，且在修复 XTTS 合成层（第十四轮）后**依旧无声**。按
systematic-debugging 逐段验证，音频链路（TTS → ASR 回传 → 渲染循环 →
`output.push_audio_frame` → HumanPlayer → WebRTC 轨道 → 客户端 `AudioPlayer` →
sounddevice）在**服务端**完全正确接线，问题出在**客户端本地播放**这一小段。

**真实根因（经本地 Opus 编解码往返取证）**：

aiortc 的 `OpusDecoder`（`aiortc/codecs/opus.py`）固定输出
`format="s16" / layout="stereo" / sample_rate=48000`，每帧 960 samples/channel。
`s16` 是 **packed** 格式，av 的 `AudioFrame.to_ndarray()` 对 packed 返回
**交织的一行** `(1, samples*channels)`，即客户端收到的是 **`(1, 1920)`**。

而 `AudioPlayer.run` 直接拿 `arr.shape[1]` 当声道数 → `channels=1920` →
`sd.OutputStream(channels=1920)` 抛 `ValueError` → 原代码用 `except: pass`
静默吞掉 → **任何 TTS 引擎都无声、且日志里什么都看不到**。

> 注意：服务端 `HumanPlayer.push_audio` 推的是单声道 16kHz 的 `(1, 320)` 帧，
> 与客户端经 Opus 解码后收到的 `(1, 1920)` **形状含义不同**。两者都是
> "1 行"，无法从形状区分单/双声道——所以客户端必须用帧元数据
> （`layout.channels` / `format.is_planar`），不能猜。

此外还有两个会一并造成"静音"的坑：
- float 格式（`fltp`）样本幅度在 `[-1, 1]`，`astype(int16)` 会把 `0.3` 截断成 `0`；
- 真机多屏/接 HDMI 时，Windows 系统默认输出常指向显示器而非音箱。

## What Changes
- 新增模块 `utils/audio_playback.py`：
  - `prepare_audio_frame(frame)` —— 按帧元数据（`layout.channels` /
    `format.is_planar`）把 av 帧还原成 `(samples, channels)`，并正确处理
    packed 交织 / planar 分行两种布局；
  - `reshape_for_sounddevice(arr)` —— 纯 numpy 兜底（仅在拿不到元数据时用）；
  - `list_output_devices()` —— 枚举具备输出通道的音频设备；
  - float 幅度按峰值缩放到 int16，不再截断成静音。
- `desktop_app.py`：
  - `_pump_loop` 在**收到帧的当场**用 `prepare_audio_frame` 还原（元数据在这一层
    就丢了，不能推到下游再猜），并把声道数透传给播放层；
  - `AudioPlayer` 支持指定输出设备（`device=`），移除静默吞错改为
    `logger.error`，并新增 `play_test_tone()` 自检提示音；
  - 设置页新增「音频输出」卡片：输出设备下拉框 + 刷新 + 测试播放 + 保存。
- `utils/settings_store.py`：新增 `audio.output_device`（输出设备）与
  `audio.test_tone`（启动提示音）两个设置项，走既有 `/api/settings` 通道；
  顺带修正 `_as_bool` —— 原实现"非空字符串即真"，导致布尔字段形同虚设
  （`"not-a-bool"` 会被当成 True），现只接受明确真假字面量。
- 新增回归测试 `tests/test_audio_playback.py`（15 项）与 `tests/conftest.py`
  （开发机无声卡时的 sounddevice 桩）。

## Impact
- 桌面端数字人播报恢复出声（所有 TTS 引擎），且声音为正常音色与语速
  （不再出现双倍速噪声）。
- 播放失败会在日志打印明确错误，不再"无声无痕"。
- 真机可一键自检：设置页「测试播放」与连接建立时的提示音，能立刻区分
  "本机扬声器通路有问题"与"数字人没产生语音"。
- 不影响服务端逻辑、不影响 WebRTC 轨道与 TTS 引擎。
- 设置项 `audio.*` 为新增，未显式配置时保持原行为（系统默认设备 + 不播提示音）。
