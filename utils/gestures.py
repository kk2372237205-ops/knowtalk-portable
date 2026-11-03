# -*- coding: utf-8 -*-
"""手势素材的发现与加载。

目录约定（与形象的默认素材同构，因此渲染层可以整体替换帧源）：

    data/avatars/{avatar_id}/gestures/{gesture_id}/
        full_imgs/     全帧序列（说话时作底图、静音时直接输出）
        face_imgs/     逐帧对齐的人脸裁剪（口型模型的输入）
        coords.pkl     逐帧人脸框 [(y1, y2, x1, x2), ...]
        gesture.json   可选；{"label": "讲解手势"} 作为显示名

三件套任一项缺失即视为不可用，不进入可用列表（`list_gestures`）。

为什么在加载期归一化尺寸：推流分辨率若在会话中途变化会触发编码器重建，
所以约定手势素材单帧尺寸与默认素材一致；不一致时在**加载阶段**把画面缩放到
默认素材尺寸、人脸框坐标按同比例缩放，并记一条警告，保证输出尺寸全程恒定。
"""

import json
import os
import pickle
import re
import shutil
from collections import OrderedDict
from datetime import datetime
from threading import Lock

from utils.logger import logger
from utils.numpy_compat import ensure_numpy_core_alias

# 素材里的 coords.pkl / mask_coords.pkl 由 numpy 2.x 序列化，本环境是 1.x；
# 注册别名必须在**任何 pickle.load 之前**完成，放在这里最省心——本模块的所有
# 读盘入口（load_gesture / build_muse_assets / load_muse_assets）都要先 import 它。
ensure_numpy_core_alias()

GESTURES_DIRNAME = 'gestures'
GESTURE_META_FILENAME = 'gesture.json'
GESTURE_FULL_DIR = 'full_imgs'
GESTURE_FACE_DIR = 'face_imgs'
GESTURE_COORDS_FILE = 'coords.pkl'

# ── MuseTalk 专属资产（手势目录下的子目录）─────────────────────────────
# MuseTalk 推理需要 latent + 遮罩，和 wav2lip 的三件套完全不同，因此单独放一层，
# 互不覆盖：`coords.pkl` 的元素序两个模型也不一样（wav2lip 是 (y1,y2,x1,x2)，
# MuseTalk 是 (x1,y1,x2,y2)）。
#
# 这套资产是**形象自己的一份**共享副本（长度恒为 1），不是逐帧编码动作帧：
# 动作素材只贡献身体与姿态，人脸永远由形象自身驱动。原因见
# `_load_avatar_face_assets` 的说明。
MUSE_DIRNAME = 'musetalk'
MUSE_LATENTS_FILE = 'latents.pt'
MUSE_MASK_DIR = 'mask'
MUSE_MASK_COORDS_FILE = 'mask_coords.pkl'
MUSE_COORDS_FILE = 'coords.pkl'
MUSE_ASSET_META = 'asset.json'
MUSE_ASSET_MODE_SHARED = 'shared'

# 显示名长度上限（去空白后按字符计）
GESTURE_LABEL_MAX = 40
# 预览动画：输出高度与帧间隔（25fps）。预览是给管理页看效果的，压低分辨率即可。
PREVIEW_HEIGHT = 480
PREVIEW_FRAME_MS = 40

# 动作素材的原始帧率。素材由外部视频导入（MiniMax H3 出片固定 24fps），
# 播放时按**输出帧率**重采样，否则 24fps 的 4 秒动作在 20fps 输出下会播 5 秒多，
# 肉眼就是"动作变慢、慢半拍"。`gesture.json` 里的 `fps` 优先，缺失时用这个默认值。
DEFAULT_SOURCE_FPS = 24.0
GESTURE_FPS_KEY = 'fps'

# 全帧序列的按需解码缓存深度。
#
# 一套动作的上百张 538×1080 全帧常驻要 150~200MB；而单帧 PNG 解码只要约 0.8ms
# （远小于推理的 50ms/帧预算）。因此全帧改成"用谁解谁 + 保留最近几帧"，
# 单套素材的内存降到 ~2MB，会话才可能把一讲课用到的动作**全部**留在缓存里，
# 不至于讲到一半又去读盘解码——那正是"动作一出现就卡一下"的来源。
GESTURE_FRAME_CACHE_SIZE = 4

# 与 server/avatar_routes.py 的形象标识字符集一致
GESTURE_ID_RE = re.compile(r'[A-Za-z0-9_.-]+')
# `.` 与 `..` 虽在字符集内，但会指向已有目录而不是手势，必须拒绝
_RESERVED_IDS = frozenset({'.', '..'})
_IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg')


class GestureError(RuntimeError):
    """手势素材不可用（标识非法、目录不完整或无法读取）。"""


class GestureNotFound(GestureError):
    """手势目录不存在（区别于"标识非法"：前者 404，后者 400）。"""


def avatars_root():
    """形象素材根目录（绝对路径），与 `wav2lip_avatar.load_avatar` 的约定一致。"""
    return os.path.abspath(os.path.join('.', 'data', 'avatars'))


def is_valid_gesture_id(gesture_id):
    """手势标识只接受 `A-Za-z0-9_.-`，且拒绝空串与路径穿越。"""
    if not isinstance(gesture_id, str) or not gesture_id:
        return False
    if '..' in gesture_id or gesture_id in _RESERVED_IDS:
        return False
    return bool(GESTURE_ID_RE.fullmatch(gesture_id))


def gesture_dir(avatar_id, gesture_id, data_dir=None):
    return os.path.join(data_dir or avatars_root(), str(avatar_id),
                        GESTURES_DIRNAME, str(gesture_id))


def _sorted_images(directory):
    """列出目录下的图片，按文件名数字升序（与 `load_avatar` 的抽帧顺序一致）。"""
    if not os.path.isdir(directory):
        return []
    names = [name for name in os.listdir(directory)
             if name.lower().endswith(_IMAGE_SUFFIXES)]

    def sort_key(name):
        stem = os.path.splitext(name)[0]
        if stem.isdigit():
            return (0, int(stem), '')
        return (1, 0, name)

    return [os.path.join(directory, name) for name in sorted(names, key=sort_key)]


def _has_pieces(path):
    """三件套齐备：`coords.pkl` 存在，且 `face_imgs/`、`full_imgs/` 均有图片。"""
    if not os.path.isfile(os.path.join(path, GESTURE_COORDS_FILE)):
        return False
    return bool(_sorted_images(os.path.join(path, GESTURE_FACE_DIR))
                and _sorted_images(os.path.join(path, GESTURE_FULL_DIR)))


def _label_of(path, gesture_id):
    """显示名：`gesture.json` 的 `label` 优先，缺失或为空时回退为标识。"""
    meta_path = os.path.join(path, GESTURE_META_FILENAME)
    if not os.path.isfile(meta_path):
        return gesture_id
    try:
        with open(meta_path, 'r', encoding='utf-8') as handle:
            meta = json.load(handle)
    except Exception:  # noqa: BLE001
        logger.warning('[手势] %s 的 %s 无法解析，显示名回退为 %s',
                       gesture_id, GESTURE_META_FILENAME, gesture_id)
        return gesture_id
    if isinstance(meta, dict):
        label = meta.get('label')
        if isinstance(label, str) and label.strip():
            return label.strip()
    return gesture_id


def _read_meta(path):
    """读 `gesture.json`；缺失或损坏时返回空字典（元数据不是必需项）。"""
    meta_path = os.path.join(path, GESTURE_META_FILENAME)
    if not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, 'r', encoding='utf-8') as handle:
            meta = json.load(handle)
    except Exception:  # noqa: BLE001
        return {}
    return meta if isinstance(meta, dict) else {}


def gesture_source_fps(avatar_id, gesture_id, data_dir=None):
    """该手势素材的原始帧率：`gesture.json` 的 `fps` 优先，否则用默认值。"""
    path = gesture_dir(avatar_id, gesture_id, data_dir=data_dir)
    raw = _read_meta(path).get(GESTURE_FPS_KEY)
    try:
        fps = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_SOURCE_FPS
    return fps if fps > 0 else DEFAULT_SOURCE_FPS


def playback_indices(count, source_fps, target_fps):
    """把「按 `source_fps` 均匀采样」的帧序列映射到 `target_fps` 时间线的取帧下标。

    播放端是"每输出一帧前进一帧"，所以素材帧率与输出帧率不一致时动作速度就会跑偏：
    24fps 素材在 20fps 输出下变成 0.83 倍速（4 秒的动作播成 5.3 秒）。这里按时间轴
    重采样——每个输出时刻取素材里该时刻对应的那一帧，动作就恢复原速。

    返回长度 = 素材时长 × 输出帧率；末尾一帧固定取 `count-1`，保证动作收尾
    （通常是"回到体侧"的末帧）一定播到。帧率已知且相等时返回恒等映射。
    """
    count = int(count or 0)
    if count <= 0:
        return []
    try:
        source_fps = float(source_fps or 0)
        target_fps = float(target_fps or 0)
    except (TypeError, ValueError):
        return list(range(count))
    if source_fps <= 0 or target_fps <= 0 or abs(source_fps - target_fps) < 1e-3:
        return list(range(count))
    duration = count / source_fps
    target_count = max(1, int(round(duration * target_fps)))
    if target_count <= 1:
        return [0]
    ratio = source_fps / target_fps
    indices = [min(count - 1, int(round(i * ratio))) for i in range(target_count)]
    indices[-1] = count - 1
    return indices


def _resample_seq(value, indices):
    """按下标重采样一段序列；torch 张量与 list 都支持，长度不足时钳到末位。"""
    resample = getattr(value, 'resample', None)
    if callable(resample):
        # 按需解码的帧序列：只改下标映射，不解码
        return resample(indices)
    total = len(value)
    if not total or not indices:
        return value
    picked = [min(indices[i] if i < len(indices) else total - 1, total - 1)
              for i in range(len(indices))]
    if isinstance(value, (list, tuple)):
        items = [value[index] for index in picked]
        return items if isinstance(value, list) else tuple(items)
    try:
        import torch
        if isinstance(value, torch.Tensor):
            return value.index_select(0, torch.as_tensor(picked, device=value.device))
    except Exception:  # noqa: BLE001
        pass
    return [value[index] for index in picked]


def list_gestures(avatar_id, data_dir=None):
    """列出某形象下可用的手势，按标识升序；没有手势时返回空集合（不是错误）。

    每项除 `id` / `label` 外还带 `frames`（`full_imgs/` 帧数）与 `width` / `height`
    （首帧尺寸）——数字人管理页要靠它们显示帧数并决定预览的宽高比。
    """
    root = os.path.join(data_dir or avatars_root(), str(avatar_id), GESTURES_DIRNAME)
    if not os.path.isdir(root):
        return []
    result = []
    for name in sorted(os.listdir(root)):
        if not is_valid_gesture_id(name):
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path) or not _has_pieces(path):
            continue
        frames = _sorted_images(os.path.join(path, GESTURE_FULL_DIR))
        size = _image_size(frames[0]) if frames else None
        result.append({
            'id': name,
            'label': _label_of(path, name),
            'frames': len(frames),
            'width': size[0] if size else 0,
            'height': size[1] if size else 0,
            # MuseTalk 形象必须靠 musetalk/ 下的逐帧资产才能播动作；
            # 未就绪时该手势在 musetalk 下会被降级（照常朗读、不播动作）。
            'muse_ready': _muse_ready(path),
        })
    return result


def _image_size(path):
    """只读图片头拿尺寸（不整帧解码）——列举时逐套手势调用，必须便宜。"""
    try:
        from PIL import Image

        with Image.open(path) as image:
            return (int(image.size[0]), int(image.size[1]))
    except Exception:  # noqa: BLE001 - 尺寸只用于展示，读不到就退化为 0
        return None


def _require_gesture_dir(avatar_id, gesture_id, data_dir=None):
    """校验标识并返回存在的手势目录；不存在抛 :class:`GestureNotFound`。"""
    if not is_valid_gesture_id(gesture_id):
        raise GestureError('非法的手势标识: %r' % (gesture_id,))
    path = gesture_dir(avatar_id, gesture_id, data_dir=data_dir)
    if not os.path.isdir(path):
        raise GestureNotFound('手势不存在: %s' % (gesture_id,))
    return path


def set_gesture_label(avatar_id, gesture_id, label, data_dir=None):
    """改写显示名（`gesture.json` 的 `label`），保留文件中的其它键。

    显示名去空白后必须非空且不超过 ``GESTURE_LABEL_MAX`` 个字符；不合法直接抛
    :class:`GestureError`，**不写文件**——校验必须先于落盘。
    """
    text = label.strip() if isinstance(label, str) else ''
    if not text:
        raise GestureError('显示名不能为空')
    if len(text) > GESTURE_LABEL_MAX:
        raise GestureError('显示名不能超过 %d 个字符' % GESTURE_LABEL_MAX)

    path = _require_gesture_dir(avatar_id, gesture_id, data_dir=data_dir)
    meta_path = os.path.join(path, GESTURE_META_FILENAME)
    meta = {}
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                meta = loaded
        except Exception:  # noqa: BLE001 - 损坏的元数据不应阻止改名
            logger.warning('[手势] %s 的 %s 无法解析，改名时将重写该文件',
                           gesture_id, GESTURE_META_FILENAME)
    meta['label'] = text

    tmp_path = meta_path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, meta_path)
    logger.info('[手势] %s 的显示名已改为 %s', gesture_id, text)
    return text


def delete_gesture(avatar_id, gesture_id, data_dir=None):
    """删除整套手势素材（整个目录）。标识非法抛 GestureError，不存在抛 GestureNotFound。"""
    import shutil

    path = _require_gesture_dir(avatar_id, gesture_id, data_dir=data_dir)
    shutil.rmtree(path)
    logger.warning('[手势] 已删除手势 %s（%s）', gesture_id, path)


def gesture_frames(avatar_id, gesture_id, data_dir=None):
    """该手势的 `full_imgs/` 帧路径，按帧序升序。"""
    path = _require_gesture_dir(avatar_id, gesture_id, data_dir=data_dir)
    return _sorted_images(os.path.join(path, GESTURE_FULL_DIR))


def _preview_fingerprint(frames):
    """缓存指纹 = (帧数, 最新帧的 mtime_ns)。任一变化即视为素材已更新。"""
    newest = 0
    for path in frames:
        try:
            newest = max(newest, os.stat(path).st_mtime_ns)
        except OSError:
            continue
    return (len(frames), newest)


def _preview_cache_files(cache_dir, avatar_id, gesture_id):
    prefix = '%s__%s__' % (avatar_id, gesture_id)
    if not os.path.isdir(cache_dir):
        return []
    return [os.path.join(cache_dir, name) for name in os.listdir(cache_dir)
            if name.startswith(prefix) and name.endswith('.webp')]


def clear_preview_cache(cache_dir, avatar_id, gesture_id, keep=None):
    """清理某手势的预览缓存；`keep` 指定要保留的那一个（重建后的新指纹文件）。"""
    removed = 0
    keep_path = os.path.abspath(keep) if keep else None
    for stale in _preview_cache_files(cache_dir, avatar_id, gesture_id):
        if keep_path and os.path.abspath(stale) == keep_path:
            continue
        try:
            os.remove(stale)
            removed += 1
        except OSError:
            pass
    return removed


def gesture_preview_file(avatar_id, gesture_id, cache_dir, data_dir=None):
    """返回该手势的循环动画 WebP 路径，必要时生成。

    缓存文件名带 ``(帧数, 最新 mtime)`` 指纹：素材一变，文件名就变，因此重建时
    **不需要覆盖**旧文件——正在被读取的旧缓存可以安全地继续发完，重建完成后再清理。
    """
    if not is_valid_gesture_id(gesture_id):
        raise GestureError('非法的手势标识: %r' % (gesture_id,))

    path = gesture_dir(avatar_id, gesture_id, data_dir=data_dir)
    if not os.path.isdir(path):
        raise GestureNotFound('手势不存在: %s' % (gesture_id,))
    if not _has_pieces(path):
        raise GestureNotFound('手势素材不完整（需要 full_imgs/、face_imgs/ 与 coords.pkl）')

    frames = _sorted_images(os.path.join(path, GESTURE_FULL_DIR))
    count, newest = _preview_fingerprint(frames)
    target = os.path.join(cache_dir,
                          '%s__%s__%d_%d.webp' % (avatar_id, gesture_id, count, newest))
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        return target

    os.makedirs(cache_dir, exist_ok=True)
    build_preview(frames, target)
    # 指纹命名的旧缓存已无用：清理同手势的其它条目（保留刚生成的那个）
    clear_preview_cache(cache_dir, avatar_id, gesture_id, keep=target)
    return target


def build_preview(frame_paths, target):
    """把帧序列写成循环播放的 WebP 动画（先写临时文件再原子替换）。

    预览是**可再生数据**：高度压到 ``PREVIEW_HEIGHT``，帧间隔固定
    ``PREVIEW_FRAME_MS``；不做人脸对齐，只做等比缩放。
    """
    from PIL import Image

    images = []
    for frame_path in frame_paths:
        with Image.open(frame_path) as image:
            image = image.convert('RGB')
            width, height = image.size
            scale = PREVIEW_HEIGHT / float(height)
            images.append(image.resize(
                (max(1, int(round(width * scale))), PREVIEW_HEIGHT), Image.LANCZOS))
    if not images:
        raise GestureNotFound('手势没有可用帧')

    tmp_path = target + '.tmp'
    images[0].save(
        tmp_path, format='WEBP', save_all=True, append_images=images[1:],
        duration=PREVIEW_FRAME_MS, loop=0, quality=65, method=4)
    os.replace(tmp_path, target)
    logger.info('[手势] 已生成预览动画 %s（%d 帧）', os.path.basename(target), len(images))
    return target


def _frame_size(frames):
    if not frames:
        return None
    frame = frames[0]
    return (int(frame.shape[1]), int(frame.shape[0]))


def _resize_all(frames, size):
    import cv2

    width, height = int(size[0]), int(size[1])
    return [cv2.resize(frame, (width, height), interpolation=cv2.INTER_LANCZOS4)
            for frame in frames]


class LazyFrames:
    """按需解码的全帧序列（只读、序列协议）。

    存在的理由：一套动作的上百张全帧常驻要 150~200MB，会话想同时留住一讲课用到的
    所有动作就必然要反复淘汰/重载，重载发生在讲课中途就是"卡一下"。而单帧解码只要
    约 0.8ms，完全可以现用现解，内存只剩最近几帧。

    `size` 给定时在解码后立即缩放（手势素材尺寸与默认素材不一致的情况），
    这样归一化也不必把整套帧materialize 到内存里。
    """

    def __init__(self, paths, size=None, cache_size=GESTURE_FRAME_CACHE_SIZE):
        self._paths = list(paths)
        self._size = (int(size[0]), int(size[1])) if size else None
        self._cache_size = max(1, int(cache_size))
        self._cache = OrderedDict()
        self._lock = Lock()

    # ── 序列协议 ────────────────────────────────────────────────
    def __len__(self):
        return len(self._paths)

    def __getitem__(self, index):
        if not isinstance(index, int):
            raise TypeError('LazyFrames 只支持整数下标')
        total = len(self._paths)
        if not total:
            raise IndexError('空的帧序列')
        index %= total
        with self._lock:
            cached = self._cache.get(index)
            if cached is not None:
                self._cache.move_to_end(index)
                return cached
        frame = self._decode(index)
        with self._lock:
            self._cache[index] = frame
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return frame

    def __iter__(self):
        for index in range(len(self._paths)):
            yield self[index]

    # ── 重采样 / 探查 ───────────────────────────────────────────
    def resample(self, indices):
        """按输出帧率重采样：只换下标映射，不预先解码任何一帧。"""
        return LazyFrames([self._paths[i % len(self._paths)] for i in indices],
                          size=self._size, cache_size=self._cache_size)

    @property
    def size(self):
        """单帧 (宽, 高)；读第 0 帧的图片头即可，不必解码全部。"""
        return self._size or (_image_size(self._paths[0]) if self._paths else None)

    def _decode(self, index):
        import cv2

        path = self._paths[index]
        frame = cv2.imread(path)
        if frame is None:
            raise GestureError('无法读取手势帧: %s' % path)
        if self._size and (frame.shape[1], frame.shape[0]) != self._size:
            frame = cv2.resize(frame, self._size, interpolation=cv2.INTER_LANCZOS4)
        return frame


def load_gesture(avatar_id, gesture_id, ref_size=None, ref_face_size=None, data_dir=None,
                 target_fps=None):
    """读取一套手势的三件套，必要时归一化到参考尺寸。

    `ref_size` / `ref_face_size` 是默认素材的 (宽, 高)。给定且与手势素材不一致时，
    画面缩放到参考尺寸、`coords.pkl` 按同比例缩放，并记一条警告——归一化必须在
    会话运行前完成，否则中途改变输出尺寸会让编码器重建。

    `target_fps` 是**输出帧率**。素材按自己的原始帧率采样（见 `gesture_source_fps`），
    播放端却是"每输出一帧前进一帧"，两者不等就会变速播放。给定 `target_fps` 时按时间轴
    重采样到该帧率，动作即恢复原速；为 None 时按原样返回（管理页预览等场景）。
    """
    if not is_valid_gesture_id(gesture_id):
        raise GestureError('非法的手势标识: %r' % (gesture_id,))

    path = gesture_dir(avatar_id, gesture_id, data_dir=data_dir)
    if not _has_pieces(path):
        raise GestureError('手势素材不完整（需要 full_imgs/、face_imgs/ 与 coords.pkl）: %s'
                           % path)

    from utils.image import read_imgs

    frame_paths = _sorted_images(os.path.join(path, GESTURE_FULL_DIR))
    faces = read_imgs(_sorted_images(os.path.join(path, GESTURE_FACE_DIR)))
    try:
        with open(os.path.join(path, GESTURE_COORDS_FILE), 'rb') as handle:
            coords = pickle.load(handle)
    except Exception as exc:  # noqa: BLE001
        raise GestureError('无法读取 %s: %s' % (GESTURE_COORDS_FILE, exc))
    if not isinstance(coords, list):
        raise GestureError('%s 的内容不是人脸框列表' % GESTURE_COORDS_FILE)

    # 全帧按需解码：一套动作的上百张 538×1080 常驻要 150~200MB，改成"用谁解谁"
    # 后只占几 MB，会话才留得住一讲课用到的全部动作（避免讲到一半又去读盘）。
    frames = LazyFrames(frame_paths)

    if len(coords) != len(frames):
        logger.warning('[手势] %s 的帧数(%d)与人脸框数(%d)不一致，按帧源长度取模取用',
                       gesture_id, len(frames), len(coords))

    if ref_size:
        size = frames.size
        if size and size != (int(ref_size[0]), int(ref_size[1])):
            scale_x = float(ref_size[0]) / size[0]
            scale_y = float(ref_size[1]) / size[1]
            # 缩放交给解码阶段做（`LazyFrames(size=...)`），不必先把整套帧读进内存
            frames = LazyFrames(frame_paths, size=ref_size)
            # 人脸框是**像素坐标**，缩放后必须仍是整数：下游 `paste_back_frame` 会把
            # `x2-x1` / `y2-y1` 直接当作 `cv2.resize` 的 dsize，而 OpenCV 5\.x 拒绝
            # 浮点 dsize（`Can't parse 'dsize'`）。实测这会让人脸贴回**逐帧失败**，
            # 表现成"手势期间嘴不动"，且只留一条 warning，极难发现。
            coords = [(int(round(y1 * scale_y)), int(round(y2 * scale_y)),
                       int(round(x1 * scale_x)), int(round(x2 * scale_x)))
                      for (y1, y2, x1, x2) in coords]
            logger.warning(
                '[手势] %s 的画面尺寸 %sx%s 与默认素材 %sx%s 不一致，已归一化（人脸框等比缩放）',
                gesture_id, size[0], size[1], int(ref_size[0]), int(ref_size[1]))

    if ref_face_size:
        size = _frame_size(faces)
        if size and size != (int(ref_face_size[0]), int(ref_face_size[1])):
            faces = _resize_all(faces, ref_face_size)
            logger.warning('[手势] %s 的人脸裁剪尺寸 %sx%s 已归一化到 %sx%s',
                           gesture_id, size[0], size[1],
                           int(ref_face_size[0]), int(ref_face_size[1]))

    if target_fps:
        source_fps = gesture_source_fps(avatar_id, gesture_id, data_dir=data_dir)
        indices = playback_indices(len(frames), source_fps, target_fps)
        if len(indices) != len(frames):
            frames = _resample_seq(frames, indices)
            faces = _resample_seq(faces, indices)
            coords = _resample_seq(coords, indices)
            logger.info('[手势] %s 按 %g→%g fps 重采样到输出帧率：%d 帧',
                        gesture_id, source_fps, float(target_fps), len(frames))

    logger.info('[手势] 已加载手势 %s：%d 帧', gesture_id, len(frames))
    return frames, faces, coords


# ─── MuseTalk 侧资产（手势动作在 musetalk 模型下播放所需）──────────────────

def muse_asset_dir(gesture_path):
    return os.path.join(gesture_path, MUSE_DIRNAME)


def _muse_ready(gesture_path):
    """MuseTalk 资产是否齐备：共享人脸资产 + 元数据。

    元数据（`asset.json`）里记着 `mode`：只有 `shared` 才算就绪。旧版的**逐帧**
    资产没有这个文件，会被判为未就绪，从而由 `build_muse_assets` 自动迁移成
    共享资产——否则升级后动作会一直用旧的动作帧脸，胖瘦照旧。
    """
    base = muse_asset_dir(gesture_path)
    meta_path = os.path.join(base, MUSE_ASSET_META)
    if not os.path.isfile(meta_path):
        return False
    try:
        with open(meta_path, 'r', encoding='utf-8') as handle:
            meta = json.load(handle)
    except Exception:  # noqa: BLE001
        logger.warning('[手势] %s 的 %s 无法解析，按未就绪处理', gesture_path, MUSE_ASSET_META)
        return False
    if not isinstance(meta, dict) or meta.get('mode') != MUSE_ASSET_MODE_SHARED:
        return False
    return (
        os.path.isfile(os.path.join(base, MUSE_LATENTS_FILE))
        and os.path.isfile(os.path.join(base, MUSE_MASK_COORDS_FILE))
        and os.path.isfile(os.path.join(base, MUSE_COORDS_FILE))
        and bool(_sorted_images(os.path.join(base, MUSE_MASK_DIR)))
    )


def has_muse_assets(avatar_id, gesture_id, data_dir=None):
    """该手势是否已补齐 MuseTalk 资产（供列表/接口展示就绪状态）。"""
    if not is_valid_gesture_id(gesture_id):
        return False
    return _muse_ready(gesture_dir(avatar_id, gesture_id, data_dir=data_dir))


def load_muse_assets(avatar_id, gesture_id, data_dir=None, target_fps=None):
    """读取手势的 MuseTalk 资产；不齐备或读失败时返回 None。

    返回 None 是**可预期**的降级路径：调用方据此继续用基础素材朗读，不播动作。

    `target_fps` 与 `load_gesture` 同义：给定且与素材原始帧率不同时，四份资产按**同一套**
    下标重采样，保证 latent / 遮罩 / 人脸框与画面帧仍然逐帧对齐。
    """
    if not is_valid_gesture_id(gesture_id):
        return None
    path = gesture_dir(avatar_id, gesture_id, data_dir=data_dir)
    if not _muse_ready(path):
        return None

    import torch
    from utils.image import read_imgs

    base = muse_asset_dir(path)
    try:
        latents = torch.load(os.path.join(base, MUSE_LATENTS_FILE))
        masks = read_imgs(_sorted_images(os.path.join(base, MUSE_MASK_DIR)))
        with open(os.path.join(base, MUSE_MASK_COORDS_FILE), 'rb') as handle:
            mask_coords = pickle.load(handle)
        with open(os.path.join(base, MUSE_COORDS_FILE), 'rb') as handle:
            coords = pickle.load(handle)
    except Exception as exc:  # noqa: BLE001
        logger.warning('[手势] %s 的 MuseTalk 资产读取失败，本次不播动作: %s', gesture_id, exc)
        return None

    if not (len(latents) and len(masks) and len(mask_coords) and len(coords)):
        logger.warning('[手势] %s 的 MuseTalk 资产为空，本次不播动作', gesture_id)
        return None

    if target_fps:
        source_fps = gesture_source_fps(avatar_id, gesture_id, data_dir=data_dir)
        indices = playback_indices(len(latents), source_fps, target_fps)
        if len(indices) != len(latents):
            latents = _resample_seq(latents, indices)
            masks = _resample_seq(masks, indices)
            mask_coords = _resample_seq(mask_coords, indices)
            coords = _resample_seq(coords, indices)

    return {'latents': latents, 'masks': masks,
            'mask_coords': mask_coords, 'coords': coords}


def _load_avatar_face_assets(avatar_id, data_dir=None):
    """取形象自身的人脸参考资产（第 0 份）：latent / 遮罩 / 遮罩框 / 人脸框。

    动作素材是**生成**出来的视频，即使同一个人、同一构图，脸型也会漂移——实测六套
    动作的帧比形象本身胖一圈（下颌更宽、两颊更满、脸更圆），而人脸框的尺寸与位置
    其实几乎一致（宽高差 1%~5%、位移不到 4px）。MuseTalk 的输出脸型跟随参考 latent，
    所以只要拿动作帧自己的脸做参考，动作期间就"换了张胖脸"。

    结论：动作只贡献身体与姿态，人脸由形象这一份资产驱动，长度恒为 1。
    """
    import torch
    from utils.image import read_imgs

    root = os.path.join(data_dir or avatars_root(), avatar_id)
    latents_path = os.path.join(root, MUSE_LATENTS_FILE)
    mask_files = _sorted_images(os.path.join(root, MUSE_MASK_DIR))
    mask_coords_path = os.path.join(root, MUSE_MASK_COORDS_FILE)
    coords_path = os.path.join(root, GESTURE_COORDS_FILE)
    for label, exists in (
        (MUSE_LATENTS_FILE, os.path.isfile(latents_path)),
        (MUSE_MASK_DIR, bool(mask_files)),
        (MUSE_MASK_COORDS_FILE, os.path.isfile(mask_coords_path)),
        (GESTURE_COORDS_FILE, os.path.isfile(coords_path)),
    ):
        if not exists:
            raise GestureError('形象 %s 缺少 %s，无法生成动作的人脸资产' % (avatar_id, label))

    latents = torch.load(latents_path)
    masks = read_imgs(mask_files[:1])
    with open(mask_coords_path, 'rb') as handle:
        mask_coords = pickle.load(handle)
    with open(coords_path, 'rb') as handle:
        coords = pickle.load(handle)
    if not (len(latents) and len(masks) and len(mask_coords) and len(coords)):
        raise GestureError('形象 %s 的人脸资产为空' % avatar_id)
    return {
        'latent': latents[0],
        'mask': masks[0],
        'mask_coord': mask_coords[0],
        'coord': coords[0],
        'source_frames': len(latents),
    }


def build_muse_assets(avatar_id, gesture_id, data_dir=None, device=None):
    """为已存在的手势补齐 MuseTalk 资产（幂等）。

    产出 `gestures/<gid>/musetalk/` 下的**一份**共享人脸资产，直接取自形象本身：
    latent、遮罩、遮罩框、人脸框各一条，与动作帧数无关（动作 89 帧也只用 1 份）。
    这样动作期间的脸恒等于形象的脸，不会随生成素材漂移。

    只读该手势的 `full_imgs/`、`face_imgs/`、`coords.pkl` 做完整性校验，
    **不修改**它们与 `gesture.json`。

    `device` 仅为兼容旧调用保留：现在不需要跑 VAE 编码与 face parsing，补齐是纯
    文件拷贝，耗时从"百帧数十秒"降到毫秒级，也不再抢 GPU。
    """
    if not is_valid_gesture_id(gesture_id):
        raise GestureError('非法的手势标识: %r' % (gesture_id,))

    path = gesture_dir(avatar_id, gesture_id, data_dir=data_dir)
    if not _has_pieces(path):
        raise GestureError('手势素材不完整（需要 full_imgs/、face_imgs/ 与 coords.pkl）: %s' % path)
    if _muse_ready(path):
        return {'ready': True, 'frames': 0, 'skipped': True}

    assets = _load_avatar_face_assets(avatar_id, data_dir=data_dir)

    import cv2
    import torch

    base = muse_asset_dir(path)
    mask_dir = os.path.join(base, MUSE_MASK_DIR)
    # 旧版是逐帧资产（每帧一份 latent 与遮罩）。整份清掉再写，否则残留的 89 张
    # 遮罩会被 `load_muse_assets` 当成共享资产读进来，尺寸全对不上。
    if os.path.isdir(mask_dir):
        shutil.rmtree(mask_dir, ignore_errors=True)
    os.makedirs(mask_dir, exist_ok=True)

    cv2.imwrite(os.path.join(mask_dir, '%08d.png' % 0), assets['mask'])
    with open(os.path.join(base, MUSE_COORDS_FILE), 'wb') as handle:
        pickle.dump([assets['coord']], handle)
    with open(os.path.join(base, MUSE_MASK_COORDS_FILE), 'wb') as handle:
        pickle.dump([assets['mask_coord']], handle)
    torch.save([assets['latent']], os.path.join(base, MUSE_LATENTS_FILE))

    face_box = [int(round(float(v))) for v in list(assets['coord'])[:4]]
    meta = {
        'mode': MUSE_ASSET_MODE_SHARED,
        'avatar_id': avatar_id,
        'source_frames': int(assets['source_frames']),
        'face_box': face_box,
        'created_at': datetime.now().isoformat(timespec='seconds'),
    }
    with open(os.path.join(base, MUSE_ASSET_META), 'w', encoding='utf-8') as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)

    logger.info('[手势] %s 的人脸资产已复用形象 %s（1 份，脸框 %s）',
                gesture_id, avatar_id, tuple(face_box))
    return {'ready': True, 'frames': 1, 'skipped': False}
