# -*- coding: utf-8 -*-
"""暂停播放：画面必须回到**形象自身**的默认素材帧，而不是当前帧源。

现场问题（2026-10-27）：提问模式抛出题目后自动暂停，画面上的嘴还在动。
原因有两层：

1. 客户端只静音了本地音轨，**从未**把暂停态提交给 `POST /api/playback`
   ——服务端不知情，照旧推理并推送说话帧（前端源码守卫在
   `tests/js/quiz-pause-on-question.test.js`）。
2. 服务端的暂停分支取的是 `_frame_at()`（**当前帧源**）：动作播放期间暂停会停在
   "手臂抬起"的动作帧上，而不是形象的默认姿态。

对应 openspec/changes/restore-default-frame-on-pause/。
"""

import unittest
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace

import numpy as np

from avatars.base_avatar import BaseAvatar

DEFAULT_VALUE = 250      # 形象默认素材（第 0 帧 / 第 1 帧为 251、252）
SPEAK_VALUE = 99         # 贴回后的说话帧
GESTURE_STEP = 10        # 手势第 i 帧的像素值 = 10 + i*10


class _RecordingOutput:
    def __init__(self):
        self.video = []
        self.audio = []

    def start(self):
        pass

    def stop(self):
        pass

    def push_video_frame(self, frame):
        self.video.append(None if frame is None else np.array(frame, copy=True))

    def push_audio_frame(self, frame, userdata=None):
        self.audio.append(len(frame))


def _audio(avatar, speaking=True):
    """渲染线程按 `type` 判说话/静音：type=0 才走贴回（paste_back_frame）。"""
    from avatars.base_avatar import AudioFrameData
    data = (np.full(avatar.chunk, 0.01, dtype=np.float32) if speaking
            else np.zeros(avatar.chunk, dtype=np.float32))
    return AudioFrameData(data=data, type=(0 if speaking else 1))


def _avatar(default_count=3, gesture_count=6, fps=50, paused=False, gesture=True):
    """装配一个只够跑 `process_frames` 的替身（不加载模型）。"""
    avatar = BaseAvatar.__new__(BaseAvatar)
    avatar.opt = SimpleNamespace(fps=fps, batch_size=2, sessionid='s1', avatar_id='T')
    avatar.sample_rate = 16000
    avatar.chunk = 16000 // (fps * 2)
    avatar.sessionid = 's1'
    avatar.batch_size = 2
    avatar.speaking = False
    avatar.speech_request_id = 0
    avatar.playback_paused = paused
    avatar.custom_index = {}
    avatar.custom_img_cycle = {}
    avatar.custom_audio_cycle = {}
    avatar.custom_audio_index = {}
    avatar.res_frame_queue = Queue(64)
    avatar.frame_list_cycle = [np.full((24, 16, 3), DEFAULT_VALUE + index, np.uint8)
                               for index in range(default_count)]
    avatar.face_list_cycle = [np.full((8, 8, 3), DEFAULT_VALUE, np.uint8)] * default_count
    avatar.coord_list_cycle = [(0, 0, 16, 24)] * default_count
    avatar._init_gesture_state()
    avatar._bind_default_frames(
        avatar.frame_list_cycle, avatar.face_list_cycle, avatar.coord_list_cycle)
    avatar.output = _RecordingOutput()
    avatar.record_video_data = lambda frame: None
    avatar.record_audio_data = lambda frame: None
    # 贴回结果统一为 SPEAK_VALUE，便于区分「推理帧 / 默认帧 / 手势帧」
    avatar.paste_back_frame = lambda pred, idx: np.full((24, 16, 3), SPEAK_VALUE, np.uint8)
    if gesture:
        frames = [np.full((24, 16, 3), GESTURE_STEP + index * GESTURE_STEP, np.uint8)
                  for index in range(gesture_count)]
        avatar._activate('wave', (frames, avatar.face_list_cycle,
                                  [(0, 0, 16, 24)] * gesture_count))
    return avatar


def _values(avatar):
    return [int(frame[0, 0, 0]) for frame in avatar.output.video if frame is not None]


def _run(avatar, seconds, flip_to=None, flip_after=0.1):
    """跑 `seconds` 秒渲染线程；`flip_to` 可在中途切换暂停态。"""
    quit_event = Event()
    Thread(target=lambda: (_sleep(seconds), quit_event.set()), daemon=True).start()
    if flip_to is not None:
        def flip():
            _sleep(flip_after)
            avatar.playback_paused = flip_to
        Thread(target=flip, daemon=True).start()
    avatar.process_frames(quit_event)


def _sleep(seconds):
    import time
    time.sleep(seconds)


class PausedFrameSourceTest(unittest.TestCase):
    """真跑 process_frames：暂停期间的每一帧都必须是形象的默认帧。"""

    def _feed(self, avatar, count):
        """喂「说话」帧：带推理结果 + type=0 音频，渲染线程才会走贴回。"""
        speech = _audio(avatar, speaking=True)
        for index in range(count):
            pred = np.zeros((8, 8, 3), np.uint8)
            avatar.res_frame_queue.put((pred, [speech, speech], index))

    def test_paused_output_is_avatar_default_frame(self):
        """对照：不暂停时输出的是手势帧/说话帧。"""
        avatar = _avatar(paused=False)
        self._feed(avatar, 4)
        _run(avatar, 0.25)
        values = _values(avatar)
        self.assertTrue(values, '渲染线程没跑起来')
        self.assertIn(SPEAK_VALUE, values, '未暂停时应该有贴回的说话帧：%s' % values)

    def test_paused_output_ignores_gesture_source(self):
        """动作播放期间暂停：必须回形象默认帧，不能停在动作帧上。"""
        avatar = _avatar(paused=True)
        self._feed(avatar, 4)
        _run(avatar, 0.25)
        values = _values(avatar)
        self.assertTrue(values, '渲染线程没跑起来')
        self.assertTrue(set(values) <= {DEFAULT_VALUE},
                        '暂停期间混进了非默认帧：%s' % values[:8])

    def test_paused_output_is_static_across_multi_frame_avatar(self):
        """多帧形象的素材也不能跟着推理进度继续循环——嘴闭着身体却在动。"""
        avatar = _avatar(default_count=3, paused=True)
        self._feed(avatar, 6)
        _run(avatar, 0.25)
        values = _values(avatar)
        self.assertTrue(values)
        self.assertEqual(set(values), {DEFAULT_VALUE},
                         '暂停画面必须固定在默认素材第 0 帧：%s' % values[:8])

    def test_resume_restores_playing_frames(self):
        """恢复后必须继续出说话帧，不能永远定格在默认帧。"""
        avatar = _avatar(paused=True)
        self._feed(avatar, 20)
        _run(avatar, 0.35, flip_to=False, flip_after=0.12)
        values = _values(avatar)
        self.assertEqual(values[0], DEFAULT_VALUE, '起始应是暂停帧：%s' % values[:4])
        self.assertIn(SPEAK_VALUE, values, '恢复后没有回到说话帧：%s' % values)

    def test_gesture_progress_is_frozen_while_paused(self):
        """暂停期间动作进度不得推进——否则恢复后动作被吃掉一段。"""
        avatar = _avatar(paused=True, gesture_count=6)
        self._feed(avatar, 6)
        _run(avatar, 0.25)
        self.assertEqual(avatar._gesture_frames_played, 0, '暂停时动作进度被推进了')
        self.assertEqual(avatar._current_gesture, 'wave', '暂停不该结束动作')

    def test_paused_frame_does_not_mutate_default_asset(self):
        """推给下游的默认帧必须是副本：WebRTC 编码会就地改数据。"""
        avatar = _avatar(paused=True)
        self._feed(avatar, 2)
        _run(avatar, 0.2)
        values = _values(avatar)
        self.assertTrue(values)
        self.assertEqual(int(avatar.frame_list_cycle[0][0, 0, 0]), DEFAULT_VALUE,
                         '形象素材被下游改脏了')


class BaseFrameLookupTest(unittest.TestCase):
    """`_base_frame_at` 必须无视当前帧源。"""

    def test_base_frame_ignores_active_gesture(self):
        avatar = _avatar(default_count=3, gesture=True)
        self.assertEqual(int(avatar._frame_at(0)[0, 0, 0]), GESTURE_STEP,
                         '当前帧源应已切到手势')
        self.assertEqual(int(avatar._base_frame_at(0)[0, 0, 0]), DEFAULT_VALUE)

    def test_base_frame_falls_back_to_frame_list_cycle(self):
        """MuseTalk 不调 `_bind_default_frames`，要能回退到模型自己的序列。"""
        avatar = _avatar(default_count=2)
        avatar._default_frames = None
        self.assertEqual(int(avatar._base_frame_at(0)[0, 0, 0]), DEFAULT_VALUE)

    def test_base_frame_is_index_safe(self):
        avatar = _avatar(default_count=2)
        self.assertIs(avatar._base_frame_at(88), avatar.frame_list_cycle[0])


if __name__ == '__main__':
    unittest.main()
