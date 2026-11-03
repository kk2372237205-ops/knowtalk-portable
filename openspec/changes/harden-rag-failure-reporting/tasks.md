# Tasks

## 1. Ollama 占用可回收

- [x] 1.1 `rag_service/config.py` 增加 `OLLAMA_KEEP_ALIVE`（默认 `0`＝用完即卸），
      并在 `build_runtime_config()` 里暴露，便于从 `/rag/config` 核对
- [x] 1.2 `rag_service/ollama_client.py` 的嵌入与生成请求都带上 `keep_alive`
- [x] 1.3 `.env.rag` 增加 `RAG_OLLAMA_KEEP_ALIVE=0` 并写清为什么必须卸
- [x] 1.4 压住上下文与批大小：`RAG_OLLAMA_NUM_CTX=2048` / `RAG_OLLAMA_NUM_BATCH=512`
      （Ollama 在 8GB 卡上默认 num_ctx=4096，KV cache 就要 576MB，
      会与「桌面 + 数字人」一起把显存吃穿 → 权重加载成功但 KV cache 分配失败）
- [x] 1.5 换用非思考模式的 instruct 模型：已拉取 `qwen2.5:3b-instruct`（约 1.9GB 显存），
      `.env.rag` 的 `RAG_LLM_MODEL` 已指向它
      （`qwen3:4b` 即便带 `think:false` 也会先复述规则与片段，
      四版提示词都救不回来——问题在模型不在提示词；复述出来的片段编号还会污染引用展示）
- [x] 1.6 重排模型**开回来**（`RAG_RERANK_TOP_K=3`，跑在 CPU）：
      混合召回的 RRF 融合分几乎是平的（0.0176/0.0176/0.0173/0.0173），正确片段常排第 3~4 位，
      没有重排时小模型会抓错片段答非所问（问「标识符有什么要求」却答「列表可以包含不同类型的元素」）。
      现在生成模型小了、用完即卸，腾得出 1.2GB 内存给重排。
- [x] 1.7 重排输入只取融合分前 12 条（`RERANK_CANDIDATE_LIMIT`）：
      CPU 上逐条编码很慢（20 条约 7s，是延迟大头），正确片段本就在最前面几条，截断几乎不损召回
- [x] 1.8 提示词加严：禁止复述规则、禁止出现「片段N」编号、禁止开场白、禁止推断、
      资料不支持时只回固定那一句；服务端再清理一次模型照抄的兜底句尾巴
      （`rag_service/generator.py`）

## 2. 失败原因可操作

- [x] 2.1 `rag_service/ollama_client.py::chat` 补上错误处理：不可达 / 显存不足 / 超时
      分别给出可操作的中文提示（点名模型、显存需求、处置办法），与嵌入路径同口径
- [x] 2.2 `rag_service/api.py` 的 `detail` 保留可读原因（不得只剩 requests 原始异常）

## 3. 前端不谎报

- [x] 3.1 `web/knowtalk-chat.html::askRag` 的 `catch` 分支改为「知识库服务暂时不可用」
      并带上原因，MUST NOT 再说「知识库未覆盖」
- [x] 3.2 区分「检索为空」（沿用资料未覆盖的固定回答）与「请求失败」两条路径

## 4. 验证

- [x] 4.1 新增 `tests/test_rag_ollama_client.py`：断言嵌入/生成请求都带 `keep_alive`、
      `chat` 在 OOM 时给出可操作提示
- [x] 4.2 新增 JS 守卫：展示页的失败提示 MUST NOT 出现「知识库未覆盖」
- [x] 4.3 新增 `tests/test_rag_answer_quality.py`：提示词约束 + 兜底句噪声清理
- [x] 4.4 端到端（主程序运行、占着 1.2GB 显存时）：
      连续 4 问全部 HTTP 200，答案正确（问「标识符有什么要求」答的是标识符命名规则，
      问「今天天气怎么样」答固定未覆盖句），答完模型已卸载、显存回落到基线，
      主程序不再因内存不足退出
