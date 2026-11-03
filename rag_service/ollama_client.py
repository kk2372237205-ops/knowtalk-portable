import time

import requests

from .config import (
    OLLAMA_BASE_URL,
    OLLAMA_EMBED_MODEL,
    OLLAMA_KEEP_ALIVE,
    OLLAMA_LLM_MODEL,
    OLLAMA_NUM_BATCH,
    OLLAMA_NUM_CTX,
    REQUEST_TIMEOUT,
)


# Ollama 永远跑在本地回环地址（127.0.0.1），必须显式绕开任何 HTTP(S) 代理，
# 否则一旦终端环境里设了 HTTP_PROXY，requests 会把 localhost 请求也转给代理，
# 导致 502 / 连接失败。这里统一在该请求上关闭代理。
PROXIES = {"http": None, "https": None}


# 单次 /api/embed 最多携带的文本条数。一次把整本教材的所有分块都塞进同一个请求，
# 既容易在推理阶段把显存撑爆（4B 嵌入模型并行处理长序列时），也容易触发代理超时。
# 分批发送既能压低峰值显存，又能避免单个超大请求卡死。
EMBED_BATCH = 64

# 显存不足相关关键字：Ollama 在 CUDA 显存不够时会在 /api/embed 返回 500，
# 错误信息里通常带这些词。这类失败重试没用（同环境下再加载同样会 OOM），
# 应当直接给出可操作的提示，而不是反复重试把时间耗完。
_OOM_MARKERS = (
    "out of memory",
    "cudamalloc failed",
    "failed to allocate",
    "unable to allocate",
    "ggml_backend",
)


def _looks_like_oom(detail: str) -> bool:
    d = (detail or "").lower()
    return any(m in d for m in _OOM_MARKERS)


def _keep_alive_value():
    """把配置里的 keep_alive 规整成 Ollama 接受的形式。

    纯数字按秒（int）传，0 表示「用完即卸」；其余按 Ollama 的时长串透传（如 "5m"）。
    """
    raw = str(OLLAMA_KEEP_ALIVE or "0").strip()
    try:
        return int(raw)
    except ValueError:
        return raw


def _error_detail(response) -> str:
    """从 Ollama 的错误响应里取出可读原因。"""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("error") or payload.get("detail") or "")
    except Exception:  # noqa: BLE001
        pass
    return (getattr(response, "text", "") or "")[:300]


def _embed_batch(texts: list[str], attempts: int = 2) -> list[list[float]]:
    url = f"{OLLAMA_BASE_URL}/api/embed"
    # keep_alive：嵌入模型只在这一次调用里需要，用完即卸，别让它占着显存/内存
    # 与生成模型抢资源（本机 8GB 显存 + 16GB 内存，挤不下）。
    # num_gpu=0：嵌入模型强制跑在 CPU 上，完全不占显存。
    # 本机只有 8GB 显存，且数字人(wav2lip)与生成模型(qwen2.5:3b)长期共用这张卡，
    # 4B 嵌入模型若上 GPU 很容易在加载 KV cache 时 CUDA OOM。嵌入只发生在「查询时
    # 对单句向量化」，CPU 上耗时很短（秒级），换来「无论开不开 Photoshop 等占显存
    # 程序」都不会 OOM，比靠手动清显存稳得多。
    payload = {"model": OLLAMA_EMBED_MODEL, "input": texts,
               "keep_alive": _keep_alive_value(),
               "options": {"num_gpu": 0}}
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT, proxies=PROXIES)
            response.raise_for_status()
            data = response.json()
            embeddings = data.get("embeddings")
            if not embeddings or len(embeddings) != len(texts):
                raise RuntimeError(f"Ollama embedding 返回异常: {data}")
            return embeddings
        except requests.HTTPError as exc:  # noqa: PERF203
            detail = ""
            try:
                detail = response.json().get("error", "")
            except Exception:
                detail = (response.text or "")[:300]
            status = response.status_code
            # 显存不足：重试无意义，立即给出可操作提示。
            if status >= 500 and _looks_like_oom(detail):
                raise RuntimeError(
                    "Ollama 向量化失败（显存不足 / CUDA OOM）：嵌入模型 "
                    f"{OLLAMA_EMBED_MODEL} 加载时需要约 2.5GB 显存，但当前 GPU 已被其它模型"
                    f"（通常是 {OLLAMA_LLM_MODEL}）占用，分配失败。\n"
                    "解决：① 上传前先释放显存——在 Ollama 所在机器执行 "
                    f"`ollama stop {OLLAMA_LLM_MODEL}`（或重启 Ollama），再重新上传；"
                    "② 或将 .env.rag 的 RAG_EMBED_MODEL 换成更小的模型"
                    "（如 nomic-embed-text，约 0.1GB），避免与 8B 争显存。\n"
                    f"原始错误：{detail} ｜ {url}"
                )
            last_exc = RuntimeError(
                f"Ollama 向量化失败（HTTP {status}）：{detail} ｜ {url}"
            )
            # 其它 5xx 多为模型冷加载，重试一次（递增退避）。
            if status >= 500 and attempt < attempts:
                time.sleep(1.5 * attempt)
                continue
            raise last_exc

    raise last_exc


def embed_texts(texts: list[str], attempts: int = 2) -> list[list[float]]:
    """向量化一批文本，返回与输入等长的 embeddings 列表。

    常见失败与对策：
    * **CUDA 显存不足（OOM）**：嵌入模型（默认 qwen3-embedding:4b，约 2.5GB 显存）与
      已驻留的生成模型（qwen3-gpu:8b，约 5.2GB 显存）争用同一张卡，加载时
      ``cudaMalloc failed: out of memory``。这种失败**重试无效**，应释放显存或换小模型。
    * 其它 5xx（模型冷加载等）：按批次重试一次通常能成功。
    """
    if not texts:
        return []
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        all_embeddings.extend(_embed_batch(texts[i:i + EMBED_BATCH], attempts=attempts))
    return all_embeddings


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]


def chat(messages: list[dict], temperature=0.3, top_p=0.8, max_tokens=800) -> str:
    """调用 Ollama 生成回答。

    失败必须给出**可操作**的中文提示（点名模型、原因、处置办法），与嵌入路径同口径：
    之前这里是裸的 raise_for_status()，Ollama 返回 500 时只抛一句
    "500 Server Error: Internal Server Error for url: ..."，既不知道是哪个依赖、
    也不知道该怎么救——上层只能原样透出，用户看到的是"请求失败"这种废话。
    """
    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": OLLAMA_LLM_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        # keep_alive：生成模型答完就卸，别长期霸占显存与内存。
        "keep_alive": _keep_alive_value(),
        "options": {
            "temperature": temperature,
            "top_p": top_p,
            "num_predict": max_tokens,
            # 上下文与批大小必须压住：Ollama 在 8GB 卡上默认 num_ctx=4096，
            # KV cache 要 576MB，加上计算缓冲就与「桌面 + 数字人」抢爆显存。
            "num_ctx": OLLAMA_NUM_CTX,
            "num_batch": OLLAMA_NUM_BATCH,
        },
    }

    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT, proxies=PROXIES)
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Ollama 不可达（生成模型 {OLLAMA_LLM_MODEL}）：{exc}\n"
            f"解决：确认 Ollama 已启动（默认地址 {OLLAMA_BASE_URL}）；"
            "若进程已退出，重新执行 `ollama serve` 后再试。"
        ) from exc

    if not response.ok:
        detail = _error_detail(response)
        if _looks_like_oom(detail):
            raise RuntimeError(
                "Ollama 生成失败（显存不足 / CUDA OOM）：生成模型 "
                f"{OLLAMA_LLM_MODEL} 需要的显存超过当前可用显存。"
                "本机是 8GB 显卡的笔记本，桌面（浏览器/PS/微信等）与数字人 wav2lip "
                "本身就在占用显存，加载模型时申请连续显存失败。\n"
                "解决：① 释放显存——执行 `ollama stop " + OLLAMA_LLM_MODEL + "` 后重试，"
                "或关掉占显存的桌面程序；② 暂停数字人会话（释放 wav2lip 占用的显存）再问答；"
                "③ 把 .env.rag 的 RAG_LLM_MODEL 换成更小的模型"
                "（如 qwen3:1.7b），并把 RAG_OLLAMA_NUM_CTX 调小。\n"
                f"原始错误：{detail} ｜ {url}"
            ) from RuntimeError(detail)
        raise RuntimeError(
            f"Ollama 生成失败（HTTP {response.status_code}）：{detail} ｜ {url}"
        ) from RuntimeError(detail)

    data = response.json()
    message = data.get("message") or {}
    return message.get("content", "").strip()
