# -*- coding: utf-8 -*-
"""问答记录要能按「问题库 / 知识库 / 设备」筛，且删单条不能只能整表清空。

界面上的三个新筛选维度只有提问那一刻就记下来才存在——靠答案文本反推是推
不出来的（同一个答案可能来自菜单固定答复，也可能来自知识库检索）。

这里还锁住两个容易写错的点：
- `facets` 必须扫**全量**记录。只扫当页几十条的话，用户会以为某个知识库
  从来没命中过；
- 单条删除要真的只删一条（运营要挑出调试时随口问的一句，整表清空没人敢点）。
"""

import csv
import os

import pytest

from server import kiosk_routes
from utils import kiosk_store as store


@pytest.fixture()
def kiosk_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "KIOSK_DIR", str(tmp_path))
    monkeypatch.setenv("KIOSK_DEVICE_NAME", "HOLO-01")
    return tmp_path


def _seed():
    store.append_qa("课程多少钱", True, "menu", "课程免费", "命中菜单",
                    source_name="课程介绍", course="", material="2")
    store.append_qa("实训室什么时候开放", True, "rag", "9:00-21:00", "命中知识库",
                    source_name="", course="python-base", material="")
    store.append_qa("你们老板是谁", False, "none", "资料未涉及", "未命中")


# ── 记录维度 ────────────────────────────────────────────────────────
def test_record_keeps_source_and_device(kiosk_tmp):
    record = store.append_qa("课程多少钱", True, "menu", "课程免费",
                             source_name="课程介绍", course="c1", material="2")
    assert record["source_name"] == "课程介绍"
    assert record["course"] == "c1"
    assert record["material"] == "2"
    assert record["device"] == "HOLO-01"


def test_device_defaults_to_host_when_unset(kiosk_tmp, monkeypatch):
    monkeypatch.delenv("KIOSK_DEVICE_NAME", raising=False)
    assert store.append_qa("你好", True, "wake", "您好")["device"]


# ── 筛选 ────────────────────────────────────────────────────────────
def test_filter_by_source_name(kiosk_tmp):
    _seed()
    total, rows = store.query_qa(source_name="课程介绍")
    assert total == 1
    assert rows[0]["question"] == "课程多少钱"


def test_filter_by_course(kiosk_tmp):
    _seed()
    total, rows = store.query_qa(course="python-base")
    assert total == 1
    assert rows[0]["source"] == "rag"


def test_filter_by_device(kiosk_tmp):
    _seed()
    assert store.query_qa(device="HOLO-01")[0] == 3
    assert store.query_qa(device="HOLO-99")[0] == 0


def test_filters_combine(kiosk_tmp):
    _seed()
    assert store.query_qa(source="menu", device="HOLO-01")[0] == 1
    assert store.query_qa(source="menu", device="HOLO-99")[0] == 0


def test_rows_are_newest_first(kiosk_tmp):
    _seed()
    _total, rows = store.query_qa()
    assert [r["question"] for r in rows][0] == "你们老板是谁"


# ── 单条删除 ────────────────────────────────────────────────────────
def test_delete_one_record_only(kiosk_tmp):
    _seed()
    target = store.query_qa(source_name="课程介绍")[1][0]
    assert store.delete_qa(target["id"]) is True
    assert store.query_qa()[0] == 2
    assert store.query_qa(source_name="课程介绍")[0] == 0


def test_delete_missing_record_reports_false(kiosk_tmp):
    _seed()
    assert store.delete_qa("") is False
    assert store.delete_qa("qa_not_there") is False
    assert store.query_qa()[0] == 3


# ── 筛选候选值 ──────────────────────────────────────────────────────
class _Req:
    def __init__(self):
        self.query = {}


def test_facets_scan_all_records_not_just_page(kiosk_tmp):
    """候选值取自全量去重：只扫当页的话，第一页之后的取值永远选不到。"""
    _seed()
    # 让"知识库"这一维只出现在第 2 页：第一页按时间倒序取 1 条
    total, _page1 = store.query_qa(limit=1)
    assert total == 3
    data = kiosk_routes.qa_log_facets(_Req()).body
    import json
    payload = json.loads(data.decode("utf-8"))["data"]
    assert payload["course"] == ["python-base"]
    assert payload["source_name"] == ["课程介绍"]
    assert payload["device"] == ["HOLO-01"]
    assert sorted(payload["source"]) == ["menu", "none", "rag"]


# ── 导出 ────────────────────────────────────────────────────────────
def test_export_has_the_new_columns(kiosk_tmp):
    _seed()
    path, total = store.export_qa_csv()
    assert total == 3
    with open(path, encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh))
    for name in ("问题库", "知识库", "显示素材", "设备名称"):
        assert name in header
    os.remove(path)
