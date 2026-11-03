import json
import logging
import pickle
import re
from pathlib import Path

import chromadb
import jieba
from rank_bm25 import BM25Okapi

from .config import BM25_FILE, CHROMA_DIR, MANIFEST_FILE, ensure_dirs
from .ollama_client import embed_texts


def _collection_name(course_id):
    return "course_" + re.sub(r"[^a-zA-Z0-9_-]", "_", course_id)[:50]


logger = logging.getLogger("rag.index_store")


class IndexStore:
    def __init__(self):
        ensure_dirs()
        self.client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self.bm25_data = self._load_pickle(BM25_FILE, {})
        self.manifest = self._load_json(MANIFEST_FILE, {})
        # 启动时把残留的非 PDF 来源（PPT/DOC 等）从内存和 Chroma 中清掉，
        # 避免旧索引污染问答；不清原文件，下次重建时自动只走 PDF。
        self._purge_non_pdf()

    @staticmethod
    def _load_json(path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    @staticmethod
    def _load_pickle(path: Path, default):
        if not path.exists():
            return default
        try:
            with path.open("rb") as file:
                return pickle.load(file)
        except (OSError, pickle.PickleError, EOFError):
            return default

    def _persist(self):
        BM25_FILE.parent.mkdir(parents=True, exist_ok=True)
        BM25_FILE.write_bytes(pickle.dumps(self.bm25_data))
        MANIFEST_FILE.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _chroma_metadata(metadata):
        """Chroma metadata 只接受标量，将结构化字段转成 JSON 字符串。"""
        result = {}
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                result[key] = value
            else:
                result[key] = json.dumps(value, ensure_ascii=False)
        return result

    def collection(self, course_id):
        return self.client.get_or_create_collection(
            name=_collection_name(course_id),
            metadata={
                "hnsw:space": "cosine",
                "hnsw:M": 32,
                "hnsw:construction_ef": 200,
            },
        )

    def rebuild_course(self, course_id, chunks, source_files):
        collection = self.collection(course_id)
        old = collection.get()
        if old.get("ids"):
            collection.delete(ids=old["ids"])

        if chunks:
            vectors = embed_texts([item["text"] for item in chunks])
            collection.upsert(
                ids=[item["chunk_id"] for item in chunks],
                documents=[item["text"] for item in chunks],
                embeddings=vectors,
                metadatas=[self._chroma_metadata(item["metadata"]) for item in chunks],
            )

        tokens = [list(jieba.cut(item["text"])) for item in chunks]
        self.bm25_data[course_id] = {
            "chunks": chunks,
            "tokens": tokens,
            "bm25": BM25Okapi(tokens) if tokens else None,
        }
        self.manifest[course_id] = {"source_files": source_files, "chunk_count": len(chunks)}
        self._persist()
        return len(chunks)

    def upsert_source(self, course_id, source_file, chunks, source_hash):
        """只删除并重写当前来源文件，其他来源的向量不动。"""
        collection = self.collection(course_id)
        old = collection.get(where={"source_file": source_file})
        if old.get("ids"):
            collection.delete(ids=old["ids"])
        if chunks:
            vectors = embed_texts([item["text"] for item in chunks])
            collection.upsert(
                ids=[item["chunk_id"] for item in chunks],
                documents=[item["text"] for item in chunks],
                embeddings=vectors,
                metadatas=[self._chroma_metadata(item["metadata"]) for item in chunks],
            )

        all_items = collection.get()
        all_chunks = []
        for chunk_id, text, metadata in zip(
            all_items.get("ids", []),
            all_items.get("documents", []),
            all_items.get("metadatas", []),
        ):
            all_chunks.append({
                "chunk_id": chunk_id,
                "text": text,
                "metadata": metadata or {},
            })
        tokens = [list(jieba.cut(item["text"])) for item in all_chunks]
        self.bm25_data[course_id] = {
            "chunks": all_chunks,
            "tokens": tokens,
            "bm25": BM25Okapi(tokens) if tokens else None,
        }
        course_manifest = self.manifest.setdefault(course_id, {"sources": {}})
        course_manifest.setdefault("sources", {})[source_file] = {
            "content_hash": source_hash,
            "chunk_count": len(chunks),
        }
        course_manifest["chunk_count"] = len(all_chunks)
        self._persist()
        return len(chunks)

    def remove_source(self, course_id, source_file):
        """删除一个来源文件的向量，并重建该课程的 BM25 快照。"""
        collection = self.collection(course_id)
        old = collection.get(where={"source_file": source_file})
        if old.get("ids"):
            collection.delete(ids=old["ids"])
        all_items = collection.get()
        all_chunks = []
        for chunk_id, text, metadata in zip(
            all_items.get("ids", []),
            all_items.get("documents", []),
            all_items.get("metadatas", []),
        ):
            all_chunks.append({"chunk_id": chunk_id, "text": text, "metadata": metadata or {}})
        tokens = [list(jieba.cut(item["text"])) for item in all_chunks]
        self.bm25_data[course_id] = {
            "chunks": all_chunks,
            "tokens": tokens,
            "bm25": BM25Okapi(tokens) if tokens else None,
        }
        self._persist()

    def vector_search(self, course_id, vector, limit):
        result = self.collection(course_id).query(query_embeddings=[vector], n_results=limit)
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits = []
        for chunk_id, text, metadata, distance in zip(ids, documents, metadatas, distances):
            hits.append({
                "chunk_id": chunk_id,
                "text": text,
                "metadata": metadata or {},
                "score": max(0.0, 1.0 - float(distance)),
            })
        for hit in hits:
            if isinstance(hit["metadata"].get("heading_path"), str):
                try:
                    hit["metadata"]["heading_path"] = json.loads(hit["metadata"]["heading_path"])
                except ValueError:
                    pass
        return hits

    def bm25_search(self, course_id, query, limit):
        data = self.bm25_data.get(course_id) or {}
        engine = data.get("bm25")
        chunks = data.get("chunks") or []
        if engine is None or not chunks:
            return []
        scores = engine.get_scores(list(jieba.cut(query)))
        indices = [
            index for index in sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
            if scores[index] > 0
        ][:limit]
        return [
            {
                "chunk_id": chunks[index]["chunk_id"],
                "text": chunks[index]["text"],
                "metadata": chunks[index]["metadata"],
                "score": float(scores[index]),
            }
            for index in indices
        ]

    def course_has_data(self, course_id):
        data = self.bm25_data.get(course_id) or {}
        return bool(data.get("chunks"))

    # --- 启动清洗：把历史索引里的非 PDF chunk 全部驱逐 --------------------

    @staticmethod
    def _is_pdf_hit(hit):
        src = str((hit or {}).get("metadata", {}).get("source_file", "")).lower()
        return src.endswith(".pdf")

    def _purge_chroma_non_pdf(self, course_id):
        """把指定课程 collection 里的非 PDF chunk 从 Chroma 中删除，返回删除数量。"""
        try:
            collection = self.collection(course_id)
        except Exception as exc:
            logger.warning("清洗跳过 %s：collection 不可用 (%s)", course_id, exc)
            return 0
        raw = collection.get()
        ids = raw.get("ids") or []
        metadatas = raw.get("metadatas") or []
        bad_ids = []
        for chunk_id, metadata in zip(ids, metadatas):
            source = str((metadata or {}).get("source_file", "")).lower()
            if not source.endswith(".pdf"):
                bad_ids.append(chunk_id)
        if bad_ids:
            collection.delete(ids=bad_ids)
        return len(bad_ids)

    def _purge_non_pdf(self):
        purged_chunks = 0
        purged_sources = 0
        for course_id in list(self.bm25_data.keys()):
            data = self.bm25_data[course_id]
            chunks = list(data.get("chunks") or [])
            keep_chunks = [item for item in chunks if self._is_pdf_hit(item)]
            if len(keep_chunks) == len(chunks) and chunks:
                # 内存里已经干净；为防 chroma 残留，仍然尝试刷一次（极少命中）
                removed = self._purge_chroma_non_pdf(course_id)
                if removed:
                    purged_chunks += removed
                continue
            purged_chunks += len(chunks) - len(keep_chunks)

            removed_chroma = self._purge_chroma_non_pdf(course_id)
            # 上面那一步可能没计到（chunks 已干净但 chroma 仍有残余）
            if removed_chroma and len(keep_chunks) == len(chunks):
                purged_chunks += removed_chroma

            if keep_chunks:
                tokens = [list(jieba.cut(item["text"])) for item in keep_chunks]
                data["chunks"] = keep_chunks
                data["tokens"] = tokens
                data["bm25"] = BM25Okapi(tokens) if tokens else None
            else:
                self.bm25_data.pop(course_id, None)

            # 同步把 manifest 里的非 PDF 文件名清掉
            course_manifest = self.manifest.get(course_id)
            if isinstance(course_manifest, dict):
                sources = course_manifest.get("sources") or {}
                if isinstance(sources, dict):
                    for fname in list(sources.keys()):
                        if not str(fname).lower().endswith(".pdf"):
                            sources.pop(fname, None)
                            purged_sources += 1
                course_manifest["chunk_count"] = len(keep_chunks)
                # 上游老版本用的是 source_files 列表，顺便清理（如果存在）
                source_files = course_manifest.get("source_files")
                if isinstance(source_files, list):
                    course_manifest["source_files"] = [
                        item for item in source_files if str(item).lower().endswith(".pdf")
                    ]

        if purged_chunks or purged_sources:
            logger.warning(
                "[IndexStore] 启动清洗：驱逐 %d 条非 PDF chunk、%d 个非 PDF 来源（PPT/DOC 等）",
                purged_chunks, purged_sources,
            )
            self._persist()


store = IndexStore()
