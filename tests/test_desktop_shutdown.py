# -*- coding: utf-8 -*-
"""退出时的资源释放：摄像头进程、克隆服务、主服务进程。

对应 openspec/changes/fix-matte-compositing-and-resource-release/。

为什么单独钉：摄像头是独占资源，漏一次就得重启整台机器才恢复，而且现场表现是
「打不开摄像头（被其它程序占用）」——这个提示完全指不到真正的原因，最费时间。
"""
import os
import subprocess

import pytest


@pytest.fixture()
def app():
    """绕过 __init__ 造一个桌面端实例（不建窗口，只测资源方法）。"""
    desktop_app = pytest.importorskip("desktop_app")
    return object.__new__(desktop_app.DesktopApp)


class FakeProc:
    """够用的进程替身：poll/terminate/kill/wait。"""

    def __init__(self, alive=True, hang_once=False):
        self.alive = alive
        self.terminated = False
        self.killed = False
        self.hang_once = hang_once
        self.waits = []

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        if not self.hang_once:
            self.alive = False

    def kill(self):
        self.killed = True
        self.alive = False

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.hang_once:
            self.hang_once = False
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0


def test_kill_sensor_terminates_and_removes_pid(app, tmp_path, monkeypatch):
    import desktop_app

    monkeypatch.setattr(desktop_app, "ROOT", str(tmp_path))
    pid_file = tmp_path / "data" / "kiosk" / "proximity.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("999999", encoding="utf-8")     # 不存在的 pid

    proc = FakeProc()
    app._sensor_proc = proc
    assert app._kill_sensor(timeout=1) is True
    assert proc.terminated, "必须真的 terminate 子进程"
    assert app._sensor_proc is None
    assert not pid_file.exists(), "pid 文件必须清掉，否则下次按旧 pid 误杀"


def test_kill_sensor_escalates_to_kill(app, tmp_path, monkeypatch):
    import desktop_app

    monkeypatch.setattr(desktop_app, "ROOT", str(tmp_path))
    proc = FakeProc(hang_once=True)
    app._sensor_proc = proc
    app._kill_sensor(timeout=0.1)
    assert proc.terminated
    assert proc.killed, "不响应 terminate 时必须强杀"


def test_kill_sensor_is_safe_when_nothing_runs(app, tmp_path, monkeypatch):
    """进程早已退出（用户手动杀过/脚本自检退出）时，退出流程不能报错。"""
    import desktop_app

    monkeypatch.setattr(desktop_app, "ROOT", str(tmp_path))
    (tmp_path / "data" / "kiosk").mkdir(parents=True)
    app._sensor_proc = FakeProc(alive=False)
    assert app._kill_sensor(timeout=1) is False


def test_shutdown_sensor_writes_switch_off(app, tmp_path, monkeypatch):
    """退出时先把 enabled 写死成 false：脚本自检看到就会走正常释放路径。"""
    import desktop_app
    from utils import kiosk_store

    seen = {}
    monkeypatch.setattr(kiosk_store, "set_proximity",
                        lambda patch: seen.update(patch) or patch)
    monkeypatch.setattr(desktop_app, "ROOT", str(tmp_path))
    app._sensor_proc = None

    app._shutdown_sensor()
    assert seen.get("enabled") is False


def test_service_launcher_stop_waits(monkeypatch):
    """请求优雅退出不通时，退回 terminate + wait。"""
    desktop_app = pytest.importorskip("desktop_app")

    def _boom(*a, **k):
        raise OSError("not listening")

    monkeypatch.setattr(desktop_app.requests, "post", _boom)
    launcher = object.__new__(desktop_app.ServiceLauncher)
    launcher.proc = FakeProc()
    assert launcher.stop(timeout=1) is True
    assert launcher.proc.terminated
    assert launcher.proc.waits, "terminate 之后必须 wait，不能投递完就走"


def test_service_launcher_prefers_graceful_shutdown(monkeypatch):
    """能请求通就先让它自己退（这样 RAG sidecar 才会被停掉），不走硬杀。"""
    desktop_app = pytest.importorskip("desktop_app")
    called = {}

    class _Resp:
        status_code = 200

    def _post(url, timeout=None, **kw):
        called["url"] = url
        return _Resp()

    monkeypatch.setattr(desktop_app.requests, "post", _post)
    launcher = object.__new__(desktop_app.ServiceLauncher)
    launcher.proc = FakeProc()
    assert launcher.stop(timeout=1) is True
    assert called["url"].endswith("/api/system/shutdown")
    assert not launcher.proc.terminated, "优雅退出成功后不该再硬杀"


def test_service_launcher_leaves_external_instance(monkeypatch):
    """复用的外部实例（proc is None）不动手——那可能是开发者手动起的服务。"""
    desktop_app = pytest.importorskip("desktop_app")
    launcher = object.__new__(desktop_app.ServiceLauncher)
    launcher.proc = None
    assert launcher.stop(timeout=1) is False


def test_stop_clone_server(monkeypatch):
    desktop_app = pytest.importorskip("desktop_app")
    proc = FakeProc()
    monkeypatch.setattr(desktop_app, "_CLONE_PROC", proc)
    assert desktop_app.stop_clone_server(timeout=1) is True
    assert proc.terminated
    assert desktop_app._CLONE_PROC is None
    # 再调一次不能炸
    assert desktop_app.stop_clone_server(timeout=1) is False


def test_avatar_shutdown_without_loop(app):
    """从未连接过（loop 还是 None）时 shutdown 不能炸。"""
    desktop_app = pytest.importorskip("desktop_app")
    ac = object.__new__(desktop_app.AvatarClient)
    ac.loop = None
    ac._thread = None
    ac._gen = 0
    ac.shutdown(timeout=0.1)                            # 不应抛异常


def test_system_shutdown_handler_stops_loop_later(monkeypatch):
    """退出接口必须「先回响应、再停循环」，否则客户端只看到连接被重置。"""
    import asyncio

    import server.routes as routes

    class FakeLoop:
        def __init__(self):
            self.calls = []

        def call_later(self, delay, fn):
            self.calls.append((delay, fn))

        def stop(self):
            pass

    fake = FakeLoop()
    monkeypatch.setattr(routes.asyncio, "get_event_loop", lambda: fake)

    loop = asyncio.new_event_loop()
    try:
        resp = loop.run_until_complete(routes.system_shutdown(None))
    finally:
        loop.close()

    assert resp.status == 200
    assert len(fake.calls) == 1, "必须只排一次 loop.stop"
    delay, fn = fake.calls[0]
    assert 0 < delay <= 1.0, "要留出把响应写出去的时间"
    assert fn == fake.stop
