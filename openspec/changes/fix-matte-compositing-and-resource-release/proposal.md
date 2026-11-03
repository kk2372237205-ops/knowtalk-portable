# 抠像合成修正 + 退出资源释放

## Why

三件现场问题，前两件同源。

1. **配了背景后新动作糊出一片白影。** 背景合成按「帧源 + 帧号」查 alpha
   （`AvatarBackground._alpha_for`）；动作在 `data/avatars/<id>/matte/gestures/` 下没有
   对应目录时，代码直接回退用 base 的 alpha。base 是双手下垂的站姿、动作是手臂前伸，
   两套轮廓完全对不上——动作帧里的白背景被当成人体保留下来，合成到深色背景上
   就是一大块白色残影（用户现场截图）。

   为什么偏偏是 `idle_sway` / `wave_one`：这两个动作是后加的，`matte/manifest.json`
   里只有 compare/count/open/press/thanks/zoom 六个（各 107 帧），新的两个（各 124 帧）
   从未生成过 alpha。

2. **同一个回退还会把画面冻住。** 回退后 `out_cache` 的键是
   `(source, idx % len(sources[source]), w, h)`，而 base 只有 1 帧 → `idx % 1 == 0`
   → 整个动作的所有帧算出同一个键 → 每帧都返回第一张合成结果。
   实测 `idle_sway` 第 0/20/60 帧的输出**逐像素相同**：配背景播新动作时画面是一张静止图。

3. **退出桌面版后摄像头不释放。** 检测进程（`scripts/proximity_detect.py`）是独立子进程，
   只靠 `enabled` 配置自检退出。用户开着感应直接关窗口时 `on_close` 完全不碰它，
   子进程继续占着摄像头；下次启动就报「打不开摄像头（被其它程序占用）」，
   严重时（现场机器无重启）整场迎宾都是死的。

## What Changes

**抠像素材（离线）**

- `scripts/avatar_matte.py` 支持增量补抠：`--gesture <gid>` 只处理指定动作，
  `--missing` 只处理还没有 alpha 的动作；新增单动作入口 `run_gesture()` 供生成流程调用。
- 动作生成流程（`utils/gesture_make.py`）末尾自动给**刚生成的那个动作**抠像，
  与已有 `_build_muse_assets` 并列；失败只记 warning，不让整次生成判失败。

**背景合成（运行时）**

- 缺该动作的 alpha 时**不再回退到 base**：直接返回原帧（保持原背景）+ 记一条显式
  warning，宁可这一帧没换背景，也不要错位的白影与冻结画面。
- `out_cache` 的键改为「真实使用的素材目录 + 该素材内的帧号 + 尺寸」，
  回退与否都不再可能撞键。
- 合成公式化简为 `out = src + (1-a)·(bg - bg_src)`（由
  `src = a·F + (1-a)·bg_src` 与 `out = a·F + (1-a)·bg` 联立得到），并用 OpenCV 定点
  运算替代 numpy float32：`bg - bg_src` 每帧不变（缓存成正/负两张 uint8），
  单帧只剩 2 次 multiply + 1 次 add + 1 次 subtract。
  实测 19.4ms → 目标 5ms 以内（1080×538）。
- 顺带修掉原实现对 `a` 做 `clip(a, 0.08, 1)` 的数值爆炸隐患——化简式在全域成立，
  不再需要这个下限。

**退出释放（桌面端）**

- `_start_sensor` 保存子进程句柄；`on_close` 新增 `_shutdown_sensor()`：
  `terminate()` → 等 3 秒 → `kill()`，再按 `proximity.pid` 兜底，最后删 pid 文件。
- `on_close` 补齐其余资源：`requests.Session.close()`、`AvatarClient.shutdown()`
  （断开 pc 并停掉 asyncio 事件循环）、`ServiceLauncher.stop()` 增加 wait/kill、
  取消所有 `after` 定时器（轮询 / 气泡 / 动画）。
- 主服务进程退出后，其持有的摄像头探测句柄、CUDA 上下文随进程回收；已有
  `app.py on_shutdown → rtc_manager.shutdown()` 负责 RTC 侧。

## Impact

- 修改：`utils/avatar_background.py`、`scripts/avatar_matte.py`、`utils/gesture_make.py`、
  `desktop_app.py`（`on_close` / `_start_sensor` / `_stop_sensor` / `AvatarClient`）。
- 新增：`openspec/specs/avatar-compositing/spec.md`（抠像与背景合成）、
  `kiosk-holographic-cabin` 增加「退出必须释放摄像头」。
- 数据：为 `musetalk_ke` 补齐 `idle_sway`、`wave_one` 两个动作的 alpha（248 帧）。
- 风险：缺 alpha 时画面不再换背景（原来是错位白影）。这是**故意**的取舍，
  状态行会给出提示，用户重跑一次抠像即可恢复。
