# -*- coding: utf-8 -*-
"""会话拆除死锁的回归守卫（变更 fix-session-teardown-deadlock）。

现场故障：断开连接后再点「开始连接」连不上。`py-spy dump` 打出闭环——
音频队列无界 put 卡住渲染线程 → 推理/特征队列依次塞满 → `render()` 回不到
quit 判断 → `HumanPlayer._stop()` 在事件循环线程里无界 join → 整个服务
不再响应任何 HTTP（连静态文件都超时）。

这里钉住修复的三件事：队列写入有界、线程 join 有界、拆除顺序先"叫醒"再"停止"。
"""

import pathlib
import queue
import threading
import time
import unittest

import numpy as np

from server.session_manager import session_manager
from server.webrtc import TEARDOWN_JOIN_TIMEOUT, HumanPlayer

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class _FakeOutput:
    _player = None

    def stop(self):
        pass


class _FakeContainer:
    """HumanPlayer 只需要容器能提供 output 与 is_speaking。"""

    def __init__(self):
        self.output = _FakeOutput()

    def is_speaking(self):
        return False

    def render(self, quit_event):  # 供 media-player 线程调用
        pass


class _StuckContainer(_FakeContainer):
    """渲染永不退出：模拟队列拥塞时线程卡死。"""

    def render(self, quit_event):
        threading.Event().wait()   # 永不返回，也不响应 quit_event


class PushAudioBoundedTestCase(unittest.TestCase):
    def test_full_audio_queue_does_not_block_producer(self):
        """连接断开后没人消费音频队列：写入必须有界返回，不能卡死渲染线程。"""
        player = HumanPlayer(_FakeContainer())
        audio_queue = player._HumanPlayer__audio._queue
        while not audio_queue.full():
            audio_queue.put(("frame", None))

        frame = np.zeros(320, dtype=np.int16)
        started = time.perf_counter()
        player.push_audio(frame, None)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 1.5, "队列满时 push_audio 必须限时返回")
        self.assertTrue(audio_queue.full(), "丢最旧腾位后仍应保持有数据")

    def test_push_audio_writes_normally_when_queue_has_room(self):
        player = HumanPlayer(_FakeContainer())
        queue_ref = player._HumanPlayer__audio._queue
        player.push_audio(np.zeros(320, dtype=np.int16), {"k": 1})
        self.assertEqual(queue_ref.qsize(), 1)
        _, eventpoint = queue_ref.get_nowait()
        self.assertEqual(eventpoint, {"k": 1})


class StopJoinBoundedTestCase(unittest.TestCase):
    def test_stop_returns_even_if_render_thread_hangs(self):
        """`_stop` 跑在事件循环线程上：渲染线程不退出时也必须按时返回。"""
        player = HumanPlayer(_StuckContainer())
        track = player.audio
        player._start(track)
        time.sleep(0.05)   # 让 media-player 线程真正进入 render

        started = time.perf_counter()
        player._stop(track)
        elapsed = time.perf_counter() - started

        self.assertLess(
            elapsed, TEARDOWN_JOIN_TIMEOUT + 1.5,
            "join 必须带超时，不得无界等待卡死的渲染线程")
        self.assertIsNone(player._HumanPlayer__thread, "放弃等待后应清空线程引用")

    def test_worker_thread_is_daemon(self):
        """拆除超时后残线程不得阻止进程退出。"""
        player = HumanPlayer(_FakeContainer())
        track = player.audio
        player._start(track)
        try:
            thread = player._HumanPlayer__thread
            self.assertIsNotNone(thread)
            self.assertTrue(thread.daemon, "渲染线程必须是守护线程")
        finally:
            player._stop(track)


class RemoveSessionOrderTestCase(unittest.TestCase):
    def test_flush_happens_before_output_stop(self):
        """先清队列把阻塞的生产者放出来，再 stop（stop 内部会 join 它们）。"""
        calls = []

        class FakeAvatar:
            quit_event = threading.Event()

            def flush_talk(self):
                calls.append("flush:" + ("quit" if self.quit_event.is_set() else "alive"))

            class output:
                @staticmethod
                def stop():
                    calls.append("stop")

        fake = FakeAvatar()
        session_manager.sessions["deadlock-test"] = fake
        try:
            session_manager.remove_session("deadlock-test")
        finally:
            session_manager.sessions.pop("deadlock-test", None)

        self.assertEqual(
            calls, ["flush:quit", "stop"],
            "必须先置 quit、再清队列，最后才 stop（stop 里的 join 才可能及时返回）")

    def test_session_is_removed_from_registry(self):
        class FakeAvatar:
            quit_event = threading.Event()

        session_manager.sessions["deadlock-test-2"] = FakeAvatar()
        session_manager.remove_session("deadlock-test-2")
        self.assertFalse(session_manager.has_session("deadlock-test-2"))


class SourceGuardTestCase(unittest.TestCase):
    """写回"无界阻塞"的防线：源码级断言，防止以后被改回去。"""

    def test_push_audio_is_bounded(self):
        src = read("server/webrtc.py")
        body = src.split("def push_audio", 1)[1].split("def get_buffer_size", 1)[0]
        self.assertIn("put_capped", body, "音频写入必须走有界写入")
        self.assertNotIn("block=True", body, "不得用无超时的阻塞写入")

    def test_stop_join_has_timeout(self):
        src = read("server/webrtc.py")
        body = src.split("def _stop", 1)[1].split("def __log_debug", 1)[0]
        self.assertIn("join(timeout=", body, "join 必须带超时")
        self.assertNotIn("join()", body, "不得无界 join")

    def test_inference_res_frame_writes_are_bounded(self):
        src = read("avatars/base_avatar.py")
        body = src.split("def inference", 1)[1].split("def process_frames", 1)[0]
        self.assertIn("put_capped", body)
        self.assertNotIn("self.res_frame_queue.put(", body, "推理结果不得用裸 put")

    def test_inference_audio_wait_is_interruptible(self):
        src = read("avatars/base_avatar.py")
        body = src.split("def inference", 1)[1].split("def process_frames", 1)[0]
        self.assertIn("AUDIO_WAIT_TIMEOUT", body, "等待音频必须限时")
        self.assertIn("quit_event.is_set()", body, "限时等待要配合 quit 检查")

    def test_render_joins_are_bounded(self):
        src = read("avatars/base_avatar.py")
        body = src.split("def render", 1)[1]
        self.assertIn("join(timeout=", body, "render 对工作线程的 join 必须带超时")
        self.assertNotIn("infer_thread.join()", body)
        self.assertNotIn("process_thread.join()", body)

    def test_mel_queue_writes_are_bounded(self):
        src = read("avatars/audio_features/mel.py")
        self.assertIn("put_capped(self.feat_queue", src, "特征队列写入必须有界")
        self.assertIn("put_capped(self.output_queue", src, "输出队列写入必须有界")
        self.assertNotIn("self.feat_queue.put(", src)


if __name__ == "__main__":
    unittest.main()
