# -*- coding: utf-8 -*-
"""题库与问答记录的持久化。

设计要点与 `utils/settings_store.py` 保持一致：
- 原子写入（先写临时文件再 `os.replace`），避免读到写了一半的文件；
- 宽容解析（文件缺失按空处理；损坏则保留原文件并记录可定位的错误）；
- 结构有问题的条目被跳过而不是让整个题库不可用。

存放位置在 `data/` 下，已被版本库忽略规则覆盖（题库属于运行时数据）。
"""

import json
import os
import tempfile
import threading
import time
import uuid

from utils.logger import logger

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUIZ_DIR = os.path.join(ROOT, "data", "quiz")
BANK_FILE = os.path.join(QUIZ_DIR, "bank.json")
RECORDS_FILE = os.path.join(QUIZ_DIR, "records.json")

BANK_MAX_ENTRIES = 5000
RECORDS_MAX_ENTRIES = 50000


class QuizStoreError(ValueError):
    """题库或记录的读写请求不合法。"""


def _read_json_list(path, what):
    """宽容读取一个 JSON 数组；任何问题都降级为空列表并保留原文件。"""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:  # noqa: BLE001
        logger.error("[提问] %s 无法解析（%s），改用空数据；原文件保持不变：%s",
                     what, exc, path)
        return []
    if not isinstance(data, list):
        logger.error("[提问] %s 结构不是 JSON 数组，改用空数据：%s", what, path)
        return []
    return data


def _write_json_atomic(path, payload):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    handle_fd, tmp_path = tempfile.mkstemp(
        dir=directory or ".", prefix=".quiz-", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _require_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise QuizStoreError("字段 %s 不能为空" % field)
    return value.strip()


def _normalize_rubric(rubric):
    if rubric is None:
        return []
    if isinstance(rubric, str):
        rubric = [rubric]
    if not isinstance(rubric, (list, tuple)):
        raise QuizStoreError("字段 rubric 必须是字符串数组")
    return [str(item).strip() for item in rubric if str(item).strip()]


class QuizBank:
    """题库。条目字段：id / question / reference_answer / rubric / slide_id / source。"""

    VALID_SOURCES = ("manual", "generated")

    def __init__(self, path=None):
        self.path = path or BANK_FILE
        self._lock = threading.RLock()
        self._entries = []
        self.load()

    def load(self):
        with self._lock:
            raw = _read_json_list(self.path, "题库文件")
            entries = []
            skipped = 0
            for item in raw:
                if not isinstance(item, dict):
                    skipped += 1
                    continue
                if not item.get("id") or not item.get("question") or not item.get("reference_answer"):
                    skipped += 1
                    continue
                item.setdefault("rubric", [])
                item.setdefault("slide_id", None)
                item.setdefault("source", "manual")
                entries.append(item)
            if skipped:
                logger.warning("[提问] 题库中有 %d 条结构不完整的条目被跳过", skipped)
            self._entries = entries
            return len(entries)

    def _save(self):
        _write_json_atomic(self.path, self._entries)

    def list_questions(self):
        with self._lock:
            return [dict(item) for item in self._entries]

    def question_ids(self):
        with self._lock:
            return [item["id"] for item in self._entries]

    def get_question(self, question_id):
        with self._lock:
            for item in self._entries:
                if item["id"] == question_id:
                    return dict(item)
        return None

    def add_question(self, question, reference_answer, rubric=None,
                     slide_id=None, source="manual", question_id=None):
        question = _require_text(question, "question")
        reference_answer = _require_text(reference_answer, "reference_answer")
        if source not in self.VALID_SOURCES:
            raise QuizStoreError(
                "字段 source 必须是 %s 之一" % "/".join(self.VALID_SOURCES)
            )
        entry = {
            "id": question_id or uuid.uuid4().hex[:12],
            "question": question,
            "reference_answer": reference_answer,
            "rubric": _normalize_rubric(rubric),
            "slide_id": slide_id,
            "source": source,
        }
        with self._lock:
            if any(item["id"] == entry["id"] for item in self._entries):
                raise QuizStoreError("题目标识已存在: %s" % entry["id"])
            if len(self._entries) >= BANK_MAX_ENTRIES:
                raise QuizStoreError("题库条数已达上限 %d" % BANK_MAX_ENTRIES)
            self._entries.append(entry)
            self._save()
        logger.info("[提问] 新增题目 id=%s source=%s", entry["id"], source)
        return dict(entry)

    def update_question(self, question_id, question=None, reference_answer=None,
                        rubric=None, slide_id=None):
        with self._lock:
            for item in self._entries:
                if item["id"] != question_id:
                    continue
                if question is not None:
                    item["question"] = _require_text(question, "question")
                if reference_answer is not None:
                    item["reference_answer"] = _require_text(
                        reference_answer, "reference_answer"
                    )
                if rubric is not None:
                    item["rubric"] = _normalize_rubric(rubric)
                if slide_id is not None:
                    item["slide_id"] = slide_id
                self._save()
                logger.info("[提问] 更新题目 id=%s", question_id)
                return dict(item)
        raise QuizStoreError("题目不存在: %s" % question_id)

    def delete_question(self, question_id):
        with self._lock:
            for index, item in enumerate(self._entries):
                if item["id"] == question_id:
                    del self._entries[index]
                    self._save()
                    logger.info("[提问] 删除题目 id=%s", question_id)
                    return True
        raise QuizStoreError("题目不存在: %s" % question_id)


class QuizRecords:
    """问答记录。字段：sessionid / question_id / answer / verdict / feedback / degraded / unanswered / timestamp。

    `feedback` 是面向学员的判分反馈语。它同时也是朗读内容，但朗读通道无法回读，
    因此必须落库，前端提问卡片才有数据源可以展示。
    """

    def __init__(self, path=None):
        self.path = path or RECORDS_FILE
        self._lock = threading.RLock()
        self._records = []
        self.load()

    def load(self):
        with self._lock:
            raw = _read_json_list(self.path, "问答记录文件")
            self._records = [item for item in raw if isinstance(item, dict)
                             and item.get("question_id")]
            return len(self._records)

    def _save(self):
        _write_json_atomic(self.path, self._records)

    def add_record(self, sessionid, question_id, answer="", verdict=None,
                   degraded=False, unanswered=False, feedback=""):
        if not question_id:
            raise QuizStoreError("字段 question_id 不能为空")
        record = {
            "sessionid": sessionid,
            "question_id": question_id,
            "answer": answer if isinstance(answer, str) else str(answer),
            "verdict": verdict,
            "feedback": feedback if isinstance(feedback, str) else str(feedback or ""),
            "degraded": bool(degraded),
            "unanswered": bool(unanswered),
            "timestamp": time.time(),
        }
        with self._lock:
            self._records.append(record)
            if len(self._records) > RECORDS_MAX_ENTRIES:
                dropped = len(self._records) - RECORDS_MAX_ENTRIES
                self._records = self._records[dropped:]
                logger.warning("[提问] 问答记录超过上限，丢弃最早的 %d 条", dropped)
            self._save()
        return dict(record)

    def list_records(self, sessionid=None):
        with self._lock:
            items = [dict(item) for item in self._records]
        if sessionid is None:
            return items
        return [item for item in items if item.get("sessionid") == sessionid]


_bank = None
_records = None


def get_bank():
    global _bank
    if _bank is None:
        _bank = QuizBank()
    return _bank


def get_records():
    global _records
    if _records is None:
        _records = QuizRecords()
    return _records
