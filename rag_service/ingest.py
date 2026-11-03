import hashlib
import json
from pathlib import Path

from .chunking import build_chunks
from .config import SOURCE_DIR
from .parsers import parse_document
from .index_store import store


# RAG 可以"保存到磁盘"的合法扩展名（解析器还会进一步拦截 PPT/DOC）。
SUPPORTED = {".pdf", ".md", ".txt", ".srt", ".vtt"}

# 真正进入 RAG 索引并参与问答的扩展名白名单；其余仅作为扩展位留用。
ALLOWED_FOR_INDEX = {".pdf"}


def file_hash(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def index_file(course_id: str, course_title: str, path: Path):
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_FOR_INDEX:
        raise ValueError(
            f"RAG 当前版本只把 PDF 接入问答；{suffix or '该扩展名'} 文件请先转 PDF 再上传"
        )
    source_hash = file_hash(path)
    previous = store.manifest.get(course_id, {}).get("sources", {}).get(path.name, {})
    if previous.get("content_hash") == source_hash:
        return {"changed": False, "chunk_count": previous.get("chunk_count", 0)}
    elements = parse_document(path)
    chunks = build_chunks(elements, course_id, course_title, "v1", source_hash)
    count = store.upsert_source(course_id, path.name, chunks, source_hash)
    return {"changed": True, "chunk_count": count}


def save_source(course_id: str, course_title: str, filename: str, content: bytes):
    target = save_source_file(course_id, filename, content)
    result = index_file(course_id, course_title or course_id, target)
    return {"source_file": target.name, "path": str(target), **result}


def save_source_file(course_id: str, filename: str, content: bytes):
    """只保存文件，索引由管理接口在后台执行。

    RAG 知识库只接收 PDF；上传管理接口会先做白名单拦截，这里再保一道。
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(
            f"RAG 知识库只接收 PDF/文本/字幕类资料，{suffix} 请走 PPT 演示库"
        )
    if suffix not in ALLOWED_FOR_INDEX:
        raise ValueError(
            f"RAG 当前版本只把 PDF 接入问答；{suffix} 文件请先转 PDF 再上传"
        )
    target_dir = SOURCE_DIR / course_id
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    target = target_dir / safe_name
    target.write_bytes(content)
    return target


def index_course(course_id: str, course_title: str | None = None):
    """重建/补建课程索引。

    只处理真正能进 RAG 问答的扩展名（ALLOWED_FOR_INDEX）；
    其他扩展名文件留在原目录中，但不会被建立向量索引。
    """
    source_dir = SOURCE_DIR / course_id
    if not source_dir.exists():
        return {"course_id": course_id, "files": 0, "chunks": 0}
    total = 0
    changed = 0
    skipped = 0
    for path in sorted(source_dir.iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in ALLOWED_FOR_INDEX:
            result = index_file(course_id, course_title or course_id, path)
            total += result["chunk_count"]
            changed += int(result["changed"])
        elif suffix in SUPPORTED:
            skipped += 1
    return {"course_id": course_id, "files": changed, "chunks": total, "skipped": skipped}
