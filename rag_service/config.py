import os
from pathlib import Path

# 用项目自带的零依赖 .env 加载器，避免对 pip python-dotenv 的硬依赖，
# 也让配置模块在测试环境（无 rag 专用依赖）里也能直接导入。
from utils.dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / ".env.rag")
DATA_DIR = Path(os.getenv("RAG_DATA_DIR", str(ROOT / "data")))
SOURCE_DIR = DATA_DIR / "sources"
CHROMA_DIR = DATA_DIR / "chroma"
BM25_FILE = DATA_DIR / "bm25.pkl"
MANIFEST_FILE = DATA_DIR / "manifest.json"
METADATA_DIR = DATA_DIR / "metadata"
COURSES_FILE = METADATA_DIR / "courses.json"
DOCUMENTS_FILE = METADATA_DIR / "documents.json"
VERSIONS_FILE = METADATA_DIR / "versions.json"
TASKS_FILE = METADATA_DIR / "tasks.json"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_LLM_MODEL = os.getenv("RAG_LLM_MODEL", "qwen3:8b")
OLLAMA_EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding:4b")
# Ollama 的 keep_alive：模型在本次调用后驻留多久。
# 默认 "0" = 用完即卸。本机是 16GB 内存 + 8GB 显存，8B 模型一加载就占 6.2GB 内存 /
# 4.6GB 显存，会把主程序（torch + wav2lip）与侧车挤死（实测主程序 import 时
# MemoryError 退出、生成模型 cudaMalloc 失败 → /rag/ask 500）。用完即卸后，
# 模型只在真正回答问题的那十几秒里占资源。
# 可设为秒数（如 "120"）或 Ollama 时长串（如 "5m"）。
OLLAMA_KEEP_ALIVE = os.getenv("RAG_OLLAMA_KEEP_ALIVE", "0").strip()
# 上下文长度与批大小：Ollama 在 8GB 卡上默认 num_ctx=4096，KV cache 就要 576MB，
# 再加计算缓冲，和「桌面（笔记本，桌面自己也吃显存）+ 数字人 wav2lip」一起
# 就会把显存吃穿（实测 qwen3:4b 权重加载成功、KV cache 分配失败）。
# 压到模型原生上下文 2048 / 批 512 后稳过。改成更小的模型时这两个值还要再降。
OLLAMA_NUM_CTX = int(os.getenv("RAG_OLLAMA_NUM_CTX", "2048"))
OLLAMA_NUM_BATCH = int(os.getenv("RAG_OLLAMA_NUM_BATCH", "512"))
DEFAULT_TOKENIZER = Path("D:/models/Qwen3-Embedding-4B")
TOKENIZER_NAME = os.getenv(
    "RAG_TOKENIZER_NAME",
    str(DEFAULT_TOKENIZER) if DEFAULT_TOKENIZER.exists() else "Qwen/Qwen3-Embedding-4B",
)
DEFAULT_RERANKER = Path("D:/models/bge-reranker-base")
RERANKER_NAME = os.getenv(
    "RAG_RERANKER_NAME",
    str(DEFAULT_RERANKER) if DEFAULT_RERANKER.exists() else "BAAI/bge-reranker-base",
)

CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "420"))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "55"))
VECTOR_TOP_K = int(os.getenv("RAG_VECTOR_TOP_K", "10"))
BM25_TOP_K = int(os.getenv("RAG_BM25_TOP_K", "10"))
RERANK_TOP_K = int(os.getenv("RAG_RERANK_TOP_K", "3"))
VECTOR_THRESHOLD = float(os.getenv("RAG_VECTOR_THRESHOLD", "0.55"))
REQUEST_TIMEOUT = float(os.getenv("RAG_REQUEST_TIMEOUT", "120"))


def ensure_dirs():
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_DIR.mkdir(parents=True, exist_ok=True)


def build_runtime_config():
    """把当前实际生效的检索/切分/重排配置装配成可暴露的结构。

    这是「知识库技术配置可经接口查询」的单一数据源——接口与前端都从这里读，
    不重复写死，保证 ``GET /rag/config`` 返回的值与 ``/rag/ask`` 实际使用的参数一致。
    """
    return {
        "vector_store": {
            "type": "chromadb + bm25 hybrid",
            "description": "稠密向量（Chroma 持久化）与稀疏 BM25 双路召回，再融合",
            "distance_metric": "cosine",
            "hnsw": {"M": 32, "construction_ef": 200},
            "bm25_tokenizer": "jieba",
        },
        "embedding": {
            "model": OLLAMA_EMBED_MODEL,
            "base_url": OLLAMA_BASE_URL,
            "tokenizer": TOKENIZER_NAME,
            "provider": "ollama",
            "keep_alive": OLLAMA_KEEP_ALIVE,
        },
        "llm": {
            "model": OLLAMA_LLM_MODEL,
            "base_url": OLLAMA_BASE_URL,
            "provider": "ollama",
            "keep_alive": OLLAMA_KEEP_ALIVE,
        },
        "chunking": {
            "method": "jieba 中文分词 + 递归语义切分 + 滑动重叠",
            "chunk_size": CHUNK_SIZE,
            "chunk_overlap": CHUNK_OVERLAP,
            "tokenizer": TOKENIZER_NAME,
            "note": "按句切分，超长递归到逗号/分词；相邻块保留尾部 token 重叠",
        },
        "reranker": {
            "model": RERANKER_NAME,
            "keep": RERANK_TOP_K,
            "normalize": True,
        },
        "retrieval": {
            "vector_top_k": VECTOR_TOP_K,
            "bm25_top_k": BM25_TOP_K,
            "vector_threshold": VECTOR_THRESHOLD,
            "fusion": "reciprocal rank fusion (1/(60+rank) 累加)",
            "final_candidates": 20,
        },
        "source": {
            "accepted": [".pdf"],
            "note": "当前仅 PDF 接入问答；PPT/DOC 等演示库资料走另一条入口",
        },
    }

