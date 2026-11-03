# MuseTalk 模型加载不再因内存枯竭而硬崩

## 为什么

桌面端启动报「主服务进程已退出（exit=3221225477）」，日志停在
`[加载] 加载 MuseTalk 模型...`，`stderr` 完全为空。`3221225477` = `0xC0000005`
访问冲突，是原生层崩溃，Python 来不及抛 traceback。

实测复现后，崩溃点有三处，都不是「显存不够」那么简单：

| 位置 | 现象 | 真实原因 |
| --- | --- | --- |
| `UNet2DConditionModel(**cfg)` | 崩在 `torch.empty` 建 Conv 权重时 | 构造 fp32 全量参数要 3.2GB CPU 内存，顶到机器上限后 malloc 返回空指针 |
| `torch.load(mmap=True)` 后 `.half()` | 崩在 dictcomp | mmap 读过的页**不会归还**，读完 3.2GB 权重就实占 3.2GB 物理内存 |
| 逐层搬卡 | 报 `CUDA error: out of memory`，但只申请 46KB | 物理内存被吃干后，CUDA 驱动做 H2D 拷贝锁不到页，伪装成显存 OOM |

关键实测数据（16GB 内存 / RTX 4060 Laptop 8G）：

- 全量读一遍 mmap 权重后，系统可用内存从 **4.85GB 掉到 1.69GB**；
  调一次 `EmptyWorkingSet` 后**立刻回到 5.03GB** —— 页确实被 mmap 占着没释放。
- 该机器 `nvidia-smi` 显示显存空闲 6.9GB 时，进程申请 20MB / 46KB 仍被拒；
  单进程实测显存预算上限约 4GB（分到 3.0GB 后再要 2.0GB 即失败）。
  所以「显存还剩很多」不能作为能加载的依据。

老实现的三份开销叠加：CPU 3.2GB（fp32 参数）+ CPU 3.2GB（mmap 页）+
GPU 6.3GB（fp32 与 fp16 参数各一份），在 8G 卡 + 16G 内存的整机上必然撞墙。
旧代码里「mmap 能省内存」的注释是错的，它只省了「一次读全量」的峰值，
不省「读过的页仍驻留」的稳态占用。

## 做什么

1. **`UNet2DConditionModel` 改用 meta device 构造**：只搭结构不分配存储，
   省掉 3.2GB fp32 参数。
2. **`load_state_dict(assign=True)` 零拷贝挂载 mmap 权重**：参数直接指向映射区，
   不再复制一份。
3. **逐层搬卡转 fp16**：每搬完 10 个 module 调一次
   `EmptyWorkingSet(GetCurrentProcess())`，把读过的 mmap 页推回 standby，
   物理内存立刻释放，CUDA 驱动始终有页可锁。
4. 无 CUDA 的兜底路径保留原有行为，只补 `gc.collect()` 与页归还。

## 不做什么

- 不改模型文件格式（不转 safetensors、不落 fp16 副本），存量权重原样可用。
- 不动 VAE / Whisper 的加载方式（334MB / 151MB，不构成瓶颈）。
- 不改 `UNet()` 的构造签名与 `.model` / `.device` / `.pe` 属性，
  调用方（`musetalk_avatar.load_model()`）无需改动。
- 不引入新的第三方依赖，`ctypes` 已在标准库。

## 影响

| 指标 | 改前 | 改后 |
| --- | --- | --- |
| 加载结果 | 0xC0000005 崩溃 | 成功 |
| CPU 峰值增量 | ~3.2GB | < 200MB |
| GPU 峰值 | ~6.3GB | 1.9GB（实测 `torch_alloc`） |
| 加载耗时 | — | 6.6–7.4s |
| 系统可用内存 | 4.85GB → 1.69GB | 6.77GB → 6.16GB（不塌） |

推理精度不变：仍为 fp16、仍在 CUDA 上，`warm_up` 与前向输出经实测通过
（`[1,4,32,32]`，全部有限值）。
