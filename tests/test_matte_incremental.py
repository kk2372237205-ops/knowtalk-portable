# -*- coding: utf-8 -*-
"""抠像素材的增量补算：只处理指定动作，且不破坏已有 manifest。

对应 openspec/changes/fix-matte-compositing-and-resource-release/。

背景：`idle_sway` / `wave_one` 两个动作是后加的，`matte/manifest.json` 里只有更早
的六个动作 —— 补的时候如果整份重写 manifest，`bg_color` / `size` 这两个「整套素材
共用」的字段会因为这次没跑 base 帧而丢掉，去溢色随之退化成普通混合，边缘又发白。
"""
import json
import os

import cv2
import numpy as np
import pytest

from scripts import avatar_matte


def write_frame(path, w=64, h=96):
    """白底 + 中间一块深色矩形（够 chroma 抠出一个人形）。"""
    img = np.full((h, w, 3), 244, np.uint8)
    cv2.rectangle(img, (w // 4, h // 5), (w * 3 // 4, int(h * 0.9)), (40, 40, 60), -1)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    assert cv2.imwrite(path, img)


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    root = str(tmp_path / "avatars")
    monkeypatch.setattr(avatar_matte, "AVATAR_ROOT", root)
    aid = "demo"
    write_frame(os.path.join(root, aid, "full_imgs", "00000000.png"))
    write_frame(os.path.join(root, aid, "gestures", "old", "full_imgs", "00000000.png"))
    write_frame(os.path.join(root, aid, "gestures", "fresh", "full_imgs", "00000000.png"))
    write_frame(os.path.join(root, aid, "gestures", "fresh", "full_imgs", "00000001.png"))
    # 先跑一遍 base，造出带 bg_color 的 manifest
    avatar_matte.run(aid, frames_mode="base", quiet=True)
    return root, aid


def test_manifest_keeps_bg_color_when_only_one_gesture(studio):
    root, aid = studio
    manifest_path = os.path.join(root, aid, "matte", "manifest.json")
    before = json.load(open(manifest_path, encoding="utf-8"))
    assert before.get("bg_color"), "前置条件：base 跑完应记下 bg_color"

    frames, out_dir = avatar_matte.run_gesture(aid, "fresh")

    after = json.load(open(manifest_path, encoding="utf-8"))
    assert frames == 2
    assert after.get("bg_color") == before["bg_color"], "增量补算不得丢 bg_color"
    assert after.get("size") == before.get("size")
    assert "gestures.fresh" in after["sources"]
    assert "gestures.old" not in after["sources"], "没点名的动作不该被抓进来"
    assert os.path.isdir(out_dir)
    assert len([n for n in os.listdir(out_dir) if n.endswith(".png")]) == 2


def test_gesture_is_skipped_when_already_done(studio):
    """`--missing` 语义：已经有够数产出的动作不再重算（文件时间不变）。"""
    import time

    root, aid = studio
    avatar_matte.run_gesture(aid, "fresh")
    out_dir = os.path.join(root, aid, "matte", "gestures", "fresh")
    before = {n: os.path.getmtime(os.path.join(out_dir, n))
              for n in os.listdir(out_dir)}
    time.sleep(0.05)
    avatar_matte.run(aid, frames_mode="gestures", quiet=True, only_missing=True)
    after = {n: os.path.getmtime(os.path.join(out_dir, n))
             for n in os.listdir(out_dir)}
    assert before == after, "已有素材的动作不该被 --missing 重算"


def test_missing_gesture_gets_filled(studio):
    """少一帧时 `--missing` 要把它补回来，而不是当成"已完成"跳过。"""
    root, aid = studio
    avatar_matte.run_gesture(aid, "fresh")
    out_dir = os.path.join(root, aid, "matte", "gestures", "fresh")
    victim = sorted(os.listdir(out_dir))[0]
    os.remove(os.path.join(out_dir, victim))

    avatar_matte.run(aid, frames_mode="gestures", quiet=True, only_missing=True)

    assert os.path.isfile(os.path.join(out_dir, victim)), "缺帧的动作必须被重新处理"


def test_unknown_gesture_yields_nothing(studio):
    root, aid = studio
    frames, out_dir = avatar_matte.run_gesture(aid, "no_such_action")
    assert frames == 0 and out_dir == ""


@pytest.mark.parametrize("bad", ["", "..", "../etc", "a/b", "a\\b", "."])
def test_unsafe_gesture_id_rejected(studio, bad):
    root, aid = studio
    with pytest.raises(ValueError):
        avatar_matte.run_gesture(aid, bad)


@pytest.mark.parametrize("ok", ["wave_one", "idle_sway", "open-2", "a.b_c"])
def test_safe_gesture_id_accepted(ok):
    assert avatar_matte.is_safe_id(ok)


def test_missing_gestures_reported_to_ui(tmp_path, monkeypatch):
    """界面要能提前看到「哪些动作还没有 alpha」，而不是现场才发现不换背景。"""
    import server.avatar_routes as avatar_routes

    root = tmp_path / "avatars"
    monkeypatch.setattr(avatar_routes, "DATA_DIR", str(root))
    aid = "demo"
    (root / aid / "matte").mkdir(parents=True)
    (root / aid / "matte" / "manifest.json").write_text("{}", encoding="utf-8")

    for gid, need in (("done", 2), ("half", 3), ("none", 1)):
        frames = root / aid / "gestures" / gid / "full_imgs"
        frames.mkdir(parents=True)
        for i in range(need):
            (frames / ("%08d.png" % i)).write_bytes(b"x")
    out = root / aid / "matte" / "gestures"
    (out / "done").mkdir(parents=True)
    (out / "half").mkdir(parents=True)
    for i in range(2):
        (out / "done" / ("%08d.png" % i)).write_bytes(b"x")
    (out / "half" / "00000000.png").write_bytes(b"x")

    missing = avatar_routes._missing_matte_gestures(aid)
    assert missing == ["half", "none"], "只报缺口，不报已完成/不存在的动作"
