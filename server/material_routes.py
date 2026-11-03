###############################################################################
#  素材管理 API 路由 — 上传/列表/幻灯片/当前状态
###############################################################################

import json
from aiohttp import web
from server.routes import json_ok, json_error
from utils import material_manager as mm
from utils.logger import logger


# ─── 幻灯片 CRUD ──────────────────────────────────────────────────────

async def slides_list(request):
    """GET /api/slides — 获取所有幻灯片"""
    try:
        return json_ok(data=mm.list_slides())
    except Exception as e:
        logger.exception("slides_list error")
        return json_error(str(e))


async def slides_add(request):
    """POST /api/slides — 新增幻灯片"""
    try:
        params = await request.json()
        name = params.get("name", "")
        image = params.get("image", "")
        keywords = params.get("keywords", [])
        ppt_id = params.get("ppt_id", "")
        ppt_name = params.get("ppt_name", "")
        if not name:
            return json_error("name 不能为空")
        slide = mm.add_slide(name, image, keywords, ppt_id, ppt_name)
        return json_ok(data=slide)
    except Exception as e:
        logger.exception("slides_add error")
        return json_error(str(e))


async def slides_update(request):
    """PUT /api/slides/{id} — 更新幻灯片"""
    try:
        slide_id = int(request.match_info["id"])
        params = await request.json()
        slide = mm.update_slide(slide_id, params.get("name"), params.get("keywords"), params.get("ppt_name"))
        if slide is None:
            return json_error("幻灯片不存在")
        return json_ok(data=slide)
    except Exception as e:
        logger.exception("slides_update error")
        return json_error(str(e))


async def slides_delete(request):
    """DELETE /api/slides/{id} — 删除单个幻灯片"""
    try:
        slide_id = int(request.match_info["id"])
        if mm.delete_slide(slide_id):
            return json_ok()
        return json_error("幻灯片不存在")
    except Exception as e:
        logger.exception("slides_delete error")
        return json_error(str(e))


async def slides_clear_all(request):
    """DELETE /api/slides — 清除所有幻灯片"""
    try:
        count = mm.clear_all_slides()
        return json_ok(data={"deleted": count})
    except Exception as e:
        logger.exception("slides_clear_all error")
        return json_error(str(e))


async def slides_batch_update(request):
    """PUT /api/slides/batch — 批量更新幻灯片"""
    try:
        params = await request.json()
        updates = params.get("updates", [])
        if not updates:
            return json_error("updates 不能为空")
        count = mm.update_slides_batch(updates)
        return json_ok(data={"updated": count, "slides": mm.list_slides()})
    except Exception as e:
        logger.exception("slides_batch_update error")
        return json_error(str(e))


# ─── 素材上传 ─────────────────────────────────────────────────────────

async def upload_image(request):
    """POST /api/materials/upload-image — 上传图片"""
    try:
        form = await request.post()
        fileobj = form.get("file")
        if not fileobj:
            return json_error("未收到文件")
        filename = fileobj.filename or "image.png"
        filebytes = fileobj.file.read()
        path = mm.upload_image(filebytes, filename)
        return json_ok(data={"path": path})
    except Exception as e:
        logger.exception("upload_image error")
        return json_error(str(e))


async def upload_ppt(request):
    """POST /api/materials/upload-ppt — 上传 PPT 并转图片"""
    try:
        form = await request.post()
        fileobj = form.get("file")
        if not fileobj:
            return json_error("未收到文件")
        filename = fileobj.filename or "slides.pptx"
        filebytes = fileobj.file.read()
        paths = mm.upload_ppt(filebytes, filename)
        return json_ok(data={"pages": paths})
    except web.HTTPRequestEntityTooLarge:
        limit_mb = request.app._client_max_size / (1024 * 1024)
        return json_error(f"PPT 文件超过上传上限（当前上限 {limit_mb:.0f}MB），可设置环境变量 UPLOAD_MAX_MB 调整", 413)
    except ImportError as e:
        return json_error("缺少 python-pptx / Pillow 依赖，请运行: pip install python-pptx Pillow")
    except Exception as e:
        logger.exception("upload_ppt error")
        return json_error(str(e))


# ─── 当前幻灯片状态（前端轮询用）─────────────────────────────────────

async def current_slide(request):
    """GET /api/current_slide?sessionid=xxx — 获取当前会话的幻灯片 ID"""
    try:
        sessionid = request.query.get("sessionid", "0")
        slide_id = mm.get_current_slide(sessionid)
        slide = mm.get_slide(slide_id)
        return json_ok(data={
            "slide_id": slide_id,
            "slide": slide,
        })
    except Exception as e:
        logger.exception("current_slide error")
        return json_error(str(e))


def setup_material_routes(app: web.Application):
    """注册素材相关路由到 aiohttp app"""
    app.router.add_get("/api/slides", slides_list)
    app.router.add_post("/api/slides", slides_add)
    app.router.add_route("PUT", "/api/slides/{id}", slides_update)
    app.router.add_route("PUT", "/api/slides/batch", slides_batch_update)
    app.router.add_route("DELETE", "/api/slides/{id}", slides_delete)
    app.router.add_route("DELETE", "/api/slides", slides_clear_all)
    app.router.add_post("/api/materials/upload-image", upload_image)
    app.router.add_post("/api/materials/upload-ppt", upload_ppt)
    app.router.add_get("/api/current_slide", current_slide)
    # 静态文件服务：素材图片
    app.router.add_static("/materials/images", path="data/materials/images")
