# KnowTalk RAG 服务

RAG 作为旁路服务独立运行，通过 `http://127.0.0.1:8090` 提供课程资料问答。主服务在启动时会自动拉起它（可用环境变量 `RAG_ENABLED=false` 禁用）。

## 安装

RAG 使用**独立的虚拟环境**，因为它的依赖（`chromadb`、`FlagEmbedding`、`transformers`）与主服务不同。

> **关键**：创建 `.venv-rag` 时使用的 Python 必须与将来运行它的 Python 是同一个版本。用 3.12 建的 venv 却用 3.10 去跑，会在导入 `numpy`、`chromadb`、`pandas` 时报
> `ImportError: No module named 'numpy._core._multiarray_umath'` —— 这是因为 site-packages 里装的是另一个 ABI 的二进制扩展。遇到这个错误就删掉 `.venv-rag` 用同一个解释器重建。

```powershell
# 主服务使用的是 conda 环境 livetalking（Python 3.10）
C:\Users\<你>\.conda\envs\livetalking\python.exe -m venv .venv-rag
.\.venv-rag\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-rag.txt
```

## 模型准备

RAG 需要两组模型：

| 用途 | 模型 | 来源 |
|---|---|---|
| 生成回答 | `qwen3:8b` | Ollama |
| 向量化 | `qwen3-embedding:4b` | Ollama |
| 重排 | `BAAI/bge-reranker-base` | Hugging Face |
| 分片分词 | `Qwen/Qwen3-Embedding-4B`（仅分词器文件） | Hugging Face |

Ollama 的两个模型：

```powershell
ollama pull qwen3:8b
ollama pull qwen3-embedding:4b
```

### Hugging Face 模型：优先放本地目录

`rag_service/config.py` 会先看这两个本地目录，存在就直接用，不存在才回退到 Hugging Face 仓库名：

```text
D:/models/bge-reranker-base
D:/models/Qwen3-Embedding-4B
```

把模型放到这两个目录，并在 `.env.rag` 里把 `RAG_TOKENIZER_NAME`、`RAG_RERANKER_NAME` 指向同样的路径，运行时就**完全不需要外网**。

需要下载的文件（可以跳过 `onnx/model.onnx`，PyTorch 路径用不到它）：

- `D:\models\bge-reranker-base\`：`config.json`、`model.safetensors`、`sentencepiece.bpe.model`、`special_tokens_map.json`、`tokenizer.json`、`tokenizer_config.json`
- `D:\models\Qwen3-Embedding-4B\`：`config.json`、`tokenizer.json`、`tokenizer_config.json`、`vocab.json`、`merges.txt`、`generation_config.json`

### 国内网络下载要点（踩过的坑）

1. **`huggingface.co` 与 `modelscope.cn` 常常无法解析**，用镜像 `https://hf-mirror.com`：
   `.env.rag` 里设 `HF_ENDPOINT=https://hf-mirror.com`。
2. **必须关闭 Xet 后端**：设 `HF_HUB_DISABLE_XET=1`。
   镜像不代理 Xet 的 `cas-server.xethub.hf.co` / `cas-bridge.xethub.hf.co`，不关会得到 `401 Unauthorized` 或长时间卡死。
3. **大文件必须并发分段下载**。hf-mirror 对单连接限速约 50 KB/s，1 GB 的 `model.safetensors` 单连接要跑 6 小时以上；实测并发 8~12 个 range 请求可把聚合速率提到约 1.5 MB/s。
4. **务必给 curl 加读超时**（`--speed-limit` + `--speed-time`）。镜像会静默挂死连接，没有超时的话 curl 会无限等待。

参考命令（把 `model.safetensors` 切成 16 MB 一段、12 段并发）：

```powershell
$url = 'https://hf-mirror.com/BAAI/bge-reranker-base/resolve/main/model.safetensors'
$out = 'D:\models\bge-reranker-base\model.safetensors'
$chunk = 16MB; $parallel = 12
# 1) 先探测总长度：curl -s -L -r 0-0 -D - -o NUL $url  ->  读 Content-Range 的 /<总字节数>
# 2) 按 16MB 切片，最多 12 个 curl 并发：
#    curl -L --fail --retry 8 --retry-all-errors --connect-timeout 20 `
#         --speed-limit 20480 --speed-time 30 --max-time 900 `
#         -r <start>-<end> -s -o <分段文件> $url
# 3) 每段字节数校验通过后按顺序拼接为 $out，最后再校验总字节数
```

小文件（几 MB 以内）直接单连接下载即可：

```powershell
curl.exe -L --fail --retry 5 --connect-timeout 20 `
  -o D:\models\bge-reranker-base\tokenizer.json `
  https://hf-mirror.com/BAAI/bge-reranker-base/resolve/main/tokenizer.json
```

### 下载完成后自检

```powershell
# 分词器
.\.venv-rag\Scripts\python.exe -c "import os; os.environ['HF_HUB_OFFLINE']='1'; import sys; sys.path.insert(0,'.'); from rag_service.chunking import token_count; print(token_count('同比增长率'))"

# 重排模型（首次加载会读权重，约 1 GB）
.\.venv-rag\Scripts\python.exe -c "import os; os.environ['HF_HUB_OFFLINE']='1'; import sys; sys.path.insert(0,'.'); from rag_service.reranker import rerank; print(rerank('什么是同比增长率', [{'text':'同比增长率反映与上年同期的变化速度'},{'text':'今天天气不错'}], keep=1))"
```

第一条能打印出 token 数、第二条能打印出带 `rerank_score` 的结果，就说明两个模型都能离线加载。

## 启动

确保 Ollama 已启动并监听 `http://127.0.0.1:11434`：

```powershell
python -m rag_service.run
```

RAG API 默认地址为 `http://127.0.0.1:8090`，健康检查 `GET /health`。

通常不需要手动启动：主服务 `app.py` 会自动拉起它。主服务日志里若出现 `RAG 服务启动超时`，说明 RAG 冷启动超过了 15 秒的等待窗口，主服务会继续运行，稍后 `/health` 一般就能通。

## 上传课程资料

当前只接受 `.pdf`。上传后自动解析、分片、向量化并增量更新 Chroma 与 BM25；传入其它扩展名会直接返回 400。

> PPT/PPTX 等课件请走 PPT 演示库（`web/ppt-library.html` 与 `/api/materials/upload-ppt`），不要上传到知识库。若希望课件内容进入知识库问答，请先导出为 PDF 再上传。

```powershell
curl.exe -X POST http://127.0.0.1:8090/rag/upload `
  -F "course_id=rail-passenger-traffic" `
  -F "course_title=铁路数据管理与客流量分析" `
  -F "file=@D:\课程资料\铁路数据管理与客流量分析.pdf"
```

## 测试问答

```powershell
curl.exe -X POST http://127.0.0.1:8090/rag/ask `
  -H "Content-Type: application/json" `
  -d "{\"question\":\"为什么要计算同比增长率？\",\"course_id\":\"rail-passenger-traffic\"}"
```

知识库为空时，回答会是固定的「课程资料中未涉及该内容。」——这说明链路正常，只是还没有入库资料。

问答页默认使用 `rail-passenger-traffic`。多课程可以在 URL 中指定：

```text
/knowtalk-chat.html?course_id=another-course
```

关闭 RAG 并回退原有问答：

```text
/knowtalk-chat.html?rag=0
```

## 配置项

环境变量写在仓库根目录的 `.env.rag`（已被 `.gitignore` 忽略）。

**推荐改用设置页配置**：<http://127.0.0.1:8010/settings.html> 的「知识库（RAG）」分组可以直接修改下表中的参数，不必编辑文件。这些参数由主服务在重启 RAG 时以环境变量注入子进程，**设置页保存的值优先于 `.env.rag`**。

> RAG 参数在 RAG 进程**模块导入期**被绑定为常量，因此修改后不会立即生效，需要在设置页点「重启 RAG 服务」。
> 一条限制：只有**由主服务自己启动**的 sidecar 才能被代为重启。如果本机 8090 已经被你手工启动的实例占用，主服务会跳过启动且不持有该进程，此时设置页上的重启按钮会被禁用并提示你自行重启。

常用项：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RAG_ENABLED` | `true` | 主服务是否自动拉起 RAG |
| `RAG_BASE_URL` | `http://127.0.0.1:8090` | RAG 服务地址 |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama 地址 |
| `RAG_LLM_MODEL` | `qwen3:8b` | 生成模型 |
| `RAG_EMBED_MODEL` | `qwen3-embedding:4b` | 向量化模型 |
| `RAG_TOKENIZER_NAME` | 本地目录优先 | 分词器路径 |
| `RAG_RERANKER_NAME` | 本地目录优先 | 重排模型路径 |
| `HF_ENDPOINT` | 无 | Hugging Face 镜像，国内建议 `https://hf-mirror.com` |
| `HF_HUB_DISABLE_XET` | 无 | 设为 `1` 关闭 Xet，国内镜像必需 |
| `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` | `420` / `55` | 分片长度与重叠（token） |
| `RAG_VECTOR_TOP_K` / `RAG_BM25_TOP_K` | `10` / `10` | 向量与 BM25 各自召回条数 |
| `RAG_VECTOR_THRESHOLD` | `0.55` | 向量召回相似度阈值 |

> 说明：`/rag/ask` 的重排保留条数目前是固定值 3，`RAG_RERANK_TOP_K` 尚未对它生效（见 `docs/audit/` 的记录）。
