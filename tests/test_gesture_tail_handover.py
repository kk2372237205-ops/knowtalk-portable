"""动作收尾交接：切回默认素材的那一刻不得卡顿。

现场故障（2026-10-27 日志）：

    [手势] count 已完整播放一遍（89 帧），回到默认素材
    帧源已切换，索引重置为 0（当前手势：None）
    paste_back_frame error: list index out of range   × 16
    audio queue underrun: count=1, queued=0           × 数十条

根因有三层，缺一不可：

1. `MuseReal.paste_back_frame` 的非手势分支用裸下标取原帧/人脸框/遮罩，
   而 `musetalk_ke` 的默认素材只有 **1 帧**、手势有 89 帧。切回默认素材时
   队列里还压着一批 idx=1..88 的在途帧 → 逐帧越界。
2. 越界异常被渲染线程的 `except` 吞掉后 `continue`，**该帧的视频与音频一起被丢弃**
   → 接收端欠载，日志刷 `audio queue underrun`。
3. 手势进度由推理线程按批量产出推进，比画面超前若干帧 → 切换时机早于最后一帧
   动作真正播出，观感是"动作快结束时突然收回去"。

本文件覆盖这三层：贴回索引安全（两种模型）、收尾帧数与末帧、失败不丢帧。
"""

import inspect
import copy
import unittest
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace

import cv2
import numpy as np

import avatars.base_avatar as base_avatar
import avatars.musetalk_avatar as musetalk_avatar
import avatars.ultralight_avatar as ultralight_avatar
from avatars.base_avatar import BaseAvatar
from avatars.musetalk_avatar import MuseReal
from avatars.ultralight_avatar import LightReal


# --------------------------------------------------------------------------
# 1. 贴回索引安全
# --------------------------------------------------------------------------

class MusePasteBackIndexTest(unittest.TestCase):
    """默认素材 1 帧、手势 89 帧：任何在途索引都必须安全落位。"""

    def _avatar(self, default_count=1, gesture_count=None):
        avatar = MuseReal.__new__(MuseReal)
        avatar.opt = SimpleNamespace(fps=20, avatar_id='T')
        avatar._init_gesture_state()
        height, width = 24, 16
        hand_box = (2, 2, 10, 10)               # MuseTalk 序：(x1, y1, x2, y2)
        avatar.frame_list_cycle = [
            np.full((height, width, 3), 200, dtype=np.uint8)] * default_count
        avatar.coord_list_cycle = [hand_box] * default_count
        avatar.mask_list_cycle = [
            np.full((height, width, 3), 255, dtype=np.uint8)] * default_count
        avatar.mask_coords_list_cycle = [(0, 0, width, height)] * default_count
        avatar._bind_default_frames(
            avatar.frame_list_cycle, [np.zeros((8, 8, 3), np.uint8)] * default_count,
            avatar.coord_list_cycle)
        if gesture_count:
            frames = [np.full((height, width, 3), 10 + index * 5, dtype=np.uint8)
                      for index in range(gesture_count)]
            extra = {
                'latents': [None] * gesture_count,
                'masks': [np.full((height, width, 3), 255, dtype=np.uint8)] * gesture_count,
                'mask_coords': [(0, 0, width, height)] * gesture_count,
                'coords': [hand_box] * gesture_count,
            }
            avatar._activate('wave', (frames, [None] * gesture_count,
                                     [hand_box] * gesture_count, extra))
        return avatar

    def test_stale_index_on_single_frame_default_does_not_raise(self):
        """复现现场：切回只有 1 帧的默认素材后，idx=88 的在途帧到达。"""
        avatar = self._avatar(default_count=1)
        pred = np.zeros((8, 8, 3), dtype=np.uint8)
        for stale in (1, 5, 40, 88):
            with self.subTest(idx=stale):
                out = avatar.paste_back_frame(pred, stale)
                self.assertIsNotNone(out)
                self.assertEqual(out.shape, (24, 16, 3))

    def test_gesture_index_wraps_within_gesture_sequences(self):
        avatar = self._avatar(default_count=1, gesture_count=3)
        out = avatar.paste_back_frame(np.zeros((8, 8, 3), np.uint8), 4)
        self.assertEqual(out.shape, (24, 16, 3))

    def test_empty_source_returns_none_instead_of_raising(self):
        avatar = MuseReal.__new__(MuseReal)
        avatar._init_gesture_state()
        avatar.frame_list_cycle = []
        avatar.coord_list_cycle = []
        avatar.mask_list_cycle = []
        avatar.mask_coords_list_cycle = []
        avatar._bind_default_frames([], [], [])
        self.assertIsNone(avatar.paste_back_frame(np.zeros((8, 8, 3), np.uint8), 3))


class UltraLightPasteBackIndexTest(unittest.TestCase):
    """wav2lip 系同样不能裸下标：两套素材帧数不同时切回默认素材会越界。"""

    def test_stale_index_does_not_raise(self):
        avatar = LightReal.__new__(LightReal)
        # 帧源重构成"当前生效三件套"后，取帧走 _active_frames；
        # 这里不装配手势状态，显式置 None 让它回退到模型自己的序列。
        avatar._active_frames = None
        avatar._current_gesture = None
        height, width = 200, 120
        avatar.frame_list_cycle = [np.full((height, width, 3), 200, np.uint8)]
        avatar.coord_list_cycle = [(10, 10, 60, 60)]
        avatar.face_list_cycle = [np.full((200, 200, 3), 9, np.uint8)]
        pred = np.zeros((160, 160, 3), dtype=np.uint8)
        for stale in (1, 7, 88):
            with self.subTest(idx=stale):
                out = avatar.paste_back_frame(pred, stale)
                self.assertIsNotNone(out)
                self.assertEqual(out.shape, (height, width, 3))

    def test_empty_source_returns_none(self):
        avatar = LightReal.__new__(LightReal)
        avatar._active_frames = None
        avatar._current_gesture = None
        avatar.frame_list_cycle = []
        avatar.coord_list_cycle = []
        avatar.face_list_cycle = []
        self.assertIsNone(avatar.paste_back_frame(np.zeros((160, 160, 3), np.uint8), 2))


# --------------------------------------------------------------------------
# 2. 收尾交接：帧数与末帧
# --------------------------------------------------------------------------

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


def _render_avatar(gesture_count=6, default_count=1, fps=50,
                   face_coords=(2, 8, 2, 6)):
    """装配一个只够跑 process_frames 的替身；默认素材像素值 250，手势从 10 起递增。"""
    avatar = BaseAvatar.__new__(BaseAvatar)
    avatar.opt = SimpleNamespace(fps=fps, batch_size=2, sessionid='s1', avatar_id='T')
    avatar.sample_rate = 16000
    avatar.chunk = 16000 // (fps * 2)
    avatar.sessionid = 's1'
    avatar.batch_size = 2
    avatar.speaking = False
    avatar.speech_request_id = 0
    avatar.playback_paused = False
    avatar.custom_index = {}
    avatar.custom_img_cycle = {}
    avatar.custom_audio_cycle = {}
    avatar.custom_audio_index = {}
    avatar.res_frame_queue = Queue(8)
    avatar.frame_list_cycle = [np.full((24, 16, 3), 250, np.uint8)] * default_count
    avatar.face_list_cycle = [np.full((8, 8, 3), 250, np.uint8)] * default_count
    avatar.coord_list_cycle = [(0, 0, 16, 24)] * default_count
    avatar._init_gesture_state()
    avatar._bind_default_frames(
        avatar.frame_list_cycle, avatar.face_list_cycle, avatar.coord_list_cycle)
    avatar.output = _RecordingOutput()
    avatar.record_video_data = lambda frame: None
    avatar.record_audio_data = lambda frame: None
    frames = [np.full((24, 16, 3), 10 + index * 10, np.uint8)
              for index in range(gesture_count)]
    avatar._activate('wave', (frames, avatar.face_list_cycle,
                             [face_coords] * gesture_count))
    return avatar


def _silence(avatar):
    from avatars.base_avatar import AudioFrameData
    return AudioFrameData(data=np.zeros(avatar.chunk, dtype=np.float32), type=1)


class TailHandoverTest(unittest.TestCase):
    """真跑 process_frames：动作必须整段播完再交回默认素材。"""

    def _run(self, avatar, seconds):
        quit_event = Event()
        Thread(target=lambda: (__import__('time').sleep(seconds), quit_event.set()),
               daemon=True).start()
        avatar.process_frames(quit_event)
        return [int(frame[0, 0, 0]) for frame in avatar.output.video
                if frame is not None and frame.ndim == 3]

    def test_exactly_n_gesture_frames_then_default(self):
        """6 帧手势：前 6 个输出帧必须依次是手势 0..5 帧，第 7 帧起回到默认素材。"""
        avatar = _render_avatar(gesture_count=6)
        silent = _silence(avatar)
        # 只喂 1 帧，其余靠补位推进——正是现场"推理落后于输出"的情形
        avatar.res_frame_queue.put((None, [silent, silent], 0))
        values = self._run(avatar, 0.35)

        self.assertGreaterEqual(len(values), 8, '渲染线程没跑够帧：%s' % values)
        self.assertEqual(values[:6], [10, 20, 30, 40, 50, 60],
                         '手势帧序被跳帧或提前切走：%s' % values[:8])
        self.assertEqual(values[6], 250, '第 7 帧应已回到默认素材：%s' % values[:8])

    def test_last_output_frame_is_gesture_tail(self):
        """末帧（收尾动作）必须播出来，不能被提前切回默认素材吃掉。"""
        avatar = _render_avatar(gesture_count=4)
        silent = _silence(avatar)
        avatar.res_frame_queue.put((None, [silent, silent], 0))
        values = self._run(avatar, 0.3)
        self.assertIn(40, values[:6], '手势末帧丢了：%s' % values[:8])

    def test_progress_counter_resets_after_handover(self):
        avatar = _render_avatar(gesture_count=3)
        silent = _silence(avatar)
        avatar.res_frame_queue.put((None, [silent, silent], 0))
        self._run(avatar, 0.25)
        self.assertIsNone(avatar._current_gesture)
        self.assertEqual(avatar._gesture_frames_played, 0)

    def test_every_output_frame_has_audio(self):
        """不得因为补位/切源丢帧：视频帧数与音频推送必须成对推进。"""
        avatar = _render_avatar(gesture_count=5)
        silent = _silence(avatar)
        avatar.res_frame_queue.put((None, [silent, silent], 0))
        self._run(avatar, 0.3)
        self.assertEqual(len(avatar.output.video), len(avatar.output.audio) // 2,
                         '视频帧与音频对不上，说明有帧被丢掉了')


# --------------------------------------------------------------------------
# 3. 贴回失败不丢帧 + 进度归属
# --------------------------------------------------------------------------

class PasteBackFailureTest(unittest.TestCase):
    def test_failure_does_not_drop_the_frame(self):
        avatar = _render_avatar(gesture_count=4)
        from avatars.base_avatar import AudioFrameData

        def boom(*_args, **_kwargs):
            raise IndexError('list index out of range')

        avatar.paste_back_frame = boom
        speech = AudioFrameData(data=np.full(avatar.chunk, 0.1, dtype=np.float32), type=0)
        for index in range(4):
            avatar.res_frame_queue.put((np.zeros((8, 8, 3), np.uint8), [speech, speech], index))

        quit_event = Event()
        Thread(target=lambda: (__import__('time').sleep(0.25), quit_event.set()),
               daemon=True).start()
        avatar.process_frames(quit_event)

        self.assertEqual(len(avatar.output.video), len(avatar.output.audio) // 2)
        self.assertTrue(all(frame is not None for frame in avatar.output.video),
                        '贴回失败时推了空帧')
        self.assertGreaterEqual(len(avatar.output.video), 4)


class ProgressOwnershipTest(unittest.TestCase):
    """进度归渲染线程：推理线程超前计数会让切换早于最后一帧动作。"""

    def test_inference_never_advances_progress(self):
        source = inspect.getsource(BaseAvatar.inference)
        self.assertNotIn('self.advance_gesture(', source)

    def test_render_advances_once_per_output_frame(self):
        source = inspect.getsource(BaseAvatar.process_frames)
        # 正常出帧路径 + 补位路径各一处
        self.assertGreaterEqual(source.count('self.advance_gesture(1)'), 2)
        self.assertIn('if not self.playback_paused:', source)

    def test_frame_index_uses_render_progress_during_gesture(self):
        source = inspect.getsource(BaseAvatar.process_frames)
        self.assertIn('frame_idx = self._gesture_frames_played', source)

    def test_avatar_modules_do_not_index_sequences_with_bare_subscript(self):
        for module in (musetalk_avatar, ultralight_avatar):
            source = inspect.getsource(module)
            for name in ('coord_list_cycle', 'mask_list_cycle',
                         'mask_coords_list_cycle', 'frame_list_cycle'):
                self.assertNotIn('self.%s[idx]' % name, source,
                                 '%s 里仍有裸下标 self.%s[idx]' % (module.__name__, name))


if __name__ == '__main__':
    unittest.main()
