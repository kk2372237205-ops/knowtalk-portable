#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""制作 KnowTalk 便携包：换一台 Windows 机器，整个目录拷过去就能跑。

要解决的三个现实问题：
1. 模型权重有 4.2G，但其中约 0.4G 是同一份权重的不同格式副本
   （.bin / .safetensors / .h5 / .msgpack），加载时只会用 .safetensors。
2. 代码里写死的解释器路径（`C:\\Users\\xxx\\.conda\\...`）换机必失效 ——
   便携包把环境放 runtime/，启动脚本用 %~dp0 现算路径，并把结果写进
   .python-path.txt（桌面端第一个就读它）。
3. 依赖 torch 2.14+cu126 的环境本身 6.9G，重装一遍网络稍有闪失就废，
   直接整体搬过去最稳。

用法：
    python scripts/make_portable.py --dest D:\\knowtalk-portable
    python scripts/make_portable.py --dest E:\\kt --steps app,models     # 分步
    python scripts/make_portable.py --dest E:\\kt --steps verify          # 只校验

分步是幂等的：中断后重跑只会补齐缺失的文件，不会从头再来。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 模型：只搬 MuseTalk 真正会加载的那些 ───────────────────────────
# HuggingFace 仓库习惯把同一份权重存成好几种格式，内容一样。
# transformers / diffusers 加载时优先 .safetensors，其余格式从来不读 ——
# 它们是纯冗余，加起来 751MB。
# 另：wav2lip.pth（205M）只有 --model wav2lip 才用，默认 musetalk 用不到。
MODEL_KEEP = {
    "musetalkV15": ["musetalk.json", "unet.pth"],
    "sd-vae": ["config.json", "diffusion_pytorch_model.safetensors"],
    "whisper": [
        "config.json", "generation_config.json", "preprocessor_config.json",
        "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt",
        "special_tokens_map.json", "added_tokens.json", "normalizer.json",
        "model.safetensors",
    ],
    "face-parse-bisent": ["79999_iter.pth", "resnet18-5c106cde.pth"],
    "rvm": ["rvm_mobilenetv3.pth"],
}

# ── 外部模型：.env 里指向项目外的绝对目录，换机必失效 ─────────────
# STT_MODEL 常被写成 `D:\models\faster-whisper-small`，那个目录不在仓库里，
# 只搬源码就只搬了个路径字符串 —— 到目标机 STT 直接报"加载语音模型失败"。
# 这里按 .env 里的实际值把它搬进 models/<目录名>，再把 .env 改成包内相对路径。
# 用相对路径是安全的：三个 bat 都先 cd /d "%~dp0"，进程 CWD 就是包根。
EXTERNAL_MODEL_VARS = ("STT_MODEL",)

# 绝对路径 -> 包内相对路径，step_models 填、step_bootstrap 消费
_REWRITES = {}

# ── 运行时数据：哪些进包 ──────────────────────────────────────────
# data/tmp（340M）是历次抠像/预览的中间产物，avatar-motion-poc 是 POC 残留，
# _backup 是本机的旧备份 —— 都不该跟着走。
DATA_KEEP_DIRS = [
    "avatars", "backgrounds", "settings", "kiosk", "voices",
    "materials", "workflows", "quiz", "img3d", "record", "seedance",
    # auth 存着登录账号和 session 签名密钥，漏了那边就没人能登录
    "auth",
]
DATA_SKIP_DIRS = {"tmp", "_backup", "avatar-motion-poc", "trash"}

# ── 环境搬运时要扔掉的垃圾 ────────────────────────────────────────
RUNTIME_SKIP_DIRS = {"__pycache__", ".conda_trash", ".tmp", "pkgs"}
RUNTIME_SKIP_FILES = {"*.pyc", "*.pyo", "*.log"}

STEPS = ("app", "models", "data", "runtime", "bootstrap", "verify")


# ────────────────────────── 基础工具 ──────────────────────────

def _hsize(n):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024.0:
            return "%.1f%s" % (n, unit)
        n /= 1024.0
    return "%.1fTB" % n


def _fsize(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def _ensure_dir(p):
    os.makedirs(p, exist_ok=True)
    return p


def _free_bytes(path):
    """目标盘的剩余空间（Windows 用 disk_free，跨平台落到 shutil）。"""
    try:
        total, used, free = shutil.disk_usage(os.path.abspath(path))
        return free
    except Exception:
        return -1


def _robocopy(src, dst, xd=None, xf=None, quiet=True):
    """整目录复制优先 robocopy：多线程 /MT 比 Python 逐文件快得多，
    且自带断点续传语义（/R:1 /W:1 让它别在坏文件上死等）。

    robocopy 退出码 0~7 都算成功（>=8 才是真出错），别按常规 0 判断。
    """
    exe = shutil.which("robocopy")
    if not exe:
        return False
    cmd = [exe, str(src), str(dst), "/E", "/MT:16", "/R:1", "/W:1", "/COPY:DAT"]
    if xd:
        cmd.append("/XD:" + " ".join(str(x) for x in xd))
    if xf:
        cmd.append("/XF:" + " ".join(str(x) for x in xf))
    if quiet:
        cmd += ["/NFL", "/NDL", "/NJH", "/NJS", "/NP"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    except Exception:
        return False
    return r.returncode < 8


def _copy_file(src, dst, skip_if_same=True):
    """单文件复制；同名且大小一致就跳过（分步重跑的关键）。"""
    dst = Path(dst)
    if skip_if_same and dst.is_file() and _fsize(dst) == _fsize(src):
        return 0
    _ensure_dir(dst.parent)
    shutil.copy2(src, dst)
    return _fsize(src)


def _copy_tree_fallback(src, dst, skip_dirs=()):
    """robocopy 不可用时的退路：逐文件复制。"""
    total = 0
    src = Path(src)
    for dp, dn, fn in os.walk(src):
        dn[:] = [d for d in dn if d not in skip_dirs]
        rel = os.path.relpath(dp, src)
        for f in fn:
            total += _copy_file(os.path.join(dp, f),
                                os.path.join(dst, rel, f))
    return total


# ────────────────────────── 各个步骤 ──────────────────────────

def step_app(dest, log):
    """源码：直接用 git ls-files 的清单。

    好处是不用手工维护排除规则 —— .gitignore 里已经把一次性调试产物
    （_diag_*.py / _shot_*.py）、日志、模型、data 全排除了，
    git ls-files 拿到的就是干净且完整的源码集。
    """
    try:
        r = subprocess.run(["git", "ls-files", "-z"], cwd=str(ROOT),
                           capture_output=True, check=True, text=True)
    except Exception as exc:
        raise SystemExit("取不到 git 文件清单（%s）。"
                         "源码步骤依赖 git ls-files，请在仓库里运行。" % exc)

    names = [n for n in r.stdout.split("\0") if n.strip()]
    total, n = 0, 0
    for name in names:
        src = ROOT / name
        if not src.is_file():
            continue
        total += _copy_file(src, dest / name)
        n += 1
    log("  源码 %d 个文件，%s" % (n, _hsize(total)))
    return total


def _read_env_items(env_path):
    """把 .env 读成 dict（忽略注释与空行）。"""
    items = {}
    env_path = Path(env_path)
    if not env_path.is_file():
        return items
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        items[key.strip()] = val.strip()
    return items


def _norm_path(p):
    """比较路径用：统一斜杠与大小写（Windows 文件系统不区分大小写）。"""
    return str(p).replace("\\", "/").rstrip("/").lower()


def _external_models():
    """找出 .env 里指向项目外绝对目录的模型。

    返回 [(变量名, 源绝对路径, 包内相对路径)]。只认已存在的目录 ——
    路径写错或还没下载的，交给用户在目标机上自己处理。
    """
    items = _read_env_items(ROOT / ".env")
    out = []
    for var in EXTERNAL_MODEL_VARS:
        raw = items.get(var, "")
        if not re.match(r"^[A-Za-z]:[\\/]", raw):
            continue
        src = Path(raw)
        if not src.is_dir():
            continue
        out.append((var, src, "models/" + src.name))
    return out


def _sync_rewrites(dest):
    """让改写表与目标包的实际内容同步。

    不能只靠 step_models 顺手记 —— 分步跑（--steps bootstrap）时那块内存是空的，
    .env 就会被写回绝对路径。以"包内目录是否存在"为准才幂等。
    """
    for _var, src, rel in _external_models():
        if (Path(dest) / rel).is_dir():
            _REWRITES[_norm_path(src)] = rel
    return _REWRITES


def step_models(dest, log):
    """模型：按白名单搬，跳过重复格式和 wav2lip 那套。

    另外把 .env 指向的外部模型目录（如 faster-whisper-small）一并搬进
    models/ 下 —— 它不在仓库里，不搬的话目标机上语音识别起不来。
    """
    total, n = 0, 0
    skipped = 0
    models_root = ROOT / "models"
    for sub, files in MODEL_KEEP.items():
        for name in files:
            src = models_root / sub / name
            if not src.is_file():
                log("  [缺失] %s —— 目标机上对应功能会起不来" % (src.relative_to(ROOT)))
                continue
            total += _copy_file(src, dest / "models" / sub / name)
            n += 1
    # 算一下省了多少
    for dp, dn, fn in os.walk(models_root):
        for f in fn:
            rel = Path(os.path.relpath(os.path.join(dp, f), models_root)).as_posix()
            if rel.split("/")[0] in MODEL_KEEP and rel not in \
                    {k + "/" + v for k, vs in MODEL_KEEP.items() for v in vs}:
                skipped += _fsize(os.path.join(dp, f))
            elif rel.split("/")[0] not in MODEL_KEEP:
                skipped += _fsize(os.path.join(dp, f))
    log("  模型 %d 个文件，%s（省掉 %s 冗余副本）" % (n, _hsize(total), _hsize(skipped)))

    # 外部模型（不在仓库里，只能按 .env 的线索去搬）
    for var, src, rel in _external_models():
        tgt = dest / rel
        _ensure_dir(tgt)
        before = sum(_fsize(os.path.join(dp, f))
                     for dp, dn, fn in os.walk(tgt) for f in fn)
        if not _robocopy(src, tgt):
            _copy_tree_fallback(src, tgt)
        after = sum(_fsize(os.path.join(dp, f))
                    for dp, dn, fn in os.walk(tgt) for f in fn)
        _sync_rewrites(dest)
        total += after
        n += 1
        log("  外部模型 %s -> %s  %s（%s）"
            % (src, rel, _hsize(after), "已存在，仅补差" if before else "首次搬运"))
        log("         .env 的 %s 会改写成 %s" % (var, rel))
    return total


SECRET_KEYS = ("api_key", "apikey", "secret", "token", "password", "pwd",
               "access_key", "sk-")


def _scrub(obj):
    """递归把敏感字段的值抹掉，保留结构（这样到那边界面还能正常打开，
    只是需要重新填一次密钥）。"""
    n = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            low = str(k).lower()
            if any(s in low for s in SECRET_KEYS) and isinstance(v, str) and v:
                obj[k] = ""
                n += 1
            else:
                n += _scrub(v)
    elif isinstance(obj, list):
        for item in obj:
            n += _scrub(item)
    return n


def _copy_setting_json(src, dst):
    """配置类 json：带脱敏选项地复制。"""
    try:
        with open(src, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return _copy_file(src, dst), 0
    n = _scrub(data)
    _ensure_dir(Path(dst).parent)
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return _fsize(dst), n


def step_data(dest, log, scrub=False):
    """运行时数据：形象素材、背景、设置、全息舱配置、音色等。

    注意 data/settings 里有明文 API Key（LLM 服务商的），默认原样带走 ——
    目标是自己的机器时省得重配；包要交给别人时用 --scrub-secrets 抹掉。
    """
    total = 0
    wiped = 0
    data_root = ROOT / "data"
    for name in DATA_KEEP_DIRS:
        src = data_root / name
        if not src.is_dir():
            continue
        sub_total = 0
        for dp, dn, fn in os.walk(src):
            dn[:] = [d for d in dn if d not in DATA_SKIP_DIRS]
            rel = os.path.relpath(dp, src)
            for f in fn:
                target = os.path.join(dest, "data", name, rel, f)
                if scrub and name == "settings" and f.endswith(".json"):
                    size, n = _copy_setting_json(os.path.join(dp, f), target)
                    wiped += n
                else:
                    size = _copy_file(os.path.join(dp, f), target)
                sub_total += size
        total += sub_total
        log("    data/%s  %s" % (name, _hsize(sub_total)))
    log("  运行时数据合计 %s" % _hsize(total))
    if wiped:
        log("  已抹掉 %d 处密钥/口令（到目标机上重新填一次）" % wiped)
    return total


def step_runtime(dest, log):
    """Python 环境：整体搬运，换机不用重装 torch。

    目标是 envs/livetalking 那种 conda 环境目录。复制后它在新机器上的
    路径和原来不一样，但 Python 与 site-packages 之间是相对布局，
    torch 的 DLL 也由它自己 add_dll_directory 加载，所以整体搬是可行的；
    唯一硬性前提是目标机装了 VC++ 运行库（部署说明里写明了）。
    """
    env = os.environ.get("KNOWTALK_PYTHON_ENV")
    if not env:
        # 默认认为当前解释器就在一个 conda 环境里
        env = os.path.dirname(sys.executable)
    env = Path(env)
    if not (env / "python.exe").is_file():
        raise SystemExit("这个目录里没有 python.exe，不像是 Python 环境：%s\n"
                         "可用 KNOWTALK_PYTHON_ENV 指定要搬的环境目录。" % env)

    target = dest / "runtime"
    _ensure_dir(target)
    log("  搬运 %s -> %s（6~7G，慢是正常的）" % (env, target))
    t0 = time.time()
    xd = list(RUNTIME_SKIP_DIRS)
    xf = list(RUNTIME_SKIP_FILES)
    if not _robocopy(env, target, xd=xd, xf=xf):
        log("  robocopy 不可用，退回逐文件复制")
        _copy_tree_fallback(env, target, skip_dirs=RUNTIME_SKIP_DIRS)
    total = sum(_fsize(os.path.join(dp, f))
                for dp, dn, fn in os.walk(target)
                for f in fn)
    log("  环境搬运完成，用时 %.0f 秒，共 %s" % (time.time() - t0, _hsize(total)))
    return total


# ────────────────────────── 生成的启动脚本 ──────────────────────────

# 用 GBK 写 + chcp 936：中文在任意 Windows 上都显示正常，
# 不像 UTF-8 的 bat 在默认 936 代码页下会显示成乱码。
BAT_UI = r'''@echo off
chcp 936 >nul
rem 知课交互数字人 —— 启动桌面端（会自动拉起主服务）
cd /d "%~dp0"

set PY=%~dp0runtime\python.exe
set PYW=%~dp0runtime\pythonw.exe

if not exist "%PY%" (
  echo.
  echo   [错误] 没找到 runtime\python.exe
  echo   这个包不完整，请重新拷贝 runtime 目录。
  echo.
  pause
  exit /b 1
)

rem 把真实路径写进 .python-path.txt：桌面端第一个就读它，
rem 这样无论整个包被放在哪个盘哪个目录，它都能找到正确的解释器。
if exist "%PY%" > "%~dp0.python-path.txt" echo %PY%

if exist "%PYW%" (
  start "" "%PYW%" "%~dp0desktop_app.py"
) else (
  start "" "%PY%" "%~dp0desktop_app.py"
)
'''

BAT_SERVICE = r'''@echo off
chcp 936 >nul
rem 知课交互数字人 —— 只启动主服务（带控制台，看日志用）
cd /d "%~dp0"

set PY=%~dp0runtime\python.exe
if not exist "%PY%" (
  echo [错误] 没找到 runtime\python.exe
  pause
  exit /b 1
)

if exist "%PY%" > "%~dp0.python-path.txt" echo %PY%
"%PY%" "%~dp0app.py" %*
pause
'''

BAT_CHECK = r'''@echo off
chcp 936 >nul
rem 环境自检：确认 runtime、模型、素材齐全
cd /d "%~dp0"
set PY=%~dp0runtime\python.exe
if not exist "%PY%" (
  echo [错误] 没找到 runtime\python.exe
  pause
  exit /b 1
)
"%PY%" "%~dp0scripts\make_portable.py" --dest "%~dp0" --steps verify
pause
'''

# 仓库里自带的 start_app.bat / start_local_tts.bat 写死了作者本机的
# conda 路径和 LiveTalking-main 源码路径，包里点了不是报错就是跑了别处的代码。
# 这里用同名文件覆盖成自适应版本（只改包内副本，仓库里那份不动）。
BAT_TTS = r'''@echo off
chcp 936 >nul
rem 知课交互数字人 —— 本地 TTS 服务（XTTS-v2，端口 50000）
cd /d "%~dp0"

set PY=%~dp0runtime\python.exe
set MODEL=%~dp0data\models\xtts_v2

if not exist "%PY%" (
  echo [错误] 没找到 runtime\python.exe
  pause
  exit /b 1
)
if not exist "%MODEL%" (
  echo.
  echo   [错误] 没找到声音克隆模型：%MODEL%
  echo.
  echo   本包默认不含 XTTS-v2（约 2.0G）。
  echo   需要自定义音色时，把 XTTS-v2 模型目录整个拷到：
  echo     %~dp0data\models\xtts_v2
  echo   再运行本脚本。
  echo.
  pause
  exit /b 1
)

echo 正在启动 XTTS-v2（模型加载约 2 分钟，勿关此窗口）...
"%PY%" "%~dp0tts_servers\xtts_server.py" --model "%MODEL%" --port 50000 --fp16
pause
'''

README_TXT = """知课交互数字人 —— 便携版
=====================================

【怎么跑起来】
  双击 "启动桌面端.bat"
  第一次会加载模型，等 1~2 分钟（画面上有提示）。
  想看主服务日志时双击 "启动主服务.bat"。
  不确定包全不全，先跑 "环境自检.bat"。

【搬过去之前，目标机要先满足这些】
  1. Windows 10/11 x64。
  2. 装好 VC++ 运行库（Visual C++ Redistributable 2015-2022，x64）。
     没有它 python.exe 会直接起不来、也没报错。
     离线装不上就从别的机器拷 msvcp140.dll / vcruntime140.dll
     到 runtime\\ 目录下。
  3. NVIDIA 显卡驱动已装（1050Ti 即可）。torch 是 cu126 版，
     会自带 CUDA 运行时，不需要单独装 CUDA Toolkit。
  4. 建议 16G 内存起步。8G 机器上 MuseTalk 会把内存吃满，
     用小模型或降低并发。

【目录说明】
  runtime\\    Python 运行环境（含 torch 2.14+cu126），别动它
  models\\     MuseTalk 模型权重 + 语音识别模型（faster-whisper-small）
  data\\       形象素材、背景图、配置
  app.py      主服务入口
  desktop_app.py  桌面端入口

【注意】
  整个包是"拷贝即用"的，挪到别的目录也没关系 ——
  启动脚本会按自身位置现算路径。
  但如果只拷其中一部分，路径就对不上了。

【安全提醒】
  data\\settings\\settings.json 里存着明文 API Key。
  如果是自己在用，跟着搬过去正好省得重配；
  要是这个包要交给别人或会外传，制作时加 --scrub-secrets 抹掉密钥。
"""

MD_DEPLOY = """# 便携版部署说明

由 `scripts/make_portable.py` 生成，用于把整套数字人搬到另一台 Windows 机器。

## 目标机前置条件

| 项 | 要求 | 不做会怎样 |
|---|---|---|
| 系统 | Windows 10/11 x64 | — |
| VC++ 运行库 | 2015-2022 x64 | `python.exe` 直接起不来，且没有任何报错 |
| 显卡驱动 | NVIDIA（1050Ti 起） | torch 退回 CPU，推理慢到不可用 |
| 内存 | 建议 16G | 8G 机器上 MuseTalk 会吃满内存 |

不需要装 CUDA Toolkit：torch 是 cu126 版，自带 CUDA 运行时。

## 首次运行

1. 整个目录拷到目标机（**必须整个拷**，只拷一部分会路径对不上）。
2. 双击 `启动桌面端.bat`。首次加载模型约 1~2 分钟。
3. 跑 `环境自检.bat` 可确认文件是否齐全。

## 路径为什么不会失效

桌面端 `_iter_python_candidates()` 第一个就读项目根的 `.python-path.txt`。
启动脚本每次运行都按自身位置把 `runtime\\python.exe` 的**真实路径**写进这个文件，
所以整个包被放在 `D:\\`、`E:\\` 或任意深层目录都能正常工作。

## 可选：知识库（RAG）

知识库问答依赖独立的 `.venv-rag` 环境，本包**默认不含**（1.4G）。
主服务启动时若找不到它只是打一条 warning，数字人其余功能照常可用。

需要在目标机用知识库时，二选一：
- 把本机的 `.venv-rag` 整目录拷到包根目录；
- 或在目标机联网执行 `python -m venv .venv-rag && .venv-rag\\Scripts\\pip install -r requirements-rag.txt`。

## 可选：声音克隆

`data/models/xtts_v2`（2.0G）同样默认不含。需要自定义音色时把它整个目录拷到
`data/models/xtts_v2`，然后双击 `start_local_tts.bat`（本包已把它换成自适应版本，
原版写死了作者本机的 conda 路径和 LiveTalking 源码路径，直接用会跑去加载别处的代码）。

## .env 里的路径

`.env` 不入库但主服务启动就加载，所以也随包带走。

**已自动处理**：`STT_MODEL` 原本写成 `D:\\models\\faster-whisper-small` 这种项目外
绝对路径 —— 制作脚本会把这个目录整体搬进 `models/faster-whisper-small/`，
并把 `.env` 改写成包内相对路径。相对路径成立的前提是启动脚本先 `cd /d "%~dp0"`，
所以**别在别的目录里直接跑 `python app.py`**。

**仍需人工确认**：`.env.rag` 里的 `RAG_TOKENIZER_NAME` / `RAG_RERANKER_NAME`
（如 `D:/models/Qwen3-Embedding-4B`）指向 RAG 用的向量/重排模型，本包不含。
不用知识库问答就不用管；要用的话把它们一并拷到目标机并保持路径一致。
制作脚本会把这类残留的绝对路径逐条列出来。

## 包内容

- 源码：`git ls-files` 清单（已排除调试产物与日志）
- 模型：仅 MuseTalk 链路必需项 + 语音识别模型 `faster-whisper-small`
- 数据：形象素材 / 背景 / 设置 / 全息舱配置 / 音色 / 登录账号
- 环境：`livetalking` conda 环境整体搬运
- 配置：`.env` / `.env.rag`

## 安全提醒

`data/settings/settings.json` 里存着**明文 API Key**（LLM 服务商的）。
如果是自己搬到自己的机器，跟着走最省事；包要外传时，制作时加
`--scrub-secrets`，会把密钥字段清空、保留结构，到那边重新填一次即可。
"""


# .env 里这些项常被写成绝对路径，换机后指的地方就不对了
ENV_PATH_HINT = ("STT_MODEL", "TTS_MODEL", "MODEL_DIR", "VOICE_DIR", "RAG_DATA")


def _copy_env(src, dst, scrub=False):
    """复制 .env：不入库但对主服务是必需的（app.py 启动就 load_dotenv）。

    做两件事：
    1. `--scrub-secrets` 时抹掉密钥值（保留键名，到那边重填）。
    2. 把**已经搬进包内**的外部模型路径改写成包内相对路径 —— 这是唯一
       会主动改的地方，因为路径是我们自己建的，改了必然对。
       其余绝对路径只提示、不动手：改错会让功能直接瘫掉。

    返回 (剩余绝对路径项, 已改写的键, 抹掉的密钥数)。
    """
    text = Path(src).read_text(encoding="utf-8", errors="replace")

    wiped = 0
    if scrub:
        out = []
        for line in text.splitlines(True):
            if "=" in line and not line.lstrip().startswith("#"):
                key = line.split("=", 1)[0].strip().lower()
                if any(s in key for s in SECRET_KEYS):
                    line = "%s=\n" % line.split("=", 1)[0]
                    wiped += 1
            out.append(line)
        text = "".join(out)

    # 已搬进包内的外部模型 → 相对路径
    changed = []
    out = []
    for line in text.splitlines(True):
        if "=" in line and not line.lstrip().startswith("#"):
            key, val = line.split("=", 1)
            hit = _REWRITES.get(_norm_path(val.strip()))
            if hit and _norm_path(hit) != _norm_path(val.strip()):
                line = "%s=%s\n" % (key, hit)
                changed.append("%s=%s" % (key.strip(), hit))
        out.append(line)
    text = "".join(out)

    _ensure_dir(Path(dst).parent)
    with open(dst, "w", encoding="utf-8", errors="replace", newline="") as fh:
        fh.write(text)

    # 剩下指不上的绝对路径，光提醒
    abs_items = []
    for line in text.splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        if key.strip() in ENV_PATH_HINT and re.match(r"^[A-Za-z]:[\\/]", val.strip()):
            abs_items.append("%s=%s" % (key.strip(), val.strip()))
    return abs_items, changed, wiped


def step_bootstrap(dest, log, scrub=False):
    """生成启动脚本、.env 和说明文档。"""
    _ensure_dir(dest)
    _ensure_dir(dest / "scripts")
    _sync_rewrites(dest)

    # .env / .env.rag 都不在 git 里，但主服务一启动就要读
    abs_items, changed_items, wiped = [], [], 0
    for name in (".env", ".env.rag"):
        src = ROOT / name
        if not src.is_file():
            continue
        _abs, _changed, _wiped = _copy_env(src, dest / name, scrub)
        abs_items += _abs
        changed_items += _changed
        wiped += _wiped
        log("  已带入 %s" % name)
    if changed_items:
        log("  已把搬进包内的模型路径改成相对路径：")
        for item in changed_items:
            log("         %s" % item)
    if wiped:
        log("  .env 里 %d 处密钥已抹掉" % wiped)
    if abs_items:
        log("  [注意] 这些绝对路径换机后指不到（需要时在目标机上改）：")
        for item in abs_items:
            log("         %s" % item)

    def _write(name, text, encoding):
        p = dest / name
        with open(p, "w", encoding=encoding, newline="\r\n") as fh:
            fh.write(text)
        return _fsize(p)

    total = 0
    # bat 用 GBK，配合 chcp 936 才能在任何 Windows 上显示中文
    total += _write("启动桌面端.bat", BAT_UI, "gbk")
    total += _write("启动主服务.bat", BAT_SERVICE, "gbk")
    total += _write("环境自检.bat", BAT_CHECK, "gbk")
    # 仓库自带的那两个写死了作者本机路径，用同名自适应版本覆盖
    total += _write("start_app.bat", BAT_UI, "gbk")
    total += _write("start_local_tts.bat", BAT_TTS, "gbk")
    total += _write("快速上手.txt", README_TXT, "gbk")
    total += _write("部署说明.md", MD_DEPLOY, "utf-8")
    total += _write("scripts/make_portable.py",
                    (ROOT / "scripts/make_portable.py").read_text(encoding="utf-8"),
                    "utf-8")
    log("  已生成启动脚本与说明文档")
    return total


# ────────────────────────── 校验 ──────────────────────────

VERIFY_MUST = [
    "app.py", "desktop_app.py", "config.py",
    "runtime/python.exe",
    "models/musetalkV15/unet.pth",
    "models/musetalkV15/musetalk.json",
    "models/sd-vae/diffusion_pytorch_model.safetensors",
    "models/sd-vae/config.json",
    "models/whisper/model.safetensors",
    "models/whisper/config.json",
    "models/face-parse-bisent/79999_iter.pth",
    "models/face-parse-bisent/resnet18-5c106cde.pth",
    "data/avatars",
    "data/backgrounds",
    "启动桌面端.bat",
]

# 这些是"缺了也能跑、但会掉功能"的，只提示不阻断
VERIFY_OPTIONAL = {
    "data/models/xtts_v2": "声音克隆（自定义音色）不可用",
    ".venv-rag/Scripts/python.exe": "知识库问答（RAG）不可用，其余功能正常",
    "models/rvm/rvm_mobilenetv3.pth": "不能用 AI 抠像重新生成素材（已有素材照常用）",
    "models/wav2lip.pth": "无法切到 --model wav2lip 回退模式",
    "models/faster-whisper-small": "语音识别（STT）不可用，转写/语音输入会失败",
}


# 外部搬入的模型：目录存在就必须完整 —— 半截拷贝比没有更坑（报错还看不懂）
EXTERNAL_MODEL_FILES = {
    "models/faster-whisper-small": ("config.json", "model.bin",
                                    "tokenizer.json", "vocabulary.txt"),
}


def step_verify(dest, log):
    """校验包是否完整。返回缺失的必需项数量。"""
    dest = Path(dest)
    missing = []
    for rel in VERIFY_MUST:
        p = dest / rel
        if p.is_dir():
            continue
        if not p.exists():
            missing.append(rel)

    for rel, need in EXTERNAL_MODEL_FILES.items():
        d = dest / rel
        if d.is_dir():
            for name in need:
                if not (d / name).is_file():
                    missing.append("%s/%s" % (rel, name))

    log("")
    log("── 必需项 ──")
    if missing:
        for m in missing:
            log("  [缺] %s" % m)
    else:
        log("  全部齐备（%d 项）" % len(VERIFY_MUST))

    log("── 可选项 ──")
    for rel, why in VERIFY_OPTIONAL.items():
        p = dest / rel
        ok = p.is_dir() if rel.endswith(("v2", "data/models/xtts_v2")) else p.exists()
        if not ok:
            ok = p.exists() or p.is_dir()
        log("  [%s] %-38s %s" % ("有" if ok else "无", rel, "" if ok else why))

    # 解释器能不能直接跑起来 —— 这是换机后最容易翻车的地方
    py = dest / "runtime/python.exe"
    if py.is_file():
        try:
            r = subprocess.run([str(py), "-c", "import sys;print(sys.version.split()[0])"],
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                log("  解释器可用：Python %s" % r.stdout.strip())
            else:
                log("  [严重] runtime\\python.exe 起不来：%s" % (r.stderr or "").strip()[:200])
                log("         多半是目标机缺 VC++ 运行库")
        except Exception as exc:
            log("  [严重] 解释器自检异常：%s" % exc)

    total = sum(_fsize(os.path.join(dp, f))
                for dp, dn, fn in os.walk(dest)
                for f in fn)
    log("── 包体总体积：%s ──" % _hsize(total))
    return len(missing)


# ────────────────────────── 入口 ──────────────────────────

def _write_manifest(dest, log):
    """留一份文件清单，方便对照目标机有没有少拷。"""
    dest = Path(dest)
    rows = []
    for dp, dn, fn in os.walk(dest):
        for f in fn:
            p = os.path.join(dp, f)
            rows.append((os.path.relpath(p, dest).replace("\\", "/"), _fsize(p)))
    rows.sort()
    out = dest / "PORTABLE-MANIFEST.txt"
    with open(out, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("# 便携包文件清单（共 %d 个文件）\n" % len(rows))
        for rel, size in rows:
            fh.write("%12d  %s\n" % (size, rel))
    log("  清单已写入 PORTABLE-MANIFEST.txt（%d 个文件）" % len(rows))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="制作 KnowTalk 便携包（换一台 Windows 机器直接能跑）")
    ap.add_argument("--dest", required=True,
                    help="输出目录，如 D:\\knowtalk-portable")
    ap.add_argument("--steps", default="app,models,data,runtime,bootstrap,verify",
                    help="要执行的步骤，逗号分隔：%s" % ",".join(STEPS))
    ap.add_argument("--no-manifest", action="store_true",
                    help="不生成文件清单（包很大时省点时间）")
    ap.add_argument("--scrub-secrets", action="store_true",
                    help="抹掉 data/settings 里的 API Key / 口令"
                         "（包要交给别人时用；自己的机器留着省事）")
    args = ap.parse_args(argv)

    dest = Path(args.dest)
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    bad = [s for s in steps if s not in STEPS]
    if bad:
        raise SystemExit("未知步骤：%s（可用：%s）" % (",".join(bad), ",".join(STEPS)))

    def log(msg):
        print(msg, flush=True)

    print("=" * 62)
    print("KnowTalk 便携包")
    print("  源：%s" % ROOT)
    print("  目标：%s" % dest)
    print("=" * 62)

    _ensure_dir(dest)
    free = _free_bytes(dest)
    if free > 0:
        print("  目标盘剩余空间：%s" % _hsize(free))

    t0 = time.time()
    for step in steps:
        print("\n[%s]" % step)
        if step == "app":
            step_app(dest, log)
        elif step == "models":
            step_models(dest, log)
        elif step == "data":
            step_data(dest, log, scrub=args.scrub_secrets)
        elif step == "runtime":
            step_runtime(dest, log)
        elif step == "bootstrap":
            step_bootstrap(dest, log, scrub=args.scrub_secrets)
        elif step == "verify":
            n = step_verify(dest, log)
            if n:
                print("\n有 %d 项必需内容缺失，这个包还不完整。" % n)

    if not args.no_manifest and "verify" not in steps:
        print("\n[manifest]")
        _write_manifest(dest, log)

    print("\n总耗时 %.0f 秒" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
