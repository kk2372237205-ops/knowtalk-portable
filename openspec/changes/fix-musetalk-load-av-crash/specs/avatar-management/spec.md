# Spec Delta

## ADDED Requirements

### Requirement: MuseTalk UNet 必须在物理内存受限的整机上加载成功

`avatars/musetalk/models/unet.py` 的 `UNet.__init__` 在 CUDA 设备上 MUST 按下列顺序完成加载，且任一阶段 MUST NOT 让「fp32 全量参数」「mmap 权重页」「GPU fp32 副本」三者同时驻留：

1. 用 `torch.device("meta")` 上下文构造 `UNet2DConditionModel`，MUST NOT 在此阶段分配参数存储；
2. 用 `torch.load(..., mmap=True)` 读取权重，并以 `load_state_dict(..., assign=True)` 挂载，MUST NOT 产生 fp32 权重副本；
3. 逐 module 把参数与 buffer 搬到 CUDA 并转 `float16`，每搬完至多 10 个 module MUST 调用一次 `EmptyWorkingSet(GetCurrentProcess())` 归还已读的 mmap 页。

加载完成后 `model` 的参数 MUST 全为 `float16` 且位于 CUDA；`UNet` 的构造签名、`.model`、`.device`、`.pe` 属性 MUST 与改前一致。

无 CUDA 时 MUST 保留原有 CPU 加载路径。

#### Scenario: 16G 内存机器上冷启动

- **WHEN** 在 16GB 物理内存、可用内存约 4.5GB 的机器上启动主服务
- **THEN** 日志 MUST 继续走到 `[加载] 模型加载完成！`
- **AND** 进程 MUST NOT 以退出码 `3221225477`（0xC0000005）结束
- **AND** 加载完成后 `torch.cuda.memory_allocated()` MUST 小于 2.5GB

#### Scenario: 权重页不再滞留

- **WHEN** 逐层搬卡完成
- **THEN** 系统可用内存 MUST NOT 因为读过 3.2GB 权重而减少 3GB 量级
- **AND** 连续两次启动 MUST 都成功（不存在「第一次占着页、第二次崩」的残留）

#### Scenario: 推理精度不变

- **WHEN** 加载完成后执行 `warm_up` 或一次前向
- **THEN** 输出 MUST 为 `float16`，且 MUST 不含 `inf` / `nan`

### Requirement: 加载期 OOM 必须可归因

当 CUDA 分配失败时，MUST 能区分「显存预算不足」与「物理内存枯竭导致的伪 OOM」：日志 MUST 同时给出 `torch.cuda.memory_allocated()` 与系统可用内存，MUST NOT 把「驱动锁页失败」笼统表述为「显存不足」。

#### Scenario: 出现伪 OOM 时

- **WHEN** `cudaMalloc` 因物理内存不足失败（显存报告仍有大量空闲）
- **THEN** 日志 MUST 同时显示显存占用与系统可用内存两项指标
- **AND** 排查文档 MUST 指明「先看物理内存，再看显存」
