# -*- coding: utf-8 -*-
"""抠像里"背景缝"与"白色衣物"的区分判据（防回归）。

对应 openspec/changes/fix-matte-compositing-and-resource-release/。

素材是白底影棚（背景色 ≈ [253,253,253]），白衬衫和"手臂与躯干之间透出来的背景"
**颜色完全一样**，任何"是否等于背景色"的单条判据都切不开——实测两者中位色距分别是
52.0 和 4.4~11.0，差 5 倍以上，所以颜色判据要用"中位色距"这种整体度量；但更要命
的是形状：真缝是顺着肢体延伸的窄长条（外接长宽比 6~15），白衬衫上的亮补丁是团块
（1.15）。

历史上踩过的坑，都由这几条用例钉住：
* 只靠"最大内切半径"做阈值（4.5px）——偏偏真缝有 5.0~7.4px 的，结果是"缝只清掉
  一半"，且相邻帧一会儿清一会儿不清，播出时缝隙闪烁。
* 用"区块里接近背景色的像素占比"做主判据——遇到接近白底的布料直接翻车
  （thanks 那帧被啃掉 3 倍）。
* 只在候选阶段拦、不在成品上复核——闭运算会把小缝种子向外扩胖，胖到超过最大半径
  才停下来，此时已经啃到了旁边的衬衫（实测啃掉 1148 像素）。
"""
import cv2
import numpy as np
import pytest

from scripts import avatar_matte

BG = 244          # 近白的影棚底
BODY = (40, 40, 60)
LO = 18.0


def build_frame(shapes):
    """白底 + 深色「人体」矩形，再按 shapes 在人体内部挖出白色区域。

    shapes: [(x0, y0, x1, y1), ...] 挖出来的地方会露回白色背景。
    """
    h, w = 300, 200
    img = np.full((h, w, 3), BG, np.uint8)
    cv2.rectangle(img, (20, 20), (180, 280), BODY, -1)
    for (x0, y0, x1, y1) in shapes:
        cv2.rectangle(img, (x0, y0), (x1, y1), (BG, BG, BG), -1)
    return img


def gap_mask(img):
    bg = avatar_matte.estimate_background(img)
    dist = np.linalg.norm(img.astype(np.float32) - bg, axis=2)
    hard_bg = (dist < LO).astype(np.uint8)
    num, labels = cv2.connectedComponents(hard_bg, connectivity=4)
    edge = set(labels[0, :].tolist()) | set(labels[-1, :].tolist())
    edge |= set(labels[:, 0].tolist()) | set(labels[:, -1].tolist())
    edge.discard(0)
    outer = np.isin(labels, list(edge)).astype(np.uint8)
    return avatar_matte._thin_background_gaps(img, dist, outer, LO)


def assert_enclosed(outer_shapes, img):
    """前置条件：这些白块不能连到画面外的背景，否则一进底色阶段就被透明化了，
    根本轮不到缝隙判据出场，测出来的是假结果。"""
    bg = avatar_matte.estimate_background(img)
    dist = np.linalg.norm(img.astype(np.float32) - bg, axis=2)
    hard_bg = (dist < LO).astype(np.uint8)
    _, labels = cv2.connectedComponents(hard_bg, connectivity=4)
    edge = set(labels[0, :].tolist()) | set(labels[-1, :].tolist())
    edge |= set(labels[:, 0].tolist()) | set(labels[:, -1].tolist())
    edge.discard(0)
    for (x0, y0, x1, y1) in outer_shapes:
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        assert labels[cy, cx] not in edge, "挖出的白块不该连到画面边界"


class TestGapDiscriminator:
    """形状决定真伪：窄长条是缝，团块是衣物。二者颜色完全相同。"""

    def test_thin_slit_is_cleared(self):
        slit = [(60, 60, 63, 140)]          # 3px 宽 × 80px 高，沿着肢体
        img = build_frame(slit)
        assert_enclosed(slit, img)
        gap = gap_mask(img)
        assert gap[100, 61] == 1, "细长缝隙应当被判成背景（透出 deform 背景）"

    def test_wider_slit_is_cleared(self):
        """回归：以前 max_radius=4.5 会把这种偏粗的臂缝漏掉，缝只清掉一半、播出闪烁。

        12px 宽的缝内切半径 6px，正好越过当年的阈值。
        """
        slit = [(60, 60, 72, 150)]          # 12px 宽 × 90px 高
        img = build_frame(slit)
        assert_enclosed(slit, img)
        gap = gap_mask(img)
        assert gap[100, 66] == 1, "偏粗的臂缝也必须判成背景"

    def test_compact_white_patch_is_kept(self):
        """白衬衫褶皱里被照亮的一小块：颜色与白底无异，但它是团块，不能挖。"""
        patch = [(120, 180, 145, 205)]      # 25×25 的团块
        img = build_frame(patch)
        assert_enclosed(patch, img)
        gap = gap_mask(img)
        # 整块都不能被判成缝（允许个别边缘毛刺，主体必须保住）
        sub = gap[180:205, 120:145]
        assert int(sub.sum()) < sub.size * 0.1, "团块状白补丁不能被当成缝隙挖掉"

    def test_slit_and_patch_together(self):
        """同一帧里既有缝又有衣物：只准挖缝，不许碰衣物。"""
        slit = [(60, 60, 64, 150)]
        patch = [(120, 180, 145, 205)]
        img = build_frame(slit + patch)
        assert_enclosed(slit + patch, img)
        gap = gap_mask(img)
        assert gap[100, 62] == 1, "缝要挖开"
        sub = gap[180:205, 120:145]
        assert int(sub.sum()) < sub.size * 0.1, "同一帧里的白衣物不能跟着被挖"

    def test_no_frame_time_regression_is_implicit(self):
        """同一次发布的相邻帧不该出现"一帧挖开、一帧没挖"的闪烁。

        几何稍微抖动（缝宽 ±1px）时结论必须稳定。
        """
        results = []
        for width in (62, 63, 64, 65):          # 缝在 3~6px 间抖动
            slit = [(60, 60, width, 150)]
            img = build_frame(slit)
            gap = gap_mask(img)
            results.append(int(gap[100, 61] == 1))
        assert results == [1, 1, 1, 1], \
            "缝宽抖动时判定必须稳定，否则播出会闪烁：%r" % (results,)


@pytest.mark.parametrize("ratio", [1.0, 1.5, 2.0])
def test_chunky_regions_rejected_by_aspect(ratio):
    """外接长宽比低于 min_aspect 的块一律保留（不是缝）。"""
    side = 30
    h = int(side * ratio) if ratio >= 1 else side
    w = side
    patch = [(120, 180, 120 + w, 180 + h)]
    img = build_frame(patch)
    gap = gap_mask(img)
    sub = gap[180:180 + h, 120:120 + w]
    assert int(sub.sum()) < sub.size * 0.1
