# Design

## 现状事实

- 本机：16GB 内存、RTX 4060 Laptop 8GB 显存。
- `llama-server` 加载 `qwen3-gpu:8b` 后：进程私有内存 **6.2GB**、GPU 显存 **4.6GB**。
- KnowTalking 主程序：torch + wav2lip + librosa/scipy，内存约 2GB、显存约 1.2GB。
- RAG 侧车：内存约 1.4GB。
- 同机还有 WorkBuddy 本体（1.5GB）、浏览器、IDE。

结论：**主程序 + 侧车 + 8B 常驻，三者放不下**。

### D1：用完即卸（`keep_alive=0`）

Ollama 的 `keep_alive` 默认 5 分钟，模型用完仍驻留。对本机这种「三方抢资源」的场景，
应当**只在真正需要它的那十几秒里占着**。

- 嵌入：每次问答都要调用，但只要几秒 → `keep_alive=0`。
- 生成：一次问答一次调用，答完就没用了 → `keep_alive=0`。

代价是每次问答多一次冷加载（实测 `load_duration` ≈ 8.7s，总耗时 ≈ 10s）。
课堂问答是低频操作，这个代价换回「主程序不会再被挤死」非常值。

取值走配置 `RAG_OLLAMA_KEEP_ALIVE`（默认 `0`），想要「答完先留着 2 分钟」
可以调成 `120`（秒）。

### D2：改用更小的生成模型（本机是笔记本，桌面也吃显存）

**原判断**（已被实测推翻）：以为 `keep_alive=0` 就够了，不换模型。

**实测推翻它的证据**：这台机器是**笔记本**，GPU 同时驱动着桌面
（`dwm.exe`、Photoshop、Edge、微信、VS Code、ToDesk 都在上面）。
即使没有任何模型驻留，桌面自己也占着约 2.1GB 显存；加上数字人 wav2lip 的约 1.2GB，
**可用显存只剩约 4.8GB**。而 `qwen3:8b` 需要 **4643 MiB 连续显存**——
只剩 164 MiB 余量，CUDA 分配必然失败：

```
ggml_backend_buft_alloc_buffer_n_default: failed to allocate CUDA0 buffer of size 4869357568
error loading model: unable to allocate CUDA0 buffer
```

注意：8B 在**没有数字人运行**时是能加载的（实测 10.2s 出答案），
一旦数字人同时跑就必然 OOM。也就是说「RAG 问答可用」取决于数字人开没开——
这对课堂是致命的。

**因此必须换更小的生成模型**：`qwen3:4b`（约 2.6GB 显存），
与数字人（1.2GB）+ 桌面（2.1GB）合计约 5.9GB，8GB 卡上留足余量。
`qwen3:1.7b`（约 1.4GB）更保险，代价是回答质量下降。

模型选择做成配置项（`.env.rag` 的 `RAG_LLM_MODEL`），换模型只改一行、不用改代码。

### D2b：上下文与批大小必须压住

Ollama 在 8GB 卡上默认 `num_ctx=4096`，KV cache 就要 **576MB**，
再加计算缓冲，与「桌面 + 数字人」一起把显存吃穿。
实测 `qwen3:4b` 的**权重加载成功、KV cache 分配失败**：

```
allocating 576.00 MiB on device 0: cudaMalloc failed: out of memory
llama_init_from_model: failed to initialize the context: failed to allocate buffer for kv cache
```

压到 `num_ctx=2048`（模型原生上下文）/ `num_batch=512` 后稳过（实测 5.8s 出答案）。
这两个值做成配置项 `RAG_OLLAMA_NUM_CTX` / `RAG_OLLAMA_NUM_BATCH`。

⚠️ 注意 Ollama 自己的 `gpu memory free` 报的是「总显存 - 自己的占用」，
**不扣其它进程**（桌面 + 数字人），所以它显示 6.9GB free 时实际只剩 ~4.9GB。
不要被它的日志误导。

### D2c：小模型会「把分析过程说出来」，提示词救不回来

`qwen3:4b` 即使带 `think: false`，也一律先输出「首先，用户的问题是…我需要严格依据…
回顾课程资料：[片段1]…」，把规则和片段复述一遍才给答案。
试过四版提示词（追加禁止复述 / 加 system 角色 / 少样本示例 / 温度 0），
**输出几乎一模一样**——问题在模型，不在提示词。

而且复述出来的片段编号会污染引用展示，前端还会把它当正文朗读。

因此最终选了**非思考模式的 instruct 模型** `qwen2.5:3b-instruct`（约 1.9GB 显存）：
qwen2.5 没有 qwen3 的混合思考模式，「直接给答案」是指令型模型的强项。

### D3：`chat()` 的错误处理对齐嵌入路径

`embed_texts` 早就处理了 CUDA OOM（识别关键字 → 给出「先 `ollama stop` 释放显存」
或「换更小的嵌入模型」），但 `chat()` 是裸的 `raise_for_status()`，
Ollama 返回 500 时只抛一句 `500 Server Error: Internal Server Error for url: ...`。

两边口径必须一致：**点名模型 + 说明要多少显存 + 给出处置办法**。

### D4：前端不许把失败说成「没覆盖」

`askRag` 有两条路径：
- 成功且 `answer` 有值 → 正常展示（检索为空时 `answer` 就是「课程资料中未涉及该内容。」）；
- `catch` → 之前一律说「当前知识库未覆盖」。

后者是错的：它把**服务故障**说成了**资料缺失**，用户会去检查上传的资料，方向完全错。
改成「知识库服务暂时不可用」并带上原因即可。

### D5：不改 `/rag/ask` 的失败契约

规格原就规定：外部依赖不可用时返回 HTTP 500 + `detail`，且 MUST NOT 返回
`code=0` 的成功结构、MUST NOT 编造答案。本次**不动这个契约**，
只把 `detail` 变可读、前端不再曲解它。

## Risks / Trade-offs

- **每次问答多 ~9s 冷加载** → 问答低频，可接受；可用 `RAG_OLLAMA_KEEP_ALIVE=120`
  折中（代价是 2 分钟内 6GB 内存仍被占着，主程序可能又会被挤）。
- **`keep_alive=0` 期间并发两个问答** → 各自会触发一次加载，显存不够时其中一个
  仍可能 OOM。此时 `chat()` 的可操作提示会告诉用户「释放显存或改用更小模型」。
