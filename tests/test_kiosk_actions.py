# -*- coding: utf-8 -*-
"""闲时动作 / 迎宾触发动作的存储与播放契约。

对应 openspec/changes/refine-kiosk-config-ux：
- `idle.action` / `proximity.trigger_action` 必须是干净的两字段字典；
- 主服务请求动作时必须挡住"形象不匹配"的配置（动作素材按形象归一化过）。

数据目录用 monkeypatch 指到 tmp，不碰真实的 data/kiosk。
"""

import os

import pytest

from utils import kiosk_store as store


@pytest.fixture()
def kiosk_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "KIOSK_DIR", str(tmp_path))
    return tmp_path


# ── 存储 ────────────────────────────────────────────────────────────
def test_idle_default_action_is_empty_pair(kiosk_tmp):
    idle = store.get_idle()
    assert idle["action"] == {"avatar": "", "gesture": ""}
    # 历史字段保留，避免老配置读不出来
    assert "video" in idle


def test_idle_action_roundtrip(kiosk_tmp):
    store.set_idle({"action": {"avatar": "musetalk_ke", "gesture": "idle_sway"}})
    idle = store.get_idle()
    assert idle["action"] == {"avatar": "musetalk_ke", "gesture": "idle_sway"}


def test_idle_action_is_normalized(kiosk_tmp):
    store.set_idle({"action": {"avatar": "  musetalk_ke ", "gesture": "wave_one",
                              "loop": True, "junk": 1}})
    idle = store.get_idle()
    assert idle["action"] == {"avatar": "musetalk_ke", "gesture": "wave_one"}


def test_idle_action_garbage_falls_back_to_empty(kiosk_tmp):
    store.set_idle({"action": "idle_sway"})
    assert store.get_idle()["action"] == {"avatar": "", "gesture": ""}


def test_proximity_trigger_action_roundtrip(kiosk_tmp):
    prox = store.get_proximity()
    assert prox["trigger_action"] == {"avatar": "", "gesture": ""}
    store.set_proximity({"trigger_action": {"avatar": "musetalk_ke", "gesture": "wave_one"}})
    assert store.get_proximity()["trigger_action"] == {"avatar": "musetalk_ke",
                                                       "gesture": "wave_one"}


def test_proximity_keeps_other_fields_when_setting_action(kiosk_tmp):
    store.set_proximity({"camera_index": 2, "sensitivity": 0.75,
                         "trigger_action": {"avatar": "a", "gesture": "g"}})
    prox = store.get_proximity()
    assert prox["camera_index"] == 2
    assert prox["sensitivity"] == pytest.approx(0.75)
    assert prox["trigger_action"] == {"avatar": "a", "gesture": "g"}


# ── 播放 ────────────────────────────────────────────────────────────
class FakeSession:
    def __init__(self, avatar_id="musetalk_ke"):
        self.opt = type("Opt", (), {"avatar_id": avatar_id})()
        self.calls = []

    def request_gesture(self, name, repeat=False):
        self.calls.append((name, repeat))


class SpeechSession(FakeSession):
    """覆盖唤醒回复的最小朗读会话替身。"""

    def __init__(self, avatar_id="musetalk_ke"):
        super().__init__(avatar_id)
        self.delivered = []
        self.preheated = []

    def put_msg_txt(self, text, datainfo):
        self.delivered.append((text, dict(datainfo)))

    def preheat_gestures(self, names):
        self.preheated.append(list(names))


def _routes():
    from server import kiosk_routes

    return kiosk_routes


def test_play_gesture_requests_action():
    session = FakeSession("musetalk_ke")
    ok = _routes()._play_gesture(session, {"avatar": "musetalk_ke", "gesture": "wave_one"})
    assert ok is True
    assert session.calls == [("wave_one", False)]


def test_wake_reply_supports_trailing_gesture(monkeypatch):
    """唤醒回复与讲稿一样支持“说完后再做”的句末动作标签。"""
    from server import routes

    monkeypatch.setattr(routes.gesture_utils, "list_gestures",
                        lambda _avatar: [{"id": "wave_one"}])
    session = SpeechSession("musetalk_ke")
    assert _routes()._speak(session, "您好，欢迎来到课堂[GESTURE:wave_one]") is True
    assert session.delivered == [
        ("您好，欢迎来到课堂", {"gesture_after": "wave_one"})
    ]
    assert session.preheated == [["wave_one"]]


def test_play_gesture_repeat_flag_passed():
    session = FakeSession()
    _routes()._play_gesture(session, {"avatar": "musetalk_ke", "gesture": "idle_sway"},
                            repeat=True)
    assert session.calls == [("idle_sway", True)]


def test_play_gesture_skips_mismatched_avatar():
    session = FakeSession("other_avatar")
    ok = _routes()._play_gesture(session, {"avatar": "musetalk_ke", "gesture": "wave_one"})
    assert ok is False
    assert session.calls == []


def test_play_gesture_allows_empty_avatar_field():
    """没填形象（历史配置）时不做形象校验，直接按动作名播。"""
    session = FakeSession("musetalk_ke")
    ok = _routes()._play_gesture(session, {"avatar": "", "gesture": "wave_one"})
    assert ok is True and session.calls == [("wave_one", False)]


def test_play_gesture_without_gesture_or_session():
    routes = _routes()
    assert routes._play_gesture(None, {"avatar": "a", "gesture": "g"}) is False
    assert routes._play_gesture(FakeSession(), {"avatar": "a", "gesture": ""}) is False
    assert routes._play_gesture(FakeSession(), {}) is False


def test_play_gesture_old_signature_falls_back():
    """会话对象没有 repeat 参数时退化成播一遍，不应抛异常。"""

    class OldSession(FakeSession):
        def request_gesture(self, name):
            self.calls.append((name, False))

    session = OldSession()
    ok = _routes()._play_gesture(session, {"avatar": "musetalk_ke", "gesture": "g"},
                                 repeat=True)
    assert ok is True
    assert session.calls == [("g", False)]


# ── 现场可观测性：动作为什么没播 ────────────────────────────────────
def test_play_gesture_records_success_in_state():
    routes = _routes()
    routes._STATE.update(last_gesture="", last_gesture_ok=False, last_gesture_note="")
    routes._play_gesture(FakeSession("musetalk_ke"),
                         {"avatar": "musetalk_ke", "gesture": "wave_one"})
    assert routes._STATE["last_gesture"] == "wave_one"
    assert routes._STATE["last_gesture_ok"] is True


def test_play_gesture_records_avatar_mismatch():
    """形象不匹配是最常见的"配了动作却没反应"，原因要能直接显示出来。"""
    routes = _routes()
    routes._play_gesture(FakeSession("other"), {"avatar": "musetalk_ke", "gesture": "g"})
    assert routes._STATE["last_gesture_ok"] is False
    assert "形象不匹配" in routes._STATE["last_gesture_note"]


def test_play_gesture_records_missing_session():
    routes = _routes()
    routes._play_gesture(None, {"avatar": "musetalk_ke", "gesture": "g"})
    assert routes._STATE["last_gesture_ok"] is False
    assert "会话" in routes._STATE["last_gesture_note"]
