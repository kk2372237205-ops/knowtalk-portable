## 1. 根因取证
- [x] 1.1 读 `aiortc/codecs/opus.py` 确认 Opus 解码输出：`s16 / stereo / 48000`。
- [x] 1.2 实测 av 对 packed / planar 各格式的 `to_ndarray()` 形状差异。
- [x] 1.3 本地 Opus 编解码往返取证：客户端收到 `(1, 1920)` packed 交织立体声。
- [x] 1.4 确认"仅按形状转置"的启发式会把交织立体声变双倍速噪声 → 弃用。

## 2. 实现
- [x] 2.1 新增 `utils/audio_playback.py`：`prepare_audio_frame(frame)`（按
  `layout.channels` / `format.is_planar` 还原）、`reshape_for_sounddevice`（兜底）、
  `list_output_devices`（只列输出设备）。
- [x] 2.2 float 幅度按峰值缩放到 int16，避免 `0.3 -> 0` 变静音。
- [x] 2.3 `desktop_app.py` 的 `_pump_loop` 在收帧当场用 `prepare_audio_frame`
  还原并透传声道数。
- [x] 2.4 `AudioPlayer` 支持 `device=`、异常改 `logger.error`、新增
  `play_test_tone()`。
- [x] 2.5 设置页新增「音频输出」卡片：设备下拉 / 刷新 / 测试播放 / 保存。
- [x] 2.6 `utils/settings_store.py` 新增 `audio.output_device` / `audio.test_tone`；
  修正 `_as_bool`（原"非空即真"使布尔字段失去校验）。
- [x] 2.7 会话建立时按设置播一次测试提示音。

## 3. 测试
- [x] 3.1 新增 `tests/test_audio_playback.py`（15 例）与 `tests/conftest.py`
  （sounddevice 桩，需补 `__spec__` 否则依赖自检会 `sys.exit(1)`）。
- [x] 3.2 覆盖 packed 立体声 / packed 单声道 / planar / float 幅度 /
  交织顺序 / 设备枚举 / AudioPlayer 写入形状 / 测试音非静音。

## 4. 验证
- [x] 4.1 `python -m py_compile desktop_app.py utils/audio_playback.py
  utils/settings_store.py tests/test_audio_playback.py` 通过。
- [x] 4.2 `python -m pytest tests/test_audio_playback.py` → 15 passed。
- [x] 4.3 设置项隔离验证（临时目录）：写入 / 读取 / 落盘 / 非法 bool 拒绝 /
  脏值回落，均符合预期，真实 `data/settings/settings.json` 未被污染。
- [x] 4.4 本地 Opus 全链路往返：写进声卡形状 `(960, 2)`、peak 7698、
  主频 439.5 Hz —— 既非静音也非错序噪声。
- [ ] 4.5 真机（舱体 PC）：设置页「测试播放」先验证本机扬声器通路，
  再验证数字人播报出声；若仍无声，日志须有明确的 `[AudioPlayer]` / `[WebRTC]` 错误行。
