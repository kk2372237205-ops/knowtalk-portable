# -*- coding: utf-8 -*-
"""音频轨空转计数的语义测试。

对应 openspec/changes/clarify-audio-underrun-reporting/tasks.md。

核心断言：判据是**会话是否正在朗读**，不是"是否收到过音频帧"。
第一版实现用了后者，实测把"首帧到达之后、真正开口之前"的长空闲也误报成中断粮
（刷出二十多条 count=1），因此这里专门有一条覆盖该情形的用例。
"""

import asyncio
import logging
import unittest

from server.webrtc import AUDIO_PREROLL_PACKETS, PlayerStreamTrack


class FakePlayer(object):
    """最小玩家替身：只提供音频轨判定所需的朗读状态。"""

    def __init__(self, speaking=False):
        self.speaking = speaking
        self.events = []

    def _start(self, track):
        pass

    def is_speaking(self):
        return self.speaking

    def notify(self, eventpoint):
        self.events.append(eventpoint)


class NoStatePlayer(object):
    """不提供朗读状态的玩家，用于验证"无法判定时保留原信号"。"""

    def _start(self, track):
        pass


def make_track(player):
    track = PlayerStreamTrack(player, 'audio')
    # next_timestamp 要求轨道处于 live 状态；直接置位，避免为测计数搭整个会话。
    track._readyState = 'live'
    return track


def drain(track, times):
    """连续 recv() 若干次。"""
    loop = asyncio.new_event_loop()
    try:
        return [loop.run_until_complete(track.recv()) for _ in range(times)]
    finally:
        loop.close()


def frames_queued(track, count):
    """往轨道队列里放 count 个真实音频帧。"""
    import numpy as np
    from av import AudioFrame

    for _ in range(count):
        audio = np.zeros((1, 320), dtype=np.int16)
        frame = AudioFrame.from_ndarray(audio, layout='mono', format='s16')
        frame.sample_rate = 16000
        track._queue.put((frame, None))


def collect_logs(level=logging.INFO):
    """收集日志的上下文管理器。

    刻意不用 `assertLogs`：它要求在断言区间内**至少产生一条**日志，
    而本文件多处要断言"不该有这一类日志"，用它会反过来失败。
    """
    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Collect(level)
    logger = logging.getLogger('utils.logger')
    logger.addHandler(handler)
    return records, handler, logger


def drain_collecting(track, times):
    records, handler, logger = collect_logs()
    logger.addHandler(handler)
    try:
        drain(track, times)
    finally:
        logger.removeHandler(handler)
    return records, [r.getMessage() for r in records]


class IdleStallTestCase(unittest.TestCase):
    def test_audio_end_event_reaches_player_only_when_last_packet_is_consumed(self):
        player = FakePlayer(speaking=True)
        track = make_track(player)
        import numpy as np
        from av import AudioFrame

        audio = np.zeros((1, 320), dtype=np.int16)
        frame = AudioFrame.from_ndarray(audio, layout='mono', format='s16')
        frame.sample_rate = 16000
        event = {'status': 'end', 'gesture_after': 'wave_one'}
        track._queue.put((frame, event))

        self.assertEqual(player.events, [])
        drain(track, 1)
        self.assertEqual(player.events, [event])

    def test_stalls_while_not_speaking_are_not_underruns(self):
        track = make_track(FakePlayer(speaking=False))
        records, messages = drain_collecting(track, 3)
        self.assertEqual(track.audio_underruns, 0, '未朗读时的空转不得计入中断粮')
        self.assertGreater(track.audio_idle_stalls, 0)
        self.assertEqual([m for m in messages if 'underrun' in m], [],
                         '未朗读时的空转不得以欠载措辞告警')

    def test_idle_stalls_after_first_frame_are_still_not_underruns(self):
        """第一版实现漏掉的正是这一条：收到过帧 ≠ 正在说话。"""
        player = FakePlayer(speaking=False)
        track = make_track(player)
        frames_queued(track, 1)
        drain(track, 1)                     # 收到过真实音频帧
        self.assertTrue(track._produced_real_audio)
        before = track.audio_underruns
        records, messages = drain_collecting(track, 3)
        self.assertEqual(track.audio_underruns, before, '仍未朗读，不得计入中断粮')
        self.assertEqual([m for m in messages if 'underrun' in m], [], messages)

    def test_idle_wait_is_reported_when_first_frame_arrives(self):
        track = make_track(FakePlayer(speaking=False))
        records, handler, logger = collect_logs()
        logger.addHandler(handler)
        try:
            drain(track, 3)          # 先空闲几帧
            frames_queued(track, 1)
            drain(track, 1)          # 首帧真实音频到达
        finally:
            logger.removeHandler(handler)
        self.assertTrue(track._produced_real_audio)
        idle_lines = [r.getMessage() for r in records if '空转' in r.getMessage()]
        self.assertTrue(idle_lines, [r.getMessage() for r in records])
        self.assertNotIn('underrun', ' '.join(idle_lines))


class SpeakingUnderrunTestCase(unittest.TestCase):
    def test_stalls_while_speaking_are_counted_and_warned(self):
        track = make_track(FakePlayer(speaking=True))
        records, messages = drain_collecting(track, 2)
        self.assertGreater(track.audio_underruns, 0, '朗读中的空转必须计数')
        warned = [m for m in messages if 'underrun' in m]
        self.assertTrue(warned, messages)
        self.assertEqual(track.audio_idle_stalls, 0)

    def test_transition_from_idle_to_speaking_starts_counting(self):
        player = FakePlayer(speaking=False)
        track = make_track(player)
        drain(track, 2)
        self.assertEqual(track.audio_underruns, 0)
        self.assertGreater(track.audio_idle_stalls, 0)
        player.speaking = True
        drain(track, 2)
        self.assertGreater(track.audio_underruns, 0, '进入朗读后必须开始计数')

    def test_counter_resets_after_audio_resumes(self):
        player = FakePlayer(speaking=True)
        track = make_track(player)
        frames_queued(track, 1)
        drain(track, 1)                      # 恢复
        drain(track, 2)                      # 中断，计数 > 0
        self.assertGreater(track.audio_underruns, 0)
        frames_queued(track, 1)
        drain(track, 1)                      # 再次恢复
        self.assertEqual(track.audio_underruns, 0, '恢复后计数必须重置')
        drain(track, 2)
        self.assertEqual(track.audio_underruns, 2,
                         '下一次独立中断必须从 1 重新开始（这里饿了 2 帧）')


class AudioJitterBufferTestCase(unittest.TestCase):
    def _frame(self, value=0):
        import numpy as np
        from av import AudioFrame

        pcm = np.full((1, 320), value, dtype=np.int16)
        frame = AudioFrame.from_ndarray(pcm, layout='mono', format='s16')
        frame.sample_rate = 16000
        return frame

    def test_speech_start_prerolls_before_first_packet(self):
        player = FakePlayer(speaking=True)
        track = make_track(player)
        for index in range(AUDIO_PREROLL_PACKETS):
            eventpoint = {'status': 'start'} if index == 0 else None
            track._queue.put((self._frame(index), eventpoint))

        frame = drain(track, 1)[0]

        self.assertEqual(frame.samples, 320)
        self.assertEqual(track.audio_prerolls, 1)
        self.assertEqual(len(track._audio_preroll), AUDIO_PREROLL_PACKETS - 1)
        self.assertEqual(track.audio_underruns, 0)
        self.assertEqual(player.events, [{'status': 'start'}])

    def test_speaking_waits_for_a_late_packet_before_inserting_silence(self):
        player = FakePlayer(speaking=True)
        track = make_track(player)
        loop = asyncio.new_event_loop()
        try:
            async def produce():
                await asyncio.sleep(0.025)
                track._queue.put((self._frame(123), None))

            loop.create_task(produce())
            frame = loop.run_until_complete(track.recv())
        finally:
            loop.close()

        self.assertTrue(track._produced_real_audio)
        self.assertEqual(track.audio_underruns, 0)
        self.assertEqual(frame.to_ndarray()[0, 0], 123)

    def test_clear_audio_buffer_also_discards_preroll(self):
        player = FakePlayer(speaking=True)
        track = make_track(player)
        track._audio_preroll.append((self._frame(7), {'status': 'start'}))
        track._queue.put((self._frame(8), None))

        track.clear_audio_buffer()

        self.assertEqual(len(track._audio_preroll), 0)
        self.assertTrue(track._queue.empty())


class UnknownSpeakingStateTestCase(unittest.TestCase):
    def test_player_without_state_keeps_the_old_signal(self):
        """无法判定朗读状态时按"可能正在朗读"处理，不能静默丢掉告警。"""
        track = make_track(NoStatePlayer())
        records, messages = drain_collecting(track, 2)
        self.assertGreater(track.audio_underruns, 0)
        self.assertTrue([m for m in messages if 'underrun' in m], messages)

    def test_exception_in_state_check_falls_back_to_old_signal(self):
        class ExplodingPlayer(FakePlayer):
            def is_speaking(self):
                raise RuntimeError('会话状态不可用')

        track = make_track(ExplodingPlayer())
        drain(track, 2)
        self.assertGreater(track.audio_underruns, 0)


class SilenceFrameTestCase(unittest.TestCase):
    def test_silence_frame_keeps_sample_rate_and_shape(self):
        track = make_track(FakePlayer(speaking=False))
        frame = drain(track, 1)[0]
        self.assertEqual(frame.sample_rate, 16000)
        self.assertEqual(frame.samples, 320)

    def test_public_api_unchanged(self):
        from server.webrtc import HumanPlayer

        track = make_track(FakePlayer(speaking=False))
        self.assertEqual(track.kind, 'audio')
        self.assertEqual(track._queue.maxsize, 100)
        self.assertTrue(hasattr(HumanPlayer, 'get_audio_buffer_size'))
        self.assertTrue(hasattr(HumanPlayer, 'is_speaking'))


if __name__ == '__main__':
    unittest.main()
