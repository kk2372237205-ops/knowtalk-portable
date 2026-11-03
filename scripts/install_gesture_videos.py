# -*- coding: utf-8 -*-
"""把 MiniMax 产出的手势视频转成 Mr.Chen 的手势素材三件套（暂存）。

用法: python scripts/install_gesture_videos.py

产物落 data/tmp/gesture_stage/{gid}/（full_imgs/ face_imgs/ coords.pkl），
验收通过后再由调用方拷进 data/avatars/Mr.Chen/gestures/{gid}/ 并补 gesture.json。

两个关键参数（与 Mr.Chen 默认素材逐像素核对过，别改）：
  - pad_scale=0.35：默认素材 coords.pkl[75]=(82,252,203,330) 正是检测框
    [229,117,304,217] 按 0.35 扩边所得；传 0 会得到紧贴脸的小框，与整套素材不一致。
  - 尺寸归一化用**裁切**不用 resize：源 1440x2560(0.562) 比目标 538x1080(0.498) 宽，
    裁两侧各 34px；若硬 resize 会把宽度压掉 11.2%，人明显变窄。
"""
import glob
import os
import pickle
import shutil
import sys

PROJECT = r'D:\00-Project\KnowTalking-main'
sys.path.insert(0, PROJECT)

import cv2  # noqa: E402

SRC = r'D:\MiniMax Design Data\Projects\1、严格匹配@图片1中的同一成年东亚男性\assets'
STAGE = os.path.join(PROJECT, 'data', 'tmp', 'gesture_stage')
AVATAR_ID = 'Mr.Chen'
GESTURE_ROOT = os.path.join(PROJECT, 'data', 'avatars', AVATAR_ID, 'gestures')
TARGET = (538, 1080)          # = 基础视频 full_imgs 尺寸（load_gesture 的 ref_size），避免运行期再缩放
                               # 注意不是 explain 的 536x1080——那是个既有 2px 偏差，别对齐它
FACE_SIZE = 256               # 与默认素材 face_imgs 一致

# 文件名 -> 手势 id / 显示名
JOBS = [
    ('双手摊开手势.mp4', 'open', '掌心向上展示'),
    ('右手下切手势.mp4', 'chop', '切砍强调'),
    ('双手对比手势.mp4', 'compare', '左右交替对比'),
]


def crop_to_target(out_dir, target=TARGET):
    """把抽出的帧等比裁到目标尺寸 —— 只裁不缩放，绝不压扁。

    源（1440x2560，宽高比 0.562）比目标（538x1080，0.498）宽，所以裁掉两侧各 34px。
    若曾用 resize 硬缩到 538x1080，宽度会被压掉 11.2%，人明显变窄 —— 已踩过。
    裁完之后若尺寸仍不等于 target（极端宽高比），才做一次**等比**缩放并同步缩放 coords。
    """
    files = sorted(glob.glob(os.path.join(out_dir, 'full_imgs', '*.png')))
    if not files:
        raise RuntimeError('没有抽到帧: %s' % out_dir)
    first = cv2.imread(files[0])
    h, w = first.shape[:2]
    tw, th = target
    if (w, h) == (tw, th):
        print('  尺寸已是 %dx%d，跳过' % (w, h))
        return

    ta = tw / float(th)
    if w / float(h) > ta:            # 太宽 -> 裁宽（保留全高）
        keep_w = int(round(h * ta))
        x_off = (w - keep_w) // 2
        mode = 'crop_w'
    else:                            # 太高 -> 裁高（保留全宽）
        keep_h = int(round(w / ta))
        y_off = (h - keep_h) // 2
        mode = 'crop_h'
    print('  裁切模式 %s：%dx%d -> %dx%d' % (mode, w, h, keep_w if mode == 'crop_w' else w,
                                             h if mode == 'crop_w' else keep_h))

    sx = sy = 1.0
    ox = oy = 0
    if mode == 'crop_w':
        ox, oy = x_off, 0
        if keep_w != tw:             # 理论上不会发生，兜底
            sx = tw / float(keep_w)
    else:
        ox, oy = 0, y_off
        if keep_h != th:
            sy = th / float(keep_h)

    for f in files:
        img = cv2.imread(f)
        if img is None:
            raise RuntimeError('读帧失败: %s' % f)
        if mode == 'crop_w':
            img = img[:, ox:ox + keep_w]
        else:
            img = img[oy:oy + keep_h, :]
        if (sx, sy) != (1.0, 1.0):
            img = cv2.resize(img, (tw, th), interpolation=cv2.INTER_LANCZOS4)
        cv2.imwrite(f, img)

    cp = os.path.join(out_dir, 'coords.pkl')
    with open(cp, 'rb') as fh:
        coords = pickle.load(fh)
    new_coords = []
    for (y1, y2, x1, x2) in coords:
        nx1 = int(round((x1 - ox) * sx))
        nx2 = int(round((x2 - ox) * sx))
        ny1 = int(round((y1 - oy) * sy))
        ny2 = int(round((y2 - oy) * sy))
        new_coords.append((ny1, ny2, nx1, nx2))
    with open(cp, 'wb') as fh:
        pickle.dump(new_coords, fh)
    print('  coords 已按裁切偏移/缩放同步修正')


def convert_all():
    from avatars.wav2lip.genavatar import generate_avatar

    os.makedirs(STAGE, exist_ok=True)
    for name, gid, _label in JOBS:
        src = os.path.join(SRC, name)
        out = os.path.join(STAGE, gid)
        if not os.path.isfile(src):
            print('[跳过] 找不到源视频: %s' % src)
            continue
        if os.path.isdir(out) and os.path.isfile(os.path.join(out, 'coords.pkl')):
            print('[跳过] %s 已转换过（%s）' % (gid, out))
        else:
            print('=== 转换 %s -> %s ===' % (name, gid))
            # pad_scale=0.35：与 Mr.Chen 默认素材 / explain 完全一致
            # （默认素材 coords.pkl[75]=(82,252,203,330) 正是检测框 [229,117,304,217]
            #  按 0.35 扩边所得，已逐像素核对）
            generate_avatar(video_path=src, avatar_id=gid, save_path=STAGE,
                            img_size=FACE_SIZE, pads=[0, 10, 0, 0], nosmooth=False,
                            face_det_batch_size=16, pad_scale=0.35)
            crop_to_target(out)
    print('转换阶段结束')


if __name__ == '__main__':
    convert_all()
