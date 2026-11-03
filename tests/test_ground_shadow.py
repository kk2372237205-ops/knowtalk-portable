# -*- coding: utf-8 -*-
"""虚拟投影（接地阴影）：接触点跟着人走、只压背景不改人体、缓存不被就地写坏。

对应 openspec/changes/add-ground-shadow/。

为什么这几条要钉死：抠像把原素材脚下的接触阴影一并丢了（alpha 低于外推下限，
那本来就该当背景丢），人像贴到新背景上脚下是一条齐刷刷的切口，全息舱近距离
看就是"浮"着的。补阴影这件事有三个容易踩的坑——踩错位置（人动起来时脱脚）、
糊到鞋面上（叠在合成结果上而不是背景层）、就地改了缓存的背景（阴影逐帧累积，
几秒内把脚下压成一块黑）。
"""
import asyncio
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import cv2
import numpy as np
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import server.avatar_routes as ar
from utils import avatar_background as ab

H, W = 200, 120
# 人形：躯干 + 腿（x 45~75），脚比腿宽（x 35~85），最低实心行 y=169
FOOT_BOTTOM = 169


def human_alpha(foot_x0=35, foot_x1=86, h=H, w=W):
    """躯干在上方、腿窄、脚最宽——接触带（最底 7 行）里只该看到腿和脚。"""
    a = np.zeros((h, w), np.uint8)
    a[30:150, 45:76] = 255                       # 躯干
    a[150:165, 55:66] = 255                      # 腿（比脚窄）
    a[165:170, foot_x0:foot_x1] = 255            # 脚
    return a


def write_alpha(path, mask):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    assert cv2.imwrite(path, mask.astype(np.uint8))


@pytest.fixture()
def stage(tmp_path, monkeypatch):
    """一套最小形象：一帧 base，人形 alpha；背景与阴影配置都隔离到 tmp。"""
    root = str(tmp_path / "avatars")
    monkeypatch.setattr(ab, "AVATAR_ROOT", root)
    monkeypatch.setattr(ab, "CONFIG_PATH", str(tmp_path / "background.json"))
    monkeypatch.setattr(ab, "SHADOW_CONFIG_PATH", str(tmp_path / "shadow.json"))
    # 热重载会重新读 background.json，不写一份的话会被当成「换回了原背景」
    with open(str(tmp_path / "background.json"), "w", encoding="utf-8") as fh:
        json.dump({"type": "color", "value": "#D9DDE2"}, fh)

    aid = "ground"
    write_alpha(os.path.join(root, aid, "matte", "base", "00000000.png"), human_alpha())
    return {"aid": aid, "frame": np.full((H, W, 3), 240, np.uint8),
            "alpha": human_alpha(), "cfg": {"type": "color", "value": "#D9DDE2"}}


def compose(stage, shadow):
    comp = ab.AvatarBackground(stage["aid"], stage["cfg"], shadow=shadow)
    return comp.compose(stage["frame"].copy(), None, 0), comp


# ── 接触带 ──────────────────────────────────────────────────────────
def test_foot_band_finds_contact_point():
    """触底行 = 实心像素最低行；接触宽度只在这一行上方一小段里求。"""
    band = ab.AvatarBackground._foot_band(human_alpha())
    assert band["y"] == FOOT_BOTTOM
    assert band["cx"] == 60.0
    assert band["w"] == 51


def test_foot_band_follows_weight_shift():
    """动作里重心移到另一只脚时，接触中心必须跟着走。

    用一套「画面底部居中」的固定椭圆省事，但人一抬腿就脱脚——
    阴影留在原地，人却挪开了，比没有阴影还假。
    """
    left = ab.AvatarBackground._foot_band(human_alpha(foot_x0=35, foot_x1=86))
    right = ab.AvatarBackground._foot_band(human_alpha(foot_x0=70, foot_x1=116))
    assert right["cx"] > left["cx"] + 15, "重心右移后接触中心没跟着走：%s" % right
    assert left["y"] == right["y"] == FOOT_BOTTOM
    assert right["w"] > left["w"] - 20        # 宽度量级不变（脚还是那只脚）


def test_unreliable_foot_has_no_shadow():
    """脚部像素太少（被下边缘裁掉、只剩一条边）时不得硬画一片。"""
    tiny = np.zeros((H, W), np.uint8)
    tiny[20:100, 40:60] = 255
    tiny[168, 60:63] = 255                       # 只有 3 个像素，低于 FOOT_MIN_PX
    assert ab.AvatarBackground._foot_band(tiny) is None


def test_empty_alpha_has_no_shadow():
    """空帧（还没推上来的帧）必须严格等于背景，不得凭空多出一片暗斑。"""
    aid_alpha = np.zeros((H, W), np.uint8)
    assert ab.AvatarBackground._foot_band(aid_alpha) is None


# ── 只压背景、不碰人体 ──────────────────────────────────────────────
def test_solid_body_pixels_are_untouched(stage):
    """alpha 实心区的像素 MUST 与关阴影时逐像素一致。

    阴影若叠在合成结果上，鞋面和裤脚会被糊上一层灰——抠像辛苦保住的边缘
    又被涂回去了。叠在背景层则不会：实心区被 a·F 整块覆盖。
    """
    out_off, _ = compose(stage, {"enabled": False})
    out_on, _ = compose(stage, {"enabled": True})
    solid = stage["alpha"] >= 250
    assert solid.sum() > 1000
    assert np.array_equal(out_on[solid], out_off[solid]), "阴影不得改变人体像素"


def test_ground_darkens_and_fades_outward(stage):
    """脚下的背景 MUST 变暗，且由接触中心向外单调变淡。"""
    out_off, _ = compose(stage, {"enabled": False})
    out_on, _ = compose(stage, {"enabled": True})
    diff = out_off.astype(np.int16).sum(axis=2) - out_on.astype(np.int16).sum(axis=2)

    row = FOOT_BOTTOM + 3                        # 接触带下方，还没出包围盒
    vals = [int(diff[row, x]) for x in (60, 70, 80, 90)]
    assert vals[0] > 0, "脚下背景应该被压暗"
    assert vals == sorted(vals, reverse=True), "阴影应由中心向外单调变淡：%s" % vals
    assert vals[0] > vals[-1]


def test_body_area_is_not_darkened(stage):
    """躯干上方那片背景 MUST 完全不受影响——阴影只在脚下那一小条。"""
    out_off, _ = compose(stage, {"enabled": False})
    out_on, _ = compose(stage, {"enabled": True})
    diff = out_off.astype(np.int16).sum(axis=2) - out_on.astype(np.int16).sum(axis=2)
    assert int(diff[10:100].max()) == 0


# ── 缓存与累积 ──────────────────────────────────────────────────────
def test_background_cache_is_not_mutated(stage):
    """背景图按尺寸缓存、被所有帧共用，叠阴影 MUST 先复制。

    就地写回的话阴影会一帧一帧累积，几秒内把脚下压成一块黑。
    """
    comp = ab.AvatarBackground(stage["aid"], stage["cfg"], shadow={"enabled": True})
    bg = comp._background_for(W, H)
    snapshot = bg.copy()
    key = ("base", "00000000", W, H)
    alpha = stage["alpha"]

    first = comp._layered_background(bg, key, alpha)
    for _ in range(100):
        out = comp._layered_background(bg, key, alpha)
    assert np.array_equal(out, first), "第 100 帧的阴影和第 1 帧不一样 —— 缓存被就地写坏了"
    assert np.array_equal(bg, snapshot)
    assert np.array_equal(comp._background_for(W, H), snapshot)


def test_shadow_disabled_returns_original_background(stage):
    """关掉阴影（或强度为 0）时走原路径，连复制都不做，结果与改动前一致。"""
    for shadow in ({"enabled": False}, {"enabled": True, "strength": 0.0}):
        comp = ab.AvatarBackground(stage["aid"], stage["cfg"], shadow=shadow)
        bg = comp._background_for(W, H)
        assert comp._layered_background(bg, ("base", "00000000", W, H),
                                        stage["alpha"]) is bg


def test_param_change_does_not_hit_stale_cache(stage):
    """阴影缓存键 MUST 带参数签名：改了强度后不得再命中旧结果。"""
    comp = ab.AvatarBackground(stage["aid"], stage["cfg"],
                               shadow={"enabled": True, "strength": 0.30})
    weak, _ = comp.compose(stage["frame"].copy(), None, 0), None
    comp.shadow = ab.normalize_shadow({"enabled": True, "strength": 0.95})
    comp._out_cache.clear()                      # 模拟热重载里做的清理
    strong = comp.compose(stage["frame"].copy(), None, 0)
    assert not np.array_equal(weak, strong), "改了强度却还是旧阴影 —— 缓存键漏了签名"
    assert strong.sum() < weak.sum()


# ── 配置 ────────────────────────────────────────────────────────────
def test_normalize_shadow_clamps_out_of_range():
    """越界值、非数字、缺字段都要收敛成合法值，别把整份配置带坏。"""
    got = ab.normalize_shadow({"enabled": 0, "strength": 5, "spread": -1,
                               "flatten": 0, "softness": "x", "shade": float("nan")})
    assert got["enabled"] is False
    assert got["strength"] == 1.0
    assert got["spread"] == 0.3
    assert got["flatten"] == 0.05
    assert got["softness"] == ab.DEFAULT_SHADOW["softness"]   # 非数字 → 保持默认
    assert got["shade"] == ab.DEFAULT_SHADOW["shade"]         # NaN → 保持默认
    assert ab.normalize_shadow(None) == ab.DEFAULT_SHADOW


def test_hot_reload_picks_up_shadow_file(stage):
    """界面里改完阴影参数，下一帧就生效——不用重启服务、也不用重连数字人。"""
    comp = ab.AvatarBackground(stage["aid"], stage["cfg"])
    assert comp.shadow["strength"] == ab.DEFAULT_SHADOW["strength"]   # 文件还不存在

    comp.compose(stage["frame"].copy(), None, 0)     # 预热，让阴影缓存里先有东西
    stale = set(comp._shadow_cache)
    assert stale, "预热后应该有阴影缓存"

    with open(ab.SHADOW_CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump({"enabled": True, "strength": 0.15}, fh)
    comp._next_check = 0.0                        # 绕过 0.5s 节流
    comp.compose(stage["frame"].copy(), None, 0)
    assert comp.shadow["strength"] == 0.15
    assert not (stale & set(comp._shadow_cache)), "参数变了，旧的阴影缓存必须作废"


# ── 性能 ────────────────────────────────────────────────────────────
def _bench(tmp_path, monkeypatch, shadow, tag):
    """1080×538 逐帧合成 24 帧，返回中位耗时（ms）。"""
    root = str(tmp_path / ("avatars_%s" % tag))
    monkeypatch.setattr(ab, "AVATAR_ROOT", root)
    aid, h, w = "demo", 538, 1080
    matte = os.path.join(root, aid, "matte")
    person = np.zeros((h, w), np.uint8)
    person[60:480, 380:700] = 255                # 躯干
    person[430:480, 340:740] = 255               # 脚（比躯干宽）
    person = cv2.GaussianBlur(person, (0, 0), 1.4).astype(np.uint8)
    for i in range(24):
        m = person.copy()
        if i % 2:                                # 每帧都不同，逼它真算
            m = cv2.rotate(m, cv2.ROTATE_180) if False else m[::-1]
        write_alpha(os.path.join(matte, "gestures", "ga", "%08d.png" % i), m)
    comp = ab.AvatarBackground(aid, {"type": "color", "value": "#D9DDE2"}, shadow=shadow)
    frame = np.full((h, w, 3), 240, np.uint8)
    samples = []
    for i in range(24):
        t0 = time.perf_counter()
        comp.compose(frame, "ga", i)
        samples.append((time.perf_counter() - t0) * 1000)
    return sorted(samples)[len(samples) // 2]


def test_shadow_stays_within_budget(tmp_path, monkeypatch):
    """开了阴影后单帧仍 MUST 留在 26ms 预算内，且阴影带来的增量低于 3ms。

    阴影只算脚底那一条包围盒（约 200×60），并且按「帧 + 参数签名」缓存，
    热路径上就是一次小范围乘法，不该给每帧增加可感知的开销。
    """
    off = _bench(tmp_path, monkeypatch, {"enabled": False}, "off")
    on = _bench(tmp_path, monkeypatch, {"enabled": True}, "on")
    assert on < 26.0, "开阴影后单帧 %.1fms，超出实时预算" % on
    assert on - off < 3.0, "阴影让每帧多了 %.1fms，超出它该付的代价" % (on - off)


# ── 接口 ────────────────────────────────────────────────────────────
class ShadowRouteTestCase(unittest.TestCase):
    """PUT /api/avatar/shadow 的读写往返与非法输入。

    界面那四个滑块最终就是走这个接口；它只要肯把越界值放过去，
    运行时就会拿到 strength=9 这种数，脚下直接压成一块黑。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="shadow-route-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.object(ab, "SHADOW_CONFIG_PATH",
                                    os.path.join(self.tmp, "settings", "shadow.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(ar, "DATA_DIR", os.path.join(self.tmp, "avatars"))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.app = web.Application()
        ar.setup_avatar_routes(self.app)
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)

        async def boot():
            client = TestClient(TestServer(self.app))
            await client.start_server()
            return client

        self.client = self.loop.run_until_complete(boot())
        self.addCleanup(lambda: self.loop.run_until_complete(self.client.close()))

    def request(self, method, path, payload=None, text_body=None):
        async def go():
            kwargs = {}
            if payload is not None:
                kwargs["json"] = payload
            if text_body is not None:
                kwargs["data"] = text_body
            resp = await self.client.request(method, path, **kwargs)
            return resp.status, await resp.text()

        return self.loop.run_until_complete(go())

    def test_put_then_get_round_trips(self):
        status, text = self.request("PUT", "/api/avatar/shadow", {"strength": 0.20})
        body = json.loads(text)
        self.assertEqual(body.get("code"), 0, text)
        self.assertEqual(body["data"]["shadow"]["strength"], 0.20)

        status, text = self.request("GET", "/api/avatar/background")
        body = json.loads(text)
        self.assertEqual(body.get("code"), 0, text)
        self.assertEqual(body["data"]["shadow"]["strength"], 0.20)
        self.assertTrue(body["data"]["shadow"]["enabled"])

    def test_put_clamps_out_of_range(self):
        status, text = self.request("PUT", "/api/avatar/shadow",
                                    {"strength": 9, "spread": -5, "flatten": 0})
        body = json.loads(text)
        self.assertEqual(body.get("code"), 0, text)
        self.assertEqual(body["data"]["shadow"]["strength"], 1.0)
        self.assertEqual(body["data"]["shadow"]["spread"], 0.3)
        self.assertEqual(body["data"]["shadow"]["flatten"], 0.05)

    def test_put_rejects_broken_body(self):
        status, text = self.request("PUT", "/api/avatar/shadow", text_body="{not json")
        self.assertNotEqual(json.loads(text).get("code"), 0, text)
        # 数组不是对象，接口里没有 .get() 可用，必须挡在门口
        status, text = self.request("PUT", "/api/avatar/shadow", [1, 2, 3])
        self.assertNotEqual(json.loads(text).get("code"), 0, text)
