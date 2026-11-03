# -*- coding: utf-8 -*-
"""系统状态采集：给「系统状态」页提供一份可直接渲染的数据。

为什么单独放一个模块：状态页要的东西横跨运行时（模型/音色/会话）、
操作系统（CPU/内存）、以及显卡（nvidia-smi），全塞进 routes.py 会让那个
文件继续膨胀，而这里的东西也不该被别的业务接口依赖。

采集全部失败也不能拖垮 /api/status——每块都单独兜底，缺哪块返回空。
"""
import os
import subprocess
import time

from utils.logger import logger

_START = time.time()

_GPU_CACHE = {"at": 0.0, "data": None}
_GPU_TTL = 10.0           # nvidia-smi 起进程约 0.1~0.3s，不必每次状态刷新都重启它


def _run(cmd, timeout=3.0):
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if out.returncode != 0:
            return None
        return out.stdout.decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001
        return None


def gpu_info():
    """显卡信息；没 NVIDIA 卡或 nvidia-smi 不可用时返回 None（页面会隐藏这一块）。"""
    now = time.time()
    if _GPU_CACHE["data"] is not None and now - _GPU_CACHE["at"] < _GPU_TTL:
        return _GPU_CACHE["data"]
    text = _run(["nvidia-smi",
                 "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu",
                 "--format=csv,noheader,nounits"])
    data = None
    if text:
        parts = [p.strip() for p in text.split("\n")[0].split(",")]
        if len(parts) >= 5:
            try:
                total, used, free, util = (float(parts[1]), float(parts[2]),
                                           float(parts[3]), float(parts[4]))
                data = {"name": parts[0], "total_mb": total, "used_mb": used,
                        "free_mb": free, "util_percent": util,
                        "used_percent": round(used / total * 100.0, 1) if total else 0.0}
            except ValueError:
                data = None
    _GPU_CACHE["at"], _GPU_CACHE["data"] = now, data
    return data


def system_info():
    """CPU 与内存。psutil 缺失时返回空 dict，页面会降级显示。"""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return {}
    try:
        vm = psutil.virtual_memory()
        return {
            # interval>0 会阻塞 aiohttp 事件循环；状态页允许读上一采样值，
            # 因此用非阻塞模式避免每 5 秒卡住所有接口约 250ms。
            "cpu_percent": psutil.cpu_percent(interval=None),
            "ram_total_mb": round(vm.total / 1048576.0, 1),
            "ram_used_mb": round(vm.used / 1048576.0, 1),
            "ram_free_mb": round(vm.available / 1048576.0, 1),
            "ram_percent": vm.percent,
        }
    except Exception:  # noqa: BLE001
        return {}


def _rag_available():
    import urllib.request
    import urllib.error
    base_url = os.getenv("RAG_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=0.8) as resp:
            return resp.status == 200
    except (OSError, urllib.error.URLError):
        return False


def engine_info(session_manager=None):
    """当前引擎/形象/音色：优先从活跃会话取（那是真实生效的配置），
    没有会话时退回启动参数。"""
    info = {"model": "", "avatar_id": "", "voice": "", "fps": 0, "sessions": 0}
    try:
        from server import avatar_routes
        info["model"] = getattr(avatar_routes, "ACTIVE_MODEL", "") or ""
    except Exception:  # noqa: BLE001
        pass

    sessions = []
    try:
        sessions = list((getattr(session_manager, "sessions", {}) or {}).values())
    except Exception:  # noqa: BLE001
        sessions = []
    info["sessions"] = len(sessions)

    for sess in sessions:
        opt = getattr(sess, "opt", None)
        if opt is None:
            continue
        info["model"] = getattr(opt, "model", "") or info["model"]
        info["avatar_id"] = getattr(opt, "avatar_id", "") or info["avatar_id"]
        info["voice"] = getattr(opt, "REF_FILE", "") or info["voice"]
        info["fps"] = getattr(opt, "fps", 0) or info["fps"]
        break

    if not info["model"] or not info["voice"]:
        try:
            import app as _app
            opt = getattr(_app, "opt", None)
            if opt is not None:
                info["model"] = info["model"] or getattr(opt, "model", "")
                info["avatar_id"] = info["avatar_id"] or getattr(opt, "avatar_id", "")
                info["voice"] = info["voice"] or getattr(opt, "REF_FILE", "")
                info["fps"] = info["fps"] or getattr(opt, "fps", 0)
        except Exception:  # noqa: BLE001
            pass
    return info


def collect(session_manager=None):
    """汇总一份状态快照。"""
    data = {
        "uptime_s": round(time.time() - _START, 1),
        "system": system_info(),
        "gpu": gpu_info(),
        "rag_available": _rag_available(),
        "llm_configured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
    }
    try:
        data.update(engine_info(session_manager))
    except Exception:  # noqa: BLE001
        logger.exception("[状态] 引擎信息采集失败")
        data.setdefault("sessions", 0)
    return data
