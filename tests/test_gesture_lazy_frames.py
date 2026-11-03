# -*- coding: utf-8 -*-
"""手势全帧按需解码 + 缺帧补位，目标是"带动作时不卡"。

两个来源：
- 一套动作上百张 538×1080 全帧常驻要 150~200MB，会话留不住一讲课用到的所有动作，
  讲到中间就要淘汰重载 → 卡一下。改成用谁解谁（单帧约 0.8ms）后单套只占几 MB。
- 推理约 19fps、输出 20fps，必然周期性缺帧。缺帧时冻结上一帧会让手臂一顿一顿，
  直接换动作原始帧又会让嘴闪回闭合；补的是"新手势帧 + 贴回上一帧的脸"。
"""

import inspect
import os
import pickle
import shutil
import tempfile
import time
import unittest
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

import avatars.base_avatar as base_avatar
from avatars.base_avatar import AudioFrameData, BaseAvatar, splice_face
from utils import gestures as g


def write_png(path, width=32, height=48, value=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image = np.full((height, width, 3), value, dtype=np.uint8)
    cv2.imwrite(path, image)


def make_gesture(root, avatar_id='Mus', gesture_id='wave', frames=6):
    base = os.path.join(root, avatar_id, 'gestures', gesture_id)
    for index in range(frames):
        write_png(os.path.join(base, 'full_imgs', '%08d.png' % index), value=index)
        write_png(os.path.join(base, 'face_imgs', '%08d.png' % index), 16, 16)
    with open(os.path.join(base, 'coords.pkl'), 'wb') as handle:
        pickle.dump([(0, 10, 0, 10)] * frames, handle)
    return base


class LazyFramesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='lazy-frames-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.paths = []
        for index in range(5):
            path = os.path.join(self.tmp, '%08d.png' % index)
            write_png(path, value=index * 10)
            self.paths.append(path)

    def test_len_and_modulo_index(self):
        seq = g.LazyFrames(self.paths)
        self.assertEqual(len(seq), 5)
        self.assertEqual(int(seq[0][0, 0, 0]), 0)
        # 越界按取模取用，而不是抛异常打断渲染线程
        self.assertEqual(int(seq[7][0, 0, 0]), 20)

    def test_only_requested_frames_are_decoded(self):
        """加载不再把整套帧解进内存——只解用到的那几帧。"""
        calls = []
        real_imread = cv2.imread

        def counting_imread(path, *args, **kwargs):
            calls.append(path)
            return real_imread(path, *args, **kwargs)

        with mock.patch.object(cv2, 'imread', counting_imread):
            seq = g.LazyFrames(self.paths)
            self.assertEqual(calls, [])
            seq[2]
            self.assertEqual(len(calls), 1)

    def test_cache_size_bounds_decodes(self):
        calls = []
        real_imread = cv2.imread

        def counting_imread(path, *args, **kwargs):
            calls.append(path)
            return real_imread(path, *args, **kwargs)

        with mock.patch.object(cv2, 'imread', counting_imread):
            seq = g.LazyFrames(self.paths, cache_size=2)
            for index in [0, 0, 0, 1, 1, 2, 3]:
                seq[index]
        # 0 与 1 命中缓存，2、3 各解一次
        self.assertEqual(len(calls), 4)

    def test_resample_is_lazy(self):
        seq = g.LazyFrames(self.paths)
        calls = []
        real_imread = cv2.imread

        def counting_imread(path, *args, **kwargs):
            calls.append(path)
            return real_imread(path, *args, **kwargs)

        with mock.patch.object(cv2, 'imread', counting_imread):
            widened = seq.resample([0, 0, 1, 1, 2, 2, 3, 3, 4, 4])
            self.assertEqual(calls, [])
        self.assertEqual(len(widened), 10)
        self.assertEqual(int(widened[4][0, 0, 0]), 20)

    def test_size_scales_on_decode(self):
        seq = g.LazyFrames(self.paths, size=(16, 24))
        self.assertEqual(seq[1].shape, (24, 16, 3))
        self.assertEqual(seq.size, (16, 24))

    def test_missing_file_raises_gesture_error(self):
        seq = g.LazyFrames([os.path.join(self.tmp, 'nope.png')])
        with self.assertRaises(g.GestureError):
            seq[0]

    def test_load_gesture_returns_lazy_sequence(self):
        tmp = tempfile.mkdtemp(prefix='lazy-gesture-')
        self.addCleanup(shutil.rmtree, tmp, True)
        patcher = mock.patch.object(g, 'avatars_root', lambda data_dir=None: tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        make_gesture(tmp, frames=6)
        frames, faces, coords = g.load_gesture('Mus', 'wave', data_dir=tmp)
        self.assertIsInstance(frames, g.LazyFrames)
        self.assertEqual(len(frames), 6)
        self.assertEqual(len(faces), 6)
        self.assertEqual(len(coords), 6)
        self.assertEqual(frames[3].shape[:2], (48, 32))


class SpliceFaceTest(unittest.TestCase):
    """缺帧补位：手臂用新帧、人脸用上一张已合成帧。"""

    def make(self, value, size=(40, 60)):
        return np.full((size[1], size[0], 3), value, dtype=np.uint8)

    def test_face_rect_taken_from_previous_frame(self):
        new = self.make(10)
        prev = self.make(200)
        out = splice_face(new, prev, (10, 20, 30, 40))
        self.assertEqual(int(out[25, 15, 0]), 200, '人脸矩形内必须沿用上一帧（保嘴型）')
        self.assertEqual(int(out[5, 5, 0]), 10, '人脸外必须用新帧（手臂要继续动）')
        self.assertEqual(int(new[25, 15, 0]), 10, 'MUST NOT 修改入参（可能是缓存帧）')

    def test_rect_is_clipped_to_frame(self):
        new = self.make(10)
        prev = self.make(200)
        out = splice_face(new, prev, (-5, -5, 999, 999))
        self.assertEqual(out.shape, new.shape)
        self.assertEqual(int(out[0, 0, 0]), 200)

    def test_degenerate_rect_returns_copy(self):
        new = self.make(10)
        out = splice_face(new, self.make(200), (30, 20, 30, 40))  # 零宽，无可拼接区域
        self.assertTrue(np.array_equal(out, new))
        self.assertIsNot(out, new)

    def test_swapped_coordinates_are_normalised(self):
        """x1/x2 写反也不该整块贴错位置：归一化后按矩形处理。"""
        new = self.make(10)
        prev = self.make(200)
        out = splice_face(new, prev, (30, 20, 10, 40))
        self.assertEqual(int(out[25, 15, 0]), 200)
        self.assertEqual(int(out[5, 5, 0]), 10)

    def test_shape_mismatch_returns_new_frame(self):
        new = self.make(10)
        prev = self.make(200, size=(80, 120))
        out = splice_face(new, prev, (10, 20, 30, 40))
        self.assertEqual(out.shape, new.shape)


class FillWiringTest(unittest.TestCase):
    """渲染线程内的接线用源码断言。"""

    def test_fill_branch_advances_gesture_and_keeps_face(self):
        source = inspect.getsource(BaseAvatar.process_frames)
        self.assertIn('splice_face(', source)
        # 补位帧按渲染线程自己的进度取帧，不用 `_proc_idx`：后者会被下一帧
        # 推理结果回写，补位推进的帧就被倒回去，动作末尾会回抽一下。
        self.assertIn('self._frame_at(played)', source)
        self.assertIn('self.advance_gesture(1)', source)
        # 先判手势，再退回"沿用上一帧"，否则手势期间永远走冻结分支
        self.assertLess(source.index('self._current_gesture is not None'),
                        source.index('_last_out_frame is not None'))

    def test_face_rect_normalises_both_coord_orders(self):
        avatar = BaseAvatar.__new__(BaseAvatar)
        avatar._init_gesture_state()
        avatar._active_coords = [(5, 6, 7, 8)]        # wav2lip: (y1,y2,x1,x2)
        avatar._active_extra = None
        self.assertEqual(avatar._face_rect(0), (7, 5, 8, 6))
        avatar._active_extra = {'coords': [(1, 2, 3, 4)]}   # MuseTalk: (x1,y1,x2,y2)
        self.assertEqual(avatar._face_rect(0), (1, 2, 3, 4))

    def test_fill_rate_is_logged(self):
        source = inspect.getsource(BaseAvatar.process_frames)
        self.assertIn('_note_frame(', source)


class _RecordingOutput:
    """只记录推了什么帧的输出端替身（WebRTC/RTMP 的接口面很小）。"""

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


def _render_avatar(gesture_count=8, default_count=1, fps=50, face_coords=(2, 8, 2, 6)):
    """装配一个只够跑 process_frames 的替身（不加载模型）。

    `face_coords` 按 wav2lip 的 `coords.pkl` 元素序 `(y1, y2, x1, x2)` 给。
    """
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
    avatar.frame_list_cycle = [np.full((24, 16, 3), 250, dtype=np.uint8)] * default_count
    avatar.face_list_cycle = [np.full((8, 8, 3), 250, dtype=np.uint8)] * default_count
    avatar.coord_list_cycle = [(0, 0, 16, 24)] * default_count
    avatar._init_gesture_state()
    avatar._bind_default_frames(
        avatar.frame_list_cycle, avatar.face_list_cycle, avatar.coord_list_cycle)
    avatar.output = _RecordingOutput()
    avatar.record_video_data = lambda frame: None
    avatar.record_audio_data = lambda frame: None
    # 手势帧用不同灰度，便于断言"补帧时画面在往前走"
    frames = [np.full((24, 16, 3), 10 + index * 10, dtype=np.uint8)
              for index in range(gesture_count)]
    avatar._activate('wave', (frames, avatar.face_list_cycle, [face_coords] * gesture_count))
    return avatar


class RenderFillTest(unittest.TestCase):
    """真跑 process_frames：队列饿死时手势必须继续推进，且脸不能闪回原始帧。"""

    def _run(self, avatar, seconds):
        quit_event = Event()
        timer = Thread(target=lambda: (time.sleep(seconds), quit_event.set()), daemon=True)
        timer.start()
        avatar.process_frames(quit_event)

    def test_starved_queue_keeps_gesture_moving(self):
        avatar = _render_avatar()
        silent = AudioFrameData(data=np.zeros(avatar.chunk, dtype=np.float32), type=1)
        # 只喂 2 帧（每帧两份音频），之后队列必然空 → 走补帧分支
        for index in range(2):
            avatar.res_frame_queue.put((None, [silent, silent], index))
        self._run(avatar, 0.25)

        values = [int(frame[0, 0, 0]) for frame in avatar.output.video
                  if frame is not None and frame.ndim == 3]
        self.assertGreaterEqual(len(values), 5, '渲染线程没跑起来')
        # 补帧期间画面必须继续前进（而不是卡在同一帧上）
        head = values[:5]
        self.assertEqual(len(set(head)), len(head), '补帧把画面冻住了：%s' % head)

    def test_gesture_returns_to_default_after_one_pass(self):
        avatar = _render_avatar(gesture_count=3)
        silent = AudioFrameData(data=np.zeros(avatar.chunk, dtype=np.float32), type=1)
        avatar.res_frame_queue.put((None, [silent, silent], 0))
        self._run(avatar, 0.3)
        self.assertIsNone(avatar._current_gesture, '播满一遍后应回到默认素材')
        self.assertIs(avatar._active_frames, avatar.frame_list_cycle)

    def test_fill_keeps_face_from_previous_composited_frame(self):
        """补帧 = 新的动作帧 + 上一张已合成画面的脸（否则嘴会闪回闭合）。"""
        avatar = _render_avatar(gesture_count=8)
        avatar.paste_back_frame = lambda res_frame, idx: np.full((24, 16, 3), 99, dtype=np.uint8)
        speaking = AudioFrameData(data=np.full(avatar.chunk, 0.1, dtype=np.float32), type=0)
        silent = AudioFrameData(data=np.zeros(avatar.chunk, dtype=np.float32), type=1)
        # 一个"说话"帧撑起 _last_out_frame，之后饿死走补帧分支
        avatar.res_frame_queue.put((np.zeros((8, 8, 3), dtype=np.uint8), [speaking, speaking], 0))
        self._run(avatar, 0.2)

        frames = [frame for frame in avatar.output.video if frame is not None]
        self.assertTrue(frames)
        spliced = [f for f in frames
                   if int(f[3, 3, 0]) == 99 and int(f[20, 12, 0]) != 99]
        self.assertTrue(spliced, '补帧没有在保留人脸的同时推进动作：%s'
                        % [(int(f[3, 3, 0]), int(f[20, 12, 0])) for f in frames])


if __name__ == '__main__':
    unittest.main()
