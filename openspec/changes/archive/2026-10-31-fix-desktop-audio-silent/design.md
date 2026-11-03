# Design: 桌面端音频播放声道修复

## 根因定位（本地 Opus 往返取证，非推测）

客户端收到的不是服务端推送的原始帧，而是 **Opus 解码后的帧**。取证过程：

1. 读 `aiortc/codecs/opus.py`：`OpusDecoder` 固定
   `format="s16" / layout="stereo" / sample_rate=48000`，`SAMPLES_PER_FRAME=960`。
2. 实测 av 对各格式的 `to_ndarray()` 行为：
   - packed `s16` mono `(1, 320)` → `(1, 320)`；packed `s16` stereo → `(1, 1920)`（交织）
   - planar `fltp` stereo `(2, 960)` → `(2, 960)`（分行）
3. 本地跑完整 Opus 编解码往返（16kHz 单声道 → 服务端 320 样本切帧 →
   重采样 48k 立体声 → libopus 编码 → libopus 解码），确认客户端拿到的帧是
   **`s16 / stereo / 48000 / packed` → `to_ndarray()` 形状 `(1, 1920)`**。

于是：

| 环节 | 声道判定 | 结果 |
|---|---|---|
| 旧代码 `arr.shape[1]` | 1920 | `sd.OutputStream(channels=1920)` → `ValueError` → 被 `except: pass` 吞掉 → **永久静音** |
| 只按形状转置的启发式 | 1 | 转成 `(1920, 1)` → 交织立体声当单声道放 → **双倍速噪声**（有声但不对） |

**结论**：packed 交织数据的形状不携带声道信息，`(1, 320)` 与 `(1, 1920)` 无法区分。
必须用帧元数据 `layout.channels` 与 `format.is_planar`。

## 修复方案

### 1. `utils/audio_playback.py`（新增）
- `prepare_audio_frame(frame)`：
  - `channels = frame.layout.channels`（兼容 int 与 channel 元组两种形态）；
  - `is_planar` → `(channels, samples)`，转置；
  - packed → `(1, samples*channels)`，`reshape(-1, channels)` 拆出交织布局；
  - float → 按峰值缩放到 int16（`0.3` 不会被截断成 `0`）。
- `reshape_for_sounddevice(arr)`：纯 numpy 兜底，**仅在拿不到元数据时用**。
- `list_output_devices()`：枚举 `max_output_channels > 0` 的设备（排除麦克风）。

### 2. `desktop_app.py`
- `_pump_loop`：在收到帧的**当场**调用 `prepare_audio_frame`，把声道数透传下去
  （元数据在这一层丢失，推到下游只能靠猜）。
- `AudioPlayer`：`device=` 参数指定输出设备；异常改 `logger.error`；
  新增 `play_test_tone()`。
- 设置页新增「音频输出」卡片（设备下拉 / 刷新 / 测试播放 / 保存）。
- 会话建立时按设置播一次测试提示音。

### 3. `utils/settings_store.py`
- 新增 `audio.output_device`（str，留空=系统默认）、`audio.test_tone`（bool）。
- 修正 `_as_bool`：原实现"非空字符串即真"，布尔字段形同虚设；现只接受
  `true/false/1/0/yes/no/on/off`，其余报 `SettingsError`。读取路径仍容错
  （脏值回落默认值，不让界面崩）。

## 为什么抽成独立模块
- `desktop_app` 导入受"解释器自检 + 重投"守卫影响，单测难以直接 import；
  把音频还原逻辑放进 `utils.audio_playback` 后，回归测试可干净运行。
- `tests/conftest.py` 提供 sounddevice 桩（仅在真缺 sounddevice 时安装），
  且必须给桩补 `__spec__`——`find_spec()` 遇到 `__spec__ is None` 的模块会抛
  `ValueError`，被 `desktop_app` 的依赖自检当成"没装 sounddevice"而 `sys.exit(1)`。

## 验证
- `tests/test_audio_playback.py` 15 例全过（含 packed 立体声/单声道、planar、
  float 幅度、设备枚举、AudioPlayer 写入形状与测试音非静音）。
- 本地 Opus 全链路往返：还原后主频 439.5 Hz（期望 440）、peak 7698、
  写入声卡形状 `(960, 2)` —— 证明既不是静音也不是错序噪声。
- `python -m py_compile` 通过；`openspec validate --all` 通过。
- 真机（舱体 PC）：设置页「测试播放」先验证本机扬声器通路，再验证数字人播报；
  若仍无声，日志会有明确的 `[AudioPlayer]` / `[WebRTC]` 错误行。
