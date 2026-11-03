import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from .config import ensure_dirs, build_runtime_config, RERANK_TOP_K
from .generator import generate_answer
from .ingest import index_course, save_source
from .reranker import rerank
from .retriever import hybrid_retrieve
from .schemas import AskRequest, IndexRequest
from .admin_api import router as admin_router
from .admin_store import list_courses, register_document


ensure_dirs()
app = FastAPI(title="KnowTalk Micro-course RAG", version="1.0.0")
app.include_router(admin_router)


@app.get("/health")
def health():
    return {"code": 0, "data": {"status": "ok"}}


@app.get("/config")
def config():
    """暴露实际生效的检索/切分/重排配置，供前端展示与核对。

    与 ``/rag/ask`` 共用 ``config.build_runtime_config()`` 这一数据源，
    保证接口返回值与实际召回、重排参数一致。
    """
    return {"code": 0, "data": build_runtime_config()}


@app.post("/rag/upload")
async def upload_source(
    course_id: str = Form(..., pattern=r"^[a-zA-Z0-9_-]+$"),
    course_title: str = Form(""),
    file: UploadFile = File(...),
):
    try:
        # RAG 当前只接收 PDF；前端上传一旦混入 PPT/DOC 立刻 400。
        filename = file.filename or "source.txt"
        suffix = Path(filename).suffix.lower()
        if suffix != ".pdf":
            raise ValueError(
                f"RAG 知识库当前只接收 .pdf 文件，{suffix or '该扩展名'} 请走 PPT 演示库"
            )
        content = await file.read()
        result = save_source(
            course_id,
            course_title or course_id,
            filename,
            content,
        )
        register_document(
            course_id,
            course_title or course_id,
            filename,
            result["path"],
            result["chunk_count"],
            "indexed",
        )
        return {"code": 0, "data": result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/rag/index")
def index(req: IndexRequest):
    try:
        return {"code": 0, "data": index_course(req.course_id)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _resolve_course_id(course_id: str | None) -> str:
    """把"没指定课程"落成具体课程。

    端侧菜单按钮、语音提问都不带 course_id，而检索必须按课程分集合查，
    所以这里兜一层：**只要当前恰好有一门已索引且启用的课程，就用它**。
    多门课并存时不猜——猜错课程比报"没命中"更误导人，交给调用方显式指定。
    """
    if course_id:
        return course_id
    try:
        usable = [
            item for item in list_courses()
            if item.get("enabled", True) and (item.get("chunk_count") or 0) > 0
        ]
    except Exception:  # noqa: BLE001  课程元数据坏掉时不要连累问答
        return ""
    return usable[0]["course_id"] if len(usable) == 1 else ""


@app.post("/rag/ask")
def ask(req: AskRequest):
    started = time.perf_counter()
    try:
        course_id = _resolve_course_id(req.course_id)
        if not course_id:
            empty = "课程资料中未涉及该内容。"
            return {"code": 0, "data": {
                "answer": empty, "speak_text": empty, "citations": [],
                "retrieval": {"error": "没有可用的课程（未指定 course_id 且启用课程不唯一）",
                              "latency_ms": round((time.perf_counter() - started) * 1000, 2)},
            }}
        candidates, retrieval = hybrid_retrieve(req.question, course_id, req.top_k)
        retrieval["course_id"] = course_id
        # keep 来自 .env.rag 的 RAG_RERANK_TOP_K：设为 0 时 reranker 直接返回混合召回
        # 结果、不加载重排模型（本机显存/内存都吃紧，重排模型约 1.2GB 会引发 OOM/1455）。
        ranked = rerank(req.question, candidates, keep=RERANK_TOP_K) if candidates else []
        retrieval["pdf_only"] = True
        retrieval["pdf_candidates"] = len(candidates)
        generated = generate_answer(req.question, ranked)
        retrieval.update({
            "rerank_candidates": len(ranked),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        })
        return {"code": 0, "data": {**generated, "retrieval": retrieval}}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
