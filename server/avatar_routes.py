import os
import json
import uuid
import re
import shutil
from pathlib import Path
from aiohttp import web
from server.task_manager import task_manager
from server.blocking import run_blocking
from utils import gestures as gesture_utils
from utils import path_guard
from utils import media_type
from utils.logger import logger
from utils.net_guard import (
    NetGuardError,
    parse_allowed_hosts,
    validate_notify_url,
)

DATA_DIR = os.path.abspath('./data/avatars')
TMP_DIR = os.path.abspath('./data/tmp')
# 手势预览动画的缓存目录：可再生数据，可随时清空。
GESTURE_PREVIEW_DIR = os.path.abspath('./data/tmp/gesture_preview')
AVATAR_ID_PATTERN = r'[A-Za-z0-9_.-]+'

# 纯点段（"."、".."、"..." 以及任何只由点组成的取值）必须单独拒绝。
# 原因：Windows 会剥掉路径末段的结尾点，`Path(DATA_DIR, '...').resolve()` 于是
# 等于 DATA_DIR 本身——配合 shutil.rmtree 就是"一次请求清空整个形象库"。
# 注意判定必须是"整段只由点组成"，不能写成"含点即拒"，否则会误伤 Mr.Chen 这类
# 完全正常的标识。
_DOTS_ONLY_RE = re.compile(r'^\.+$')


class AvatarIdError(ValueError):
    """avatar_id 不合法，或解析后没有严格落在形象素材根目录之内。"""


def resolve_avatar_dir(avatar_id):
    """把 ``avatar_id`` 解析为形象目录，并保证它**严格位于** ``DATA_DIR`` 之内。

    不合法时抛 :class:`AvatarIdError`。四个以 ``{avatar_id}`` 寻址的接口共用本函数，
    避免出现"某一处漏校验"的漂移。

    第二道防线（包含性校验）是故意重复的：即使将来有人把正则改回宽松取值，
    纯点段与越界路径仍然会被拦住。
    """
    if not avatar_id or not re.fullmatch(AVATAR_ID_PATTERN, avatar_id):
        raise AvatarIdError('invalid avatar id')
    if _DOTS_ONLY_RE.match(avatar_id):
        raise AvatarIdError('invalid avatar id')
    root = os.path.realpath(DATA_DIR)
    target = os.path.realpath(os.path.join(root, avatar_id))
    # 不允许等于根目录本身（纯点段的解析结果就是它），也不允许落在根之外。
    if os.path.normcase(target) == os.path.normcase(root) or not path_guard.is_within(root, target):
        raise AvatarIdError('invalid avatar id')
    return Path(target)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 允许作为 video_path 来源的根目录。缺省只允许项目自己的 data/ —— 上传落盘
# （data/tmp）与形象素材（data/avatars）是仅有的两个合法来源。
VIDEO_ROOTS_ENV = 'AVATAR_VIDEO_ROOTS'

# 允许接收任务状态回调的主机。缺省为空 = 拒绝一切 notifyurl，必须显式登记。
NOTIFY_HOSTS_ENV = 'AVATAR_NOTIFY_ALLOWED_HOSTS'


def _server_data_roots():
    """服务自己管理的 ``data/`` 目录（形象素材与上传落盘都在其下）。

    这些目录**永远是**允许根，理由有两条：
    1. 服务必须接受它刚刚自己写出的上传文件，否则一旦配置了允许根，上传功能就整体失效；
    2. 相对路径 ``video_path`` 会被解析到 ``data/avatars``，它不能因为允许根的配置而失效。
    """
    roots = []
    for candidate in (os.path.join(PROJECT_ROOT, 'data'),
                      os.path.dirname(os.path.abspath(DATA_DIR)),
                      os.path.dirname(os.path.abspath(TMP_DIR))):
        if candidate not in roots:
            roots.append(candidate)
    return roots


def allowed_video_roots(env_value=None):
    """允许的 ``video_path`` 根目录 = 服务自己的 data/ **加上** ``AVATAR_VIDEO_ROOTS``。

    配置值是**追加**而非替换：运维设置该变量的意图是"再允许一个素材目录"，
    不是"关掉上传与形象素材"。未配置时就是服务自己的 data/ 目录。
    """
    if env_value is None:
        env_value = os.environ.get(VIDEO_ROOTS_ENV, '')
    roots = _server_data_roots()
    for root in (item.strip() for item in str(env_value or '').split(',')):
        if not root:
            continue
        if not os.path.isabs(root):
            # 相对路径按项目根解析，便于写成 ./data 这种形式。
            root = os.path.join(PROJECT_ROOT, root)
        root = os.path.abspath(root)
        if root not in roots:
            roots.append(root)
    return roots


def allowed_notify_hosts(env_value=None):
    """解析 ``AVATAR_NOTIFY_ALLOWED_HOSTS``；未配置时返回空列表（拒绝一切）。"""
    if env_value is None:
        env_value = os.environ.get(NOTIFY_HOSTS_ENV, '')
    try:
        return parse_allowed_hosts(env_value)
    except NetGuardError as exc:
        logger.error("[头像任务] %s，本次按拒绝一切回调处理", exc)
        return ()

# 当前运行的模型，由 app.py 在解析完启动参数后写入。
# None 表示尚未确定（例如单测直接调用）——此时任一素材形态齐备即视为可用。
ACTIVE_MODEL = None


def _avatar_material_ready(full: str) -> bool:
    """形象素材是否齐备，且与当前运行的模型匹配。

    - wav2lip：`coords.pkl` + 含 `.png` 的 `face_imgs/`
    - musetalk：`coords.pkl` + `latents.pt` + `mask_coords.pkl` + 含 `.png` 的 `mask/`

    形态不匹配时判为不完整：前端只把 `ready !== false` 的形象放进下拉框，
    否则在 musetalk 下会选中只有 wav2lip 素材的形象，一连就崩。
    """
    if not os.path.exists(os.path.join(full, 'coords.pkl')):
        return False
    face_dir = Path(full, 'face_imgs')
    has_face = face_dir.is_dir() and any(face_dir.glob('*.png'))
    mask_dir = Path(full, 'mask')
    has_musetalk = (
        os.path.exists(os.path.join(full, 'latents.pt'))
        and os.path.exists(os.path.join(full, 'mask_coords.pkl'))
        and mask_dir.is_dir()
        and any(mask_dir.glob('*.png'))
    )
    if ACTIVE_MODEL == 'wav2lip':
        return has_face
    if ACTIVE_MODEL == 'musetalk':
        return has_musetalk
    return has_face or has_musetalk


async def list_avatars(request):
    """GET /api/avatars — 获取可用的 Avatar ID 列表"""
    try:
        avatars = []
        if os.path.isdir(DATA_DIR):
            for name in sorted(os.listdir(DATA_DIR)):
                full = os.path.join(DATA_DIR, name)
                if os.path.isdir(full) and not name.startswith('.'):
                    full_imgs = Path(full, 'full_imgs')
                    image_files = [
                        candidate for candidate in sorted(full_imgs.glob('*'))
                        if candidate.suffix.lower() in {'.png', '.jpg', '.jpeg'}
                    ]
                    if image_files:
                        ready = _avatar_material_ready(full)
                        avatars.append({
                            'id': name,
                            'name': name,
                            'thumbnail': f"/api/avatars/{name}/thumbnail",
                            'ready': ready,
                            'status': 'ready' if ready else 'incomplete',
                        })
        return json_ok(data=avatars)
    except Exception as e:
        logger.exception("list_avatars error")
        return json_error(str(e))


async def avatar_thumbnail(request):
    try:
        avatar_dir = resolve_avatar_dir(request.match_info.get('avatar_id', ''))
    except AvatarIdError:
        return json_error('invalid avatar id', 400)
    candidates = sorted(path for path in (avatar_dir / 'full_imgs').glob('*') if path.suffix.lower() in {'.png', '.jpg', '.jpeg'})
    if not candidates:
        return json_error('thumbnail not found', 404)
    return web.FileResponse(candidates[0])


async def list_avatar_gestures(request):
    """GET /api/avatars/{avatar_id}/gestures — 该形象下可用的手势（供前端插入标签）

    非法 `avatar_id` 必须在读取任何路径之前被拒绝；形象存在但没有手势时返回空集合
    而不是错误——"没有手势"是正常状态，不是故障。
    """
    try:
        avatar_dir = resolve_avatar_dir(request.match_info.get('avatar_id', ''))
    except AvatarIdError:
        return json_error('invalid avatar id', 400)
    if not avatar_dir.is_dir():
        return json_error('Avatar 不存在', 404)
    try:
        gestures = gesture_utils.list_gestures(
            request.match_info.get('avatar_id', ''), data_dir=DATA_DIR)
        return json_ok(data={'gestures': gestures})
    except Exception as e:
        logger.exception('list_avatar_gestures error')
        return json_error(str(e))


async def prepare_gesture_assets(request):
    """POST /api/avatars/{avatar_id}/gestures/{gesture_id}/prepare

    为手势补齐 MuseTalk 侧资产——**形象自己的一份**人脸 latent 与遮罩（动作只贡献
    身体与姿态，人脸恒由形象驱动，否则动作期间脸型会跟着生成素材漂移）。
    幂等：已就绪直接返回。旧版逐帧资产会被自动迁移成这一份共享资产。
    补齐是纯文件拷贝，很快，但仍放线程池以免阻塞事件循环。
    """
    avatar_id = request.match_info.get('avatar_id', '')
    gesture_id = request.match_info.get('gesture_id', '')
    try:
        avatar_dir = resolve_avatar_dir(avatar_id)
    except AvatarIdError:
        return json_error('invalid avatar id', 400)
    if not avatar_dir.is_dir():
        return json_error('Avatar 不存在', 404)
    if not gesture_utils.is_valid_gesture_id(gesture_id):
        return json_error('invalid gesture id', 400)

    try:
        result = await run_blocking(
            gesture_utils.build_muse_assets, avatar_id, gesture_id, data_dir=DATA_DIR)
    except gesture_utils.GestureError as exc:
        return json_error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        logger.exception('prepare_gesture_assets error')
        return json_error(str(exc))
    return json_ok(data=result)


async def gesture_preview(request):
    """GET /api/avatars/{avatar_id}/gestures/{gesture_id}/preview — 循环动画 WebP。

    首次请求生成并缓存，素材变化后按指纹重建。缓存是再生数据，落在 data/tmp 下。
    """
    avatar_id = request.match_info.get('avatar_id', '')
    gesture_id = request.match_info.get('gesture_id', '')
    try:
        resolve_avatar_dir(avatar_id)
    except AvatarIdError:
        return json_error('invalid avatar id', 400)
    try:
        path = gesture_utils.gesture_preview_file(
            avatar_id, gesture_id, GESTURE_PREVIEW_DIR, data_dir=DATA_DIR)
    except gesture_utils.GestureNotFound as exc:
        return json_error(str(exc), 404)
    except gesture_utils.GestureError as exc:
        return json_error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001 - 生成失败要给出可读原因
        logger.exception('gesture_preview error')
        return json_error('预览生成失败：%s' % exc)
    # .webp 在部分平台的 mimetypes 里没有登记，显式指定避免回落到 octet-stream
    return web.FileResponse(path, headers={'Content-Type': 'image/webp'})


async def rename_gesture(request):
    """PUT /api/avatars/{avatar_id}/gestures/{gesture_id} — 改显示名。"""
    avatar_id = request.match_info.get('avatar_id', '')
    gesture_id = request.match_info.get('gesture_id', '')
    try:
        resolve_avatar_dir(avatar_id)
    except AvatarIdError:
        return json_error('invalid avatar id', 400)
    try:
        payload = await request.json()
    except Exception:
        return json_error('请求体必须是合法 JSON', 400)
    if not isinstance(payload, dict):
        return json_error('请求体必须是 JSON 对象', 400)
    try:
        label = gesture_utils.set_gesture_label(
            avatar_id, gesture_id, payload.get('label', ''), data_dir=DATA_DIR)
    except gesture_utils.GestureNotFound as exc:
        return json_error(str(exc), 404)
    except gesture_utils.GestureError as exc:
        return json_error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        logger.exception('rename_gesture error')
        return json_error(str(exc))
    return json_ok(data={'id': gesture_id, 'label': label})


async def delete_gesture(request):
    """DELETE /api/avatars/{avatar_id}/gestures/{gesture_id} — 删除整套手势素材。"""
    avatar_id = request.match_info.get('avatar_id', '')
    gesture_id = request.match_info.get('gesture_id', '')
    try:
        resolve_avatar_dir(avatar_id)
    except AvatarIdError:
        return json_error('invalid avatar id', 400)
    try:
        gesture_utils.delete_gesture(avatar_id, gesture_id, data_dir=DATA_DIR)
    except gesture_utils.GestureNotFound as exc:
        return json_error(str(exc), 404)
    except gesture_utils.GestureError as exc:
        return json_error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        logger.exception('delete_gesture error')
        return json_error(str(exc))
    # 素材没了，预览缓存也没有意义；清理失败不影响删除结果
    gesture_utils.clear_preview_cache(GESTURE_PREVIEW_DIR, avatar_id, gesture_id)
    return json_ok(data={'deleted': gesture_id})


async def rename_avatar(request):
    old_id = request.match_info.get('avatar_id', '')
    try:
        payload = await request.json()
        new_id = str(payload.get('new_id', '')).strip()
        try:
            old_dir = resolve_avatar_dir(old_id)
            new_dir = resolve_avatar_dir(new_id)
        except AvatarIdError:
            return json_error('Avatar ID 只能包含字母、数字、下划线和短横线', 400)
        if not old_dir.is_dir():
            return json_error('Avatar 不存在', 404)
        if new_dir.exists():
            return json_error('新的 Avatar ID 已存在', 409)
        old_dir.rename(new_dir)
        # avator_info.json 里也记着 avatar_id，不同步的话新目录名和内部记录会对不上
        info_path = new_dir / 'avator_info.json'
        if info_path.is_file():
            try:
                info = json.loads(info_path.read_text(encoding='utf-8'))
                if isinstance(info, dict):
                    info['avatar_id'] = new_id
                    info.pop('display_name', None)
                    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2),
                                         encoding='utf-8')
            except Exception:  # noqa: BLE001 - 记录同步失败不该让改名失败
                logger.warning('[形象库] 改名后 avator_info.json 同步失败: %s', new_id)
        return json_ok(data={'old_id': old_id, 'new_id': new_id})
    except Exception as e:
        logger.exception('rename_avatar error')
        return json_error(str(e))


async def delete_avatar(request):
    avatar_id = request.match_info.get('avatar_id', '')
    try:
        avatar_dir = resolve_avatar_dir(avatar_id)
    except AvatarIdError:
        logger.warning("[形象库] 拒绝非法或越界的 avatar_id: %r", avatar_id)
        return json_error('invalid avatar id', 400)
    if not avatar_dir.is_dir():
        return json_error('Avatar 不存在', 404)
    try:
        shutil.rmtree(avatar_dir)
        return json_ok(data={'deleted_id': avatar_id})
    except Exception as e:
        logger.exception('delete_avatar error')
        return json_error(str(e))

def json_ok(data=None):
    body = {"code": 0, "msg": "ok"}
    if data is not None:
        body["data"] = data
    return web.Response(
        content_type="application/json",
        text=json.dumps(body),
    )

def json_error(msg: str, code: int = -1):
    return web.Response(
        content_type="application/json",
        text=json.dumps({"code": code, "msg": str(msg)}),
    )

async def create_avatar_task(request):
    """
    POST /api/avatar/task
    Parameters: model, avatar_id, video_file (upload), video_path (local), ...
    """
    try:
        if request.content_type == 'multipart/form-data':
            reader = await request.multipart()
            params = {}
            video_path = None

            while True:
                part = await reader.next()
                if part is None:
                    break

                if part.name == 'video_file':
                    # 原始文件名由请求方控制，直接拼进落盘路径可以逐级上跳。
                    filename = path_guard.sanitize_filename(part.filename, 'video.mp4')
                    temp_dir = TMP_DIR
                    os.makedirs(temp_dir, exist_ok=True)
                    video_path = os.path.join(temp_dir, f"{uuid.uuid4()}_{filename}")
                    with open(video_path, 'wb') as f:
                        while True:
                            chunk = await part.read_chunk()
                            if not chunk:
                                break
                            f.write(chunk)
                else:
                    value = await part.text()
                    params[part.name] = value

            if video_path:
                # 生成任务现在既收视频也收图片：拒绝其它类型（PDF/PPT/音频等），
                # 避免把不相关文件当成素材源喂给 genavatar。仅校验「上传」分支——
                # JSON 分支的 video_path 可能是本地目录（musetalk 支持传目录），
                # 那里不按扩展名判定，沿用原有路径语义。
                if not media_type.is_media_file(video_path):
                    logger.warning("[头像任务] 拒绝非音视频图片素材: %r", params.get('video_file') or filename)
                    return json_error("素材必须是视频或图片（video/* 或 image/*）")
                params['video_path'] = video_path
        else:
            params = await request.json()

        model_type = params.get('model')
        avatar_id = params.get('avatar_id')

        if not model_type or not avatar_id:
            return json_error("model and avatar_id are required")

        if 'video_path' not in params:
            return json_error("video_file 或 video_path 是必填的（支持视频或图片）")

        data_path = './data/avatars'

        video_path = params['video_path']
        if not os.path.isabs(video_path):
            video_path = os.path.join(data_path, video_path)

        # 绝对路径原先被原样接受，等于"读任意本地文件"；改为先校验必须落在允许根内。
        # 校验通过后仍沿用原有的路径语义（相对路径保持 ./data/avatars/<name> 形式），
        # 只加一道闸门，不改下游拿到的取值。
        try:
            path_guard.resolve_within_any(allowed_video_roots(), video_path)
        except path_guard.PathGuardError:
            logger.warning("[头像任务] 拒绝越界 video_path: %r", params['video_path'])
            return json_error(
                "video_path 必须位于允许目录内"
                "（可用环境变量 {0} 配置，缺省为项目 data/ 目录）".format(VIDEO_ROOTS_ENV)
            )

        save_path = data_path

        task_params = {
            "video_path": video_path,
            "save_path": save_path,
            "img_size": int(params.get('img_size', 256)),
            "nosmooth": params.get('nosmooth', 'false').lower() == 'true' if isinstance(params.get('nosmooth'), str) else params.get('nosmooth', False),
            "bbox_shift": int(params.get('bbox_shift', 0)),
            "extra_margin": int(params.get('extra_margin', 10)),
            "parsing_mode": params.get('parsing_mode', 'jaw'),
            "version": params.get('version', 'v15'),
            "face_det_batch_size": int(params.get('face_det_batch_size', 16))
        }

        pads_str = params.get('pads', "0 10 0 0")
        if isinstance(pads_str, str):
            task_params['pads'] = [int(x) for x in pads_str.split()]
        else:
            task_params['pads'] = pads_str

        task_id_input = params.get('task_id')

        # notifyurl 会让服务按请求方的意愿发起 POST，必须在创建任务前过允许列表。
        try:
            notify_url = validate_notify_url(
                params.get('notifyurl'), allowed_notify_hosts())
        except NetGuardError as exc:
            logger.warning("[头像任务] 拒绝 notifyurl: %s", exc)
            return json_error(
                "notifyurl 不被接受：{0}"
                "（可用环境变量 {1} 登记可信主机）".format(exc, NOTIFY_HOSTS_ENV)
            )

        task_id = task_manager.add_task(model_type, avatar_id, task_params, task_id=task_id_input, notify_url=notify_url)
        return json_ok(data={"task_id": task_id})

    except Exception as e:
        logger.exception("create_avatar_task error:")
        return json_error(str(e))

async def get_avatar_task_status(request):
    """
    GET /api/avatar/task/{task_id}
    """
    task_id = request.match_info.get('task_id')
    task = task_manager.get_task(task_id)
    if not task:
        return json_error("Task not found", code=404)

    return json_ok(data=task.to_dict())

async def list_avatar_tasks(request):
    """
    GET /api/avatar/tasks
    """
    tasks = task_manager.list_tasks()
    return json_ok(data={"tasks": tasks})

async def delete_avatar_task(request):
    """
    DELETE /api/avatar/task/{task_id}
    """
    task_id = request.match_info.get('task_id')
    success, msg = task_manager.delete_task(task_id)
    if not success:
        return json_error(msg)
    return json_ok(data={"msg": msg})

# ── 数字人背景 ───────────────────────────────────────────────────────
BACKGROUND_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
BACKGROUND_MAX_MB = 20


def _matte_ready(avatar_id):
    """该形象是否已经抠过像（决定界面上能不能选背景）。"""
    path = os.path.join(DATA_DIR, str(avatar_id or ""), "matte", "manifest.json")
    return os.path.isfile(path)


def _missing_matte_gestures(avatar_id):
    """列出「有动作素材、但还没有 alpha」的动作。

    这些动作播到时会**保持原背景**（合成层不会拿默认素材的 alpha 硬套，那样
    轮廓对不上会糊出一片白影）。界面上要能提前看到，而不是等人走近了才发现
    "配了背景怎么有的动作不换"。
    """
    base = os.path.join(DATA_DIR, str(avatar_id or ""))
    gestures_root = os.path.join(base, "gestures")
    matte_root = os.path.join(base, "matte", "gestures")
    missing = []
    try:
        names = sorted(os.listdir(gestures_root))
    except Exception:  # noqa: BLE001
        return missing
    for gid in names:
        frames_dir = os.path.join(gestures_root, gid, "full_imgs")
        if not os.path.isdir(frames_dir):
            continue
        try:
            need = len([n for n in os.listdir(frames_dir)
                        if n.lower().endswith((".png", ".jpg", ".jpeg"))])
        except Exception:  # noqa: BLE001
            continue
        alpha_dir = os.path.join(matte_root, gid)
        try:
            have = len([n for n in os.listdir(alpha_dir) if n.lower().endswith(".png")])
        except Exception:  # noqa: BLE001
            have = 0
        if need and have < need:
            missing.append(gid)
    return missing


def _pick_avatar_with_matte(explicit=""):
    """优先用调用方指定的形象；否则挑一个已经抠过像的。"""
    if explicit and _matte_ready(explicit):
        return explicit
    try:
        for name in sorted(os.listdir(DATA_DIR)):
            if _matte_ready(name):
                return name
    except Exception:  # noqa: BLE001
        pass
    return explicit or ""


async def get_background(request):
    """GET /api/avatar/background — 当前配置 + 可选背景 + 抠像状态。"""
    from utils import avatar_background as bgmod
    avatar_id = _pick_avatar_with_matte(request.query.get("avatar") or "")
    return json_ok(data={
        "config": bgmod.load_config(),
        "shadow": bgmod.load_shadow_config(),
        "backgrounds": bgmod.list_backgrounds(),
        "presets": [{"id": i, "name": n, "value": c} for i, n, c in bgmod.PRESET_COLORS],
        "avatar": avatar_id,
        "matte_ready": bool(avatar_id) and _matte_ready(avatar_id),
        "missing_gestures": _missing_matte_gestures(avatar_id) if avatar_id else [],
    })


async def set_background(request):
    """PUT /api/avatar/background — {"type": "none|color|image", "value": "#RRGGBB|data/..."}"""
    from utils import avatar_background as bgmod
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return json_error("请求体不是合法 JSON")
    kind = str(body.get("type") or "none")
    value = str(body.get("value") or "")
    if kind not in ("none", "color", "image"):
        return json_error("type 只能是 none / color / image")
    if kind == "color" and bgmod.resolve_color(value) is None:
        return json_error("颜色格式应为 #RRGGBB")
    if kind == "image":
        path = bgmod.abs_path(value)
        root = os.path.realpath(bgmod.BACKGROUND_DIR)
        if not path or not os.path.isfile(path) or not path_guard.is_within(root, os.path.realpath(path)):
            return json_error("背景图不存在")
    cfg = bgmod.save_config({"type": kind, "value": value})
    logger.info("[背景] 已设置为 %s", cfg)
    return json_ok(data={"config": cfg})


async def put_shadow(request):
    """PUT /api/avatar/shadow — 接地阴影（虚拟投影）参数。

    {"enabled": true, "strength": 0~1, "spread": 0.3~3, "flatten": 0.05~1,
     "softness": 0.1~1, "offset_y": -0.1~0.1, "shade": 0~1}

    越界值由 `normalize_shadow` 收敛，不在这里逐个拒绝——滑块拖到底就是边界值，
    因为浮点尾数被判非法反而更烦人。
    """
    from utils import avatar_background as bgmod
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return json_error("请求体不是合法 JSON")
    if not isinstance(body, dict):
        return json_error("请求体应为对象")
    cfg = bgmod.save_shadow_config(body)
    logger.info("[背景] 虚拟投影参数已更新：%s", cfg)
    return json_ok(data={"shadow": cfg})


async def upload_background(request):
    """POST /api/avatar/background/upload — multipart 上传背景图。"""
    from utils import avatar_background as bgmod
    try:
        post = await request.post()
    except Exception as exc:  # noqa: BLE001
        return json_error("读取上传内容失败：%s" % exc)
    field = post.get("file")
    if field is None or not getattr(field, "filename", ""):
        return json_error("没有收到文件")
    ext = os.path.splitext(field.filename)[1].lower()
    if ext not in BACKGROUND_EXTS:
        return json_error("只支持 %s 格式" % "、".join(sorted(BACKGROUND_EXTS)))
    content = field.file.read()
    if len(content) > BACKGROUND_MAX_MB * 1024 * 1024:
        return json_error("图片不能超过 %d MB" % BACKGROUND_MAX_MB)
    os.makedirs(bgmod.BACKGROUND_DIR, exist_ok=True)
    # 文件名用 uuid：中文/空格/超长名字一律不落盘，避免路径与编码问题
    name = uuid.uuid4().hex[:12] + ext
    path = os.path.join(bgmod.BACKGROUND_DIR, name)
    try:
        with open(path, "wb") as fh:
            fh.write(content)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[背景] 保存失败")
        return json_error("保存失败：%s" % exc)
    return json_ok(data={"name": name, "path": bgmod.rel_path(path),
                         "backgrounds": bgmod.list_backgrounds()})


async def background_preview(request):
    """GET /api/avatar/background/preview — 用当前（或指定）背景合成一张预览图。"""
    from utils import avatar_background as bgmod
    avatar_id = _pick_avatar_with_matte(request.query.get("avatar") or "")
    if not avatar_id:
        return json_error("没有可用形象")
    gesture = request.query.get("gesture") or None
    try:
        idx = int(request.query.get("index") or 0)
    except ValueError:
        idx = 0
    try:
        data = await run_blocking(bgmod.render_preview, avatar_id, None, gesture, idx)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[背景] 预览生成失败")
        return json_error("预览生成失败：%s" % exc)
    if not data:
        return json_error("还没有抠像素材，或当前没有设置背景")
    return web.Response(body=data, content_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})


def setup_avatar_routes(app):
    app.router.add_get("/api/avatars", list_avatars)
    app.router.add_get("/api/avatars/{avatar_id}/thumbnail", avatar_thumbnail)
    app.router.add_get("/api/avatars/{avatar_id}/gestures", list_avatar_gestures)
    app.router.add_post("/api/avatars/{avatar_id}/gestures/{gesture_id}/prepare",
                        prepare_gesture_assets)
    # 预览放在列举之后注册：更具体的路径先匹配，避免被短路径吃掉
    app.router.add_get("/api/avatars/{avatar_id}/gestures/{gesture_id}/preview",
                       gesture_preview)
    app.router.add_put("/api/avatars/{avatar_id}/gestures/{gesture_id}", rename_gesture)
    app.router.add_delete("/api/avatars/{avatar_id}/gestures/{gesture_id}", delete_gesture)
    app.router.add_put("/api/avatars/{avatar_id}", rename_avatar)
    app.router.add_delete("/api/avatars/{avatar_id}", delete_avatar)
    app.router.add_post("/api/avatar/task", create_avatar_task)
    app.router.add_get("/api/avatar/task/{task_id}", get_avatar_task_status)
    app.router.add_delete("/api/avatar/task/{task_id}", delete_avatar_task)
    app.router.add_get("/api/avatar/tasks", list_avatar_tasks)
    # 数字人背景（抠像后换背景）
    app.router.add_get("/api/avatar/background", get_background)
    app.router.add_put("/api/avatar/background", set_background)
    app.router.add_put("/api/avatar/shadow", put_shadow)
    app.router.add_post("/api/avatar/background/upload", upload_background)
    app.router.add_get("/api/avatar/background/preview", background_preview)
