# Proposal

## Why

本机装了 **NVIDIA GeForce RTX 4060 Laptop（8188 MiB 显存）**，但项目一直只用 CPU 跑推理——因为环境里装的是 **CPU-only 构建的 PyTorch**：

```
torch 2.14.0+cpu
cuda.is_available()  == False
torch.cuda.device_count() == 0
依赖树中没有 nvidia-* 任何包
```

这是本项目最大的一处未兑现性能：8 GB 显存长期闲置，wav2lip 口型推理、MuseTalk、Whisper 特征全部压在 CPU 上。

顺带发现一个既有的规格违反：**`requirements.txt` 根本没有声明 `torch`**（只有 `torch-ema`），而代码里到处 `import torch`。这违反了 `repository-hygiene` 的「清单文件 MUST 覆盖代码实际导入的所有第三方包」。

而且既有规格只要求声明**哪些包**，没有要求声明**哪个构建变体**。这正是这次问题的根源：即使把 `torch` 写进清单，从默认源装出来的仍是 CPU 构建，GPU 能力会**静默消失**——没人会注意到。

## What Changes

- 把环境中的 PyTorch 从 `2.14.0+cpu` 换为 **`2.14.0+cu126`**（版本号完全一致，仅换构建变体，以最小化对 `torch-ema` 等依赖的破坏）。CUDA 12.6 与本机驱动（CUDA UMD 13.4）兼容，且 `torch-2.14.0+cu126-cp310-cp310-win_amd64.whl` 已确认存在于官方源。
- 在 `requirements.txt` 中**补上 `torch` 的声明**，并声明其构建变体与安装来源；在文档中给出可复现的安装命令。
- 新增设备校验：启动时报告实际使用的设备（cuda/cpu），并在「声明了 CUDA 构建但设备不可用」时**明确报告**，而不是静默退回 CPU。
- **回归验证既有四条链路不得退化**：wav2lip 口型、MuseTalk、Whisper 特征提取、TTS。设备切换会暴露 `.to(device)` 相关的隐藏假设。
- 记录回滚步骤（`pip freeze` 快照已保存在 `%TEMP%\pip-freeze-before-cuda.txt`），回滚源 `https://download.pytorch.org/whl/cpu` 已验证可达。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

无。本变更**新增**一条要求而不是改动既有要求——既有「依赖必须被完整声明并有统一的版本策略」管的是"哪些包要被声明"，而"哪个构建变体要被声明"是一个它没有覆盖的新关注点。按 OpenSpec 的规则，新增关注点应使用 ADDED 而非 MODIFIED，以免重写既有要求文本时丢失细节。

新增的要求落在既有能力 `repository-hygiene` 下：

- `repository-hygiene`: 新增「GPU 加速依赖的构建变体必须被声明并可校验」——要求需要 GPU 加速的依赖声明其构建变体与可复现的安装命令，并在安装后校验实际设备；声明与实际情况不一致时必须明确报告，不得静默退回 CPU。

## Impact

- **运行环境**：新增约 2.5 GB 的 `nvidia-*` 依赖（C 盘可用 31 GB，够用）。注意 **D 盘仅剩 6 GB**，不用于此。
- **代码**：理想情况下**零代码改动**——设备选择已由 `utils/*.py` 的 `initialize_device()` 统一处理（`torch.cuda.is_available()` 为真即返回 `cuda`）。
- **已知硬编码风险**：`avatars/ultralight_avatar.py:171` 直接调用 `self.model(img_batch.cuda(), ...)`，未走 `initialize_device()`。该分支在当前 CPU 环境下本就不可用，换装后反而会变可用；但它依赖 CUDA 才成立的写法必须记录。
- **依赖清单**：`requirements.txt` 补上 `torch` 声明。
- **不受影响**：所有 HTTP 接口的请求与响应结构、数据库与数据文件格式、前端。
- **不回滚的后果**：若换装导致某条链路退化且未发现，会直接影响课堂可用性。因此回归验证是本次变更的验收核心。
