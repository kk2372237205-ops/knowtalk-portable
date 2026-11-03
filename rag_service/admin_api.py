from pathlib import Path
from threading import Thread
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from .admin_store import (
    create_course,
    create_task,
    delete_course,
    delete_document,
    get_course,
    get_document,
    get_task,
    list_chunks,
    list_courses,
    list_documents,
    list_versions,
    read_document,
    register_document,
    reindex_document,
    restore_version,
    update_course,
    update_document_content,
    update_task,
    update_document_index,
)
from .ingest import index_course, index_file, save_source, save_source_file
from .index_store import store


router = APIRouter(prefix="/rag/admin", tags=["knowledge-base-admin"])


def _error(exc, status=400):
    raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get("/courses")
def courses(keyword: str = ""):
    items = list_courses()
    if keyword:
        value = keyword.lower()
        items = [item for item in items if value in item.get("title", "").lower() or value in item.get("course_id", "").lower()]
    return {"code": 0, "data": items}


@router.post("/courses")
def course_create(payload: dict):
    try:
        course_id = payload.get("course_id", "")
        if not course_id or not __import__("re").fullmatch(r"[A-Za-z0-9_-]+", course_id):
            raise ValueError("课程 ID 只能包含字母、数字、下划线和短横线")
        return {"code": 0, "data": create_course(course_id, payload.get("title", course_id), payload.get("description", ""), payload.get("enabled", True))}
    except Exception as exc:
        _error(exc)


@router.get("/courses/{course_id}")
def course_detail(course_id: str):
    item = get_course(course_id)
    if not item:
        _error(ValueError("课程不存在"), 404)
    return {"code": 0, "data": item}


@router.put("/courses/{course_id}")
def course_update(course_id: str, payload: dict):
    try:
        return {"code": 0, "data": update_course(course_id, payload)}
    except Exception as exc:
        _error(exc)


@router.delete("/courses/{course_id}")
def course_remove(course_id: str):
    try:
        delete_course(course_id)
        return {"code": 0, "data": {"course_id": course_id}}
    except Exception as exc:
        _error(exc)


@router.get("/courses/{course_id}/documents")
def documents(course_id: str, keyword: str = "", status: str = ""):
    return {"code": 0, "data": list_documents(course_id, keyword, status)}


@router.post("/courses/{course_id}/documents/upload")
async def document_upload(
    course_id: str,
    background_tasks: BackgroundTasks,
    course_title: str = Form(""),
    file: UploadFile = File(...),
):
    try:
        filename = file.filename or "source.txt"
        suffix = Path(filename).suffix.lower()
        # 入口拦截：RAG 知识库当前只把 PDF 接入问答；PPT/DOC 等演示库资料请另寻入口。
        if not suffix:
            raise ValueError("上传文件必须带有扩展名")
        if suffix not in {".pdf", ".md", ".txt", ".srt", ".vtt"}:
            raise ValueError(
                f"RAG 知识库只接收 PDF/文本/字幕类资料，{suffix} 请走 PPT 演示库"
            )
        if suffix != ".pdf":
            raise ValueError(
                f"RAG 当前版本只把 PDF 接入问答；{suffix} 请先转 PDF 再上传"
            )
        content = await file.read()
        path = save_source_file(course_id, filename, content)
        document = register_document(course_id, course_title or course_id, filename, path, 0, "uploaded")
        task = create_task("document_upload_index", document_id=document["document_id"], course_id=course_id)
        background_tasks.add_task(_run_upload_task, task["task_id"], document["document_id"], course_id, course_title or course_id, str(path))
        return {"code": 0, "data": {"document": document, "task": task}}
    except Exception as exc:
        _error(exc)


@router.get("/documents/{document_id}")
def document_detail(document_id: str):
    item = get_document(document_id)
    if not item:
        _error(ValueError("文档不存在"), 404)
    return {"code": 0, "data": item}


@router.get("/documents/{document_id}/content")
def document_content(document_id: str):
    try:
        return {"code": 0, "data": {"content": read_document(document_id)}}
    except Exception as exc:
        _error(exc, 404)


@router.put("/documents/{document_id}/content")
def document_content_update(document_id: str, payload: dict):
    try:
        return {"code": 0, "data": update_document_content(
            document_id,
            payload.get("title", ""),
            payload.get("content", ""),
            payload.get("base_version", 0),
            bool(payload.get("reindex", True)),
        )}
    except Exception as exc:
        _error(exc)


@router.delete("/documents/{document_id}")
def document_remove(document_id: str):
    try:
        delete_document(document_id)
        return {"code": 0, "data": {"document_id": document_id}}
    except Exception as exc:
        _error(exc)


@router.get("/documents/{document_id}/download")
def document_download(document_id: str):
    item = get_document(document_id)
    if not item or not Path(item["source_path"]).exists():
        _error(ValueError("原始文件不存在"), 404)
    return FileResponse(item["source_path"], filename=item["file_name"])


@router.get("/documents/{document_id}/preview")
def document_preview(document_id: str):
    """以内嵌方式返回 PDF，供浏览器预览而不是触发下载。"""
    item = get_document(document_id)
    if not item:
        _error(ValueError("文档不存在"), 404)
    path = Path(item["source_path"]).resolve()
    if path.suffix.lower() != ".pdf" or not path.exists():
        _error(ValueError("只有已存在的 PDF 文件支持内嵌预览"), 404)
    return Response(
        content=path.read_bytes(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'inline; filename="document.pdf"; filename*=UTF-8\'\'{quote(path.name)}'
            )
        },
    )


@router.post("/documents/{document_id}/reindex")
def document_reindex(document_id: str, background_tasks: BackgroundTasks):
    try:
        task = create_task("document_reindex", document_id=document_id)
        background_tasks.add_task(_run_document_task, task["task_id"], document_id)
        return {"code": 0, "data": task}
    except Exception as exc:
        _error(exc)


def _run_document_task(task_id, document_id):
    update_task(task_id, status="running", progress=10)
    try:
        reindex_document(document_id)
        update_task(task_id, status="completed", progress=100)
    except Exception as exc:
        update_task(task_id, status="failed", progress=100, error=str(exc))


def _run_upload_task(task_id, document_id, course_id, course_title, path):
    try:
        update_task(task_id, status="running", progress=10, stage="正在解析文档")
        result = index_file(course_id, course_title, Path(path))
        update_document_index(document_id, "indexed", result["chunk_count"])
        update_task(task_id, status="completed", progress=100, stage="索引完成", chunk_count=result["chunk_count"])
    except Exception as exc:
        update_document_index(document_id, "failed", 0, str(exc))
        update_task(task_id, status="failed", progress=100, stage="索引失败", error=str(exc))


@router.get("/documents/{document_id}/versions")
def document_versions(document_id: str):
    return {"code": 0, "data": list_versions(document_id)}


@router.post("/documents/{document_id}/versions/{version}/restore")
def document_restore(document_id: str, version: int):
    try:
        return {"code": 0, "data": restore_version(document_id, version)}
    except Exception as exc:
        _error(exc)


@router.get("/documents/{document_id}/chunks")
def document_chunks(document_id: str):
    try:
        return {"code": 0, "data": list_chunks(document_id)}
    except Exception as exc:
        _error(exc)


@router.get("/tasks/{task_id}")
def task_detail(task_id: str):
    item = get_task(task_id)
    if not item:
        _error(ValueError("任务不存在"), 404)
    return {"code": 0, "data": item}


@router.post("/courses/{course_id}/reindex")
def course_reindex(course_id: str, background_tasks: BackgroundTasks):
    if not get_course(course_id):
        _error(ValueError("课程不存在"), 404)
    task = create_task("course_reindex", course_id=course_id)
    background_tasks.add_task(_run_course_task, task["task_id"], course_id)
    return {"code": 0, "data": task}


def _run_course_task(task_id, course_id):
    update_task(task_id, status="running", progress=10)
    try:
        result = index_course(course_id)
        update_task(task_id, status="completed", progress=100, result=result)
    except Exception as exc:
        update_task(task_id, status="failed", progress=100, error=str(exc))
