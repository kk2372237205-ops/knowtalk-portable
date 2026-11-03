# -*- coding: utf-8 -*-
"""判分/转写不得阻塞事件循环。

现场问题：判分体感十几秒、期间数字人画面卡住、题目久等不出。
实测单次判分只要 ~0.9s，慢的根因是**同步阻塞调用直接跑在事件循环里**：
aiortc 收发、提问 tick、状态轮询同在这条循环上，一起停摆。

这里用"心跳协程还能不能跑"直接度量是否让出了事件循环——比断言有没有调用
run_in_executor 更贴近现场问题。
"""

import asyncio
import json
import os
import shutil
import tempfile
import threading
import time
import unittest

import server.quiz_routes as quiz_routes
import server.stt_routes as stt_routes
from server.blocking import run_blocking

GRADE_SECONDS = 0.6


def parse(response):
    """路由返回 aiohttp Response，测试里统一取 JSON 体。"""
    return json.loads(response.text)


class RunBlockingTestCase(unittest.TestCase):
    def test_body_runs_off_the_calling_thread(self):
        caller = threading.current_thread().name
        name = asyncio.run(run_blocking(lambda: threading.current_thread().name))
        self.assertNotEqual(name, caller, "阻塞调用必须在别的线程里跑")
        self.assertNotEqual(name, "MainThread")

    def test_result_is_returned(self):
        self.assertEqual(asyncio.run(run_blocking(lambda a, b=0: a + b, 2, b=3)), 5)

    def test_exception_propagates(self):
        def boom():
            raise ValueError("炸了")

        with self.assertRaises(ValueError):
            asyncio.run(run_blocking(boom))


class FakeRequest(object):
    def __init__(self, body=None, query=None):
        self._body = body
        self.query = query or {}

    async def json(self):
        return self._body


class FakeOrchestrator(object):
    """只关心 submit_answer 的耗时与执行线程。"""

    def __init__(self, seconds=GRADE_SECONDS):
        self.seconds = seconds
        self.stage = "awaiting_answer"
        self.threads = []

    def submit_answer(self, text):
        self.threads.append(threading.current_thread().name)
        time.sleep(self.seconds)
        self.stage = "feedback"
        return True


class FakeRegistry(object):
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator

    def get(self, sessionid):
        return self.orchestrator


HEARTBEAT_SECONDS = 0.05
# 允许丢掉的节拍比例：调度抖动、线程池启动开销都会吃掉几拍。
LIVENESS_TOLERANCE = 0.4


def measure_liveness(coro_factory):
    """跑一个协程，同时数"心跳"跳了几次。

    阻塞实现下心跳在整段等待里一次都跳不了；卸载到线程池后，心跳应当跑满
    `耗时 / 间隔` 次左右。用**相对比例**判定，避免机器快慢让阈值失真
    （固定阈值会被"只阻塞了一小段"的实现蒙过去）。
    """
    async def main():
        beats = 0
        done = {"v": False}

        async def heartbeat():
            nonlocal beats
            while not done["v"]:
                beats += 1
                await asyncio.sleep(HEARTBEAT_SECONDS)

        task = asyncio.ensure_future(heartbeat())
        await asyncio.sleep(0)  # 让心跳先跑起来
        started = time.perf_counter()
        result = await coro_factory()
        elapsed = time.perf_counter() - started
        done["v"] = True
        await task
        return beats, elapsed, result

    return asyncio.run(main())


def assert_loop_was_alive(case, beats, elapsed):
    expected = elapsed / HEARTBEAT_SECONDS
    case.assertGreaterEqual(
        beats, expected * (1 - LIVENESS_TOLERANCE),
        "等待期间事件循环被冻住了：%.2fs 里心跳只跳了 %d 次（应约 %.0f 次）"
        % (elapsed, beats, expected),
    )


class AnswerRouteTestCase(unittest.TestCase):
    """文字作答：判分期间心跳协程必须继续跑。"""

    def setUp(self):
        self.orchestrator = FakeOrchestrator()
        self._saved = quiz_routes.registry
        quiz_routes.registry = FakeRegistry(self.orchestrator)

    def tearDown(self):
        quiz_routes.registry = self._saved

    def test_grading_does_not_block_the_event_loop(self):
        beats, elapsed, raw = measure_liveness(
            lambda: quiz_routes.quiz_answer(
                FakeRequest(body={"sessionid": "s1", "answer": "我的回答"})
            )
        )
        result = parse(raw)
        self.assertEqual(result["code"], 0, result)
        assert_loop_was_alive(self, beats, elapsed)

    def test_grading_runs_off_the_event_loop_thread(self):
        response = asyncio.run(quiz_routes.quiz_answer(
            FakeRequest(body={"sessionid": "s1", "answer": "我的回答"})
        ))
        self.assertEqual(parse(response)["code"], 0)
        self.assertEqual(len(self.orchestrator.threads), 1)
        self.assertNotEqual(self.orchestrator.threads[0], "MainThread",
                            "判分不得在事件循环所在线程执行")


class AudioAnswerRouteTestCase(unittest.TestCase):
    """语音作答：转写与判分都必须卸载，否则先冻几秒再冻一次。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="stt-offload-")
        self.orchestrator = FakeOrchestrator(seconds=0.3)
        self.transcribe_threads = []
        self.saved = {
            "registry": quiz_routes.registry,
            "save_upload": stt_routes._save_upload,
            "transcribe": stt_routes.transcribe_audio,
            "remove": stt_routes._remove_quietly,
        }
        quiz_routes.registry = FakeRegistry(self.orchestrator)

        async def fake_save_upload(request, config):
            return os.path.join(self.tmpdir, "fake.wav")

        def fake_transcribe(path):
            self.transcribe_threads.append(threading.current_thread().name)
            time.sleep(0.4)
            return "这是识别出来的回答"

        stt_routes._save_upload = fake_save_upload
        stt_routes.transcribe_audio = fake_transcribe
        stt_routes._remove_quietly = lambda path: None

    def tearDown(self):
        quiz_routes.registry = self.saved["registry"]
        stt_routes._save_upload = self.saved["save_upload"]
        stt_routes.transcribe_audio = self.saved["transcribe"]
        stt_routes._remove_quietly = self.saved["remove"]
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_transcribe_and_grade_do_not_block_the_event_loop(self):
        beats, elapsed, raw = measure_liveness(
            lambda: stt_routes.quiz_answer_audio(FakeRequest(query={"sessionid": "s1"}))
        )
        result = parse(raw)
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(result["data"]["text"], "这是识别出来的回答")
        assert_loop_was_alive(self, beats, elapsed)
        self.assertNotEqual(self.transcribe_threads[0], "MainThread",
                            "转写不得在事件循环所在线程执行")
        self.assertNotEqual(self.orchestrator.threads[0], "MainThread",
                            "判分不得在事件循环所在线程执行")


if __name__ == "__main__":
    unittest.main()
