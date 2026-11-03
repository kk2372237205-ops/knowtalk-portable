from os import listdir, path
import numpy as np
import scipy, cv2, os, sys, argparse
import json, subprocess, random, string
from tqdm import tqdm
from glob import glob
import torch
import pickle
import time

# 本脚本既能被 `python -m avatars.wav2lip.genavatar` 调用，也常被直接当脚本跑
# （`python avatars/wav2lip/genavatar.py --video ...`）。直接跑时 sys.path[0] 是
# `avatars/wav2lip/`，`from avatars...` 会 ImportError，所以先把项目根加进 sys.path。
_PROJECT_ROOT = path.dirname(path.dirname(path.dirname(path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from avatars.wav2lip import face_detection


device = 'cuda' if torch.cuda.is_available() else 'cpu'
print('Using {} for inference.'.format(device))

def osmakedirs(path_list):
    for path in path_list:
        os.makedirs(path) if not os.path.exists(path) else None

def video2imgs(vid_path, save_path, ext='.png', cut_frame=10000000, max_res=1080):
    # 单张图片直接作为第一帧：复制进 full_imgs，不解码视频。
    # 与 musetalk 的 genavatar 对齐——图片输入产出「静态背景 + 运行时嘴部驱动」的肖像形象。
    if _is_image_path(vid_path):
        frame = cv2.imread(vid_path)
        if frame is None:
            raise ValueError(f"无法读取图片素材：{vid_path}")
        h, w = frame.shape[:2]
        scale = min(max_res / max(h, w), 1.0)
        if scale < 1.0:
            new_w = int(w * scale) // 2 * 2
            new_h = int(h * scale) // 2 * 2
            print(f'原始分辨率: {w}x{h}, 缩放到 {new_w}x{new_h}')
            frame = cv2.resize(frame, (new_w, new_h))
        cv2.imwrite(f"{save_path}/00000000.png", frame)
        print(f"图片素材已作为单帧写入：{save_path}/00000000.png")
        return 1

    cap = cv2.VideoCapture(vid_path)
    count = 0
    first_frame = True
    while True:
        if count > cut_frame:
            break
        ret, frame = cap.read()
        if ret:
            # 缩放到最长边不超过 max_res，保持宽高比，且确保宽高为偶数（H.264 要求）
            h, w = frame.shape[:2]
            scale = min(max_res / max(h, w), 1.0)
            if scale < 1.0:
                new_w = int(w * scale) // 2 * 2
                new_h = int(h * scale) // 2 * 2
                if first_frame:
                    print(f'原始分辨率: {w}x{h}, 缩放到 {new_w}x{new_h}')
                    first_frame = False
                frame = cv2.resize(frame, (new_w, new_h))
            cv2.imwrite(f"{save_path}/{count:08d}.png", frame)
            count += 1
        else:
            break
    return count


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


def _is_image_path(path: str) -> bool:
    return os.path.splitext(str(path))[1].lower() in _IMAGE_EXTS


def read_imgs(img_list):
    frames = []
    print('reading images...')
    for img_path in tqdm(img_list):
        frame = cv2.imread(img_path)
        frames.append(frame)
    return frames

def get_smoothened_boxes(boxes, T):
	for i in range(len(boxes)):
		if i + T > len(boxes):
			window = boxes[len(boxes) - T:]
		else:
			window = boxes[i : i + T]
		boxes[i] = np.mean(window, axis=0)
	return boxes

def generate_avatar(video_path, avatar_id, save_path='./data/avatars', img_size=96, pads=[0, 10, 0, 0], nosmooth=False, face_det_batch_size=16, progress_callback=None, pad_scale=0.35):
    """
    生成avatar的核心逻辑

    Args:
        video_path: 输入视频路径
        avatar_id: Avatar ID
        save_path: 保存根路径
        img_size: 缩放后的图像大小
        pads: 人脸框固定填充 [top, bottom, left, right]（仅在 pad_scale=0 时生效）
        nosmooth: 是否禁用平滑
        face_det_batch_size: 人脸检测批处理大小
        progress_callback: 进度回调函数，接收 0-100 的整数
        pad_scale: 自适应填充比例，按人脸框宽高的比例扩展裁剪区域（0 则使用固定 pads）
    """
    total_start = time.time()
    avatar_path = os.path.join(save_path, avatar_id)
    full_imgs_path = os.path.join(avatar_path, "full_imgs")
    face_imgs_path = os.path.join(avatar_path, "face_imgs")
    coords_path = os.path.join(avatar_path, "coords.pkl")

    osmakedirs([avatar_path, full_imgs_path, face_imgs_path])

    if progress_callback: progress_callback(5)

    print(f"正在处理视频: {video_path}")
    video2imgs(video_path, full_imgs_path, ext='png')

    if progress_callback: progress_callback(20)

    input_img_list = sorted(glob(os.path.join(full_imgs_path, '*.[jpJP][pnPN]*[gG]')))
    frames = read_imgs(input_img_list)

    if progress_callback: progress_callback(40)

    print('正在检测人脸...')
    batch_size = face_det_batch_size
    print(f'总帧数: {len(frames)}, 批处理大小: {batch_size}')
    print('正在初始化人脸检测模型...')
    detector = face_detection.FaceAlignment(face_detection.LandmarksType._2D,
                                            flip_input=False, device=device)

    predictions = []
    start_time = time.time()
    total_batches = (len(frames) + batch_size - 1) // batch_size

    while 1:
        predictions = []
        try:
            for i in range(0, len(frames), batch_size):
                batch_idx = i // batch_size + 1
                print(f'\r人脸检测: 批次 {batch_idx}/{total_batches} ({i}/{len(frames)} 帧)', end='', flush=True)
                predictions.extend(detector.get_detections_for_batch(np.array(frames[i:i + batch_size])))
                
                if progress_callback:
                    progress = 40 + int((i + batch_size) / len(frames) * 40)
                    progress_callback(min(progress, 80))
            print(f'\n人脸检测完成，耗时: {time.time() - start_time:.1f}秒')
        except RuntimeError:
            if batch_size == 1:
                raise RuntimeError('Image too big to run face detection on GPU.')
            batch_size //= 2
            total_batches = (len(frames) + batch_size - 1) // batch_size
            print(f'\n显存不足! 批处理大小降为: {batch_size}')
            continue
        break

    results = []
    pady1, pady2, padx1, padx2 = pads
    for rect, image in zip(predictions, frames):
        if rect is None:
            rect = [0, 0, image.shape[1], image.shape[0]]

        if pad_scale > 0:
            # 自适应填充：按人脸检测框宽高比例扩展，确保裁剪区域包含完整面部
            face_w = rect[2] - rect[0]
            face_h = rect[3] - rect[1]
            pady1 = int(face_h * pad_scale)
            pady2 = int(face_h * pad_scale)
            padx1 = int(face_w * pad_scale)
            padx2 = int(face_w * pad_scale)

        y1 = max(0, rect[1] - pady1)
        y2 = min(image.shape[0], rect[3] + pady2)
        x1 = max(0, rect[0] - padx1)
        x2 = min(image.shape[1], rect[2] + padx2)
        results.append([x1, y1, x2, y2])

    boxes = np.array(results)
    if not nosmooth:
        boxes = get_smoothened_boxes(boxes, T=5)

    if pad_scale > 0:
        crop_w = int(boxes[0][2] - boxes[0][0])
        crop_h = int(boxes[0][3] - boxes[0][1])
        print(f'自适应填充: pad_scale={pad_scale}, 人脸裁剪尺寸 ~{crop_w}x{crop_h}')

    if progress_callback: progress_callback(85)

    coord_list = []
    print(f"正在保存人脸图片和坐标...")
    for idx, (rect, frame) in tqdm(enumerate(zip(boxes, frames)), total=len(boxes), desc='保存图片', unit='帧', ncols=100):
        face_frame = frame[int(rect[1]):int(rect[3]), int(rect[0]):int(rect[2])]
        resized_crop_frame = cv2.resize(face_frame, (img_size, img_size))
        cv2.imwrite(f"{face_imgs_path}/{idx:08d}.png", resized_crop_frame)
        coord_list.append((int(rect[1]), int(rect[3]), int(rect[0]), int(rect[2])))

        if progress_callback:
            progress = 85 + int((idx + 1) / len(boxes) * 15)
            progress_callback(progress)

    print(f"写入数据到坐标文件: {coords_path}")
    with open(coords_path, 'wb') as f:
        pickle.dump(coord_list, f)

    del detector
    if progress_callback: progress_callback(100)
    total_time = time.time() - total_start
    print(f"Avatar 生成完成！总耗时: {total_time:.1f}秒")

if __name__ == '__main__':
    import os
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    save_path = os.path.join(project_root, 'data', 'avatars')

    # 命令行用法（原来这里是写死的别人的视频路径，实际没法用）：
    #   python avatars/wav2lip/genavatar.py --video "D:\视频\88.mp4" --avatar_id 88
    # 参数说明：
    #   --video   必填，基础视频路径（人物正面、脸部清晰、光照均匀）
    #   --avatar_id  形象 id，默认 88
    #   --img_size  人脸裁剪边长，默认 256（与现有素材一致）
    #   --pad_scale 人脸框自适应外扩比例，默认 0.35（与现有手势素材同口径）
    # 生成结果落在 data/avatars/{avatar_id}/（full_imgs/ + face_imgs/ + coords.pkl）。
    ap = argparse.ArgumentParser(description='从视频生成 wav2lip 数字人素材')
    ap.add_argument('--video', required=True, help='基础视频路径')
    ap.add_argument('--avatar_id', default='88', help='形象 id')
    ap.add_argument('--img_size', type=int, default=256, help='人脸裁剪边长')
    ap.add_argument('--pad_scale', type=float, default=0.35, help='人脸框外扩比例')
    ap.add_argument('--face_det_batch_size', type=int, default=8)
    args = ap.parse_args()

    if not os.path.isfile(args.video):
        raise SystemExit('视频不存在: %s' % args.video)

    generate_avatar(
        video_path=args.video,
        avatar_id=args.avatar_id,
        save_path=save_path,
        img_size=args.img_size,
        pads=[0, 10, 0, 0],
        nosmooth=False,
        face_det_batch_size=args.face_det_batch_size,
        pad_scale=args.pad_scale,
    )
