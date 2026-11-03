# -*- coding: utf-8 -*-
"""菜单的「配置答案」与「显示素材」是真字段，不是界面上的装饰。

运营配了固定答复就说明这句话要一字不改地念出去——再去查知识库可能念出别的
内容，还会把命中率统计搅乱；配了显示素材就说明回答这一条时要换画面。

这里锁住三件事：
- 存储层两个字段能存能改（update_menu 的白名单别把它们漏掉）；
- 命中时**配了答案就不查知识库**（把 _rag_answer 换成会抛异常的桩来验证）；
- 配了素材要真的切画面。
"""

import asyncio

import pytest

from server import kiosk_routes
from utils import kiosk_store as store


@pytest.fixture()
def kiosk_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "KIOSK_DIR", str(tmp_path))
    return tmp_path


# ── 存储 ────────────────────────────────────────────────────────────
def test_new_menu_has_empty_answer_and_material(kiosk_tmp):
    item = store.add_menu("离职补偿", "离职补偿")
    assert item["answer"] == ""
    assert item["material"] == ""


def test_add_menu_keeps_answer_and_material(kiosk_tmp):
    item = store.add_menu("薪资新政", "薪资新政", answer="分五档", material="7")
    assert item["answer"] == "分五档"
    assert item["material"] == "7"


def test_update_menu_accepts_answer_and_material(kiosk_tmp):
    item = store.add_menu("劳动法", "劳动法")
    store.update_menu(item["id"], answer="按新规执行", material="2")
    saved = store.get_menu(item["id"])
    assert saved["answer"] == "按新规执行"
    assert saved["material"] == "2"


def test_update_menu_clears_answer_when_set_empty(kiosk_tmp):
    item = store.add_menu("劳动法", "劳动法", answer="旧话术")
    store.update_menu(item["id"], answer="")
    assert store.get_menu(item["id"])["answer"] == ""


# ── 命中之后怎么答 ──────────────────────────────────────────────────
class _Session:
    """只要 _speak / _show_material 能落进去的最小会话替身。"""

    def __init__(self):
        self.materials = []


def _run(coro):
    """起一个干净的循环跑协程：pytest 主线程里没有"当前循环"可借用。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_fixed_answer_skips_rag(kiosk_tmp, monkeypatch):
    """配了「配置答案」就不该再碰知识库——查出来的内容会盖掉锁定的话术。"""

    async def boom(*_a, **_kw):
        raise AssertionError("配了固定答复还去查了知识库")

    monkeypatch.setattr(kiosk_routes, "_rag_answer", boom)
    monkeypatch.setattr(kiosk_routes, "_speak", lambda *_a, **_kw: True)
    monkeypatch.setattr(kiosk_routes, "_show_material", lambda *_a, **_kw: None)

    menu = {"title": "薪资新政", "question": "薪资新政", "answer": "分五档"}
    result = _run(kiosk_routes._answer_menu(menu, _Session()))
    assert result["answer"] == "分五档"
    assert result["matched"] is True
    assert "固定答复" in result["detail"]


def test_preset_question_still_goes_to_rag(kiosk_tmp, monkeypatch):
    """没配答案但有预置问题时，原来的检索链路必须照旧。"""

    async def fake_rag(question, *_a, **_kw):
        assert question == "2024年新劳动法"
        return {"matched": True, "answer": "新规内容", "course": "labor-law",
                "detail": "命中知识库"}

    monkeypatch.setattr(kiosk_routes, "_rag_answer", fake_rag)
    monkeypatch.setattr(kiosk_routes, "_speak", lambda *_a, **_kw: True)
    monkeypatch.setattr(kiosk_routes, "_show_material", lambda *_a, **_kw: None)

    menu = {"title": "劳动法新规", "question": "2024年新劳动法", "answer": ""}
    result = _run(kiosk_routes._answer_menu(menu, _Session()))
    assert result["answer"] == "新规内容"
    assert result["course"] == "labor-law"


def test_title_is_spoken_when_nothing_configured(kiosk_tmp, monkeypatch):
    async def boom(*_a, **_kw):
        raise AssertionError("既没答案也没问题，不该去查知识库")

    monkeypatch.setattr(kiosk_routes, "_rag_answer", boom)
    monkeypatch.setattr(kiosk_routes, "_speak", lambda *_a, **_kw: True)
    monkeypatch.setattr(kiosk_routes, "_show_material", lambda *_a, **_kw: None)

    result = _run(kiosk_routes._answer_menu({"title": "课程介绍"}, _Session()))
    assert result["answer"] == "课程介绍"


def test_material_is_switched_on_hit(kiosk_tmp, monkeypatch):
    monkeypatch.setattr(kiosk_routes, "_speak", lambda *_a, **_kw: True)
    shown = []
    monkeypatch.setattr(kiosk_routes, "_show_material",
                        lambda session, material: shown.append(material))

    menu = {"title": "劳动法", "answer": "按新规", "material": "2"}
    result = _run(kiosk_routes._answer_menu(menu, _Session()))
    assert shown == ["2"]
    assert result["material"] == "2"
