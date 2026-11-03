###############################################################################
#  Copyright (C) 2024 KnowTalking@lipku https://github.com/lipku/KnowTalking
#  Copyright (C) 2026 KnowTalk Contributors
#  email: lipku@foxmail.com
# 
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  
#       http://www.apache.org/licenses/LICENSE-2.0
# 
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

import os
# 修复：在 IDE(PyCharm/VSCode) 环境中，numba 默认缓存路径检查会卡死，
# 显式指定一个干净可写的缓存目录即可解决（需在导入 librosa 前设置）。
os.environ.setdefault("NUMBA_CACHE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".numba_cache"))
# 显存分配器配置：只保留垃圾回收阈值，**不要**启用 expandable_segments。
#
# WDDM（Windows 显示驱动模型）下 expandable_segments 走虚拟内存映射，
# 而每进程显存预算约 4GB，映射申请会被驱动拒绝并伪装成 CUDA OOM：
#   expandable_segments: memory mapping failed with OOM on device 0
#   while trying to map 20971520 bytes (free: 5473566720, total: 8585216000)
#   torch.OutOfMemoryError: Tried to allocate 30.00 MiB. 5.10 GiB is free.
# 实测「显存空闲 5.1GB、只申请 30MB」照样失败，加载直接崩在 UNet 搬卡阶段；
# 去掉后同一台机器可正常加载到约 3.5GB。
# Linux 上如需碎片优化，可用环境变量显式覆盖该值。
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "garbage_collection_threshold:0.6")

# 加载 .env 配置（环境变量优先），必须在读取任何 os.getenv 之前执行
from utils.dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ── 启动前自检：先确认解释器选对了，再往下导入一堆重依赖 ────────────────
# 用错解释器（PATH 里的 python 不是项目环境）时，原来只会抛一条
# ModuleNotFoundError: No module named 'xxx'，看不出「该用哪个 python」，
# 于是要么逐个补安装，要么把 requirements 装进错误的 Python，越修越乱。
import importlib.util
import subprocess
import sys

_RUNTIME_DEPS = ("numpy", "aiohttp", "aiortc", "torch")


def _missing_runtime_deps():
    """探测运行期依赖是否存在。find_spec 只查得到找不到，不执行模块（torch 导入要好几秒）。"""
    missing = []
    for name in _RUNTIME_DEPS:
        try:
            found = importlib.util.find_spec(name) is not None
        except Exception:      # 装坏了、父包缺 __path__ 之类，一律当缺
            found = False
        if not found:
            missing.append(name)
    return missing


def _env_candidates():
    """列出常见 conda 布局下的解释器路径，供失败提示直接给出。"""
    home = os.path.expanduser("~")
    roots = (
        os.path.join(home, ".conda", "envs"),
        os.path.join(home, "anaconda3", "envs"),
        os.path.join(home, "miniconda3", "envs"),
    )
    for root in roots:
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            exe = os.path.join(root, name, "python.exe" if os.name == "nt" else "bin/python")
            if os.path.isfile(exe):
                yield exe


def _usable_interpreter(exe):
    """真正问一遍那个解释器：依赖齐不齐。只在这条失败路径上花这一两秒。"""
    probe = ("import importlib.util as u, sys;"
             "sys.exit(0 if all(u.find_spec(m) for m in %r) else 1)" % (_RUNTIME_DEPS,))
    try:
        done = subprocess.run([exe, "-c", probe], timeout=20,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    return done.returncode == 0


def _check_runtime_deps():
    missing = _missing_runtime_deps()
    if not missing:
        return                      # 依赖齐全：静默放行，行为和加自检之前一样

    hint = next((exe for exe in _env_candidates() if exe != sys.executable
                 and _usable_interpreter(exe)), None)

    lines = [
        "",
        "=" * 78,
        "启动失败：当前解释器缺少运行期依赖",
        "=" * 78,
        "  当前解释器：%s" % sys.executable,
        "  缺少的模块：%s" % ", ".join(missing),
        "",
        "这几乎总是「用错了 Python」——PATH 里的 python 不是项目环境。",
    ]
    if hint:
        lines += [
            "",
            "  本机已有一个依赖齐全的解释器：",
            "    %s" % hint,
            "",
            "  用它启动（沿用你原来的参数）：",
            '    "%s" app.py' % hint,
        ]
    else:
        lines += ["", "  请先激活项目环境（conda activate <环境名>），或直接运行 start_app.bat。"]
    lines += [
        "",
        "  也可以直接双击 start_app.bat —— 它会自己挑解释器并启动桌面版。",
        "  不要在当前解释器里 pip install -r requirements.txt：会装到错误的 Python 上。",
        "=" * 78,
        "",
    ]
    sys.stderr.write("\n".join(lines))
    sys.exit(1)


_check_runtime_deps()

# 运行期是 aiohttp（见 setup_routes / web.run_app）。
# 历史上这里是 Flask + flask_sockets，迁到 aiohttp 后那两行 import 与 app = Flask(...)
# 一直没人删：它们从未被读取，却让没装 flask 的解释器在第一屏就报 ModuleNotFoundError，
# 把「解释器选错了」伪装成「缺 flask」。2026-10-26 清掉。
import base64
import json
#import gevent
#from gevent import pywsgi
#from geventwebsocket.handler import WebSocketHandler
import re
import os
import numpy as np
from threading import Thread,Event

def _register_numpy_core_alias():
    """avatar 素材的 pickle 是 numpy 2.x 写的，本环境是 1.22（见 utils/numpy_compat.py）。

    实现搬到 utils 里，是为了让**命令行脚本**（动作资产补齐、素材体检）也能用同
    一套逻辑：以前只有 app.py 注册过别名，脚本里直接 pickle.load 会抛
    `No module named 'numpy._core.multiarray'`，看着像素材损坏。
    """
    from utils.numpy_compat import ensure_numpy_core_alias
    if ensure_numpy_core_alias():
        print('[兼容] 已注册 numpy._core -> numpy.core alias (numpy %s)' % np.__version__, flush=True)
    else:
        print('[兼容] numpy._core alias 注册失败，素材可能读不出来', flush=True)
#import multiprocessing
import torch.multiprocessing as mp

from aiohttp import web
import aiohttp
from aiortc import RTCPeerConnection, RTCSessionDescription,RTCIceServer,RTCConfiguration
from aiortc.rtcrtpsender import RTCRtpSender
from server.webrtc import HumanPlayer
from avatars.base_avatar import BaseAvatar
from llm import llm_response
import registry
from server.routes import setup_routes
from server.rtc_manager import RTCManager
from server.session_manager import session_manager
from server.settings_routes import setup_settings_routes
from utils.settings_store import get_store

import argparse
import shutil
import asyncio
import sys
import time
import torch
from io import BytesIO
from typing import Dict
from utils.logger import logger
import copy
import gc
import subprocess
import urllib.error
import urllib.request
from urllib.parse import urlparse


opt = None
model = None
global_avatars = {} # avatar_id: payload
rag_process = None
rag_log_file = None

# 用户显式在命令行给出的、可被运行期设置覆盖的参数。
# 这些必须优先于设置文件与环境变量，否则显式传的 --tts sapi 会被默认值悄悄改回去。
_CLI_OVERRIDABLE_OPTIONS = (
    '--tts', '--TTS_FALLBACK', '--TTS_RATE', '--REF_FILE', '--REF_TEXT', '--avatar_id',
    '--mouth_alpha', '--mouth_offset_x', '--mouth_offset_y',
)
_CLI_LOCKED = set()


def _cli_locked_options(argv=None):
    """从命令行中找出用户显式给出的可覆盖选项名（如 '--tts'）。

    只做字面匹配：`--tts sapi` 与 `--tts=sapi` 两种写法都能识别。
    """
    tokens = list(sys.argv[1:] if argv is None else argv)
    locked = set()
    for token in tokens:
        for name in _CLI_OVERRIDABLE_OPTIONS:
            if token == name or token.startswith(name + '='):
                locked.add(name)
    return locked


#####webrtc###############################
# rtc_manager replaces the old pcs set and duplicate offer handlers.
rtc_manager = None

def build_avatar_session(sessionid:str, params:dict)->BaseAvatar:
    # 把「对新建会话生效」的运行期设置刷进 opt，再派生本会话的副本。
    # 已存在的会话各自持有副本，因此不会被打断。
    get_store().apply_to_opt(opt, cli_locked=_CLI_LOCKED)
    opt_this = copy.deepcopy(opt)
    opt_this.sessionid = sessionid

    avatar_id = resolve_avatar_id(params.get('avatar', opt.avatar_id), opt.model)
    opt_this.avatar_id = avatar_id
    ref_audio = params.get('refaudio','') #音色
    ref_text = params.get('reftext','')
    if (avatar_id and avatar_id != opt.avatar_id):
        # Avoid reloading if already cached globally
        if avatar_id not in global_avatars:
            global_avatars[avatar_id] = load_avatar(avatar_id)
        avatar_this = global_avatars[avatar_id]
    else:
        # Default avatar loaded at startup
        avatar_this = global_avatars.get(opt.avatar_id)
    if ref_audio: #请求参数配置了参考音频
        opt_this.REF_FILE = ref_audio
        opt_this.REF_TEXT = ref_text
    custom_config=params.get('custom_config','') #动作编排配置
    if custom_config:
        opt_this.customopt = json.loads(custom_config)

    avatar_session = registry.create("avatar", opt.model, opt=opt_this, model=model, avatar=avatar_this)
    return avatar_session


def resolve_avatar_id(avatar_id: str, model_name: str) -> str:
    """校验默认数字人，找不到时回退到第一个完整形象。"""
    if model_name != 'wav2lip':
        return avatar_id

    def is_valid(candidate: str) -> bool:
        avatar_dir = os.path.join('.', 'data', 'avatars', candidate)
        return (
            os.path.isfile(os.path.join(avatar_dir, 'coords.pkl'))
            and os.path.isdir(os.path.join(avatar_dir, 'full_imgs'))
        )

    if is_valid(avatar_id):
        return avatar_id

    avatar_root = os.path.join('.', 'data', 'avatars')
    candidates = []
    if os.path.isdir(avatar_root):
        candidates = sorted(
            name for name in os.listdir(avatar_root)
            if not name.startswith('.') and is_valid(name)
        )
    if not candidates:
        raise FileNotFoundError(
            f"未找到可用的 Wav2Lip 数字人: {avatar_id}。"
            "每个形象目录必须包含 coords.pkl 和 full_imgs。"
        )

    fallback = candidates[0]
    logger.warning("默认数字人 %s 不完整，自动使用可用数字人 %s", avatar_id, fallback)
    return fallback

async def offer(request):
    return await rtc_manager.handle_offer(request)

def _rag_health_url():
    base_url = os.getenv("RAG_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
    return base_url, f"{base_url}/health"


def _rag_is_available():
    _, health_url = _rag_health_url()
    try:
        with urllib.request.urlopen(health_url, timeout=0.8) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


async def cleanup_idle_sessions():
    """
    后台任务：定期清理空闲会话（无 WebRTC 且超过 IDLE_TIMEOUT 未活动）。
    默认 30 分钟，可通过环境变量 SESSION_IDLE_TIMEOUT 覆盖（秒）。
    """
    timeout = int(os.getenv("SESSION_IDLE_TIMEOUT", "1800"))
    logger.info("[会话清理] 空闲超时=%ss 的会话将被自动回收", timeout)
    while True:
        await asyncio.sleep(60)
        try:
            from server.session_manager import session_manager
            removed = session_manager.cleanup_idle(timeout)
            if removed:
                logger.info("[会话清理] 回收 %d 个空闲会话", removed)
        except Exception:
            logger.exception("[会话清理] 扫描异常")


def start_rag_service():
    """启动本地 RAG sidecar；已有实例或远程地址不重复启动。"""
    global rag_process, rag_log_file
    # 界面写入的 RAG 参数优先于 .env.rag：先导入进程环境，子进程会继承
    get_store().export_rag_env()
    if os.getenv("RAG_ENABLED", "true").lower() in {"0", "false", "no", "off"}:
        logger.info("RAG 自动启动已禁用")
        return

    base_url, _ = _rag_health_url()
    parsed = urlparse(base_url)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        logger.info("RAG_BASE_URL=%s，使用远程 RAG，不自动启动本地 sidecar", base_url)
        return
    if _rag_is_available():
        logger.info("RAG 服务已在运行: %s", base_url)
        return

    root = os.path.dirname(os.path.abspath(__file__))
    rag_python = os.path.join(root, ".venv-rag", "Scripts", "python.exe")
    if not os.path.isfile(rag_python):
        logger.warning("未找到 RAG Python 环境: %s", rag_python)
        return

    log_path = os.path.join(root, "rag_service", "runtime.autostart.log")
    rag_log_file = open(log_path, "a", encoding="utf-8")
    rag_log_file.write("\n--- RAG autostart ---\n")
    rag_log_file.flush()
    rag_process = subprocess.Popen(
        [rag_python, "-m", "rag_service.run"],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=rag_log_file,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    logger.info("已自动启动 RAG 服务: pid=%s, log=%s", rag_process.pid, log_path)

    for _ in range(30):
        if rag_process.poll() is not None:
            logger.warning("RAG 服务启动后立即退出，exit_code=%s", rag_process.returncode)
            return
        if _rag_is_available():
            logger.info("RAG 服务已就绪: %s", base_url)
            return
        time.sleep(0.5)
    logger.warning("RAG 服务启动超时，主服务继续运行；请查看 %s", log_path)


def stop_rag_service():
    """只停止由当前 app.py 启动的 RAG 进程。"""
    global rag_process, rag_log_file
    if rag_process is not None and rag_process.poll() is None:
        logger.info("停止由主服务启动的 RAG 进程: pid=%s", rag_process.pid)
        rag_process.terminate()
        try:
            rag_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            rag_process.kill()
    if rag_log_file is not None:
        rag_log_file.close()
        rag_log_file = None
    rag_process = None


def _rag_enabled():
    return os.getenv("RAG_ENABLED", "true").lower() not in {"0", "false", "no", "off"}


def rag_status_snapshot():
    """RAG 运行状态：可达性、参数来源，以及是否可由主服务代为重启。"""
    base_url, _ = _rag_health_url()
    parsed = urlparse(base_url)
    local = parsed.hostname in {"127.0.0.1", "localhost"}
    enabled = _rag_enabled()
    available = _rag_is_available()
    managed = rag_process is not None and rag_process.poll() is None

    hint = ""
    if not enabled:
        hint = "RAG 已被禁用（RAG_ENABLED=false），主服务不会启动或重启它"
    elif not local:
        hint = "RAG 地址不是本机，主服务不会代为重启"
    elif available and not managed:
        hint = "本机已有 RAG 实例在运行，但它不是由主服务启动的，请自行重启该实例"
    elif not os.path.isfile(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".venv-rag", "Scripts", "python.exe")):
        hint = "未找到 .venv-rag 环境，无法启动 RAG"

    return {
        "enabled": enabled,
        "base_url": base_url,
        "local": local,
        "available": available,
        "managed_by_main_service": managed,
        "restartable": hint == "",
        "hint": hint,
        "settings_source": get_store().rag_source(),
        "pid": rag_process.pid if managed else None,
    }


# 同一时刻只允许一次重启。用普通布尔标志而不是 asyncio.Lock：
# 检查和置位都在第一个 await 之前同步完成，aiohttp 单线程下不可能被插入，
# 因此判定是确定的（用锁时，若临界区里没有真正的 await，第二个请求根本来不及到达）。
_rag_restart_busy = False


def _restart_rag_blocking():
    """同步执行停止+重新拉起；由调用方放到线程池里跑，避免阻塞事件循环。"""
    snapshot = rag_status_snapshot()
    if not snapshot["restartable"]:
        return False, {"error": snapshot["hint"] or "当前状态不可重启"}

    get_store().export_rag_env()
    stop_rag_service()
    # 等端口真正释放，否则 start_rag_service 会误判为“已在运行”
    deadline = time.time() + 10
    while time.time() < deadline and _rag_is_available():
        time.sleep(0.5)

    start_rag_service()

    for _ in range(40):
        if _rag_is_available():
            managed = rag_process is not None and rag_process.poll() is None
            return True, {
                "status": "ready",
                "base_url": snapshot["base_url"],
                "pid": rag_process.pid if managed else None,
            }
        if rag_process is not None and rag_process.poll() is not None:
            break
        time.sleep(0.5)

    exit_code = rag_process.returncode if rag_process is not None else None
    return False, {
        "status": "failed",
        "exit_code": exit_code,
        "error": "RAG 重启后未就绪，请查看 rag_service/runtime.autostart.log",
    }


async def restart_rag_service():
    """由主服务停止并重新拉起自己启动的 RAG sidecar。

    阻塞部分放进线程池，否则重启期间整个服务会失去响应。
    """
    global _rag_restart_busy
    if _rag_restart_busy:
        return {"ok": False, "in_progress": True, "error": "已有一次 RAG 重启正在进行，请稍后重试"}
    _rag_restart_busy = True
    try:
        loop = asyncio.get_event_loop()
        ok, detail = await loop.run_in_executor(None, _restart_rag_blocking)
        result = {"ok": ok}
        result.update(detail)
        return result
    finally:
        _rag_restart_busy = False


async def on_shutdown(app): 
    await rtc_manager.shutdown()
    stop_rag_service()
async def download_record(request):
    sessionid = request.match_info.get('sessionid')
    if not sessionid:
        return web.Response(status=400, text="sessionid is required")
    
    record_file = os.path.join('data', 'record', f"{sessionid}.mp4")
    
    if os.path.exists(record_file):
        return web.FileResponse(record_file)
    else:
        return web.Response(status=404, text="Record not found")


def main():
    global rtc_manager, opt, model,load_avatar
    print("=" * 50, flush=True)
    print("KnowTalk 服务启动中...", flush=True)
    print("=" * 50, flush=True)
    
    # 解析命令行参数
    from config import parse_args
    opt = parse_args()

    # 形象库的「可用」判定必须与当前模型匹配：前端只把 ready !== false 的形象
    # 放进下拉框，否则在 musetalk 下会选中只带 wav2lip 素材的形象，一连就崩。
    from server import avatar_routes as _avatar_routes
    _avatar_routes.ACTIVE_MODEL = opt.model

    # MuseTalk 的 VAE 解码是硬成本（实测约 42ms/帧，推理上限约 19fps）。
    # 目标 25fps 时必然周期性缺帧——表现就是嘴型一卡一卡、音频被插入静音。
    # 把它压到 20fps，缺帧率从约 23% 降到个位数，输出节奏稳定。
    # 只影响 musetalk，wav2lip 仍是 25fps；命令行显式给了 --fps 则以命令行为准。
    if opt.model == 'musetalk' and '--fps' not in sys.argv:
        if opt.fps != 20:
            logger.info("[配置] MuseTalk 输出帧率 25 → 20（VAE 解码约 42ms/帧，25fps 必然缺帧）")
        opt.fps = 20

    # 运行期设置：把「对新建会话生效」的设置刷进 opt（不再有访问口令环节）
    store = get_store()

    # 用户在命令行上显式给出的参数优先级最高，必须记录下来避免被设置覆盖
    global _CLI_LOCKED
    _CLI_LOCKED = _cli_locked_options()
    if _CLI_LOCKED:
        logger.info("命令行显式指定的参数（不被运行期设置覆盖）: %s",
                    ", ".join(sorted(_CLI_LOCKED)))
    store.apply_to_opt(opt, cli_locked=_CLI_LOCKED)

    opt.avatar_id = resolve_avatar_id(opt.avatar_id, opt.model)
    start_rag_service()
    print(f"[配置] 模型: {opt.model}", flush=True)
    print(f"[配置] 形象ID: {opt.avatar_id}", flush=True)
    print(f"[配置] 端口: {opt.listenport}", flush=True)
    print(f"[配置] TTS: {opt.tts}", flush=True)

    _register_numpy_core_alias()

    # ─── 加载 avatar 插件（触发 @register 注册）──────────────────────
    _avatar_modules = {
        'musetalk':   'avatars.musetalk_avatar',
        'wav2lip':    'avatars.wav2lip_avatar',
        'ultralight': 'avatars.ultralight_avatar',
    }
    import importlib
    print(f"[导入] 正在导入模块: {_avatar_modules[opt.model]}", flush=True)
    avatar_mod = importlib.import_module(_avatar_modules[opt.model])
    print("[导入] 模块导入完成", flush=True)
    load_model = avatar_mod.load_model
    load_avatar = avatar_mod.load_avatar
    warm_up = avatar_mod.warm_up
    logger.info(opt)

    print("[加载] 正在加载模型...", flush=True)
    if opt.model == 'musetalk':
        print("[加载] 加载 MuseTalk 模型...", flush=True)
        model = load_model()
        print("[加载] 加载形象数据...", flush=True)
        global_avatars[opt.avatar_id] = load_avatar(opt.avatar_id) 
        print("[加载] 预热模型...", flush=True)
        warm_up(min(opt.batch_size, 2), model)      
    elif opt.model == 'wav2lip':
        print("[加载] 加载 Wav2Lip 模型...", flush=True)
        model = load_model("./models/wav2lip.pth")
        print("[加载] 加载形象数据...", flush=True)
        global_avatars[opt.avatar_id] = load_avatar(opt.avatar_id)
        print("[加载] 预热模型...", flush=True)
        warm_up(min(opt.batch_size, 2), model, 256)
    elif opt.model == 'ultralight':
        print("[加载] 加载 Ultralight 模型...", flush=True)
        model = load_model(opt)
        print("[加载] 加载形象数据...", flush=True)
        global_avatars[opt.avatar_id] = load_avatar(opt.avatar_id)
        print("[加载] 预热模型...", flush=True)
        warm_up(min(opt.batch_size, 2), global_avatars[opt.avatar_id], 160)
    print("[加载] 模型加载完成！", flush=True)
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    import gc as _gc
    _gc.collect()

    # init rtc manager
    session_manager.init_builder(build_avatar_session)
    rtc_manager = RTCManager(opt)
    print("[初始化] RTC管理器初始化完成")
    # share avatar_sessions (RTCManager handles it but routes.py expects it)
    
    if opt.transport=='virtualcam' or opt.transport=='rtmp':
        thread_quit = Event()
        params = {}
        # session 0 for virtualcam
        session_manager.add_session('0', build_avatar_session('0', params))
        rendthrd = Thread(target=session_manager.get_session('0').render,args=(thread_quit,))
        rendthrd.start()

    #############################################################################
    # PPT/视频等素材可能超过 100MB；通过环境变量保留可调上限。
    upload_limit_mb = int(os.getenv("UPLOAD_MAX_MB", "512"))
    appasync = web.Application(client_max_size=1024**2 * upload_limit_mb)
    appasync["llm_response"] = llm_response
    appasync["opt"] = opt
    appasync["rtc_manager"] = rtc_manager
    appasync["settings_store"] = store
    appasync["rag_status_provider"] = rag_status_snapshot
    appasync["rag_restarter"] = restart_rag_service

    appasync.on_shutdown.append(on_shutdown)
    appasync.router.add_post("/offer", offer)
    appasync.router.add_get("/record/{sessionid}", download_record)

    # 后台任务：空闲会话自动清理（防内存/显存泄漏）
    # 注意：必须使用 async def 回调；aiohttp 3.13 中同步 lambda 内 create_task 会阻塞 setup。
    async def _on_startup_cleanup(app_):
        app_["idle_cleanup_task"] = asyncio.get_event_loop().create_task(
            cleanup_idle_sessions()
        )
    appasync.on_startup.append(_on_startup_cleanup)

    async def _on_shutdown_cleanup(app_):
        """取消空闲清理协程，避免正常退出时留下 pending-task 错误日志。"""
        task = app_.get("idle_cleanup_task")
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    appasync.on_shutdown.append(_on_shutdown_cleanup)

    # 设置与控制路由必须注册在 setup_routes 里的 add_static('/') 之前
    setup_settings_routes(appasync)

    # 本地账号登录：登录接口 + 保护中间件。
    # 同样必须在 add_static('/') 之前——否则静态挂载会吃掉 /api/auth/* 之外的根路径。
    from server.auth_mw import install_auth
    install_auth(appasync)

    # 提问模式路由（同样必须在 add_static 之前注册）
    from server.quiz_routes import setup_quiz_routes, tick_quiz_sessions
    setup_quiz_routes(appasync)

    # 语音转写与语音作答路由（课堂面，免口令）
    from server.stt_routes import setup_stt_routes
    setup_stt_routes(appasync)

    # 动作制作：MiniMax H3 出片 → 拆成手势素材。作业在后台线程里跑，接口只提交与查询。
    from server.gesture_make_routes import setup_gesture_make_routes
    setup_gesture_make_routes(appasync)

    # 提问模式的编排需要周期性推进；放在后台任务里，避免在请求处理中等待朗读结束。
    async def _on_startup_quiz(app_):
        app_["quiz_tick_task"] = asyncio.get_event_loop().create_task(
            tick_quiz_sessions()
        )
    appasync.on_startup.append(_on_startup_quiz)

    async def _on_shutdown_quiz(app_):
        task = app_.get("quiz_tick_task")
        if task is not None and not task.done():
            task.cancel()
    appasync.on_shutdown.append(_on_shutdown_quiz)

    # 网页版已移除（项目只保留桌面版）：不再注册根页面路由与 web/ 静态目录。
    # 桌面版（desktop_app.py）通过 /api/*、/offer 等接口与本服务通信。

    # 注册 server/routes.py 中的通用 API 路由
    setup_routes(appasync)

    # 注册素材管理路由
    from server.material_routes import setup_material_routes
    setup_material_routes(appasync)

    # 全息舱（展项）路由：唤醒词 / 菜单库 / 闲时待机 / 人体感应 / 问答记录
    from server.kiosk_routes import setup_kiosk_routes
    setup_kiosk_routes(appasync)


    # 跨域来源收敛为显式允许列表（CORS_ALLOWED_ORIGINS）；未配置时仅允许同源访问。
    # 必须放在所有路由注册之后——后注册的路由不会被覆盖。
    from server.cors_policy import install_cors
    install_cors(appasync)

    # logger.info('如果使用webrtc，推荐访问webrtc集成前端: http://<serverip>:'+str(opt.listenport)+'/dashboard.html')
    def run_server(runner):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, '0.0.0.0', opt.listenport)
        loop.run_until_complete(site.start())
        print("=" * 50, flush=True)
        print("🚀 服务启动成功（桌面版专用后端）！", flush=True)
        print(f"🌐 桌面版客户端将连接: http://127.0.0.1:{opt.listenport}", flush=True)
        print("=" * 50, flush=True)
        logger.info('start http server; 桌面版后端监听 ' + str(opt.listenport))
        if opt.transport=='rtcpush':
            for k in range(opt.max_session):
                push_url = opt.push_url
                if k!=0:
                    push_url = opt.push_url+str(k)
                loop.run_until_complete(rtc_manager.handle_rtcpush(push_url, str(k)))
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            logger.info('收到 Ctrl+C，主服务退出')
        finally:
            # 以前 loop.run_forever() 外面什么都没有：`app.on_shutdown` 永远等不到
            # 触发，RAG sidecar 会一直挂在那里占端口和内存。cleanup() 才会发
            # on_shutdown 信号（关 PeerConnection + 停 RAG），之后仍需兜底一次。
            try:
                loop.run_until_complete(runner.cleanup())
            except Exception:  # noqa: BLE001 - 退出路径上不该再抛
                logger.exception('runner.cleanup 失败')
            try:
                stop_rag_service()
            except Exception:  # noqa: BLE001
                logger.exception('停止 RAG 进程失败')
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass
    #Thread(target=run_server, args=(web.AppRunner(appasync),)).start()
    run_server(web.AppRunner(appasync))

    #app.on_shutdown.append(on_shutdown)
    #app.router.add_post("/offer", offer)

    # print('start websocket server')
    # server = pywsgi.WSGIServer(('0.0.0.0', 8000), app, handler_class=WebSocketHandler)
    # server.serve_forever()


# os.environ['MKL_SERVICE_FORCE_INTEL'] = '1'
# os.environ['MULTIPROCESSING_METHOD'] = 'forkserver'                                                    
if __name__ == '__main__':
    try:
        mp.set_start_method('spawn')
    except RuntimeError:
        pass
    main()
    
    
    
