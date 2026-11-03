# -*- coding: utf-8 -*-
"""内联问答对的变异检查：确认这轮新写的测试真能抓住问题。

比 test 通过更强的证据。每处变异测完立即还原；字节码缓存用
`PYTHONPYCACHEPREFIX` 指向**每次新建**的临时目录，既不会读到仓库里的旧
`.pyc`，也完全不碰仓库（避免触发宿主的批量删除拦截）。

运行：python tests/_mutation_check_inline_questions.py
"""

import os
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = r"C:\Users\24306\.conda\envs\livetalking\python.exe"

PLAN = ROOT / "utils" / "quiz_plan.py"
SESSION = ROOT / "utils" / "quiz_session.py"
ROUTES = ROOT / "server" / "quiz_routes.py"

TESTS = ["tests.test_quiz_plan", "tests.test_quiz_session", "tests.test_quiz_routes"]

MUTATIONS = [
    ("缺题干不再被拒绝", PLAN,
     "if not isinstance(question, str) or not question.strip():",
     "if False:  # mutated"),
    ("内联页码不做整数转换", PLAN,
     '"slide_id": _normalize_slide_id(item.get("slide_id")),',
     '"slide_id": item.get("slide_id"),'),
    ("纯提问模式多出一个提问点", PLAN,
     "points = list(range(len(items)))",
     "points = list(range(len(items) + 1))"),
    ("两对指定同一幻灯片不再被拒", PLAN,
     "if slide_id in slide_owner:",
     "if False:  # mutated"),
    ("讲稿提问点不足不再被拒", PLAN,
     "if len(free) > len(free_pool):",
     "if False:  # mutated"),
    ("未指定幻灯片的问答对对调顺序", PLAN,
     "for point, item in zip(sorted(picked), free):",
     "for point, item in zip(sorted(picked), list(reversed(free))):"),
    ("显式选取方式不再被拒", PLAN,
     "if mode == MODE_EXPLICIT:",
     "if False:  # mutated"),
    ("独立提问点不再生成（纯提问走不动）", SESSION,
     "for index in sorted(point_set - set(range(len(segments)))):",
     "for index in []:  # mutated"),
    ("题干朗读不回退到问答对自带幻灯片", SESSION,
     'slide_id = question.get("slide_id")',
     "slide_id = None  # mutated"),
    ("内联分支永不进入", ROUTES,
     "if raw_questions:",
     "if False:  # mutated"),
    ("内联与绑定表可同时使用", ROUTES,
     'raise QuizConfigError("内联问答对与绑定表不能同时使用，请二选一")',
     "pass  # mutated"),
    ("内联模式仍强制要求讲稿", ROUTES,
     "segments = _build_segments(body, required=False)",
     "segments = _build_segments(body)"),
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
            results.append((name, "SKIP(未匹配)"))
            continue
        try:
            target.write_text(original.replace(old, new, 1), encoding="utf-8")
            passed = run_tests()
        finally:
            target.write_text(original, encoding="utf-8")
        results.append((name, "存活(未抓住)" if passed else "已抓住"))

    print("=== 内联问答对变异检查 ===")
    caught = 0
    for name, verdict in results:
        print("[%s] %s" % ("CAUGHT" if verdict == "已抓住" else "MISS", name))
        if verdict == "已抓住":
            caught += 1
    print("抓住 %d/%d" % (caught, len(results)))

    for target, text in originals.items():
        if target.read_text(encoding="utf-8") != text:
            print("!!! 还原失败: %s" % target)
            return 1
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
