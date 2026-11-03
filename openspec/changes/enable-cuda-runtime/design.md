# Design

## Context

本机实测到的硬件与网络事实（全部为【实测】，非推断）：

| 项 | 值 |
|---|---|
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU，**8188 MiB** |
| 驱动 | 616.92，CUDA UMD 13.4 |
| 当前 torch | `2.14.0+cpu`（**无 CUDA**，`device_count == 0`，依赖树无 `nvidia-*`） |
| Python | 3.10.21 AMD64（Windows） |
| C 盘可用 | 31 GB |
| D 盘可用 | **6 GB**（不用于本变更） |
| 回滚源 | `https://download.pytorch.org/whl/cpu` 可达 |

网络可达性实测：`download.pytorch.org/whl/cu124/` 739 ms、`cu121/` 573 ms、`mirrors.aliyun.com/pytorch-wheels/cu124/` 685 ms，均 HTTP 200。`mirrors.tuna.tsinghua.edu.cn/pytorch-wheels/cu124/` 为 404（该路径不存在），但 `.../anaconda/cloud/pytorch/` 可达。

## Goals / Non-Goals

**Goals:**

- 让 8 GB 显存真正被用上，且**不破坏任何现有功能**。
- 把「依赖需要哪个构建变体」变成可复现、可校验的事实，而不是环境里的偶然状态。

**Non-Goals:**

- **不升级 torch 版本**。只换构建变体（`2.14.0+cpu` → `2.14.0+cu126`），把对 `torch-ema 0.3` 等依赖的影响压到最小。
- 不动 CUDA 相关的业务逻辑，不改任何接口。
- 不为 GPU 添加新的推理功能或用例。
- 不处理 D 盘空间（那是另一件事，见 `docs/avatar-motion-selection.md`）。

## Decisions

### 决策 1：选 cu126 而不是 cu124 / cu128

| CUDA 源 | 可得的 torch 版本 | 与本机现状 |
|---|---|---|
| cu124 | 最高 `2.6.0+cu124` | **需要降级 torch 版本**（当前 2.14.0），风险高 |
| **cu126** | **`2.14.0+cu126` 与当前版本号完全一致** | ✅ 只换构建变体 |
| cu128 | 从 `2.7.0+cu128` 起 | 需换版本号 |
| cu129 | 从 `2.8.0+cu129` 起 | 需换版本号 |

只有 cu126 提供与当前**完全同版本号**的 CUDA 构建，因此选它。驱动 CUDA UMD 13.4 ≥ 要求的 12.6，兼容。

**并且已验证 `torch-2.14.0+cu126-cp310-cp310-win_amd64.whl` 确实存在于官方源索引中**——Windows 上 CUDA 构建不一定每个版本都有，这一步不能省。

### 决策 2：安装后必须做四条链路回归，这是验收核心

设备从 CPU 切到 CUDA 会暴露 `.to(device)` 相关的隐藏假设。必须回归的四条链路：

1. wav2lip 口型推理（`avatars/wav2lip_avatar.py`）
2. MuseTalk（`avatars/musetalk_avatar.py`）
3. Whisper 音频特征提取（`avatars/audio_features/whisper.py`）
4. TTS（`tts/`，本身多为 CPU/网络调用，但要确认不被牵连）

**判定标准**：服务能启动、模型能加载、生成出可用的帧与音频。不能只看"import 成功"。

### 决策 3：设备校验要报告"落差"，而不是只报告设备名

`initialize_device()` 在 CUDA 不可用时**静默返回 cpu**——这正是这次问题的隐蔽之处：装错构建变体不会有任何提示，只会一直慢。因此要求里加了「声明了 GPU 构建但设备不可用 MUST 明确报告」这一条：不能让人以为在用 GPU。

### 决策 4：回滚路径必须先准备好

- 安装前已保存 `pip freeze` 快照到 `%TEMP%\pip-freeze-before-cuda.txt`（142 个包）。
- 回滚命令：`pip install torch==2.14.0+cpu --index-url https://download.pytorch.org/whl/cpu` 并卸载 `nvidia-*`。
- 回滚源可达性已验证。

**先准备回滚再动手**，因为换 torch 会影响整个环境，一旦某条链路退化需要能立刻回到已知可用状态。

## Risks / Trade-offs

- **[换装破坏现有链路]** → 四条链路回归验证；回滚快照已备好；只换构建变体不换版本号，把变量降到最少。
- **[磁盘不足]** → 新增约 2.5 GB 依赖，C 盘 31 GB 充足；D 盘 6 GB 不参与。
- **[下载失败或中断留下半装状态]** → 若 pip 中断，torch 可能处于不可用状态。处理：重新执行安装命令（pip 可续），或按回滚步骤回到 CPU 构建。
- **[显存被桌面占用]** → 实测安装前已有 1137 MiB 被占用（8 GB 中约 14%），且 GPU 利用率 28%。推理可用显存会少于标称值，需要在验证时留意是否出现 OOM。
- **[ultralight 硬编码 `.cuda()`]** → `avatars/ultralight_avatar.py:171` 未走 `initialize_device()`，直接 `.cuda()`。该分支在 CPU 环境下本就不可用；换装后它会变可用，但写法仍是隐患，需在任务中记录（改动它属于代码变更，不在本变更范围内）。

## Migration Plan

1. 记录现状与回滚快照（已完成）。
2. 确认 Windows + cp310 的 wheel 存在（已完成）。
3. 安装 `torch==2.14.0+cu126`，确认 `cuda.is_available()` 为真且显存读数合理。
4. 逐条回归四条链路。
5. 补 `requirements.txt` 的 `torch` 声明与构建变体说明；文档给出可复现安装命令。
6. 启动时输出实际设备，并在声明 GPU 却落到 CPU 时明确警告。
7. 若第 3 或第 4 步失败 → 按回滚步骤回到 CPU 构建，并把失败结论记录到本文档。

## Open Questions

- 是否需要为 GPU 路径设定显存上限（如 `torch.cuda.set_per_process_memory_fraction`）以防与桌面/其它程序争抢？本变更先只做验证，若出现 OOM 再单独立项。
- MuseTalk 在 8 GB 显存下的实际占用未测——若显存不足，可能需要调整 `--batch_size`（默认 16）。

## 实施结果（实测，2026-10-13）

### 换装本身：只改了一个包

```
包数 前=142 后=142
差异（Compare-Object 全量比对）：
  <= torch==2.14.0
  => torch==2.14.0+cu126
```

**除 torch 之外零传递变更**。原因是 Windows 上 torch 的 wheel 把 CUDA 运行时 DLL 打包在包内，不依赖 Linux 那样的独立 `nvidia-*` wheels——安装前对"会拉入约 2.5 GB nvidia 依赖"的预估在 Windows 上不成立，实际只下载了 2.6 GB 的单个 wheel，没有新增任何包。

安装过程中连接中断过一次，pip 自动续传成功（`Resuming download ... 1002.6 MB/2602.7 MB`）。

### CUDA 可用性（对应任务 2.2）

```
torch            2.14.0+cu126
cuda build       12.6
is_available     True
device_count     1
device name      NVIDIA GeForce RTX 4060 Laptop GPU
total_memory     8188 MiB   ← 与 nvidia-smi 的 8188MiB 完全一致
GPU 矩阵乘法自测  通过（1000×1000）
pip check        无冲突
```

### 性能对比（对应任务 3.6）

按 `avatars/wav2lip_avatar.py:105` 的真实调用签名 `model(mel_batch, img_batch)`，`batch_size=16`、`modelres=256`（服务 warmup 用的就是这个值），预热后取 10 次平均：

| 设备 | 每批 16 帧 | 每帧 | 输出有效性 |
|---|---|---|---|
| CPU | 2790.9 ms | **174.4 ms** | 有限值 ✓ |
| CUDA | 187.6 ms | **11.7 ms** | 有限值 ✓ |
| — | — | **加速 14.88×** | |

**这条数字改变了结论的性质**：CPU 上 174.4 ms/帧意味着吞吐上限约 **5.7 fps**，本来就不可能满足 `--fps 25` 的实时要求。因此换装不只是"变快"，而是**从"达不到实时"变成"满足实时"**。

### 四条链路的实际验证程度（不夸大）

| 链路 | 结论 | 证据 |
|---|---|---|
| wav2lip 口型 | ✅ **已验证** | 服务启动设备为 cuda；按生产签名做的前向基准输出有限值；模型加载与 warmup 全部在 CUDA 上完成 |
| MuseTalk | ⚠️ **无法验证** | **权重不在本机**（`models/` 只有 `wav2lip.pth`，全仓库未找到 musetalk 的 `.pth/.pkl/.onnx/.bin`） |
| Whisper 特征 | ⚠️ **无法验证** | 同上，`avatars/musetalk/whisper/` 下无权重文件 |
| TTS | ✅ 环境层面已确认 | 导入 12/12 通过、`registry.create("tts","edgetts",…)` 构造成功；该链路走 `edge_tts` 云调用，**不依赖 torch**。未在换装后做端到端合成（独立测试台与 `BaseTTS` 自建事件循环的架构冲突） |

**这是一个必须如实说明的验证缺口**：MuseTalk 与 Whisper 特征这两条链路在换装前就因缺权重而不可用，因此既不能说"换装破坏了它们"，也不能说"换装后它们正常"。它们需要在补齐权重后才能验证。

### 一个已知的硬编码隐患（本变更不改动）

`avatars/ultralight_avatar.py:171` 直接调用 `self.model(img_batch.cuda(), audiofeat_batch.cuda())`，未走 `initialize_device()`。该分支在换装前的 CPU 环境下本就不可用，换装后反而变得可运行——但写法仍是隐患，应单独立项修掉。本次已在规格中新增「设备选择集中在单一入口」这一条来约束后续。
