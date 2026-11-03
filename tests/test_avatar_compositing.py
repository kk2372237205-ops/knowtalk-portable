# -*- coding: utf-8 -*-
"""抠像与背景合成：缺素材不回退、缓存不撞键、单帧合成在实时预算内。

对应 openspec/changes/fix-matte-compositing-and-resource-release/。

为什么这几条要钉死：合成层原来在「该动作没有 alpha」时会静默回退用默认素材的
alpha。默认是站姿、动作是抬手，两套轮廓对不上，动作帧里的白底被当成人体保留，
深色背景上就是一大块白影；更糟的是默认素材只有一帧，回退后所有动作帧的缓存键
撞在一起，整段动作返回同一张图（画面冻结）。两个症状现场都见过。
"""
import json
import os
import time

import cv2
import numpy as np
import pytest

from utils import avatar_background as ab


def write_alpha(path, mask):
    """写一张 8 位灰度 alpha（mask 是 0/255 的二维数组）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    assert cv2.imwrite(path, mask.astype(np.uint8))


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    """搭一套最小形象：默认素材（上下两半）+ 一个两帧动作，另有一个没有素材的动作。"""
    root = str(tmp_path / "avatars")
    monkeypatch.setattr(ab, "AVATAR_ROOT", root)
    aid = "demo"
    matte = os.path.join(root, aid, "matte")
    h, w = 60, 40

    half = np.zeros((h, w), np.uint8)
    half[: h // 2] = 255                       # 上半是人、下半是背景
    write_alpha(os.path.join(matte, "base", "00000000.png"), half)

    # 动作 ga：第 0 帧人占上半，第 1 帧人占左半 —— 两帧 alpha 不同
    left = np.zeros((h, w), np.uint8)
    left[:, : w // 2] = 255
    write_alpha(os.path.join(matte, "gestures", "ga", "00000000.png"), half)
    write_alpha(os.path.join(matte, "gestures", "ga", "00000001.png"), left)

    with open(os.path.join(matte, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"avatar": aid, "bg_color": [255, 255, 255], "size": [w, h]}, fh)

    cfg = {"type": "color", "value": "#101F33"}
    frame = np.full((h, w, 3), 255, np.uint8)  # 源帧是白底人像
    return {"comp": ab.AvatarBackground(aid, cfg), "frame": frame, "h": h, "w": w,
            "matte": matte}


def test_missing_gesture_alpha_keeps_original_frame(studio):
    """没有该动作的 alpha 时保持原帧，绝不拿默认素材的 alpha 硬套。

    默认 alpha 是"上半是人"，若真回退了，下半会被换成深色背景；
    正确行为是整幅原样返回。
    """
    comp, frame = studio["comp"], studio["frame"]
    out = comp.compose(frame, "nope", 0)
    assert np.array_equal(out, frame), "缺素材时不得合成（更不得回退 base 的 alpha）"


def test_two_frames_of_same_gesture_differ(studio):
    """同一动作的不同帧必须算出不同结果 —— 否则就是缓存撞键导致的画面冻结。"""
    comp, frame = studio["comp"], studio["frame"]
    a = comp.compose(frame, "ga", 0)
    b = comp.compose(frame, "ga", 1)
    assert not np.array_equal(a, b)


def test_same_frame_hits_cache(studio):
    """同一帧反复合成（静默时来回播同一套图）走缓存，返回同一个对象。"""
    comp, frame = studio["comp"], studio["frame"]
    first = comp.compose(frame, "ga", 0)
    second = comp.compose(frame, "ga", 0)
    assert first is second


def test_base_still_composites(studio):
    """默认素材（base）自己有 alpha，照常合成。"""
    comp, frame = studio["comp"], studio["frame"]
    out = comp.compose(frame, None, 0)
    assert not np.array_equal(out, frame)
    # 下半是背景色 #101F33 → BGR (51, 31, 16)
    assert tuple(int(v) for v in out[-1, -1]) == (51, 31, 16)


def test_speed_within_budget(tmp_path, monkeypatch):
    """1080×538 的单帧合成必须留在实时预算内（25fps 时每帧 40ms）。

    几版实现的实测：numpy float32 全图三次乘加 19.4ms →
    `out = src + (1-a)(bg - bg_src)` 定点版约 2ms → 加上前景去污染的外推后
    **真实素材 17ms 中位**（外推 12ms + 混合 2ms + PNG 解码 1.5ms）。
    外推从 2 轮加到 6 轮多花的 6ms 买的是边缘不再掺背景色（见
    `test_deep_soft_edge_has_no_haze`），值。阈值取 26ms，留足机器差异的余量。

    注意羽化宽度要贴近真实素材（AI 抠像的过渡带 3~5px）：用窄斜坡的话外推循环
    会在第 3 轮就因环带为空而退出，轮数再大也测不出真实开销。
    """
    root = str(tmp_path / "avatars")
    monkeypatch.setattr(ab, "AVATAR_ROOT", root)
    aid, h, w = "demo", 538, 1080                   # 与 musetalk_ke 素材同尺寸
    matte = os.path.join(root, aid, "matte")
    mask = np.zeros((h, w), np.uint8)
    mask[:, : w // 2] = 255
    mask = cv2.GaussianBlur(mask, (0, 0), 1.4).astype(np.uint8)   # 真实软边 3~5px，不是刀切
    for i in range(24):                             # 每帧都不同，逼它真算
        m = mask.copy()
        if i % 2:
            m = cv2.bitwise_not(m)
        write_alpha(os.path.join(matte, "gestures", "ga", "%08d.png" % i), m)
    with open(os.path.join(matte, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"avatar": aid, "bg_color": [255, 255, 255]}, fh)

    comp = ab.AvatarBackground(aid, {"type": "color", "value": "#101F33"})
    frame = np.full((h, w, 3), 240, np.uint8)
    samples = []
    for i in range(24):
        t0 = time.perf_counter()
        comp.compose(frame, "ga", i)
        samples.append((time.perf_counter() - t0) * 1000)

    median = sorted(samples)[len(samples) // 2]
    assert median < 26.0, "单帧合成 %.1fms，超出实时预算" % median


def test_background_area_equals_new_background_exactly(tmp_path, monkeypatch):
    """alpha=0 的区域必须严格等于新背景，源素材的投影/反光一律不带过来。

    现场看到「人像旁边拖着一团黑影/白影」，根因就在这里：源素材是棚拍，脚下有
    接触阴影、身体周围有打光反光、影棚本身上亮下暗，那一片的 `src` 根本不等于
    标准背景色。旧式子 `out = src + (1-a)(bg - bg_src)` 在 a=0 处会原样保留
    `src - bg_src` 这个差 —— 换完背景还能看见原来那张照片的影子。
    实测每帧有 5000~10000 个像素偏离新背景 15 以上，残差图里是一个完整的人形轮廓。
    """
    root = str(tmp_path / "avatars")
    monkeypatch.setattr(ab, "AVATAR_ROOT", root)
    aid, h, w = "shadow", 40, 60
    matte = os.path.join(root, aid, "matte")
    alpha = np.zeros((h, w), np.uint8)
    alpha[:, : w // 2] = 255                      # 左半是人
    write_alpha(os.path.join(matte, "base", "00000000.png"), alpha)

    frame = np.full((h, w, 3), 255, np.uint8)     # 标准背景是纯白
    frame[:, w // 2:] = 200                       # 右半是"人像旁边的投影"，比背景暗

    comp = ab.AvatarBackground(aid, {"type": "color", "value": "#101F33"})
    out = comp.compose(frame, None, 0)
    assert tuple(int(v) for v in out[10, w - 5]) == (51, 31, 16), \
        "背景区（含投影）必须严格等于新背景，不得残留源素材"


def test_soft_edge_blends_foreground_not_background(tmp_path, monkeypatch):
    """羽化出来的半透明边缘，混进去的必须是人体色，不能是源背景色。

    抠像为了不吃到白边做了腐蚀 + 羽化，代价是轮廓内侧 1~3px 的 alpha 掉到 0.2~0.8，
    但那些像素**本来是实心人体**（实测边缘带 100% 更接近不透明前景，误差 0.0 对
    93.5）。旧式子在这种像素上会算出负数被截断成纯黑 —— 比人体和背景都暗，
    就是那道黑边；换到浅色底上则是反过来的亮边。
    """
    root = str(tmp_path / "avatars")
    monkeypatch.setattr(ab, "AVATAR_ROOT", root)
    aid, h, w = "soft", 40, 60
    matte = os.path.join(root, aid, "matte")
    alpha = np.zeros((h, w), np.uint8)
    alpha[:, :28] = 255                           # 实心人体
    alpha[:, 28:32] = 128                         # 羽化出来的斜坡
    write_alpha(os.path.join(matte, "base", "00000000.png"), alpha)

    frame = np.full((h, w, 3), 255, np.uint8)
    frame[:, :32] = 10                            # 含斜坡在内都是深色人体，不是白边

    comp = ab.AvatarBackground(aid, {"type": "color", "value": "#101F33"})
    out = comp.compose(frame, None, 0)
    edge = out[20, 30]
    # 正确值 ≈ 0.5×人体(10) + 0.5×背景(51,31,16) ≈ 30；旧式子会算出 -92 被截断成 0
    assert 20 < int(edge[0]) < 45, "半透明边缘应是人体色与背景的混合，不该被压成纯黑"
    assert np.all(out[:, 40:] == np.array([51, 31, 16], np.uint8))


def test_deep_soft_edge_has_no_haze(tmp_path, monkeypatch):
    """宽软边（AI 抠像那种 5px 过渡带）外圈不得残留源背景色。

    外推是按圈往外推的。只推 2 圈时，过渡带外圈约 30% 的像素（真实素材
    3000~4000 个/帧）拿不到干净前景，仍带着源背景的白，合成后就是手部和身体
    轮廓上那层去不掉的白雾——挥手、胸前手势这些手部动作尤其明显。

    这条把「过冲」钉死：合成结果必须落在 [人体色, 背景色] 之间。混进去的若是
    源背景色，结果就会顶穿这个区间。r2 会挂（外圈过冲 25 上下），r6 全过。
    """
    root = str(tmp_path / "avatars")
    monkeypatch.setattr(ab, "AVATAR_ROOT", root)
    aid, h, w = "haze", 40, 60
    matte = os.path.join(root, aid, "matte")

    FG, BG_SRC = 10.0, 255.0                      # 深色人体 / 白影棚底
    alpha = np.zeros((h, w), np.uint8)
    alpha[:, :20] = 255                           # 实心人体
    ramp = np.linspace(220, 30, 6).astype(np.uint8)   # 5~6px 宽的过渡带
    alpha[:, 20:26] = ramp

    frame = np.full((h, w, 3), BG_SRC, np.uint8)
    a = alpha[:, :26].astype(np.float32) / 255.0
    # 源帧在过渡带上就是「人体与背景的混合」——这是软边像素的真实来源
    frame[:, :26] = np.clip(a[:, :, None] * FG + (1 - a)[:, :, None] * BG_SRC, 0, 255)

    write_alpha(os.path.join(matte, "base", "00000000.png"), alpha)
    comp = ab.AvatarBackground(aid, {"type": "color", "value": "#101F33"})
    out = comp.compose(frame, None, 0).astype(np.float32)

    bg = np.array([51.0, 31.0, 16.0])             # #101F33 的 BGR
    lo, hi = np.minimum(FG, bg), np.maximum(FG, bg)
    band = out[:, 20:26]                          # 只看过渡带
    over = np.maximum(0.0, band - hi) + np.maximum(0.0, lo - band)
    worst = float(over.max())
    assert worst < 3.0, "过渡带残留了源背景色，过冲 %.1f（外推没盖住整条软边）" % worst
    # 实心区保持人体色、背景区严格等于新背景
    assert abs(float(out[10, 5, 0]) - FG) < 3.0
    assert np.allclose(out[10, 40], bg, atol=1.0)


def test_switching_background_drops_caches(studio):
    """换背景后旧合成结果必须失效（否则画面还是上一张背景）。"""
    comp, frame = studio["comp"], studio["frame"]
    first = comp.compose(frame, "ga", 0)
    comp._bgimg = np.full((8, 8, 3), (0, 0, 255), np.uint8)   # 换成纯红
    comp._reset_caches()
    second = comp.compose(frame, "ga", 0)
    assert not np.array_equal(first, second)
