# -*- coding: utf-8 -*-
"""路径与跨域加固的变异检查：确认这轮新写的测试真能抓住漏洞。

比"测试通过"更强的证据：把每道闸门逐个改坏，看测试是否立刻失败。
每处变异测完立即还原（进程被强杀也不会把实现留在改坏的状态）；
字节码缓存用 `PYTHONPYCACHEPREFIX` 指向新建的临时目录，既不读仓库里的旧 `.pyc`，
也完全不碰仓库。

运行：python tests/_mutation_check_path_guards.py
"""

import os
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = r"C:\Users\24306\.conda\envs\livetalking\python.exe"

PATH_GUARD = ROOT / "utils" / "path_guard.py"
NET_GUARD = ROOT / "utils" / "net_guard.py"
ROUTES = ROOT / "server" / "routes.py"
AVATAR_ROUTES = ROOT / "server" / "avatar_routes.py"
CORS_POLICY = ROOT / "server" / "cors_policy.py"

TESTS = [
    "tests.test_path_guard",
    "tests.test_net_guard",
    "tests.test_voice_guard",
    "tests.test_avatar_guard",
    "tests.test_cors_policy",
]

MUTATIONS = [
    # ── 包含性判定本身 ────────────────────────────────────────────────
    # 说明：把 `common == root_norm` 改成 `common.startswith(root_norm)` 是**等价变异**
    # ——`commonpath` 的结果必然是两个输入路径的前缀，而 root 本身就是输入之一，所以
    # common 要么等于 root、要么是 root 的严格前缀；后者下 `startswith(root)` 为假。
    # 也就是说真正提供防护的是"用 commonpath 而不是字符串前缀"，因此这里变异成
    # 历史上的真实漏洞形态：直接拿候选路径去 startswith 根路径。
    ("包含判定退回字符串前缀（data/voices2 会被放行）", PATH_GUARD,
     "    return common == root_norm",
     "    return candidate_norm.startswith(root_norm)"),
    ("不再解开符号链接（可被链接带出根目录）", PATH_GUARD,
     "    return os.path.normcase(os.path.realpath(os.path.abspath(path)))",
     "    return os.path.normcase(os.path.abspath(path))"),
    ("跨盘符的 ValueError 被吞掉后放行", PATH_GUARD,
     "    except ValueError:\n"
     "        # 跨盘符（Windows）、混合绝对/相对路径 —— 一律不在之内。\n"
     "        return False",
     "    except ValueError:\n        return True  # mutated"),
    ("resolve_within 不再校验直接返回", PATH_GUARD,
     "    if not is_within(root, candidate):\n"
     "        raise PathGuardError('路径越出允许目录')",
     "    if False:  # mutated\n        raise PathGuardError('路径越出允许目录')"),
    ("resolve_within_any 不再校验直接返回", PATH_GUARD,
     "    for root in roots or ():\n"
     "        if is_within(root, candidate):\n"
     "            return os.path.realpath(os.path.abspath(str(candidate)))\n"
     "    raise PathGuardError('路径越出允许目录')",
     "    return os.path.realpath(os.path.abspath(str(candidate)))"),
    ("文件名净化不再去掉目录成分", PATH_GUARD,
     "    text = text.replace('\\\\', '/').rstrip('/').rsplit('/', 1)[-1]",
     "    pass  # mutated"),
    ("validate_filename 不再拒绝分隔符", PATH_GUARD,
     "    if _FORBIDDEN_FILENAME_RE.search(text):\n"
     "        raise PathGuardError('名称不能包含路径分隔符、控制字符或保留字符')",
     "    if False:  # mutated\n        raise PathGuardError('boom')"),
    # ── 回调允许列表 ──────────────────────────────────────────────────
    ("主机允许列表被绕过（任意主机都放行）", NET_GUARD,
     "        elif host == entry:\n            return True",
     "        elif True:  # mutated\n            return True"),
    ("协议白名单被去掉（file:// 也能通过）", NET_GUARD,
     "    if scheme not in ALLOWED_NOTIFY_SCHEMES:",
     "    if False:  # mutated"),
    # 说明：删掉 `validate_notify_url` 里"空允许列表直接拒绝"这一段是**等价变异**
    # ——后续的 `host_allowed(host, port, ())` 本来就返回 False，仍会抛错，且错误信息
    # 同样带 AVATAR_NOTIFY_ALLOWED_HOSTS。这一段的价值是给出更直白的提示，不是唯一防线。
    # ── 声音接口 ──────────────────────────────────────────────────────
    ("声音删除不再做包含性校验", ROUTES,
     "        try:\n"
     "            full_path = path_guard.resolve_within(VOICES_DIR, candidate)\n"
     "        except path_guard.PathGuardError:\n"
     "            logger.warning(\"[声音克隆] 拒绝越界删除: %r\", file_path)\n"
     "            return json_error(\"只能删除 data/voices 目录下的声音文件\")",
     "        full_path = candidate  # mutated"),
    ("声音名称不再过合法性校验", ROUTES,
     "        except path_guard.PathGuardError as exc:\n"
     "            logger.warning(\"[声音克隆] 拒绝非法名称 %r: %s\", voice_name, exc)\n"
     "            return json_error(\"声音名称不合法：{0}\".format(exc))",
     "        except path_guard.PathGuardError as exc:\n"
     "            safe_name = voice_name  # mutated"),
    ("声音扩展名不再过白名单", ROUTES,
     "        if ext not in ALLOWED_VOICE_EXTS:",
     "        if False:  # mutated"),
    # ── 形象任务 ──────────────────────────────────────────────────────
    ("上传文件名不再净化", AVATAR_ROUTES,
     "                    filename = path_guard.sanitize_filename(part.filename, 'video.mp4')",
     "                    filename = part.filename  # mutated"),
    ("video_path 不再做包含性校验", AVATAR_ROUTES,
     "        try:\n"
     "            path_guard.resolve_within_any(allowed_video_roots(), video_path)\n"
     "        except path_guard.PathGuardError:",
     "        try:\n"
     "            pass  # mutated\n"
     "        except path_guard.PathGuardError:"),
    ("notifyurl 不再过允许列表", AVATAR_ROUTES,
     "            notify_url = validate_notify_url(\n"
     "                params.get('notifyurl'), allowed_notify_hosts())",
     "            notify_url = params.get('notifyurl')  # mutated"),
    ("允许根退化为通配（任意绝对路径都放行）", AVATAR_ROUTES,
     "    roots = _server_data_roots()\n"
     "    for root in (item.strip() for item in str(env_value or '').split(',')):",
     "    roots = [os.path.abspath(os.sep)]  # mutated\n"
     "    for root in (item.strip() for item in str(env_value or '').split(',')):"),
    # ── 跨域 ──────────────────────────────────────────────────────────
    ("跨域配置退回通配来源", CORS_POLICY,
     "    defaults = {\n"
     "        origin: aiohttp_cors.ResourceOptions(",
     "    defaults = {\n"
     "        '*': aiohttp_cors.ResourceOptions("),
    ("未登记来源时仍然安装跨域中间件", CORS_POLICY,
     "    if not origins:\n"
     "        logger.info(\"[跨域] 未配置 %s，仅允许同源访问\", CORS_ORIGINS_ENV)\n"
     "        return None",
     "    if False:  # mutated\n        return None\n"
     "    origins = origins or ('*',)"),
    ("通配来源的配置不再 fail-closed", CORS_POLICY,
     "    except NetGuardError as exc:\n"
     "        logger.error(\"[跨域] %s；本次按不信任任何来源处理\", exc)\n"
     "        return ()",
     "    except NetGuardError as exc:\n        return ('*',)  # mutated"),
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

    print("=== 路径与跨域加固变异检查 ===")
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
