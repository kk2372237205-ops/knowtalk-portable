# Tasks

## 1. 预检（破坏性操作之前）

- [x] 1.1 记录当前环境用于回滚：`pip freeze` 快照（142 个包，保存到 `%TEMP%\pip-freeze-before-cuda.txt`）、Python 版本、torch 版本与构建变体。验证：快照文件存在且包数与 `pip list` 一致
- [x] 1.2 确认存在与本机 Python/平台匹配的 CUDA wheel。验证：官方源索引中确认 `torch-2.14.0+cu126-cp310-cp310-win_amd64.whl` 存在
- [x] 1.3 选择不改变 torch 版本号的 CUDA 源：逐个比对 cu124/cu126/cu128/cu129 可得的版本，只有 cu126 提供与当前完全一致的 `2.14.0`。验证：记录四个源的版本对比结论
- [x] 1.4 确认回滚源可达。验证：`https://download.pytorch.org/whl/cpu` 返回可用版本列表
- [x] 1.5 确认磁盘足够。验证：C 盘可用空间大于依赖体积（约 2.5 GB）的 3 倍
- [x] 1.6 确认设备选择已集中：检查各推理模块是否都走统一入口 `initialize_device()`，列出硬编码设备调用作为已知风险。验证：给出模块清单与例外项（`avatars/ultralight_avatar.py:171` 直接 `.cuda()`）

## 2. 换装 CUDA 构建

- [x] 2.1 安装 `torch==2.14.0+cu126`（来源 `https://download.pytorch.org/whl/cu126`）。验证：`pip show torch` 显示 `2.14.0+cu126`
- [x] 2.2 验证 CUDA 可用性与显存读数。验证：`torch.cuda.is_available()` 为 `True`、`device_count()` 为 1、`get_device_properties(0).total_memory` 与 `nvidia-smi` 的 8188 MiB 一致
- [x] 2.3 确认 `torch-ema` 等既有依赖未被破坏。验证：逐条 import 成功，且 `pip check` 无冲突
- [x] 2.4 记录换装前后的依赖差异清单，写入文档。验证：新增的 `nvidia-*` 包被逐个列出

## 3. 四条链路回归（本次变更的验收核心）

- [x] 3.1 主服务能启动且模型能加载到 CUDA。验证：启动日志中设备为 cuda 而非 cpu，wav2lip 权重加载成功
- [x] 3.2 wav2lip 口型链路可用。验证：走一次 `/human` 并确认产生了新帧（不是空帧/异常退出），且画面正常
- [ ] 3.3 MuseTalk 链路可用（切换 `--model musetalk`）。验证：模型加载成功、`timesteps` 与 unet 在同一设备上、能产出帧；若显存不足则记录并给出 `--batch_size` 调整结论
      ⚠️ **无法验证：MuseTalk 权重不在本机**（`models/` 只有 `wav2lip.pth`，全仓库未找到 musetalk 的 `.pth/.pkl/.onnx/.bin`）。该链路在换装前同样不可用，因此既不能判定为退化也不能判定为正常。补齐权重后需重做此项。
- [ ] 3.4 Whisper 音频特征提取可用。验证：特征提取产出非空且形状符合预期
      ⚠️ **无法验证：`avatars/musetalk/whisper/` 下无权重文件**，同上。
- [ ] 3.5 TTS 链路未受牵连。验证：走一次朗读并确认产生了音频帧
      ⚠️ **未做端到端**：已验证导入 12/12 与 `registry.create("tts","edgetts",…)` 构造成功；该链路走 `edge_tts` 云调用、不依赖 torch。独立测试台与 `BaseTTS` 自建事件循环冲突，端到端合成待下次真实运行服务时确认。
- [x] 3.6 性能对比：记录换装前后的推理耗时。验证：给出同一段输入在 CPU 与 CUDA 上的实测耗时对比

## 4. 依赖声明与设备校验

- [x] 4.1 在 `requirements.txt` 中补上 `torch` 的声明（含版本与构建变体）。验证：清单中出现 `torch` 且写明了 CUDA 构建变体，不再只写 `torch-ema`
- [x] 4.2 在文档中给出可复现的安装命令（含 `--index-url`）。验证：按文档命令在干净环境中能装出所声明的构建变体
- [x] 4.3 让服务在启动时输出实际使用的设备，并在「已装 GPU 构建但当前落到 CPU」时给出明确警告。验证：分别构造两处场景（CUDA 可用、不可用）确认输出有区分

## 5. 验证与文档

- [x] 5.1 `python -m unittest` 全部通过。验证：退出码为 0
- [x] 5.2 `openspec validate --all --strict` 通过。验证：退出码为 0
- [x] 5.3 更新 `docs/avatar-motion-selection.md` 的前置状态：把「换装 CUDA 版 PyTorch」从待办改为已完成，并写入实测性能数字。验证：文档中不再声称该前置未做
- [x] 5.4 记录失败与回滚情形（若发生）。验证：若 2.x 或 3.x 失败，必须写明失败现象、已执行的回滚步骤与最终状态，不允许只留"未完成"
- [x] 5.5 复核仓库状态：无未跟踪文件、无密钥文件入库。验证：`git status` 干净

## Workflow follow-up

- 用户确认回归结果后归档本变更：`openspec archive enable-cuda-runtime`。
- 归档后确认 `openspec/specs/repository-hygiene/spec.md` 中新增了「GPU 加速依赖的构建变体必须被声明并可校验」这条要求。
