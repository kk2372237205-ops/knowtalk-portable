# -*- coding: utf-8 -*-
"""验收 Mr.Chen 新版手势素材（裁切 + pad_scale=0.35 版）。

对标默认素材 explain，逐项检查：
  1. 帧尺寸 == 538x1080（运行期零缩放）
  2. 脸框尺寸/宽高比 vs explain（不压扁的核心指标：宽高比应≈源视频检测比≈0.76，
     若被横向压扁会掉到 0.67 附近）
  3. 头部漂移：脸框中心全程位移量（越小越好，explain 为 17px 基线）
  4. 首尾回位：首帧 vs 末帧（脸框差 + 画面差分）
  5. 人物剪影框：非白像素外接框，确认全身入画、无横向压缩

用法: python scripts/verify_gestures.py
"""
import os
import pickle
import sys

PROJECT = r'D:\00-Project\KnowTalking-main'
sys.path.insert(0, PROJECT)

import cv2
import numpy as np

GESTURE_ROOT = os.path.join(PROJECT, 'data', 'avatars', 'Mr.Chen', 'gestures')
STAGE = os.path.join(PROJECT, 'data', 'tmp', 'gesture_stage')
TARGET = (538, 1080)
GIDS = ['open', 'chop', 'compare', 'explain']


def load_set(gid):
    """返回 (full_dir, coords)。优先读暂存区，explain 读已安装区。"""
    if gid == 'explain':
        base = os.path.join(GESTURE_ROOT, 'explain')
    else:
        base = os.path.join(STAGE, gid)
    full_dir = os.path.join(base, 'full_imgs')
    with open(os.path.join(base, 'coords.pkl'), 'rb') as fh:
        coords = pickle.load(fh)
    files = sorted(os.listdir(full_dir))
    return full_dir, files, coords


def face_metrics(coords):
    """脸框统计：尺寸/宽高比/中心/全程漂移。coords 元素为 (y1,y2,x1,x2)。"""
    w = np.array([x2 - x1 for (_y1, _y2, x1, x2) in coords], dtype=float)
    h = np.array([y2 - y1 for (y1, y2, _x1, _x2) in coords], dtype=float)
    cx = np.array([(x1 + x2) / 2.0 for (_y1, _y2, x1, x2) in coords])
    cy = np.array([(y1 + y2) / 2.0 for (y1, y2, _x1, _x2) in coords])
    drift = np.sqrt((cx - cx[0]) ** 2 + (cy - cy[0]) ** 2)
    return dict(w=float(w.mean()), h=float(h.mean()), ar=float((w / h).mean()),
                cx0=float(cx[0]), cy0=float(cy[0]),
                drift_max=float(drift.max()), drift_mean=float(drift.mean()))


def silhouette_bbox(img):
    """非白（人物剪影）外接框，返回 (x, y, w, h)。

    白底影棚，但视频压缩让背景变成 234~251 的噪点白，固定阈值会把整帧误判为
    前景——用 Otsu 自适应分开「背景白」与「人物/阴影」。
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    thresh, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask = gray < max(thresh, 128)   # Otsu 偏低时兜底，防止把背景噪点算进来
    # 基础视频/部分源视频带 1~2px 黑边（项目既有特征），排除边缘 3px 再算外接框
    m = np.zeros_like(mask)
    m[3:-3, 3:-3] = mask[3:-3, 3:-3]
    mask = m
    if not mask.any():
        return None
    ys, xs = np.where(mask)
    return int(xs.min()), int(ys.min()), int(xs.max() - xs.min()), int(ys.max() - ys.min())


def frame_diff(a, b):
    """两帧差分（排除近白背景噪声）。"""
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.int16)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.int16)
    d = np.abs(ga - gb)
    mask = d > 25
    return int(mask.sum()), float(d[mask].mean()) if mask.any() else 0.0


def main():
    print('=' * 78)
    print('验收：帧尺寸 / 脸框(尺寸·宽高比·漂移) / 首尾回位 / 人物剪影')
    print('=' * 78)
    summary = {}
    for gid in GIDS:
        full_dir, files, coords = load_set(gid)
        first = cv2.imread(os.path.join(full_dir, files[0]))
        h, w = first.shape[:2]
        fm = face_metrics(coords)

        # 首尾回位
        last = cv2.imread(os.path.join(full_dir, files[-1]))
        n_px, m_diff = frame_diff(first, last)
        y1, y2, x1, x2 = coords[0]
        y1e, y2e, x1e, x2e = coords[-1]
        face_ret = (abs(x1 - x1e) + abs(x2 - x2e) + abs(y1 - y1e) + abs(y2 - y2e)) / 4.0

        # 剪影（首/中/末三帧）
        sil = []
        for idx in (0, len(files) // 2, len(files) - 1):
            img = cv2.imread(os.path.join(full_dir, files[idx]))
            sil.append(silhouette_bbox(img))

        summary[gid] = dict(size=(w, h), fm=fm, face_ret=face_ret,
                            tail_px=n_px, tail_mean=m_diff, sil=sil)

        print('\n【%s】共 %d 帧，尺寸 %dx%d' % (gid, len(files), w, h))
        print('  脸框      : %.0fx%.0f  宽高比 %.3f  (explain 基线见下)' % (fm['w'], fm['h'], fm['ar']))
        print('  头部漂移  : max %.1fpx  mean %.1fpx' % (fm['drift_max'], fm['drift_mean']))
        print('  首尾回位  : 脸框均值差 %.1fpx，画面变化像素 %d (mean %.1f)' % (face_ret, n_px, m_diff))
        for tag, s in zip(('首', '中', '末'), sil):
            if s:
                print('  %s帧剪影   : x=%d y=%d w=%d h=%d  (宽高比 %.3f，占高 %.0f%%)'
                      % (tag, s[0], s[1], s[2], s[3], s[2] / float(s[3]), 100.0 * s[3] / h))

    ex = summary['explain']
    print('\n' + '=' * 78)
    print('对标 explain（脸框 %.0fx%.0f，宽高比 %.3f，漂移 max %.1fpx）：'
          % (ex['fm']['w'], ex['fm']['h'], ex['fm']['ar'], ex['fm']['drift_max']))
    for gid in ('open', 'chop', 'compare'):
        s = summary[gid]
        dw = 100.0 * (s['fm']['w'] - ex['fm']['w']) / ex['fm']['w']
        dar = s['fm']['ar'] - ex['fm']['ar']
        print('  %-8s 脸框 %+.0f%%  宽高比 %+.3f  漂移 max %.1fpx  回位 %.1fpx'
              % (gid, dw, dar, s['fm']['drift_max'], s['face_ret']))

    # 生成对比图：每个手势取 首/中/末 三帧横排
    panels = []
    for gid in GIDS:
        full_dir, files, _ = load_set(gid)
        row = []
        for idx in (0, len(files) // 2, len(files) - 1):
            img = cv2.imread(os.path.join(full_dir, files[idx]))
            img = cv2.resize(img, (179, 360))
            cv2.putText(img, '%s f%d' % (gid, idx), (4, 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
            row.append(img)
        panels.append(np.hstack(row))
    out = np.vstack(panels)
    out_path = os.path.join(PROJECT, 'data', 'tmp', 'gesture_stage', 'framing_compare.png')
    cv2.imwrite(out_path, out)
    print('\n对比图已存: %s' % out_path)
    print('（每行 = 首帧 | 中间帧 | 末帧；行序 open/chop/compare/explain）')


if __name__ == '__main__':
    main()
