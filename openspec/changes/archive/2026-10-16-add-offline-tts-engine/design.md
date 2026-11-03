# Design

## Context

### 实测到的失效

```
edge-tts 7.2.8（PyPI 最新版，无升级路径）
constants.py: CHROMIUM_FULL_VERSION = "143.0.3650.75"
constants.py: SEC_MS_GEC_VERSION = f"1-{CHROMIUM_FULL_VERSION}"

连续两次请求结果：
  WSServerHandshakeError 403  wss://speech.platform.bing.com/.../edge/v1
```

微软侧收紧了客户端版本校验，本地无对应修复手段。

### 现有 TTS 的失败处理是静默的

`tts/edge.py`：

```
L49  asyncio.new_event_loop().run_until_complete(self.__main(...))
L51  if local_stream.getbuffer().nbytes<=0:      # 合成没产出
L52      logger.error('edgetts err!!!!!')
L53      return                                    # ← 直接返回，上层无从察觉
L106 except Exception as e:
L107     logger.exception('edgetts')               # ← 吞掉异常
```

课堂表现：数字人不说话，使用者看不到原因。

### 引擎接口契约（实测）

- `txt_to_audio(self, msg: tuple[str, dict])` 是**普通方法**，不是协程也不是生成器；由 `BaseTTS.process_tts` 在后台线程中同步调用（`tts/base_tts.py:52-68`）。
- 它在内部自己合成到内存音频缓冲，再用 `soundfile` + `resampy` 转成 16 kHz 单声道 `float32`，然后按 `self.chunk = 16000 // (fps*2)`（默认 320 样本 = 20 ms）切帧，逐帧调用 `self.parent.put_audio_frame(frame, eventpoint)`。
- `eventpoint` 在首帧带 `status: 'start'`、末帧带 `status: 'end'`，并合并调用方传入的 `textevent`。
- `edge.py` 用 `asyncio.new_event_loop().run_until_complete(...)`，因此该引擎**只能在非事件循环线程中调用**（这正是先前在独立测试台里调用会报 "Cannot run the event loop while another loop is running" 的原因）。

### 本机系统语音（实测）

```
Microsoft Huihui Desktop     可用=True   （中文）
Microsoft Zira Desktop       可用=True   （英文）
```

用 `System.Speech` 合成中文成功产出 wav（214.6 KB / 283.5 KB）。

## Goals / Non-Goals

**Goals:**

- 外部语音服务不可用时，课堂**仍然能发声**。
- 合成失败**不再静默**，且给出可操作的提示。
- 换引擎不破坏下游（保持 16 kHz 单声道与既有帧长）。

**Non-Goals:**

- **不追求与 edgetts 相当的音质**。SAPI 是拼接式合成，音质明显更机械。这是"离线可用"的代价，必须如实告知使用者。
- 不改 `put_audio_frame` 与下游消费方式。
- 不新增客户端可见的错误上报接口（见决策 4）。
- 不修 `edge-tts` 本身（无法修，等上游）。
- 不为非 Windows 平台提供离线引擎。Linux/macOS 上选择该引擎时明确报错。

## Decisions

### 决策 1：用 Windows SAPI，通过 `comtypes` 驱动

候选与取舍：

| 方案 | 依赖 | 结论 |
|---|---|---|
| **`comtypes` + SAPI COM** | 1 个轻量包 | ✅ 采用 |
| `pyttsx3` | 会连带 `comtypes` + `pywin32` | ✗ 本项目只需"文本→wav"一件事，多拖依赖不值 |
| 每次调用 `powershell.exe` + `System.Speech` | 零新依赖 | ✗ 每句多出一次进程启动开销（约 0.5–1.5 s），且把合成绑死在 PowerShell 上 |
| `pythonnet` + .NET `System.Speech` | 重量级 | ✗ 为一个 TTS 引擎引入 .NET 互操作层不划算 |

本机实测 SAPI 有中文语音且能成功合成，因此 SAPI 路线成立。

### 决策 2：失败可见的机制是"引擎抛异常，基类统一处理"

引擎方法不是协程，无法靠返回值优雅传递失败（调用点也不检查返回值）。因此：

- 新增 `TTSSynthesisError(message, hint)`；
- 引擎在合成产出为空或出错时**抛出**它，而不是 `return`；
- `BaseTTS.process_tts` 捕获它，记录 ERROR 级日志（含 `hint`），然后执行回退（决策 3）。

这样"引擎不吞异常"与"基类统一处理"职责清晰：每个引擎实现保持简单，回退逻辑只写一处。

### 决策 3：回退在 `BaseTTS` 里做，且至多一次

- 备用引擎通过参数与运行期设置配置（`--TTS_FALLBACK`）；
- 主引擎失败 → 懒加载备用引擎插件 → 用同一段文本与同一组参数再合成一次 → 成功则记录"已回退"；
- 备用引擎配置成与主引擎同名 → 视为无效配置，明确报告并**不重试**（避免自环）；
- 备用引擎也失败 → 记录两次失败并结束该次合成，**不再尝试任何引擎**（避免环）。

**为什么至多一次**：无界重试会在两个都不可用时形成死循环，把 TTS 工作线程卡死，比不发声更糟。

### 决策 4：不新增客户端可见的错误上报接口

"失败可见"落在服务端：引擎抛异常、基类记录 ERROR 日志（含可操作提示）并把最近一次错误记在实例属性上。**不新增 API**，因为：

- 新增接口属于新的对外契约，会扩大本变更的范围；
- 现有前端没有承载"播报失败"提示的 UI，加接口也没有消费方；
- 日志 + 明确的 hint 已经能让运维定位（这是本次问题最难的地方——**之前只有一行 `edgetts err!!!!!`，看不出原因**）。

若日后需要课堂上的可见提示，应作为独立变更，连同前端一起设计。

### 决策 5：离线引擎只保证 Windows，非 Windows 明确报错

`comtypes` 与 SAPI 都是 Windows 专有。在非 Windows 上导入该插件时 MUST 明确报错并说明原因，而不是让 `ImportError` 冒到顶层。这是规格里「引擎不可用的平台」场景对应的实现。

## Risks / Trade-offs

- **[音质下降]** → 文档如实说明取舍并给出切回云端引擎的方法；不把 SAPI 说成等价替代。
- **[SAPI 合成是阻塞调用]** → `SpVoice.Speak` 同步阻塞。但 `txt_to_audio` 本就在 TTS 工作线程中被同步调用，阻塞该线程与 `edge.py` 用 `run_until_complete` 阻塞的语义一致，因此不额外恶化。
- **[新增依赖 `comtypes`]** → 按 `repository-hygiene` 声明进 `requirements.txt`；仅驱动 COM，不引入运行时服务。
- **[外部服务恢复后仍用低音质引擎]** → 保持 `edgetts` 为可选，默认值的选择要显式记录；回退只在失败时触发，不会在成功时改变音质。
- **[回退掩盖了主引擎的长期故障]** → 每次回退都记 WARNING 日志并说明原因，使长期故障在日志中可见。

## Migration Plan

1. 新增 `TTSSynthesisError` 与 `tts/sapi.py` 引擎。
2. 改造 `tts/edge.py`：把静默 `return` 改为抛 `TTSSynthesisError`，并给出可操作 hint。
3. 在 `BaseTTS.process_tts` 中加入至多一次的回退编排。
4. 参数与设置：`--TTS_FALLBACK`、引擎枚举加入新引擎。
5. 单元测试：异常抛出、回退一次、同名回退被拒、回退也失败不循环、产出采样率与帧长。
6. 端到端：用新引擎实际合成一段中文并确认产出帧；再验证 edgetts 失败时会回退到新引擎。

**回滚**：把 `--tts` 改回原引擎、移除 `--TTS_FALLBACK` 即可；新插件文件留着不影响。

## Open Questions

- SAPI 是否支持语速参数（`SpVoice.Rate`，范围 -10..10）与现有 `--TTS_RATE`（形如 `+20%`）的换算？实现时需要做一次映射并记录换算规则；百分比无法精确对应，只能就近取整。
- 是否需要为 SAPI 提供音色选择（本机有两个语音，仅一个是中文）？当前设计按语言自动选中文语音，不额外暴露音色参数。
