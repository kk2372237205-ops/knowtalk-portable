# -*- coding: utf-8 -*-
"""动作制作 API：预设列表、提交作业、查作业。

路径刻意不放在 `/api/avatars/{avatar_id}/...` 下面——那里已经有
`/gestures/{gesture_id}` 的 PUT/DELETE 与 `/preview`，再挂一层 `make`
容易和 `{gesture_id}` 通配撞车。形象写在请求体里更省事。
"""

from aiohttp import web

from server.routes import json_ok, json_error
from utils import gesture_make, gesture_presets, minimax_video
from utils.logger import logger


async def list_presets(request):
    """GET /api/gestures/presets — 预设动作 + 是否已配置密钥。"""
    try:
        return json_ok(data={
            'presets': gesture_presets.presets_payload(),
            'default_id': gesture_presets.DEFAULT_PRESET_ID,
            'configured': minimax_video.is_configured(),
            'face_size': gesture_make.FACE_SIZE,
        })
    except Exception as exc:  # noqa: BLE001
        logger.exception('list_presets error')
        return json_error(str(exc))


async def submit(request):
    """POST /api/gestures/make — 提交一个制作作业。

    体：{avatar_id, preset_id?, name?, prompt?, duration?, resolution?}
    """
    try:
        params = await request.json()
    except Exception:  # noqa: BLE001
        return json_error('请求体不是合法 JSON')
    avatar_id = str(params.get('avatar_id') or '').strip()
    if not avatar_id:
        return json_error('avatar_id 不能为空')
    try:
        job = gesture_make.start(
            avatar_id,
            preset_id=(params.get('preset_id') or '').strip() or None,
            name=params.get('name'),
            prompt=params.get('prompt'),
            duration=params.get('duration'),
            resolution=params.get('resolution'),
        )
    except minimax_video.MinimaxNotConfigured as exc:
        return json_error(str(exc))
    except gesture_make.GestureMakeBusy as exc:
        # 409 语义上更准，但前端按 code 判断，这里保持 body 结构一致
        return web.Response(
            content_type='application/json', status=409,
            text='{"code": -1, "msg": %s}' % _json_str(str(exc)))
    except gesture_make.GestureMakeError as exc:
        return json_error(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception('gesture make submit error')
        return json_error(str(exc))
    return json_ok(data={'job': job})


async def job_status(request):
    """GET /api/gestures/make/{job_id} — 查单个作业。"""
    job_id = request.match_info.get('job_id', '')
    job = gesture_make.get_job(job_id)
    if not job:
        return json_error('作业不存在：%s' % job_id)
    return json_ok(data={'job': job})


async def avatar_jobs(request):
    """GET /api/gestures/make?avatar_id=xxx — 该形象进行中的作业与最近几条。"""
    avatar_id = (request.query.get('avatar_id') or '').strip()
    if not avatar_id:
        return json_error('avatar_id 不能为空')
    return json_ok(data={
        'active': gesture_make.active_job(avatar_id),
        'recent': gesture_make.recent_jobs(avatar_id),
    })


def _json_str(text):
    import json
    return json.dumps(text, ensure_ascii=False)


def setup_gesture_make_routes(app):
    app.router.add_get('/api/gestures/presets', list_presets)
    app.router.add_get('/api/gestures/make', avatar_jobs)
    app.router.add_post('/api/gestures/make', submit)
    app.router.add_get('/api/gestures/make/{job_id}', job_status)
