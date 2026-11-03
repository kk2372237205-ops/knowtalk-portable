# -*- coding: utf-8 -*-
"""屏幕对话气泡取哪句话。

两条链路都可能让数字人开口：LLM 问答（流式累加）与直接朗读的整句（欢迎语、
菜单介绍）。取错的表现很难查：气泡里一直挂着上一轮的旧回答，或者欢迎语被
旧回答压掉。这里钉住"谁后开始念谁显示"的规则。
"""

from server.routes import bubble_snapshot


class FakeSession:
    def __init__(self, **kw):
        self.llm_text = kw.get("llm_text", "")
        self.llm_done = kw.get("llm_done", False)
        self.llm_started_at = kw.get("llm_started_at", 0.0)
        self.last_question = kw.get("last_question", "")
        self.say_text = kw.get("say_text", "")
        self.say_at = kw.get("say_at", 0.0)
        self.speech_request_id = kw.get("speech_request_id", 0)


def test_llm_round_is_shown_while_streaming():
    data = bubble_snapshot(FakeSession(
        llm_text="这门课一共 32 学时，", llm_done=False, llm_started_at=100.0,
        last_question="这门课多少学时？"), speaking=True)
    assert data["text"].startswith("这门课")
    assert data["done"] is False
    assert data["question"] == "这门课多少学时？"
    assert data["speaking"] is True
    assert data["bubble_key"] == "llm:100.000"


def test_direct_speech_wins_when_it_starts_later():
    # 上一轮问答还留在 llm_text 里，此刻先说欢迎语——气泡必须跟着欢迎语走
    data = bubble_snapshot(FakeSession(
        llm_text="上一轮的长回答", llm_done=True, llm_started_at=100.0,
        last_question="旧问题", say_text="您好，欢迎来到知课展区", say_at=200.0),
        speaking=True)
    assert data["text"] == "您好，欢迎来到知课展区"
    assert data["done"] is True
    assert data["question"] == ""
    assert data["bubble_key"] == "say:200.000"


def test_llm_wins_again_after_a_new_round_starts():
    data = bubble_snapshot(FakeSession(
        llm_text="新回答", llm_done=False, llm_started_at=300.0,
        say_text="更早的欢迎语", say_at=200.0))
    assert data["text"] == "新回答"
    assert data["bubble_key"] == "llm:300.000"


def test_empty_session_yields_empty_snapshot():
    data = bubble_snapshot(FakeSession())
    assert data["text"] == ""
    assert data["bubble_key"] == ""
    assert data["done"] is True


def test_bubble_key_changes_between_rounds():
    first = bubble_snapshot(FakeSession(llm_text="a", llm_started_at=1.0))
    second = bubble_snapshot(FakeSession(llm_text="b", llm_started_at=2.0))
    assert first["bubble_key"] != second["bubble_key"]
