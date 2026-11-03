import hashlib
import re
from datetime import datetime, timezone

from transformers import AutoTokenizer

from .config import CHUNK_OVERLAP, CHUNK_SIZE, TOKENIZER_NAME


_tokenizer = None


def tokenizer():
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_NAME, trust_remote_code=True)
    return _tokenizer


def token_count(text: str) -> int:
    return len(tokenizer().encode(text, add_special_tokens=False))


def _split_sentences(text: str):
    return [part.strip() for part in re.split(r"(?<=[。！？!?；;\n])", text) if part.strip()]


def _split_long_text(text: str):
    """仅对超过目标长度的单元素做递归语义切分。"""
    if token_count(text) <= CHUNK_SIZE:
        return [text]
    pieces = _split_sentences(text)
    if len(pieces) <= 1:
        pieces = [part.strip() for part in re.split(r"(?<=[，,、])", text) if part.strip()]
    if len(pieces) <= 1:
        ids = tokenizer().encode(text, add_special_tokens=False)
        return [tokenizer().decode(ids[i:i + CHUNK_SIZE]) for i in range(0, len(ids), CHUNK_SIZE)]

    result = []
    current = ""
    for piece in pieces:
        candidate = f"{current}{piece}" if current else piece
        if current and token_count(candidate) > CHUNK_SIZE:
            result.append(current.strip())
            current = piece
        else:
            current = candidate
    if current.strip():
        result.append(current.strip())
    return result


def _overlap_text(text: str) -> str:
    ids = tokenizer().encode(text, add_special_tokens=False)
    return tokenizer().decode(ids[-CHUNK_OVERLAP:]) if ids else ""


def build_chunks(elements, course_id: str, course_title: str, version: str, source_hash: str):
    chunks = []
    pending_text = ""
    pending_metadata = None
    chunk_index = 0

    def emit(text, metadata):
        nonlocal chunk_index
        text = text.strip()
        if not text:
            return
        chunk_index += 1
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        chunks.append({
            "chunk_id": f"{course_id}-{version}-c{chunk_index:04d}-{digest}",
            "course_id": course_id,
            "course_title": course_title,
            "version": version,
            "text": text,
            "token_count": token_count(text),
            "metadata": {
                **metadata,
                "course_id": course_id,
                "course_title": course_title,
                "version": version,
                "content_hash": source_hash,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        })

    for element in elements:
        pieces = _split_long_text(element["text"])
        for piece in pieces:
            metadata = element["metadata"]
            candidate = f"{pending_text}\n{piece}" if pending_text else piece
            same_context = pending_metadata == metadata
            if pending_text and (not same_context or token_count(candidate) > CHUNK_SIZE):
                emit(pending_text, pending_metadata)
                overlap = _overlap_text(pending_text) if same_context else ""
                pending_text = f"{overlap}\n{piece}".strip() if overlap else piece
                pending_metadata = metadata
            else:
                pending_text = candidate
                pending_metadata = metadata

            if token_count(pending_text) >= CHUNK_SIZE:
                emit(pending_text, pending_metadata)
                overlap = _overlap_text(pending_text)
                pending_text = overlap

    if pending_text.strip():
        emit(pending_text, pending_metadata or {})
    return chunks
