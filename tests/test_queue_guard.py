# -*- coding: utf-8 -*-
"""有界队列写入的语义（变更 fix-session-teardown-deadlock 任务 1.1）。

现场故障是"无界 put 把渲染线程永久卡住，最终拖死事件循环"。这里钉住
`put_capped` 的三条契约：绝不无限阻塞、队列满时丢最旧、可被 quit 中止。
"""

import queue
import time
import unittest

from utils.queue_guard import DropReporter, put_capped


class PutCappedTestCase(unittest.TestCase):
    def test_writes_when_queue_has_room(self):
        q = queue.Queue(maxsize=2)
        self.assertTrue(put_capped(q, "a", timeout=0.01))
        self.assertEqual(q.qsize(), 1)

    def test_never_blocks_forever_on_full_queue(self):
        """队列满且没有消费者时，写入必须在有限时间内返回。"""
        q = queue.Queue(maxsize=1)
        q.put("old")
        started = time.perf_counter()
        result = put_capped(q, "new", timeout=0.05)
        elapsed = time.perf_counter() - started
        self.assertTrue(result, "丢最旧腾位后应能写入")
        self.assertLess(elapsed, 1.0, "写入必须在有界时间内返回（最坏 2×timeout）")
        self.assertEqual(q.get_nowait(), "new", "应保留最新一帧")

    def test_drops_item_when_queue_keeps_being_refilled(self):
        """极端情形（其它生产者持续抢位置）：放弃本次写入而不是等待。"""

        class StubbornQueue(queue.Queue):
            """永远报满，模拟"位置永远抢不到"。"""

            def __init__(self):
                super().__init__(maxsize=1)
                self.removals = 0

            def put(self, item, block=True, timeout=None):
                raise queue.Full

            def get_nowait(self):
                self.removals += 1
                return "old"

        q = StubbornQueue()
        started = time.perf_counter()
        result = put_capped(q, "new", timeout=0.02)
        elapsed = time.perf_counter() - started
        self.assertFalse(result, "抢不到位置时应放弃写入")
        self.assertLess(elapsed, 1.0, "放弃也要在有界时间内完成")
        self.assertEqual(q.removals, 1, "只应尝试腾位一次")

    def test_aborts_on_quit_without_waiting(self):
        q = queue.Queue(maxsize=1)
        q.put("old")
        started = time.perf_counter()
        result = put_capped(q, "new", timeout=5.0, should_abort=lambda: True)
        self.assertFalse(result)
        self.assertLess(time.perf_counter() - started, 0.2, "quit 置位后必须立刻返回")
        self.assertEqual(q.qsize(), 1, "中止时不得改动队列")

    def test_abort_is_checked_before_blocking(self):
        q = queue.Queue(maxsize=1)
        calls = []
        put_capped(q, "a", timeout=0.01, should_abort=lambda: calls.append(1) or False)
        self.assertEqual(calls, [1])

    def test_on_drop_reports_reason(self):
        reasons = []
        q = queue.Queue(maxsize=1)
        q.put("old")
        put_capped(q, "new", timeout=0.01, on_drop=reasons.append)
        self.assertEqual(reasons, ["full"], "丢最旧腾位应上报 full")

    def test_on_drop_exception_does_not_break_writer(self):
        q = queue.Queue(maxsize=1)
        q.put("old")

        def boom(_reason):
            raise RuntimeError("调用方回调自己炸了")

        self.assertTrue(put_capped(q, "new", timeout=0.01, on_drop=boom))


class DropReporterTestCase(unittest.TestCase):
    class _Logger:
        def __init__(self):
            self.records = []

        def warning(self, *args):
            self.records.append(args)

    def test_first_and_every_nth_event_logged(self):
        logger = self._Logger()
        report = DropReporter("audio", logger, every=3)
        for _ in range(4):
            report("full")
        self.assertEqual(len(logger.records), 2, "第 1、3 次上报，不得每帧刷屏")
        self.assertIn("audio", logger.records[0])

    def test_counts_drops_separately_from_full(self):
        logger = self._Logger()
        report = DropReporter("res", logger, every=1000)
        report("full")
        report("drop")
        self.assertEqual(report.count, 2)
        self.assertEqual(report.dropped, 1)

    def test_logger_failure_does_not_raise(self):
        class Bad:
            def warning(self, *args):
                raise RuntimeError("log 炸了")

        report = DropReporter("x", Bad(), every=1)
        report("full")   # 不得抛出


if __name__ == "__main__":
    unittest.main()
