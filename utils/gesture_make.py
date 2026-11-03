# -*- coding: utf-8 -*-
"""动作制作：形象首帧 + 提示词 → MiniMax H3 出片 → 拆成标准手势素材。

一个作业的内部阶段（前端按 `stage` 显示中文，`progress` 只用于进度条）：

    queued → submitting → rendering → downloading → converting → done
                                     ↘ failed（带原因）

为什么要归一化尺寸：`load_gesture` 会发现手势素材与形象默认素材尺寸不一致，
运行时把画面缩放到默认尺寸并记一条警告。H3 出片是 704×1440，切帧后 528×1080，
而形象默认素材是 538×1080——差 10px 就会每节课都触发那条警告，所以在这里补平成
默认尺寸（偏窄补白边、偏宽裁两侧，人脸框同步平移），运行时就不用再归一化。

并发：同一形象同时只允许一个作业（`_active`）。出片是按次计费的，不能一点就连点。
"""

import json
import os
import re
import threading
import time
import uuid

from utils import gesture_presets
from utils import gestures as gesture_utils
from utils import minimax_video
from utils.gestures import GESTURE_META_FILENAME, GESTURE_LABEL_MAX, GestureError
from utils.logger import logger

STAGE_QUEUED = 'queued'
STAGE_SUBMITTING = 'submitting'
STAGE_RENDERING = 'rendering'
STAGE_DOWNLOADING = 'downloading'
STAGE_CONVERTING = 'converting'
STAGE_DONE = 'done'
STAGE_FAILED = 'failed'

STAGE_TEXT = {
    STAGE_QUEUED: '已排队',
    STAGE_SUBMITTING: '正在提交给出片服务',
    STAGE_RENDERING: '正在生成动作视频（约 2 分钟）',
    STAGE_DOWNLOADING: '正在下载成片',
    STAGE_CONVERTING: '正在抽帧、对齐人脸并抠像（约 2 分钟）',
    STAGE_DONE: '已完成',
    STAGE_FAILED: '失败',
}

# 切帧参数沿用 2026-10-24 定论：256 的人脸裁剪 + 0.35 自适应扩边
FACE_SIZE = 256
PAD_SCALE = 0.35
PADS = [0, 10, 0, 0]

# 人脸检测批大小。genavatar 默认 16，但 16 帧 1080×528 转 int64 需要一块
# 209MB 的连续内存（`imgs - np.array([104,117,123])` 会升成 int64），在
# torch+CUDA 已占掉大半内存的进程里会 `Unable to allocate 209 MiB` 直接失败，
# 而且它抛的是 numpy 的 ArrayMemoryError（不是 RuntimeError），genavatar
# 自己那个「批减小一半重试」的分支接不住。降到 4 后单批只 52MB，实测稳定。
FACE_DET_BATCH = 4

_TERMINAL = (STAGE_DONE, STAGE_FAILED)
_VIDEO_DIR = os.path.abspath(os.path.join('data', 'tmp', 'gesture_make'))
_PREVIEW_DIR = os.path.abspath(os.path.join('data', 'tmp', 'gesture_preview'))

_jobs = {}
_active = {}
_lock = threading.Lock()


class GestureMakeError(RuntimeError):
    """制作请求本身不合法（参数、状态），对应 400。"""


class GestureMakeBusy(GestureMakeError):
    """该形象已有作业在跑。"""


# ─── 对外接口 ────────────────────────────────────────────────────────

def start(avatar_id, preset_id=None, name=None, prompt=None, duration=None, resolution=None):
    """建一个制作作业并立刻返回作业信息；实际出片在后台线程里跑。"""
    avatar_dir = os.path.join(gesture_utils.avatars_root(), str(avatar_id))
    if not os.path.isdir(avatar_dir):
        raise GestureMakeError('形象不存在：%s' % avatar_id)
    reference = _reference_frame(avatar_dir)
    if not reference:
        raise GestureMakeError('该形象没有 full_imgs 素材，无法取首帧做参考图')

    preset = gesture_presets.preset_by_id(preset_id) if preset_id else None
    if preset_id and not preset:
        raise GestureMakeError('没有这个预设动作：%s' % preset_id)

    text = (prompt or '').strip() or (preset['prompt'] if preset else '')
    if not text:
        raise GestureMakeError('提示词不能为空')

    label = (name or '').strip() or (preset['label'] if preset else '')
    if not label:
        raise GestureMakeError('动作名称不能为空')
    if len(label) > GESTURE_LABEL_MAX:
        raise GestureMakeError('动作名称不能超过 %d 个字符' % GESTURE_LABEL_MAX)

    if not minimax_video.is_configured():
        raise minimax_video.MinimaxNotConfigured(
            '还没配置 MiniMax API Key：设置页 → 动作制作，填入后再试')

    with _lock:
        running = _active.get(str(avatar_id))
        if running:
            raise GestureMakeBusy('这个形象已有一个动作在制作中（%s）'
                                  % running.get('stage_text', ''))
        gesture_id = _free_gesture_id(avatar_id, preset_id or _slug(label))
        job = {
            'id': uuid.uuid4().hex[:12],
            'avatar_id': str(avatar_id),
            'gesture_id': gesture_id,
            'name': label,
            'preset_id': preset_id or '',
            'prompt': text,
            'duration': duration,
            'resolution': resolution,
            'stage': STAGE_QUEUED,
            'stage_text': STAGE_TEXT[STAGE_QUEUED],
            'progress': 0,
            'error': '',
            'task_id': '',
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
        _jobs[job['id']] = job
        _active[str(avatar_id)] = job

    thread = threading.Thread(target=_run, args=(job, avatar_dir, reference), daemon=True,
                              name='gesture-make-%s' % job['id'])
    thread.start()
    return dict(job)


def get_job(job_id):
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def active_job(avatar_id):
    with _lock:
        job = _active.get(str(avatar_id))
        return dict(job) if job else None


def recent_jobs(avatar_id, limit=5):
    with _lock:
        items = [dict(j) for j in _jobs.values() if j['avatar_id'] == str(avatar_id)]
    items.sort(key=lambda j: j['created_at'], reverse=True)
    return items[:limit]


# ─── 作业执行 ────────────────────────────────────────────────────────

def _run(job, avatar_dir, reference):
    gesture_id = job['gesture_id']
    target_dir = gesture_utils.gesture_dir(job['avatar_id'], gesture_id)
    created_dir = not os.path.isdir(target_dir)
    video_path = ''
    try:
        _advance(job, STAGE_SUBMITTING, 5)
        task_id = minimax_video.create_task(
            job['prompt'], reference,
            duration=job['duration'], resolution=job['resolution'])
        job['task_id'] = task_id

        _advance(job, STAGE_RENDERING, 12)
        url = minimax_video.wait_for_video(task_id, on_poll=lambda st: _tick(job, 8))

        _advance(job, STAGE_DOWNLOADING, 74)
        os.makedirs(_VIDEO_DIR, exist_ok=True)
        video_path = os.path.join(_VIDEO_DIR, '%s.mp4' % job['id'])
        minimax_video.download(url, video_path)

        _advance(job, STAGE_CONVERTING, 80)
        _build_assets(job, avatar_dir, video_path, target_dir)

        _advance(job, STAGE_DONE, 100)
        logger.info('[动作制作] %s/%s 完成（作业 %s）', job['avatar_id'], gesture_id, job['id'])
    except Exception as exc:  # noqa: BLE001 - 任何失败都要落成可读状态
        logger.exception('[动作制作] 作业 %s 失败', job['id'])
        message = str(exc) or exc.__class__.__name__
        _fail(job, message)
        if created_dir and os.path.isdir(target_dir):
            import shutil
            shutil.rmtree(target_dir, ignore_errors=True)
            logger.warning('[动作制作] 已清理半成品目录 %s', target_dir)
    finally:
        if video_path and os.path.isfile(video_path) and job['stage'] == STAGE_DONE:
            # 成片留着没用（素材已拆好），但失败时保留便于排查
            try:
                os.remove(video_path)
            except OSError:
                pass
        with _lock:
            _active.pop(job['avatar_id'], None)


def _build_assets(job, avatar_dir, video_path, target_dir):
    """调 genavatar 拆帧，再归一化尺寸、写元数据、预热预览。"""
    from avatars.wav2lip.genavatar import generate_avatar

    # 记下源视频帧率：拆出的帧就是按这个帧率采样的，写进 `gesture.json` 后，
    # 播放时才能按输出帧率重采样——否则动作会按帧数硬播，速度跟着输出帧率一起跑偏。
    job['source_fps'] = _video_fps(video_path)

    save_root = os.path.join(gesture_utils.avatars_root(), job['avatar_id'],
                             gesture_utils.GESTURES_DIRNAME)

    def on_progress(percent):
        # genavatar 的 0-100 映射到 80-97
        _tick(job, max(0, min(100, int(percent))) * 17 // 100 + 80)

    generate_avatar(video_path, job['gesture_id'], save_path=save_root,
                    img_size=FACE_SIZE, pads=list(PADS), pad_scale=PAD_SCALE,
                    face_det_batch_size=FACE_DET_BATCH, progress_callback=on_progress)

    _advance(job, STAGE_CONVERTING, 97)
    normalize_size(avatar_dir, target_dir)
    _write_meta(target_dir, job)
    _build_muse_assets(job)
    _matte_gesture(job)
    _warm_preview(job['avatar_id'], job['gesture_id'])


def _matte_gesture(job):
    """给刚生成的这个动作补 alpha（`matte/gestures/<gid>/`）。

    缺这一步，配了自定义背景后播这个动作会是**一片白影**：合成层查不到该动作的
    alpha 就回退用默认素材的 alpha，而默认是站姿、动作是抬手，两套轮廓对不上，
    动作帧里的白底被当成人体保留下来。历史上 `idle_sway` / `wave_one` 就是这么
    漏掉的（`matte/manifest.json` 里只有更早的六个动作）。

    抠像本身约 130ms/帧（124 帧 ≈ 16 秒），放在生成流程末尾顺手做掉。
    """
    try:
        from scripts import avatar_matte

        frames, out_dir = avatar_matte.run_gesture(job['avatar_id'], job['gesture_id'])
        if not frames:
            logger.warning('[动作制作] 抠像没有产出帧：%s/%s',
                           job['avatar_id'], job['gesture_id'])
            return False
        logger.info('[动作制作] 抠像已就绪：%s（%d 帧）', job['gesture_id'], frames)
        return True
    except Exception as exc:  # noqa: BLE001 - 抠不出来也不该让整次生成判失败
        logger.warning('[动作制作] 抠像失败，配背景播放该动作时会保持原背景：'
                       '%s/%s: %s', job['avatar_id'], job['gesture_id'], exc)
        return False


def _build_muse_assets(job):
    """补齐 MuseTalk 的共享人脸资产（`gestures/<gid>/musetalk/`）。

    缺这一步，动作在 MuseTalk 形象上**不会播**：`_load_gesture_payload` 会抛
    `GestureError`，`_load_then_activate` 记一条 warning 后降级成默认素材——
    现场表现就是"配了动作、人走近了却毫无反应"，而且界面上一切正常。

    补齐本身是毫秒级文件拷贝（人脸资产取自形象自身，与动作帧数无关），
    所以放在生成流程末尾顺手做掉，不留给用户点"准备"按钮。
    """
    try:
        result = gesture_utils.build_muse_assets(job['avatar_id'], job['gesture_id'])
        logger.info('[动作制作] MuseTalk 资产已就绪：%s（%s）',
                    job['gesture_id'], result)
        return True
    except Exception as exc:  # noqa: BLE001 - 补不齐也不该让整次生成判失败
        logger.warning('[动作制作] MuseTalk 资产补齐失败，动作将无法在 MuseTalk 上播放：'
                       '%s/%s: %s', job['avatar_id'], job['gesture_id'], exc)
        return False


def normalize_size(avatar_dir, target_dir):
    """把手势画面补/裁到形象默认素材的尺寸，人脸框跟着平移或缩放。

    返回 `(old_size, new_size)` 便于测试与日志；尺寸本已一致时不写文件。
    """
    from PIL import Image

    target = _image_size(os.path.join(avatar_dir, 'full_imgs'))
    frames = _sorted_images(os.path.join(target_dir, gesture_utils.GESTURE_FULL_DIR))
    if not target or not frames:
        return (None, target)
    tw, th = target
    first = Image.open(frames[0])
    size = (first.size[0], first.size[1])
    first.close()
    if size == (tw, th):
        return (size, (tw, th))

    coords_path = os.path.join(target_dir, gesture_utils.GESTURE_COORDS_FILE)
    import pickle
    with open(coords_path, 'rb') as handle:
        coords = pickle.load(handle)

    # 三种情形分开处理，别一律 resize：
    # - 高度已一致、只是偏窄 → 两侧补白（影棚白底，补完看不出来），人脸框平移，人不被拉胖
    # - 高度已一致、只是偏宽 → 两侧裁掉，人脸框反向平移
    # - 高度也不一致 → 才缩放（同时按两个方向缩放人脸框）
    offset_x = 0
    scale = 1.0
    mode = 'resize'
    if size[1] == th and size[0] < tw:
        mode, offset_x = 'pad', (tw - size[0]) // 2
    elif size[1] == th and size[0] > tw:
        mode, offset_x = 'crop', -((size[0] - tw) // 2)
    else:
        scale = tw / float(size[0])

    for path in frames:
        with Image.open(path) as image:
            image = image.convert('RGB')
            if mode == 'pad':
                canvas = Image.new('RGB', (tw, th), (255, 255, 255))
                canvas.paste(image, ((tw - size[0]) // 2, 0))
                image = canvas
            elif mode == 'crop':
                left = (size[0] - tw) // 2
                image = image.crop((left, 0, left + tw, th))
            else:
                image = image.resize((tw, th), Image.LANCZOS)
            image.save(path)

    def to_int(pair):
        y1, y2, x1, x2 = pair
        if mode == 'resize':
            return (int(round(y1 * scale)), int(round(y2 * scale)),
                    int(round(x1 * scale)), int(round(x2 * scale)))
        return (int(y1), int(y2), int(x1 + offset_x), int(x2 + offset_x))

    with open(coords_path, 'wb') as handle:
        pickle.dump([to_int(item) for item in coords], handle)
    logger.info('[动作制作] 画面尺寸 %sx%s → %sx%s（%s），人脸框已同步',
                size[0], size[1], tw, th, mode)
    return (size, (tw, th))


def _write_meta(target_dir, job):
    meta = {
        'label': job['name'],
        'source': 'minimax-h3',
        'preset': job['preset_id'],
        'task_id': job['task_id'],
        'created_at': job['created_at'],
        'prompt': job['prompt'],
    }
    fps = job.get('source_fps')
    if fps:
        # 素材原始帧率：播放端按输出帧率重采样时要用（见 utils/gestures.py）
        meta[gesture_utils.GESTURE_FPS_KEY] = fps
    path = os.path.join(target_dir, GESTURE_META_FILENAME)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _warm_preview(avatar_id, gesture_id):
    """顺手把循环预览烘出来——列表与画布都直接读它，省得第一次打开转圈。"""
    try:
        gesture_utils.gesture_preview_file(avatar_id, gesture_id, _PREVIEW_DIR)
    except Exception:  # noqa: BLE001 - 预览失败不影响素材可用
        logger.warning('[动作制作] 预览预热失败：%s/%s', avatar_id, gesture_id, exc_info=True)


def _tick(job, progress):
    with _lock:
        job['progress'] = max(job.get('progress', 0), int(progress))
        job['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')


def _advance(job, stage, progress):
    with _lock:
        job['stage'] = stage
        job['stage_text'] = STAGE_TEXT.get(stage, stage)
        job['progress'] = max(job.get('progress', 0), int(progress))
        job['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    logger.info('[动作制作] %s/%s → %s', job['avatar_id'], job['gesture_id'],
                STAGE_TEXT.get(stage, stage))


def _fail(job, message):
    with _lock:
        job['stage'] = STAGE_FAILED
        job['stage_text'] = STAGE_TEXT[STAGE_FAILED]
        job['error'] = message
        job['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')


# ─── 小工具 ─────────────────────────────────────────────────────────

def _reference_frame(avatar_dir):
    frames = _sorted_images(os.path.join(avatar_dir, 'full_imgs'))
    return frames[0] if frames else ''


def _sorted_images(directory):
    if not os.path.isdir(directory):
        return []
    names = [n for n in os.listdir(directory)
             if n.lower().endswith(('.png', '.jpg', '.jpeg'))]

    def key(name):
        stem = os.path.splitext(name)[0]
        return (0, int(stem), '') if stem.isdigit() else (1, 0, name)

    return [os.path.join(directory, n) for n in sorted(names, key=key)]


def _image_size(full_imgs_dir):
    from PIL import Image

    frames = _sorted_images(full_imgs_dir)
    if not frames:
        return None
    with Image.open(frames[0]) as image:
        return (int(image.size[0]), int(image.size[1]))


def _video_fps(path):
    """读源视频帧率；读不到时返回 None（播放侧回退到默认帧率）。"""
    capture = None
    try:
        import cv2

        capture = cv2.VideoCapture(path)
        if not capture.isOpened():
            return None
        fps = float(capture.get(cv2.CAP_PROP_FPS))
    except Exception:  # noqa: BLE001 - 读不到不影响出素材
        return None
    finally:
        if capture is not None:
            capture.release()
    return round(fps, 3) if fps and fps > 0 else None


def _slug(text):
    cleaned = re.sub(r'[^A-Za-z0-9_.-]+', '-', text).strip('-')
    return cleaned.lower() or 'gesture'


def _free_gesture_id(avatar_id, base):
    """目录不撞名：open → open-2 → open-3。"""
    base = base if gesture_utils.is_valid_gesture_id(base) else _slug(base)
    candidate = base
    index = 1
    while os.path.isdir(gesture_utils.gesture_dir(avatar_id, candidate)):
        index += 1
        candidate = '%s-%d' % (base, index)
    return candidate
