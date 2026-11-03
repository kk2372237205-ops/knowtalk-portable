# -*- coding: utf-8 -*-
"""人像抠像：为数字人形象（含所有动作帧）生成透明背景的 alpha 素材。

生成结果：
    data/avatars/<id>/matte/manifest.json
    data/avatars/<id>/matte/base/00000000.png          # 默认素材的 alpha
    data/avatars/<id>/matte/gestures/<gid>/00000000.png # 每个动作每一帧的 alpha

alpha 是 8 位灰度图（255=人，0=背景），运行时会用它把人像合成到新背景上。

为什么不用 rembg：本项目形象都是纯色影棚底（浅灰/白），自带的"背景建模 + 距离
阈值 + 连通域填洞"反而更贴合素材、零依赖零下载，边缘也更干净；如果装了 rembg，
可以 `--engine rembg` 换它试试。

`--engine rvm` 换 AI 视频抠像（RobustVideoMatting）：模型直接知道哪里是透空、
哪里是衣服，不用再写形状判据去猜（chroma 为此有一套中位色距 + 内切半径 + 长宽比
的联合判据，还要反复拉扯"别吃掉白衬衫"）。当前素材就是用 rvm 生成的。

**RVM 是 GPL-3.0，代码不随本仓库分发**（utils/rvm/ 已加进 .gitignore），只用于本地
离线生成 alpha —— 生成物是纯像素数据，不含其代码，入库不受 copyleft 影响。
要复现或换形象时重跑：
    1. 取 RVM 的 model/ 目录放到 utils/rvm/（https://github.com/PeterL1n/RobustVideoMatting）
    2. 权重放 models/rvm/rvm_mobilenetv3.pth（14.5MB，GitHub Releases v1.0.0）
    3. python scripts/avatar_matte.py --avatar <id> --frames all --engine rvm --force
891 帧在 1050Ti 上约 46 秒。

用法：
    python scripts/avatar_matte.py --avatar musetalk_ke            # 抠全部帧 + 出预览图
    python scripts/avatar_matte.py --avatar musetalk_ke --frames base
    python scripts/avatar_matte.py --avatar musetalk_ke --force    # 忽略缓存重算
    python scripts/avatar_matte.py --avatar musetalk_ke --preview out.png
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

AVATAR_ROOT = os.path.join(ROOT, "data", "avatars")
MATTE_DIRNAME = "matte"
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")


# ── 素材发现 ─────────────────────────────────────────────────────────
def _sorted_frames(folder):
    if not os.path.isdir(folder):
        return []
    names = sorted(n for n in os.listdir(folder) if n.lower().endswith(IMG_EXTS))
    return [os.path.join(folder, n) for n in names]


def discover_sources(avatar_id):
    """返回 {源名: [帧路径...]}；源名 base / gestures.<gid>。"""
    base_dir = os.path.join(AVATAR_ROOT, avatar_id)
    sources = {"base": _sorted_frames(os.path.join(base_dir, "full_imgs"))}
    gestures_dir = os.path.join(base_dir, "gestures")
    if os.path.isdir(gestures_dir):
        for gid in sorted(os.listdir(gestures_dir)):
            frames = _sorted_frames(os.path.join(gestures_dir, gid, "full_imgs"))
            if frames:
                sources["gestures.%s" % gid] = frames
    return sources


# ── 背景建模 ─────────────────────────────────────────────────────────
def estimate_background(bgr):
    """按行估计背景色（容忍棚拍常见的上亮下暗渐变）。

    直接取全图一个中位色时，底部渐变会让脚边一片被误判为前景；按行估计能跟上
    渐变。行内再用边缘条带的中位数，避开中间的人。
    """
    h, w = bgr.shape[:2]
    band = max(4, int(w * 0.06))
    row_bg = np.zeros((h, 3), dtype=np.float32)
    for y in range(h):
        strip = np.concatenate([bgr[y, :band], bgr[y, w - band:]], axis=0)
        row_bg[y] = np.median(strip, axis=0)
    # 纵向平滑，避免某行恰好全被袖子占住导致跳变。
    # 注意不能用 np.convolve(mode="same")：它按零填充，会让图像上下两端的
    # 背景估计被稀释成接近 0，于是整张图都被判成前景（白底全糊）。
    k = max(3, (h // 40) * 2 + 1)
    row_bg = cv2.blur(row_bg.reshape(h, 1, 3), (1, k)).reshape(h, 3)
    return np.repeat(row_bg[:, None, :], w, axis=1)


def matte_chroma(bgr, lo=18.0, hi=46.0):
    """纯色背景抠像：距离阈值 + 连通域填洞 + 软边。"""
    if bgr is None:
        return None
    h, w = bgr.shape[:2]
    img = bgr.astype(np.float32)
    bg = estimate_background(bgr)
    dist = np.linalg.norm(img - bg, axis=2)

    # 硬背景：与背景足够接近。只保留"从画面四周连通过来"的那部分，
    # 被西装包住的白衬衫虽然也是白色，却连不到边界 → 判为前景。
    hard_bg = (dist < lo).astype(np.uint8)
    num, labels = cv2.connectedComponents(hard_bg, connectivity=4)
    edge_labels = set(labels[0, :].tolist()) | set(labels[-1, :].tolist())
    edge_labels |= set(labels[:, 0].tolist()) | set(labels[:, -1].tolist())
    edge_labels.discard(0)
    outer = np.isin(labels, list(edge_labels)).astype(np.uint8)

    # 软 alpha：过渡带线性映射，再乘掉外部背景
    alpha = np.clip((dist - lo) / max(1e-3, hi - lo), 0.0, 1.0)
    alpha[outer > 0] = 0.0

    # 补洞（西装内的阴影、扣子等误判）与去毛刺
    solid = (alpha > 0.5).astype(np.uint8)
    solid = cv2.morphologyEx(solid, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    solid = cv2.morphologyEx(solid, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    solid, see_through = _fill_inner_holes(solid, bgr, dist, lo)

    # 关键一步：上面的闭运算会把「手臂与躯干之间」「两腿之间」这种两三个像素宽的
    # 细缝封死，之后填洞再把它算成人体，缝里原来的白底就被保留下来——换到深色
    # 背景上就是一道白条（手指缝是一小块白斑）。背景的"真伪"只能由**没有做过
    # 形态学**的连通域（outer）决定，所以这里必须把 outer 挖回去。
    solid[outer > 0] = 0
    solid[see_through > 0] = 0

    alpha = np.maximum(alpha, solid.astype(np.float32))
    alpha[outer > 0] = 0.0
    alpha[see_through > 0] = 0.0

    # 细长白缝：手臂与躯干之间、手指之间、两腿根部这类只有两三个像素宽的透空缝。
    # 它们既不与画面边界连通（不算外部背景），又细到不构成"内部空洞"，闭运算直
    # 接把它们填进了人体——换到深色底上就是一道白光/一块白斑。判据只能靠粗细：
    # 这些缝的最大内切半径只有 2~4px，而真正的白色衣物（衬衫、袖口）是成片的，
    # 半径 10px 以上，两者差一个量级，不会误伤。
    gap = _thin_background_gaps(bgr, dist, outer, lo)
    alpha[gap > 0] = 0.0
    solid[gap > 0] = 0

    # 画面下部（脚边）把半透明的接触阴影压掉——它在浅色底上看不出来，换到深色
    # 底上就是一圈白光。只压"低饱和、偏亮、与背景有差异"的像素：白衬衫在画面上部
    # 不受影响，黑色皮鞋本身很暗同样不受影响。
    # （鞋底弓形空隙这块地面没法用颜色区分，留着不动——硬压会把鞋面高光啃出洞。）
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1].astype(np.float32)
    lum = hsv[:, :, 2].astype(np.float32)
    # 暖白 vs 中性白：指甲盖、指节高光是"暖白"（R 比 B 高 15 以上），影棚白底是
    # 中性白（R≈B）。凡是要"清白色"的地方都得排除暖白，否则指甲会被啃掉。
    rb = bgr[:, :, 2].astype(np.int16) - bgr[:, :, 0].astype(np.int16)
    neutral_white = rb <= 15

    # 缝边上通常还挂着半圈"半透明白"，深色底上会显成一条细白线。把紧邻缝的
    # **中性灰白**压下去。注意饱和度阈值不能放宽：皮肤饱和度约 55~80，若用 90
    # 会把手指、手掌边缘一起啃掉（手在深色底上就会出现一个洞、拇指像被切了）。
    if gap.any():
        rim = cv2.dilate(gap, np.ones((3, 3), np.uint8))
        rim = ((rim > 0) & (gap == 0) & (sat < 35) & (lum > 150)
               & (dist < 60) & neutral_white).astype(np.uint8)
        alpha[rim > 0] = np.minimum(alpha[rim > 0], 0.18)

    lower = np.zeros((h, w), np.uint8)
    lower[int(h * 0.66):, :] = 1
    shadow = (lower > 0) & (sat < 62) & (lum > 96) & (dist > lo) & (dist < 180)
    alpha[shadow] *= 0.15

    # 边缘略收一点，去掉背景色溢出的一圈浅边，再羽化
    alpha = cv2.erode(alpha, np.ones((3, 3), np.uint8), iterations=1)
    alpha = cv2.GaussianBlur(alpha, (0, 0), 0.8)
    # 羽化会把两侧人像的高 alpha 糊回只有两三个像素宽的缝里，缝又变得半透明，
    # 深色底上再显成一道浅色痕。最后按缝的形状再挖一次。
    if gap.any():
        alpha[gap > 0] = 0.0
        # 缝的尖端往往只剩一两个像素，且和腿/袖子连在一起（不算孤岛），会留个白点。
        # 往缝外扩 2px，只清那里"原本就是纯白"的像素——深色裤腿、肤色都不受影响。
        gd = cv2.dilate(gap, np.ones((5, 5), np.uint8))
        alpha[(gd > 0) & (lum > 195) & (dist < 90) & neutral_white] = 0.0

    # 收尾：清掉零星的小孤岛。缝的尖端常常只留下一两个像素，放大看是一个白点，
    # 正常尺寸下不明显，但换背景时会闪。人体本身是一个大连通块，其余小碎块一律丢掉。
    binary = (alpha > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if n > 2:
        areas = stats[1:, cv2.CC_STAT_AREA]
        keep = 1 + int(np.argmax(areas))
        for i in range(1, n):
            if i != keep and stats[i, cv2.CC_STAT_AREA] < 40:
                alpha[lab == i] = 0.0

    # 边缘白残留：轮廓与背景之间总有一两像素"半透明白"，alpha 估计偏大时合成后会
    # 闪白点（缝的尖端最常见）。只清"紧邻透明区、**几乎就是背景色**、alpha 又偏淡"
    # 的像素——dist 门槛必须收紧：指甲盖是暖白（dist 70~90），放宽到 100 会把指甲
    # 一起清掉，拇指末端就缺一块。
    near_bg = cv2.dilate((alpha < 0.1).astype(np.uint8), np.ones((9, 9), np.uint8))
    residue = (alpha < 0.55) & (near_bg > 0) & (lum > 200) & (sat < 35) & (dist < 40) \
              & neutral_white
    alpha[residue] = 0.0

    return np.clip(alpha * 255.0, 0, 255).astype(np.uint8)


# ── RVM（AI 视频抠像）──────────────────────────────────────────────
# 代码不随本仓库分发：RobustVideoMatting 是 **GPL-3.0**，放进要闭源交付的
# 项目里会触发 copyleft。这里只在本地显式 `--engine rvm` 时才导入，
# 生成的 alpha 是纯像素数据（不含其代码），入库不受影响。
# 想复现：把 RVM 仓库的 model/ 拷到 utils/rvm/，权重放 models/rvm/。
RVM_DIR = os.path.join(ROOT, "utils", "rvm")
RVM_WEIGHT = os.path.join(ROOT, "models", "rvm", "rvm_mobilenetv3.pth")
RVM_AVAILABLE = os.path.isdir(RVM_DIR) and os.path.isfile(RVM_WEIGHT)


def matte_rvm_sequence(frames, downsample_ratio=0.25, low_cut=0.12, target=1088):
    """整段按序抠像，返回与 frames 等长的 uint8 alpha 列表。

    相对 chroma 的两个实际好处：
    * **语义正确**——手指、臂身缝、腿缝这些"和背景同色的透空"不用再写形状判据
      去猜，模型直接知道那是透空还是衣服（chroma 里为此专门有一套
      中位色距 + 内切半径 + 长宽比的联合判据，还有"误吃掉白衬衫"的反复拉锯）。
    * 边缘是模型给的软边，不用靠腐蚀 + 羽化去补，也就没有"羽化把 alpha 压低、
      往人体里掺背景"那档事。

    `low_cut` 切掉 alpha 的低值尾巴：模型软边外沿 0~0.1 那一圈，合成时
    `a·F + (1-a)·bg` 仍会掺进 (1-a) 份源背景色，深色底上就是一圈白雾。
    取 0.12 是为了和运行时的前景外推下限（`EDGE_MIN=25/255≈0.098`）**错开**——
    凡是还留着 alpha 的像素（≥30）都必须高于外推下限，否则那一圈既没被外推
    洗干净、alpha 又不为 0，合成时会掺进 (1-a) 份源背景色。这里用硬阈值而不是
    线性拉伸：拉伸会在 0 附近留下 0.01 级的尾巴，正好落在没人管的缝里。
    """
    import torch
    sys.path.insert(0, os.path.join(ROOT, "utils"))
    from rvm import MattingNetwork

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = MattingNetwork("mobilenetv3").eval().to(dev)
    model.load_state_dict(torch.load(RVM_WEIGHT, map_location=dev))
    if dev == "cuda":
        model = model.half()

    h0, w0 = cv2.imread(frames[0], cv2.IMREAD_COLOR).shape[:2]
    scale = target / float(max(h0, w0))
    nh = max(32, int(round(h0 * scale)) // 32 * 32)
    nw = max(32, int(round(w0 * scale)) // 32 * 32)

    rec = [None] * 4
    out = []
    with torch.no_grad():
        for path in frames:
            bgr = cv2.imread(path, cv2.IMREAD_COLOR)
            if bgr is None:
                out.append(None)
                continue
            rgb = cv2.cvtColor(cv2.resize(bgr, (nw, nh)), cv2.COLOR_BGR2RGB)
            src = torch.from_numpy(rgb).to(dev).float().permute(2, 0, 1).unsqueeze(0) / 255.0
            if dev == "cuda":
                src = src.half()
            _, pha, *rec = model(src, *rec, downsample_ratio=downsample_ratio)
            a = pha[0, 0].float().cpu().numpy()
            a = cv2.resize(a, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_LINEAR)
            a[a < low_cut] = 0.0
            out.append((np.clip(a, 0, 1) * 255).astype(np.uint8))
    return out


def _thin_background_gaps(bgr, dist, outer, lo, max_radius=9.0, min_area=35,
                          min_inset=9.0, max_inset=60.0, max_med=None,
                          min_aspect=4.0):
    """找出"细到看不见"的背景缝（手臂与躯干之间、指缝、腿根），返回掩膜。

    这类缝和白色衣物（衬衫、袖口）看着一模一样——影棚白底是白的，白衬衫也是白的。
    但二者有一个稳的区别：**缝是背景从肢体之间透过来，所以缝里的颜色"就是"背景色；
    衬衫不是背景，它有布料的本色和褶皱阴影**。实测：

    * 真缝（臂身之间、腿侧的细长缝）：到背景色的中位距离 4.4~11.0，外接长宽比 6~15
      （顺着肢体延伸的窄长条），最大内切半径 6~7px；
    * 白衬衫补丁：中位距离 52.0，外接长宽比 1.15（团块状），半径 8.6px。

    因此判据用"中位色距 ≤ lo*1.2(≈21.6)"做主闸——它比几何判据更贴近"缝的本质是
    透出的背景"，也不会因为某件衣服够白就被放过去；最大内切半径放宽到 9px（真缝实测到 7.4px 且是正确目标，
    之前卡 4.5/6.0 会把偏粗的臂缝漏掉，表现就是"缝只清掉一半"）。

    再叠加 `min/max_inset` 做兜底：贴着外轮廓的是袖口/衣领（不算缝），陷到躯干
    最深处的是领带斜条（也不算），两者都排除。
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1].astype(np.float32)
    lum = hsv[:, :, 2].astype(np.float32)
    # 中性白：影棚白底是 R≈B；指甲盖这类"暖白"R 比 B 高 15 以上，不能当背景清掉。
    neutral_white = (bgr[:, :, 2].astype(np.int16) - bgr[:, :, 0].astype(np.int16)) <= 15
    inset = cv2.distanceTransform((outer == 0).astype(np.uint8), cv2.DIST_L2, 5)

    white = ((dist < lo * 4.0) & (lum > 185) & (outer == 0)).astype(np.uint8)
    gap = np.zeros_like(white)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    max_med = lo * 1.2 if max_med is None else max_med
    for i in range(1, n):
        if int(stats[i, cv2.CC_STAT_AREA]) < min_area:
            continue
        m = (lab == i).astype(np.uint8)
        # 颜色中位距：**最稳的一条判据**。真缝 = 背景从肢体间透出来，所以缝里每个
        # 像素都几乎等于背景色（实测中位距 4.4~11.0）；白衬衫看着也白，但它不是背景
        # 本身——有褶皱阴影和布料本色，实测中位距 52。两者差 5 倍以上，用一个
        # `lo*1.2`(≈21.6) 的门槛就能精确切开，且不受"衣服越白越难分"的影响。
        if float(np.median(dist[m > 0])) > max_med:
            continue                       # 不是背景色 = 白衬衫/袖口这块布料
        if float(cv2.distanceTransform(m, cv2.DIST_L2, 5).max()) > max_radius:
            continue                       # 成片的白色衣物
        # 形状必须是**顺着肢体延伸的窄长条**。缝是两条肢体夹出来的通道，天然又细
        # 又长（实测外接长宽比 6~15，顺着整条手臂/腿）；而白衬衫上被照亮的一小块
        # 白补丁是团块状的（实测 1.15），颜色上却可能和背景一样白。这条判据与颜色
        # 无关，正是它把"同样白"的两种东西分开。
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        rw, rh = sorted(cv2.minAreaRect(max(cnts, key=cv2.contourArea))[1])
        if (rh + 0.1) / (rw + 0.1) < min_aspect:
            continue                       # 团块 = 白衬衫上的亮补丁，不是缝
        depth = float(np.percentile(inset[m > 0], 10))
        if depth < min_inset:
            continue                       # 贴着轮廓 = 袖口 / 衣领这类真衣物
        if depth > max_inset:
            continue                       # 陷在躯干深处 = 领带斜纹这类衣物花纹
        gap[m > 0] = 1

    # 缝里不一定全是纯白，靠里面还有被手/袖子挡出来的灰阴影。把缝补成整块，再用
    # "中性 + 不太暗"限一遍，只清背景灰，不去碰两侧的深色西装和肤色（皮肤 sat 55~80）。
    # 下面的每一步"把缝补全"都必须再加一层同样的约束：只能向**真的是背景色**的像素
    # 生长。真缝是背景透出来，缝里处处都该满足 `dist < max_med`；白衬衫不满足，
    # 于是闭运算和后面的中性色条件就不会越过缝的边界啃到布料上（否则一个小小的
    # 缝种子经过 9x9 闭运算就能吞掉旁边整片衬衫——实测正是这样丢了 2947 像素）。
    bgish = (dist < max_med)
    if gap.any():
        solid_gap = cv2.morphologyEx(gap, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        grey = ((sat < 45) & (lum > 90) & bgish).astype(np.uint8)
        gap = np.maximum(gap, (solid_gap & grey).astype(np.uint8))
        # 缝中段常常是一小片"被遮住的背景灰"，颜色既不白也不深，靠上面那条判据
        # 兜不住。这里按缝的走向再合一次（闭运算把断成几节的缝连起来），只取
        # "陷得够深"且"确实是中性背景灰"的部分——皮肤饱和度 55~80，用中性色卡住，
        # 闭运算就不会把缝往外扩到手指/手掌上（那样手会出现一个洞）。
        joined = cv2.morphologyEx(gap, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        deep = (inset >= 7.0).astype(np.uint8)
        neutral = ((sat < 60) & (lum > 110) & bgish).astype(np.uint8)
        gap = np.maximum(gap, (joined & deep & neutral).astype(np.uint8))

        # 缝的尖端/分叉处常残留一两个纯白像素，离缝本体几像素，会留个白点。
        # 按每段缝的外接框外扩 5px，把框里的纯白像素一并算进缝——框只围绕缝，
        # 离衣服上的白色花纹（领带斜条）还远，不会误伤。
        h, w = gap.shape
        n2, lab2, stats2, _ = cv2.connectedComponentsWithStats(gap, connectivity=8)
        for i in range(1, n2):
            x, y = int(stats2[i, 0]), int(stats2[i, 1])
            bw, bh = int(stats2[i, 2]), int(stats2[i, 3])
            x0, x1 = max(0, x - 5), min(w, x + bw + 5)
            y0, y1 = max(0, y - 5), min(h, y + bh + 5)
            sub = ((dist[y0:y1, x0:x1] < max_med) & (lum[y0:y1, x0:x1] > 195)
                   & (sat[y0:y1, x0:x1] < 35) & neutral_white[y0:y1, x0:x1])
            gap[y0:y1, x0:x1][sub] = 1

    # 成品复核：**必须在最终结果上再验一遍**，不能只查候选。
    # 前面的闭运算会把一个小小的缝种子向外扩得太胖，最后变成团块时就已经啃到了
    # 旁边的白衬衫（实测啃掉 1148 像素，而且它的内切半径 10.19 已经超过上面的
    # max_radius 关卡——说明候选拦不住，只有成品才拦得住）。
    # 缝的本质特征三条，缺一不可：顺着肢体的窄长条（长宽比 ≥ min_aspect）、
    # 细（内切半径 ≤ max_radius）、整体就是背景色（中位色距 ≤ max_med）。
    final = np.zeros_like(gap)
    n3, lab3, st3, _ = cv2.connectedComponentsWithStats(gap, connectivity=8)
    for i in range(1, n3):
        m = (lab3 == i).astype(np.uint8)
        if int(m.sum()) < min_area:
            continue
        if float(cv2.distanceTransform(m, cv2.DIST_L2, 5).max()) > max_radius:
            continue
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        rw, rh = sorted(cv2.minAreaRect(max(cnts, key=cv2.contourArea))[1])
        if (rh + 0.1) / (rw + 0.1) < min_aspect:
            continue
        if float(np.median(dist[m > 0])) > max_med:
            continue
        final[m > 0] = 1
    return final


def _fill_inner_holes(solid, bgr=None, dist=None, lo=18.0, max_span=120, max_area=4000):
    """填掉人体内部的误判空洞，但保留真正"能看穿"的孔（指缝、手圈里的空隙）。

    区分靠**尺寸**而不是颜色：被西装包住的白衬衫里面同样是一片白，可它面积上千、
    垂直跨度几百像素，与指缝（几十像素）差一个量级。所以判定条件是"洞很小" +
    "洞里确实是背景色"，两者同时成立才当作穿透孔挖开。

    返回 (填洞后的 solid, 穿透孔掩膜)。
    """
    h, w = solid.shape
    flood = solid.copy()
    mask = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(flood, mask, (0, 0), 1)
    holes = (flood == 0).astype(np.uint8)
    if dist is None:
        return np.maximum(solid, holes), np.zeros_like(holes)

    n, lab, stats, _ = cv2.connectedComponentsWithStats(holes, connectivity=8)
    fill = np.zeros_like(holes)
    see_through = np.zeros_like(holes)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV) if bgr is not None else None
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < 3:
            continue
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        m = lab == i
        small = bw <= max_span and bh <= max_span and area <= max_area
        bgish = float(np.median(dist[m])) < lo * 2.2
        # 注意：小 + 白并不足以判成"看穿的孔"——领口那块被领带和西装夹住的窄条
        # 白衬衫也长这样。真正的穿透孔（手指圈起来的空档）四周是皮肤，而白衬衫
        # 四周是深色西装，用"环带里皮肤占比"把它们分开。
        skin_around = False
        if hsv is not None:
            ring = cv2.dilate(m.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool) & ~m
            if ring.any():
                s = hsv[:, :, 1][ring]
                v = hsv[:, :, 2][ring]
                skin = (s > 40) & (s < 150) & (v > 110) & (v < 240)
                skin_around = float(skin.mean()) >= 0.45
        if small and bgish and skin_around:
            see_through[m] = 1
        else:
            fill[m] = 1
    return np.maximum(solid, fill), see_through


# ── rembg（可选引擎）─────────────────────────────────────────────────
_REMBG = None


def _rembg_session():
    global _REMBG
    if _REMBG is None:
        from rembg import new_session
        _REMBG = new_session("u2net")
    return _REMBG


def matte_rembg(bgr):
    from rembg import remove
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    out = remove(rgb, session=_rembg_session())
    return np.array(out)[:, :, 3]


# ── 预览 ─────────────────────────────────────────────────────────────
def _checkerboard(h, w, cell=18):
    yy, xx = np.mgrid[0:h, 0:w]
    board = (((yy // cell) + (xx // cell)) % 2).astype(np.float32)
    return (200 + 25 * board).astype(np.uint8)


def compose_preview(bgr, alpha, size=None):
    """把抠像结果贴到几种背景上，返回并排的对比图（BGR）。"""
    h, w = bgr.shape[:2]
    a = (alpha.astype(np.float32) / 255.0)[:, :, None]
    panels = []

    # 1. 棋盘格（检查透明度）
    checker = cv2.cvtColor(_checkerboard(h, w), cv2.COLOR_GRAY2BGR)
    panels.append((bgr * a + checker * (1 - a)).astype(np.uint8))

    # 2. 深色渐变（看边缘有没有白边）
    grad = np.zeros((h, w, 3), np.float32)
    for y in range(h):
        grad[y, :] = (40 + 60 * y / h, 30 + 40 * y / h, 55 + 70 * y / h)
    panels.append((bgr * a + grad * (1 - a)).astype(np.uint8))

    # 3. 暖色场景（模拟真实换背景观感）
    warm = np.zeros((h, w, 3), np.float32)
    for y in range(h):
        warm[y, :] = (120 + 40 * y / h, 150 + 30 * y / h, 185 + 25 * y / h)
    panels.append((bgr * a + warm * (1 - a)).astype(np.uint8))

    sheet = np.concatenate(panels, axis=1)
    if size:
        scale = size / sheet.shape[1]
        sheet = cv2.resize(sheet, (int(sheet.shape[1] * scale), int(sheet.shape[0] * scale)),
                           interpolation=cv2.INTER_AREA)
    return sheet


# ── 主流程 ───────────────────────────────────────────────────────────
def run(avatar_id, frames_mode="all", engine="chroma", force=False,
        preview_path=None, lo=18.0, hi=46.0, limit=None, quiet=False,
        only_gestures=None, only_missing=False):
    """生成 alpha 素材。

    `only_gestures` 只处理指定动作（增量补抠），`only_missing` 跳过已有结果的
    动作——动作生成流程会为新动作调这两个参数，不必把整套素材重算一遍。
    """
    sources = discover_sources(avatar_id)
    if not sources["base"]:
        raise SystemExit("形象 %s 没有 full_imgs 素材" % avatar_id)

    out_root = os.path.join(AVATAR_ROOT, avatar_id, MATTE_DIRNAME)
    todo = {}
    wanted = set(only_gestures or ())
    for name, frames in sources.items():
        if frames_mode == "base" and name != "base":
            continue
        if frames_mode == "gestures" and name == "base":
            continue
        if name == "base" and wanted:
            continue                       # 指定了动作就跳过默认素材
        if wanted and name.split(".", 1)[-1] not in wanted:
            continue
        if only_missing and os.path.isdir(
                os.path.join(out_root, _rel_dir(name))):
            existing = os.listdir(os.path.join(out_root, _rel_dir(name)))
            if len([n for n in existing if n.lower().endswith(".png")]) >= len(frames):
                continue
        todo[name] = frames[:limit] if limit else frames

    if not todo:
        if not quiet:
            print("没有需要处理的帧")
        return _read_manifest(out_root)

    fn = matte_rembg if engine == "rembg" else matte_chroma
    started = time.time()
    # 增量补抠不能覆盖旧 manifest：bg_color / size 是整套素材共用的，
    # 只在 base 帧上才估得出来，丢了就会让去溢色退化成普通混合。
    old = _read_manifest(out_root)
    manifest = dict(old)
    manifest.update({
        "avatar": avatar_id,
        "engine": engine,
        "params": {"lo": lo, "hi": hi},
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sources": dict(old.get("sources") or {}),
    })

    total = sum(len(v) for v in todo.values())
    done = 0
    for name, frames in todo.items():
        rel_dir = _rel_dir(name)
        dst_dir = os.path.join(out_root, rel_dir)
        os.makedirs(dst_dir, exist_ok=True)
        manifest["sources"][name] = {"dir": rel_dir.replace("\\", "/"), "frames": len(frames)}

        # 序列引擎（RVM）必须整段按序跑：它把上一帧的循环状态带进下一帧，
        # 逐帧独立调用会丢掉时序记忆，退化成"每帧重新猜一遍"。
        seq = matte_rvm_sequence(frames) if RVM_AVAILABLE and engine == "rvm" else None

        for idx, path in enumerate(frames):
            stem = os.path.splitext(os.path.basename(path))[0]
            dst = os.path.join(dst_dir, stem + ".png")
            done += 1
            if not force and os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(path):
                continue
            bgr = cv2.imread(path, cv2.IMREAD_COLOR)
            if bgr is None:
                print("  跳过（读不出来）:", path)
                continue
            if name == "base" and not manifest.get("size"):
                manifest["size"] = [int(bgr.shape[1]), int(bgr.shape[0])]
                # 记录原始背景色：合成阶段要用它反解"去溢色"（边缘像素是原背景与
                # 人像的混合，直接线性叠加会在深色背景上留一圈白边）。取中间一行的
                # 中位色，比取角落像素稳——角落常被拍摄阴影压暗。
                row_bg = estimate_background(bgr)
                manifest["bg_color"] = [int(v) for v in
                                        np.median(row_bg[bgr.shape[0] // 2], axis=0)]
            if seq is not None:
                alpha = seq[idx] if idx < len(seq) and seq[idx] is not None else None
                if alpha is None:
                    alpha = fn(bgr, lo=lo, hi=hi)      # 该帧没抠出来就退回 chroma
            else:
                alpha = fn(bgr) if engine == "rembg" else fn(bgr, lo=lo, hi=hi)
            cv2.imwrite(dst, alpha)
            if not quiet and (done % 20 == 0 or total <= 8):
                print("  [%d/%d] %s" % (done, total, os.path.basename(dst)))

    with open(os.path.join(out_root, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    if not quiet:
        print("完成：%s（%d 帧，%.1fs）" % (out_root, total, time.time() - started))

    if preview_path:
        base_path = sources["base"][0]
        bgr = cv2.imread(base_path, cv2.IMREAD_COLOR)
        alpha = cv2.imread(os.path.join(out_root, "base",
                                       os.path.splitext(os.path.basename(base_path))[0] + ".png"),
                           cv2.IMREAD_GRAYSCALE)
        sheet = compose_preview(bgr, alpha, size=1500)
        os.makedirs(os.path.dirname(os.path.abspath(preview_path)) or ".", exist_ok=True)
        cv2.imwrite(preview_path, sheet)
        if not quiet:
            print("预览图：", preview_path)
    return manifest


def run_gesture(avatar_id, gesture_id, force=False, quiet=True, engine=None):
    """只给一个动作补 alpha —— 动作生成流程收尾时调用。

    `engine=None` 时自动选：RVM 可用就用它，缺代码/缺权重则退回 chroma。
    生成流程是不带引擎参数调这里的（见 `utils/gesture_make.py`），不自动选的话
    新动作会用 chroma 抠，和已有动作的 alpha 观感不一致——边缘一个软一个硬，
    循环播到那个动作时能看出来。

    返回 `(帧数, 输出目录)`；该动作没有帧时返回 `(0, "")`。
    """
    if engine is None:
        engine = "rvm" if RVM_AVAILABLE else "chroma"
    gid = str(gesture_id or "").strip()
    if not gid or not is_safe_id(gid):
        raise ValueError("非法的动作 id：%r" % (gesture_id,))
    manifest = run(avatar_id, frames_mode="gestures", engine=engine, force=force,
                   quiet=quiet, only_gestures=[gid])
    entry = (manifest.get("sources") or {}).get("gestures.%s" % gid) or {}
    out_dir = os.path.join(AVATAR_ROOT, avatar_id, MATTE_DIRNAME,
                           "gestures", gid)
    return int(entry.get("frames") or 0), (out_dir if entry else "")


def _rel_dir(name):
    return "base" if name == "base" else os.path.join("gestures", name.split(".", 1)[1])


def _read_manifest(out_root):
    try:
        with open(os.path.join(out_root, "manifest.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def is_safe_id(text):
    """动作 id 会拼进路径，只允许字母数字和 `-_.`，且不能是 . / .."""
    text = str(text or "")
    if not text or text in (".", ".."):
        return False
    return all(ch.isalnum() or ch in "-_." for ch in text)


def main():
    ap = argparse.ArgumentParser(description="数字人人像抠像（生成 alpha 素材）")
    ap.add_argument("--avatar", default="musetalk_ke", help="形象 ID（data/avatars 下的目录名）")
    ap.add_argument("--frames", default="all", choices=("all", "base", "gestures"))
    ap.add_argument("--engine", default="chroma", choices=("chroma", "rembg", "rvm"))
    ap.add_argument("--force", action="store_true", help="忽略已有结果重新计算")
    ap.add_argument("--preview", nargs="?", const="docs/matte-preview.png", default=None,
                    help="输出三种背景的对比预览图")
    ap.add_argument("--lo", type=float, default=18.0, help="背景判定阈值（越大越严）")
    ap.add_argument("--hi", type=float, default=46.0, help="前景判定阈值（越大边缘越软）")
    ap.add_argument("--limit", type=int, default=None, help="每个源只处理前 N 帧（调试用）")
    ap.add_argument("--gesture", action="append", default=None, metavar="GID",
                    help="只处理指定动作（可重复）；补新动作时用")
    ap.add_argument("--missing", action="store_true",
                    help="只处理还没有 alpha 的动作")
    args = ap.parse_args()

    preview = args.preview
    if preview and not os.path.isabs(preview):
        preview = os.path.join(ROOT, preview)
    run(args.avatar, args.frames, args.engine, args.force, preview,
        args.lo, args.hi, args.limit, only_gestures=args.gesture,
        only_missing=args.missing)


if __name__ == "__main__":
    main()
