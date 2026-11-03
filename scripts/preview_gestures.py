# -*- coding: utf-8 -*-
"""把动作素材合成到各种背景上录成视频，肉眼验收抠像效果。

抠像参数一改就是几百帧，光看单帧 PNG 判断不了「播起来什么样」——边缘抖动、
逐帧闪、脚下飘，都是动起来才看得见的。这个脚本走**真实合成路径**
（`utils.avatar_background.AvatarBackground.compose`），不是另写一套预览，
所以录出来的就是真机上会看到的画面。

用法::

    # 每个动作一条：同一帧并排铺在多个背景上（含未合成的原图作参照）
    python scripts/preview_gestures.py

    # 只录某几个动作
    python scripts/preview_gestures.py --gesture wave_one zoom

    # 全屏单背景（最接近真机观感）：538×1080 原生尺寸
    python scripts/preview_gestures.py --solo --gesture wave_one \\
        --background "#101F33" training_classroom.png

    # 串烧：所有动作顺序连播，看循环播放的整体观感
    python scripts/preview_gestures.py --reel --background "#101F33" training_dome.png

背景参数两种写法：`#RRGGBB` 是纯色，其余按文件名去 `data/backgrounds/` 找。
输出落在 `data/tmp/preview/`（不入库）；每条视频另存一张中间帧 PNG，
不用打开播放器就能扫一眼。
"""
import argparse
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import avatar_background as ab  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "tmp", "preview")
FPS = 24.0                      # 与动作素材的 gesture.json 一致


def _cfg(spec):
    """`#RRGGBB` → 纯色；否则当作 data/backgrounds/ 下的文件名。"""
    if str(spec).startswith("#"):
        return {"type": "color", "value": spec}
    return {"type": "image", "value": "data/backgrounds/" + spec}


def _frames(avatar, gesture):
    src = os.path.join(ROOT, "data", "avatars", avatar, "gestures", gesture, "full_imgs")
    return src, sorted(n for n in os.listdir(src) if n.endswith(".png"))


def _cell(label, frame, comp, gesture, idx, cell_h):
    """一格：合成 + 缩放 + 左上角标签（带底衬，浅色底也看得清）。"""
    out = frame if comp is None else comp.compose(frame.copy(), gesture, idx)
    cw = int(round(frame.shape[1] * cell_h / float(frame.shape[0])))
    tile = cv2.resize(out, (cw, cell_h), interpolation=cv2.INTER_AREA)
    cv2.rectangle(tile, (0, 0), (cw, 26), (0, 0, 0), -1)
    cv2.putText(tile, label, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
    return tile


def _board(panels, frame, comps, gesture, idx, cell_h):
    return np.hstack([_cell(lb, frame, c, gesture, idx, cell_h)
                      for (lb, _), c in zip(panels, comps)])


def _make_compositors(avatar, panels):
    comps = [None if cfg is None else ab.AvatarBackground(avatar, cfg) for _, cfg in panels]
    bad = [lb for (lb, _), c in zip(panels, comps) if c is not None and not c.enabled]
    if bad:
        raise SystemExit("合成器没启用（背景图路径不对或缺抠像素材）：%s" % ", ".join(bad))
    return comps


def record(avatar, gesture, panels, cell_h, tag):
    """一个动作 → 一条视频。"""
    src, names = _frames(avatar, gesture)
    comps = _make_compositors(avatar, panels)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, tag + ".mp4")
    f0 = cv2.imread(os.path.join(src, names[0]), cv2.IMREAD_COLOR)
    board = _board(panels, f0, comps, gesture, 0, cell_h)
    wri = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS,
                          (board.shape[1], board.shape[0]))
    if not wri.isOpened():
        raise SystemExit("打不开输出文件：%s" % path)
    t0 = time.perf_counter()
    for i, n in enumerate(names):
        bgr = cv2.imread(os.path.join(src, n), cv2.IMREAD_COLOR)
        wri.write(_board(panels, bgr, comps, gesture, i, cell_h))
    wri.release()
    mid = len(names) // 2
    cv2.imwrite(os.path.join(OUT_DIR, tag + "_mid.png"),
                _board(panels, cv2.imread(os.path.join(src, names[mid]), cv2.IMREAD_COLOR),
                       comps, gesture, mid, cell_h))
    dt = time.perf_counter() - t0
    print("%-34s %4d帧 %5.1fs (%3.0fms/帧)  %5.1fMB  → %s"
          % (tag, len(names), dt, dt * 1000 / len(names),
             os.path.getsize(path) / 1048576.0, path))


def reel(avatar, gestures, panels, cell_h, tag):
    """多个动作顺序连播 → 一条视频。"""
    comps = _make_compositors(avatar, panels)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, tag + ".mp4")
    src0, names0 = _frames(avatar, gestures[0])
    board = _board(panels, cv2.imread(os.path.join(src0, names0[0]), cv2.IMREAD_COLOR),
                   comps, gestures[0], 0, cell_h)
    wri = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS,
                          (board.shape[1], board.shape[0]))
    total, t0 = 0, time.perf_counter()
    for gid in gestures:
        src, names = _frames(avatar, gid)
        for i, n in enumerate(names):
            bgr = cv2.imread(os.path.join(src, n), cv2.IMREAD_COLOR)
            wri.write(_board(panels, bgr, comps, gid, i, cell_h))
            total += 1
    wri.release()
    dt = time.perf_counter() - t0
    print("%-34s %4d帧 %5.1fs            %5.1fMB  → %s"
          % (tag, total, dt, os.path.getsize(path) / 1048576.0, path))


def main(argv=None):
    ap = argparse.ArgumentParser(description="动作素材多背景合成预览（录视频）")
    ap.add_argument("--avatar", default="musetalk_ke")
    ap.add_argument("--gesture", nargs="*", default=None, help="默认全部动作")
    ap.add_argument("--background", nargs="*", default=None,
                    help='#RRGGBB 纯色，或 data/backgrounds/ 下的文件名')
    ap.add_argument("--solo", action="store_true",
                    help="全屏单背景（原生 538×1080），一个背景一条视频")
    ap.add_argument("--reel", action="store_true", help="所有动作顺序连播成一条")
    ap.add_argument("--cell-height", type=int, default=540,
                    help="并排时每格的高度（默认 540）")
    ap.add_argument("--compare", action="store_true",
                    help="并排时额外加一列未合成的原图作参照（默认已加）")
    args = ap.parse_args(argv)

    gdir = os.path.join(ROOT, "data", "avatars", args.avatar, "gestures")
    gestures = args.gesture or sorted(os.listdir(gdir))

    if args.solo:
        specs = args.background or ["#101F33", "training_classroom.png", "training_dome.png"]
        for gid in gestures:
            for spec in specs:
                record(args.avatar, gid, [(spec, _cfg(spec))], 1080,
                       "solo_%s_%s" % (gid, spec.lstrip("#").split(".")[0]))
        return 0

    if args.background:
        panels = [(s.lstrip("#").split(".")[0], _cfg(s)) for s in args.background]
    else:
        panels = [("原图-白底", None),
                  ("深蓝纯色", _cfg("#101F33")),
                  ("浅灰纯色", _cfg("#D9DDE2")),
                  ("科技大厅", _cfg("training_dome.png")),
                  ("明亮实训室", _cfg("training_classroom.png"))]

    if args.reel:
        reel(args.avatar, gestures, panels, args.cell_height, "reel_all")
    else:
        for gid in gestures:
            record(args.avatar, gid, panels, args.cell_height, "cmp_" + gid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
