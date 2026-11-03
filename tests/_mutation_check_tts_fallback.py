# -*- coding: utf-8 -*-
"""对「开箱默认兜底 + 会话内短路」这组改动做变异检查。

测试通过不等于测试有效：把实现逐处故意改坏，确认每一处都有测试能抓住。

两个环境细节（都是踩过的坑）：
- 用 `PYTHONPYCACHEPREFIX` 把字节码缓存指到临时目录，避免读到仓库里过期的
  `.pyc`（失效判定只看「mtime(秒) + 大小」，同秒内等长改写会误用旧字节码）。
- 每轮测完立刻还原，不做磁盘备份——本机有批量删除保护，清理备份反而会中断脚本，
  被强杀时就会把改坏的实现留在仓库里。
"""

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYTHON = sys.executable

BASE = ROOT / "tts" / "base_tts.py"
PLATFORM = ROOT / "utils" / "platform_support.py"
STORE = ROOT / "utils" / "settings_store.py"
CONFIG = ROOT / "config.py"

_PLATFORM_RETURN = '    return OFFLINE_TTS_ENGINE if str(platform).startswith("win") else ""\n'

MUTATIONS = (
    ("默认兜底不再看平台（非 Windows 也指向 sapi）", PLATFORM,
     _PLATFORM_RETURN,
     '    return OFFLINE_TTS_ENGINE\n'),

    ("默认兜底退化为空（回到必然失败且无兜底）", PLATFORM,
     _PLATFORM_RETURN,
     '    return ""\n'),

    ("命令行默认值退回空串", CONFIG,
     'parser.add_argument(\'--TTS_FALLBACK\', type=str, default=DEFAULT_TTS_FALLBACK,',
     'parser.add_argument(\'--TTS_FALLBACK\', type=str, default="",'),

    ("设置项默认值退回空串（页面与实际不一致）", STORE,
     '"env": "TTS_FALLBACK", "default": DEFAULT_TTS_FALLBACK, "empty_is_value": True,',
     '"env": "TTS_FALLBACK", "default": "", "empty_is_value": True,'),

    ("短路阈值改为 1（单次抖动即降级全程）", BASE,
     "PRIMARY_FAILURE_THRESHOLD = 2\n",
     "PRIMARY_FAILURE_THRESHOLD = 1  # mutated\n"),

    ("阈值比较用 > 而非 >=（阈值形同虚设）", BASE,
     "        if self._primary_fail_streak >= PRIMARY_FAILURE_THRESHOLD:\n",
     "        if self._primary_fail_streak > PRIMARY_FAILURE_THRESHOLD:\n"),

    ("主引擎成功时不清零连续失败计数", BASE,
     "            self.txt_to_audio(msg)\n"
     "            self.last_error = None\n"
     "            self._primary_fail_streak = 0\n"
     "            return True\n",
     "            self.txt_to_audio(msg)\n"
     "            self.last_error = None\n"
     "            return True\n"),

    ("短路状态下不再合成（静默丢弃文本）", BASE,
     "            if fallback is not None:\n"
     "                return self._speak_with(fallback, msg, short_circuited=True)\n",
     "            if fallback is not None:\n"
     "                return True\n"),

    ("备用引擎不可用时保留短路（不再尝试主引擎）", BASE,
     "            self._exit_short_circuit('备用引擎不可用')\n",
     "            return False\n"),

    ("备用引擎失效时不解除短路", BASE,
     "            if short_circuited:\n"
     "                self._exit_short_circuit('备用引擎失败')\n",
     "            if short_circuited:\n"
     "                pass\n"),

    ("进入短路不记日志", BASE,
     "        self._skip_primary = True\n"
     "        logger.warning(\n"
     "            '[TTS] 主引擎 %s 连续 %d 次合成失败，且备用引擎 %s 已成功接管；'\n"
     "            '本会话后续合成将直接使用备用引擎，不再重复等待主引擎（新建会话会重新尝试主引擎）',\n"
     "            getattr(self.opt, 'tts', '?'), self._primary_fail_streak, self._fallback_name)\n",
     "        self._skip_primary = True\n"),

    ("解除短路不记日志", BASE,
     "        logger.warning('[TTS] 解除主引擎短路（%s），下一次合成将重新尝试 %s',\n"
     "                       reason, getattr(self.opt, 'tts', '?'))\n",
     "        pass\n"),
)


def run_tests(pycache_prefix):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = pycache_prefix
    proc = subprocess.run(
        [PYTHON, "-m", "unittest", "tests.test_tts_engine", "tests.test_settings_store"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    return proc.returncode == 0


def main():
    originals = {}
    for _, target, _, _ in MUTATIONS:
        originals.setdefault(target, target.read_text(encoding="utf-8"))

    pycache = tempfile.mkdtemp(prefix="lt-mutation-pyc-")
    results = []
    for name, target, old, new in MUTATIONS:
        original = originals[target]
        if old not in original:
            results.append((name, "SKIP(未匹配)"))
            continue
        if len(old) == len(new):
            # 等长改写会让 .pyc 失效判定失效，必须避免
            results.append((name, "SKIP(等长改写)"))
            continue
        try:
            target.write_text(original.replace(old, new, 1), encoding="utf-8")
            passed = run_tests(pycache)
        finally:
            target.write_text(original, encoding="utf-8")
        results.append((name, "存活(未抓住)" if passed else "已抓住"))

    print("=== 变异检查结果 ===")
    caught = survived = skipped = 0
    for name, verdict in results:
        tag = {"已抓住": "CAUGHT", "存活(未抓住)": "SURVIVED"}.get(verdict, "SKIPPED")
        if tag == "CAUGHT":
            caught += 1
        elif tag == "SURVIVED":
            survived += 1
        else:
            skipped += 1
        print("[%s] %s" % (tag, name))
    print("抓住 %d，存活 %d，跳过 %d，共 %d"
          % (caught, survived, skipped, len(results)))

    # 复核还原是否干净
    dirty = [str(t) for t, text in originals.items()
             if t.read_text(encoding="utf-8") != text]
    if dirty:
        print("!!! 还原失败: %s" % ", ".join(dirty))
    return 0 if (survived == 0 and skipped == 0 and not dirty) else 1


if __name__ == "__main__":
    sys.exit(main())
