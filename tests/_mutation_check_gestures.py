# -*- coding: utf-8 -*-
"""手势模块的变异检查：确认这轮新写的测试真能抓住问题。

比 test 通过更强的证据。每处变异测完立即还原（进程被强杀也不会把实现留在改坏的状态）；
字节码缓存用 `PYTHONPYCACHEPREFIX` 指向新建的临时目录，既不读仓库里的旧 `.pyc`，
也完全不碰仓库（避免触发宿主的批量删除拦截）。

运行：python tests/_mutation_check_gestures.py
"""

import os
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = r"C:\Users\24306\.conda\envs\livetalking\python.exe"

GESTURES = ROOT / "utils" / "gestures.py"
BASE = ROOT / "avatars" / "base_avatar.py"
ROUTES = ROOT / "server" / "routes.py"

TESTS = [
    "tests.test_gestures",
    "tests.test_gesture_segments",
    "tests.test_gesture_runtime",
    "tests.test_gesture_routes",
]

MUTATIONS = [
    # ── 素材发现与加载 ────────────────────────────────────────────────
    ("缺 coords.pkl 的手势仍算可用", GESTURES,
     "if not os.path.isfile(os.path.join(path, GESTURE_COORDS_FILE)):",
     "if False:  # mutated"),
    ("face_imgs 为空的手势仍算可用", GESTURES,
     "return bool(_sorted_images(os.path.join(path, GESTURE_FACE_DIR))\n"
     "                and _sorted_images(os.path.join(path, GESTURE_FULL_DIR)))",
     "return True  # mutated"),
    ("标识不再拒绝路径穿越", GESTURES,
     "    if '..' in gesture_id or gesture_id in _RESERVED_IDS:",
     "    if False:  # mutated"),
    ("素材尺寸不一致时不再归一化", GESTURES,
     "        if size and size != (int(ref_size[0]), int(ref_size[1])):",
     "        if False:  # mutated"),
    ("人脸框不随画面一同缩放", GESTURES,
     "            coords = [(y1 * scale_y, y2 * scale_y, x1 * scale_x, x2 * scale_x)\n"
     "                      for (y1, y2, x1, x2) in coords]",
     "            coords = list(coords)  # mutated"),
    ("gesture.json 的 label 被忽略", GESTURES,
     "        if isinstance(label, str) and label.strip():\n"
     "            return label.strip()",
     "        if False:  # mutated\n            return label.strip()"),
    ("不齐备的手势也出现在列表里", GESTURES,
     "        if not os.path.isdir(path) or not _has_pieces(path):",
     "        if not os.path.isdir(path):"),
    # ── 分段解析 ──────────────────────────────────────────────────────
    ("手势标签不再作为段落边界", ROUTES,
     r"    r'|\[GESTURE:(?P<gesture>[A-Za-z0-9_.-]+)\]',",
     "    r'',"),
    ("手势跨段继承（未带标签也用上一段的）", ROUTES,
     "            segments.append((seg_text, current_slide, pending_gesture))\n"
     "            pending_gesture = None",
     "            segments.append((seg_text, current_slide, pending_gesture))"),
    ("段内多个手势标签取最后一个", ROUTES,
     "        elif pending_gesture is None:\n"
     "            pending_gesture = match.group('gesture')",
     "        else:\n            pending_gesture = match.group('gesture')"),
    # ── 受理阶段校验 ──────────────────────────────────────────────────
    ("不可用的手势不再被摘除（仍下发下去）", ROUTES,
     "                        (seg_text, slide_id, None if gesture in missing else gesture)",
     "                        (seg_text, slide_id, gesture)"),
    ("校验只看第一个手势", ROUTES,
     "    missing = [name for name in names if name not in known]",
     "    missing = []  # mutated"),
    ("校验通过后不预热", ROUTES,
     "                        avatar_session.preheat_gestures(list(dict.fromkeys(usable)))",
     "                        pass  # mutated"),
    ("受理阶段不再把警告返回给调用方", ROUTES,
     "                    warnings.append({\n"
     "                        'code': 'GESTURE_UNAVAILABLE',\n"
     "                        'message': message,\n"
     "                        'gestures': missing,\n"
     "                        'available': available_ids,\n"
     "                    })",
     "                    pass  # mutated"),
    ("受理阶段不再记录警告（静默降级）", ROUTES,
     "                    logger.warning('[手势标签] %s', message)",
     "                    pass  # mutated"),
    # ── 运行时帧源切换 ────────────────────────────────────────────────
    ("取帧不再取模（切换窗口内可能越界）", BASE,
     "    return seq[(idx or 0) % len(seq)]",
     "    return seq[idx or 0]"),
    ("切换后索引不重置（手势不从第 0 帧开始）", BASE,
     "            if self._frame_source_gen != seen_frame_source_gen:",
     "            if False:  # mutated"),
    ("播满一遍后不回到默认素材", BASE,
     "        if total and self._gesture_frames_played >= total:",
     "        if False:  # mutated"),
    ("播放进度改成按 index 判定（静音边界会算错）", BASE,
     "        self._gesture_frames_played += frames_produced",
     "        self._gesture_frames_played = 0  # mutated"),
    # 说明：`preheat_gestures` 传 `activate=False`、`_load_then_activate` 再查
    # `_requested_gesture == name`，是两道各自都能拦住"预热抢激活"的防线。
    # 只改其中任一道是**等价变异**（行为不变），因此这里改的是承担语义的那一道：
    # 去掉它会退化成"迟到的加载抢走画面"，由 test_slow_load_does_not_hijack_a_newer_request 抓住。
    ("迟到的加载抢走当前手势", BASE,
     "        if activate and self._requested_gesture == name:\n            self._activate(name, payload)",
     "        if activate or self._requested_gesture:\n            self._activate(name, payload)"),
    ("length 不随帧源更新（按默认素材长度推进）", BASE,
     "            length = self.get_avatar_length()",
     "            length = len(self.frame_list_cycle)  # mutated"),
    ("素材缓存不再淘汰", BASE,
     "        while len(self._gesture_cache) > GESTURE_CACHE_LIMIT:",
     "        while False:  # mutated"),
    ("加载失败不降级也不告警", BASE,
     "            logger.warning('[手势] %s 加载失败（%s），改用默认素材继续', name, exc)",
     "            pass  # mutated"),
    ("静音分支回退到默认素材取帧", BASE,
     "                    target_frame = self._frame_at(idx)",
     "                    target_frame = self.frame_list_cycle[idx]"),
    ("贴回帧不看当前帧源", BASE,
     "        return _indexed(self._sequence(self._active_frames, 'frame_list_cycle'), idx)",
     "        return _indexed(getattr(self, 'frame_list_cycle', None), idx)"),
]


def run_tests():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = tempfile.mkdtemp(prefix="mutcache-")
    proc = subprocess.run(
        [PYTHON, "-m", "unittest"] + TESTS,
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    return proc.returncode == 0


def main():
    originals = {}
    for _, target, _, _ in MUTATIONS:
        originals.setdefault(target, target.read_text(encoding="utf-8"))

    results = []
    for name, target, old, new in MUTATIONS:
        original = originals[target]
        if old not in original:
            results.append((name, "SKIP"))
            continue
        try:
            target.write_text(original.replace(old, new, 1), encoding="utf-8")
            passed = run_tests()
        finally:
            target.write_text(original, encoding="utf-8")
        results.append((name, "存活(未抓住)" if passed else "已抓住"))

    print("=== 手势模块变异检查 ===")
    caught = sum(1 for _, verdict in results if verdict == "已抓住")
    skipped = [name for name, verdict in results if verdict == "SKIP"]
    survived = [name for name, verdict in results if verdict == "存活(未抓住)"]
    for name, verdict in results:
        print("[%s] %s" % (verdict, name))
    print("抓住 %d/%d（跳过 %d，存活 %d）" % (caught, len(results), len(skipped), len(survived)))
    if skipped:
        print("跳过的项（说明变异点没匹配到源码，需检查）: %s" % skipped)
    if survived:
        print("存活的项（说明测试没覆盖到这条）: %s" % survived)

    for target, text in originals.items():
        if target.read_text(encoding="utf-8") != text:
            print("!!! 还原失败: %s" % target)
            return 1
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
