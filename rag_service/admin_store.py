import json
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import COURSES_FILE, DOCUMENTS_FILE, SOURCE_DIR, TASKS_FILE, VERSIONS_FILE, ensure_dirs
from .ingest import SUPPORTED, file_hash, index_file
from .index_store import _collection_name, store


_lock = threading.RLock()


def now():
    return datetime.now(timezone.utc).isoformat()


def _load(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _data():
    ensure_dirs()
    return _load(COURSES_FILE), _load(DOCUMENTS_FILE), _load(VERSIONS_FILE), _load(TASKS_FILE)


def _persist(courses, documents, versions, tasks):
    _save(COURSES_FILE, courses)
    _save(DOCUMENTS_FILE, documents)
    _save(VERSIONS_FILE, versions)
    _save(TASKS_FILE, tasks)


def _document_id(course_id, filename):
    return "doc-" + str(uuid.uuid5(uuid.NAMESPACE_URL, course_id + "/" + filename))


def _source_path(course_id, filename):
    return (SOURCE_DIR / course_id / Path(filename).name).resolve()


def _sync_legacy_sources(courses, documents, versions):
    """为之前通过 /rag/upload 写入的文件补建管理元数据。"""
    for course_dir in SOURCE_DIR.iterdir() if SOURCE_DIR.exists() else []:
        if not course_dir.is_dir():
            continue
        course_id = course_dir.name
        course = courses.setdefault(course_id, {
            "course_id": course_id,
            "title": course_id,
            "description": "",
            "enabled": True,
            "document_ids": [],
            "created_at": now(),
            "updated_at": now(),
        })
        for path in course_dir.iterdir():
            if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                continue
            document_id = _document_id(course_id, path.name)
            digest = file_hash(path)
            document = documents.setdefault(document_id, {
                "document_id": document_id,
                "course_id": course_id,
                "title": path.stem,
                "file_name": path.name,
                "source_path": str(path),
                "source_type": path.suffix.lower().lstrip("."),
                "size": path.stat().st_size,
                "content_hash": digest,
                "current_version": 1,
                "index_status": "indexed" if store.course_has_data(course_id) else "uploaded",
                "chunk_count": len([
                    item for item in (store.bm25_data.get(course_id) or {}).get("chunks", [])
                    if item.get("metadata", {}).get("source_file") == path.name
                ]),
                "last_error": None,
                "created_at": now(),
                "updated_at": now(),
            })
            if document_id not in course.setdefault("document_ids", []):
                course["document_ids"].append(document_id)
            versions.setdefault(document_id, {}).setdefault("1", {
                "version": 1,
                "content_hash": digest,
                "source_file": path.name,
                "source_path": str(path),
                "created_at": document["created_at"],
                "index_status": document["index_status"],
                "chunk_count": document.get("chunk_count", 0),
                "error": None,
            })
    return courses, documents, versions


def list_courses():
    with _lock:
        courses, documents, versions, tasks = _data()
        courses, documents, versions = _sync_legacy_sources(courses, documents, versions)
        for course in courses.values():
            docs = [documents[item] for item in course.get("document_ids", []) if item in documents]
            course["document_count"] = len(docs)
            course["chunk_count"] = sum(item.get("chunk_count", 0) for item in docs)
            course["index_status"] = _course_status(docs)
        _persist(courses, documents, versions, tasks)
        return list(courses.values())


def _course_status(documents):
    if not documents:
        return "empty"
    if any(item.get("index_status") == "failed" for item in documents):
        return "failed"
    if any(item.get("index_status") not in {"indexed", "deleted"} for item in documents):
        return "processing"
    return "indexed"


def get_course(course_id):
    return next((item for item in list_courses() if item["course_id"] == course_id), None)


def create_course(course_id, title, description="", enabled=True):
    with _lock:
        courses, documents, versions, tasks = _data()
        courses, documents, versions = _sync_legacy_sources(courses, documents, versions)
        if course_id in courses:
            raise ValueError("课程 ID 已存在")
        courses[course_id] = {
            "course_id": course_id, "title": title or course_id,
            "description": description, "enabled": enabled,
            "document_ids": [], "created_at": now(), "updated_at": now(),
        }
        _persist(courses, documents, versions, tasks)
        return courses[course_id]


def update_course(course_id, values):
    with _lock:
        courses, documents, versions, tasks = _data()
        courses, documents, versions = _sync_legacy_sources(courses, documents, versions)
        if course_id not in courses:
            raise ValueError("课程不存在")
        for key in ("title", "description", "enabled"):
            if key in values:
                courses[course_id][key] = values[key]
        courses[course_id]["updated_at"] = now()
        _persist(courses, documents, versions, tasks)
        return courses[course_id]


def delete_course(course_id):
    with _lock:
        courses, documents, versions, tasks = _data()
        courses, documents, versions = _sync_legacy_sources(courses, documents, versions)
        course = courses.pop(course_id, None)
        if course is None:
            raise ValueError("课程不存在")
        for document_id in course.get("document_ids", []):
            documents.pop(document_id, None)
            versions.pop(document_id, None)
        source_dir = (SOURCE_DIR / course_id).resolve()
        if source_dir.exists() and SOURCE_DIR.resolve() in source_dir.parents:
            shutil.rmtree(source_dir)
        try:
            store.client.delete_collection(_collection_name(course_id))
        except Exception:
            pass
        store.bm25_data.pop(course_id, None)
        store.manifest.pop(course_id, None)
        store._persist()
        _persist(courses, documents, versions, tasks)


def list_documents(course_id, keyword="", status=""):
    with _lock:
        courses, documents, versions, tasks = _data()
        courses, documents, versions = _sync_legacy_sources(courses, documents, versions)
        result = [item for item in documents.values() if item.get("course_id") == course_id]
        if keyword:
            result = [item for item in result if keyword.lower() in item.get("file_name", "").lower() or keyword.lower() in item.get("title", "").lower()]
        if status:
            result = [item for item in result if item.get("index_status") == status]
        _persist(courses, documents, versions, tasks)
        return sorted(result, key=lambda item: item.get("updated_at", ""), reverse=True)


def get_document(document_id):
    with _lock:
        courses, documents, versions, tasks = _data()
        courses, documents, versions = _sync_legacy_sources(courses, documents, versions)
        _persist(courses, documents, versions, tasks)
        return documents.get(document_id)


def register_document(course_id, course_title, filename, path, chunk_count=0, index_status="indexed"):
    with _lock:
        courses, documents, versions, tasks = _data()
        courses, documents, versions = _sync_legacy_sources(courses, documents, versions)
        courses.setdefault(course_id, {
            "course_id": course_id, "title": course_title or course_id,
            "description": "", "enabled": True, "document_ids": [],
            "created_at": now(), "updated_at": now(),
        })
        if course_title and courses[course_id].get("title") in {None, "", course_id}:
            courses[course_id]["title"] = course_title
            courses[course_id]["updated_at"] = now()
        document_id = _document_id(course_id, filename)
        path = Path(path).resolve()
        digest = file_hash(path)
        previous = documents.get(document_id)
        version = (previous or {}).get("current_version", 0) + (1 if previous and previous.get("content_hash") != digest else 0)
        document = {
            "document_id": document_id, "course_id": course_id,
            "title": (previous or {}).get("title", Path(filename).stem),
            "file_name": Path(filename).name, "source_path": str(path),
            "source_type": path.suffix.lower().lstrip("."), "size": path.stat().st_size,
            "content_hash": digest, "current_version": max(version, 1),
            "index_status": index_status, "chunk_count": chunk_count,
            "last_error": None, "created_at": (previous or {}).get("created_at", now()), "updated_at": now(),
        }
        documents[document_id] = document
        if document_id not in courses[course_id].setdefault("document_ids", []):
            courses[course_id]["document_ids"].append(document_id)
        versions.setdefault(document_id, {})[str(document["current_version"])] = {
            "version": document["current_version"], "content_hash": digest,
            "source_file": document["file_name"], "source_path": str(path),
            "created_at": now(), "index_status": index_status,
            "chunk_count": chunk_count, "error": None,
        }
        _persist(courses, documents, versions, tasks)
        return document


def delete_document(document_id):
    with _lock:
        document = get_document(document_id)
        if not document:
            raise ValueError("文档不存在")
        courses, documents, versions, tasks = _data()
        for version in (versions.get(document_id) or {}).values():
            path = Path(version.get("source_path", "")).resolve()
            if path.exists() and SOURCE_DIR.resolve() in path.parents and path.is_file():
                path.unlink()
        path = Path(document["source_path"]).resolve()
        if path.exists() and SOURCE_DIR.resolve() in path.parents and path.is_file():
            path.unlink()
        documents.pop(document_id, None)
        versions.pop(document_id, None)
        course = courses.get(document["course_id"])
        if course:
            course["document_ids"] = [item for item in course.get("document_ids", []) if item != document_id]
            course["updated_at"] = now()
        remaining = [item for item in documents.values() if item.get("course_id") == document["course_id"]]
        store.rebuild_course(document["course_id"], _chunks_for_sources(remaining), {})
        _persist(courses, documents, versions, tasks)


def _chunks_for_sources(documents):
    result = []
    for document in documents:
        path = Path(document["source_path"])
        if not path.exists():
            continue
        elements = __import__("rag_service.parsers", fromlist=["parse_document"]).parse_document(path)
        result.extend(__import__("rag_service.chunking", fromlist=["build_chunks"]).build_chunks(elements, document["course_id"], document["course_id"], "v1", document["content_hash"]))
    return result


def read_document(document_id):
    document = get_document(document_id)
    if not document:
        raise ValueError("文档不存在")
    path = Path(document["source_path"])
    if not path.exists():
        raise ValueError("原始文件不存在")
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".srt", ".vtt"}:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    from .parsers import parse_document
    return "\n\n".join(item["text"] for item in parse_document(path))


def update_document_content(document_id, title, content, base_version, reindex=True):
    with _lock:
        document = get_document(document_id)
        if not document:
            raise ValueError("文档不存在")
        if int(base_version) != int(document["current_version"]):
            raise ValueError("文档版本已变化，请刷新后再保存")
        course_id = document["course_id"]
        version = int(document["current_version"]) + 1
        target = SOURCE_DIR / course_id / "versions" / f"{Path(document['file_name']).stem}.v{version}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        status = "uploaded"
        count = 0
        error = None
        try:
            if not reindex:
                raise _SkipIndex()
            old_source_name = document["file_name"]
            store.remove_source(course_id, old_source_name)
            result = index_file(course_id, title or document["title"], target)
            count = result["chunk_count"]
            status = "indexed"
        except _SkipIndex:
            status = "stale"
        except Exception as exc:
            error = str(exc)
            status = "failed"
        document.update({
            "title": title or document["title"], "file_name": target.name,
            "source_path": str(target.resolve()), "source_type": "md",
            "size": target.stat().st_size, "content_hash": file_hash(target),
            "current_version": version, "index_status": status,
            "chunk_count": count, "last_error": error, "updated_at": now(),
        })
        courses, documents, versions, tasks = _data()
        documents[document_id] = document
        versions.setdefault(document_id, {})[str(version)] = {
            "version": version, "content_hash": document["content_hash"],
            "source_file": target.name, "source_path": str(target.resolve()),
            "created_at": now(), "index_status": status,
            "chunk_count": count, "error": error,
        }
        _persist(courses, documents, versions, tasks)
        return document


class _SkipIndex(Exception):
    pass


def list_versions(document_id):
    with _lock:
        _, _, versions, _ = _data()
        return sorted((versions.get(document_id) or {}).values(), key=lambda item: item["version"], reverse=True)


def restore_version(document_id, version):
    with _lock:
        document = get_document(document_id)
        if not document:
            raise ValueError("文档不存在")
        versions = list_versions(document_id)
        source = next((item for item in versions if int(item["version"]) == int(version)), None)
        if not source:
            raise ValueError("版本不存在")
        source_path = Path(source["source_path"])
        if not source_path.exists():
            raise ValueError("版本文件不存在")
        if source_path.suffix.lower() in {".md", ".txt", ".srt", ".vtt"}:
            content = source_path.read_text(encoding="utf-8-sig", errors="replace")
        else:
            from .parsers import parse_document
            content = "\n\n".join(item["text"] for item in parse_document(source_path))
        return update_document_content(
            document_id,
            document["title"],
            content,
            document["current_version"],
            reindex=True,
        )


def reindex_document(document_id):
    with _lock:
        document = get_document(document_id)
        if not document:
            raise ValueError("文档不存在")
        path = Path(document["source_path"])
        if not path.exists():
            raise ValueError("原始文件不存在")
        result = index_file(document["course_id"], document["title"], path)
        courses, documents, versions, tasks = _data()
        documents[document_id].update({
            "index_status": "indexed",
            "chunk_count": result["chunk_count"],
            "last_error": None,
            "updated_at": now(),
        })
        _persist(courses, documents, versions, tasks)
        return documents[document_id]


def list_chunks(document_id):
    document = get_document(document_id)
    if not document:
        raise ValueError("文档不存在")
    data = store.bm25_data.get(document["course_id"]) or {}
    return [item for item in data.get("chunks", []) if item.get("metadata", {}).get("source_file") in {document["file_name"], Path(document["source_path"]).name}]


def create_task(kind, document_id=None, course_id=None):
    with _lock:
        courses, documents, versions, tasks = _data()
        task_id = "task-" + uuid.uuid4().hex[:12]
        tasks[task_id] = {"task_id": task_id, "kind": kind, "document_id": document_id, "course_id": course_id, "status": "queued", "progress": 0, "error": None, "created_at": now(), "updated_at": now()}
        _persist(courses, documents, versions, tasks)
        return tasks[task_id]


def update_task(task_id, **values):
    with _lock:
        courses, documents, versions, tasks = _data()
        if task_id not in tasks:
            return None
        tasks[task_id].update(values, updated_at=now())
        _persist(courses, documents, versions, tasks)
        return tasks[task_id]


def get_task(task_id):
    with _lock:
        *_, tasks = _data()
        return tasks.get(task_id)


def update_document_index(document_id, status, chunk_count=0, error=None):
    with _lock:
        courses, documents, versions, tasks = _data()
        if document_id not in documents:
            return None
        documents[document_id].update({"index_status": status, "chunk_count": chunk_count, "last_error": error, "updated_at": now()})
        _persist(courses, documents, versions, tasks)
        return documents[document_id]
