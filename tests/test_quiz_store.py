# -*- coding: utf-8 -*-
"""题库与问答记录存储的测试（TDD：这些断言先于实现存在）。"""

import json
import os
import shutil
import tempfile
import unittest

from utils.quiz_store import QuizBank, QuizRecords, QuizStoreError


class QuizBankTestCase(unittest.TestCase):
    """2.1 题库条目结构。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="quiz-bank-")
        self.path = os.path.join(self.tmpdir, "bank.json")
        self.bank = QuizBank(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def read_raw(self):
        with open(self.path, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_new_bank_is_empty_and_does_not_create_file(self):
        self.assertEqual(self.bank.list_questions(), [])
        self.assertFalse(os.path.exists(self.path), "只读不应创建文件")

    def test_added_entry_has_all_required_fields(self):
        entry = self.bank.add_question(
            question="铁路客流量统计的作用是什么？",
            reference_answer="用于评估运力与调度。",
            rubric=["提到运力", "提到调度"],
            slide_id=3,
        )
        for field in ("id", "question", "reference_answer", "rubric", "slide_id", "source"):
            self.assertIn(field, entry, "条目缺少字段 %s" % field)
        self.assertEqual(entry["source"], "manual")
        self.assertEqual(entry["slide_id"], 3)
        self.assertEqual(entry["rubric"], ["提到运力", "提到调度"])

    def test_rubric_defaults_to_empty_list_so_field_always_exists(self):
        entry = self.bank.add_question(question="问", reference_answer="答")
        self.assertEqual(entry["rubric"], [])
        self.assertIsNone(entry["slide_id"])

    def test_generated_source_is_recorded(self):
        entry = self.bank.add_question(question="问", reference_answer="答", source="generated")
        self.assertEqual(entry["source"], "generated")

    def test_missing_question_is_rejected(self):
        with self.assertRaises(QuizStoreError):
            self.bank.add_question(question="  ", reference_answer="答")

    def test_missing_reference_answer_is_rejected(self):
        with self.assertRaises(QuizStoreError):
            self.bank.add_question(question="问", reference_answer="")

    def test_ids_are_unique_and_stable(self):
        first = self.bank.add_question(question="一", reference_answer="a")
        second = self.bank.add_question(question="二", reference_answer="b")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.bank.get_question(first["id"])["question"], "一")

    def test_list_returns_all_added_entries(self):
        self.bank.add_question(question="一", reference_answer="a")
        self.bank.add_question(question="二", reference_answer="b")
        self.assertEqual(len(self.bank.list_questions()), 2)

    def test_update_changes_fields(self):
        entry = self.bank.add_question(question="一", reference_answer="a")
        updated = self.bank.update_question(entry["id"], rubric=["要点"])
        self.assertEqual(updated["rubric"], ["要点"])
        self.assertEqual(updated["question"], "一", "未指定的字段不得被清空")

    def test_update_unknown_id_is_rejected(self):
        with self.assertRaises(QuizStoreError):
            self.bank.update_question("nope", rubric=["x"])

    def test_delete_removes_entry(self):
        entry = self.bank.add_question(question="一", reference_answer="a")
        self.assertTrue(self.bank.delete_question(entry["id"]))
        self.assertEqual(self.bank.list_questions(), [])

    def test_delete_unknown_id_is_rejected(self):
        self.bank.add_question(question="一", reference_answer="a")
        with self.assertRaises(QuizStoreError):
            self.bank.delete_question("nope")
        self.assertEqual(len(self.bank.list_questions()), 1, "其它题目不受影响")

    def test_persisted_across_instances(self):
        entry = self.bank.add_question(question="一", reference_answer="a")
        reloaded = QuizBank(self.path)
        self.assertEqual(reloaded.get_question(entry["id"])["question"], "一")


class QuizBankTolerantLoadTestCase(unittest.TestCase):
    """2.2 原子写入与宽容解析。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="quiz-bank-tolerant-")
        self.path = os.path.join(self.tmpdir, "bank.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_corrupt_file_keeps_original_and_starts_empty(self):
        broken = "{ not json"
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(broken)
        bank = QuizBank(self.path)
        self.assertEqual(bank.list_questions(), [])
        with open(self.path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), broken, "损坏的原文件必须保留")

    def test_non_list_json_starts_empty(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"questions": []}, handle)
        bank = QuizBank(self.path)
        self.assertEqual(bank.list_questions(), [])

    def test_entries_missing_required_fields_are_skipped(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump([{"id": "ok", "question": "问", "reference_answer": "答"},
                       {"id": "bad"}], handle)
        bank = QuizBank(self.path)
        self.assertEqual(len(bank.list_questions()), 1)
        self.assertIsNotNone(bank.get_question("ok"))

    def test_write_is_atomic_leaving_no_temp_files(self):
        bank = QuizBank(self.path)
        bank.add_question(question="一", reference_answer="a")
        leftovers = [n for n in os.listdir(self.tmpdir) if n != "bank.json"]
        self.assertEqual(leftovers, [], "原子写入不得留下临时文件")

    def test_creates_parent_directory(self):
        nested = os.path.join(self.tmpdir, "a", "b", "bank.json")
        bank = QuizBank(nested)
        bank.add_question(question="一", reference_answer="a")
        self.assertTrue(os.path.isfile(nested))


class QuizRecordsTestCase(unittest.TestCase):
    """2.3 问答记录：题目、作答原文、判分结论、是否降级、是否未作答。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="quiz-records-")
        self.path = os.path.join(self.tmpdir, "records.json")
        self.records = QuizRecords(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_has_all_required_fields(self):
        record = self.records.add_record(
            sessionid="s1", question_id="q1", answer="用来看运力",
            verdict="correct", degraded=False, unanswered=False,
        )
        for field in ("sessionid", "question_id", "answer", "verdict",
                      "degraded", "unanswered", "timestamp", "feedback"):
            self.assertIn(field, record, "记录缺少字段 %s" % field)
        self.assertEqual(record["verdict"], "correct")
        self.assertFalse(record["degraded"])

    def test_feedback_text_is_persisted(self):
        """反馈语必须落库：前端提问卡片要显示它，而朗读通道无法回读。"""
        record = self.records.add_record(
            sessionid="s1", question_id="q1", answer="用来看运力",
            verdict="partial", feedback="答对了运力口径，但没提统计时段。",
        )
        self.assertEqual(record["feedback"], "答对了运力口径，但没提统计时段。")
        reloaded = self.records.list_records("s1")[0]
        self.assertEqual(reloaded["feedback"], "答对了运力口径，但没提统计时段。")

    def test_feedback_defaults_to_empty_string(self):
        record = self.records.add_record(sessionid="s1", question_id="q1", answer="a")
        self.assertEqual(record["feedback"], "")

    def test_degraded_record_is_distinguishable(self):
        self.records.add_record(sessionid="s1", question_id="q1", answer="x",
                                verdict="unfinished", degraded=True)
        record = self.records.list_records("s1")[0]
        self.assertTrue(record["degraded"])
        self.assertEqual(record["verdict"], "unfinished")

    def test_unanswered_record_is_kept(self):
        self.records.add_record(sessionid="s1", question_id="q2", answer="",
                                verdict=None, unanswered=True)
        record = self.records.list_records("s1")[0]
        self.assertTrue(record["unanswered"])
        self.assertEqual(record["answer"], "")

    def test_records_are_filtered_by_session(self):
        self.records.add_record(sessionid="s1", question_id="q1", answer="a")
        self.records.add_record(sessionid="s2", question_id="q1", answer="b")
        self.assertEqual(len(self.records.list_records("s1")), 1)
        self.assertEqual(len(self.records.list_records()), 2)

    def test_records_survive_reload(self):
        self.records.add_record(sessionid="s1", question_id="q1", answer="a")
        reloaded = QuizRecords(self.path)
        self.assertEqual(len(reloaded.list_records("s1")), 1)

    def test_corrupt_records_file_starts_empty_and_keeps_original(self):
        broken = "[[["
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(broken)
        records = QuizRecords(self.path)
        self.assertEqual(records.list_records(), [])
        with open(self.path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), broken)


if __name__ == "__main__":
    unittest.main()
