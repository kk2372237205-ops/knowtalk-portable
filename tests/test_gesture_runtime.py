# -*- coding: utf-8 -*-
"""运行时帧源切换（手势编排）的测试。

对应 openspec/changes/add-gesture-module/tasks.md 第 3 组。

不启动真实推理模型与渲染线程：用最小的替身装配 BaseAvatar 的手势状态，并对
`inference()` 注入假 ASR 队列，直接断言"切换后索引从 0 开始""播满一遍回默认"。
静音分支与补帧分支依赖渲染线程无法在单元层执行，因此对它们的接线做源码断言
（测试里明确标注了这一点）。
"""

import inspect
import time
import unittest
from queue import Queue
from threading import Event, Lock, Thread
from types import SimpleNamespace
from unittest import mock

import numpy as np

import avatars.base_avatar as base_avatar
from avatars.base_avatar import AudioFrameData, BaseAvatar, GESTURE_CACHE_LIMIT
from avatars.wav2lip_avatar import LipReal


def make_frames(count, value, size=(8, 8)):
    return [np.full((size[1], size[0], 3), value, dtype=np.uint8) for _ in range(count)]


class FakeAvatar(BaseAvatar):
    """绕过重量级的 BaseAvatar.__init__，只装配手势编排需要的最小状态。"""

    # 贴回逻辑复用 wav2lip 的真实实现（不加载模型权重），
    # 这样"手势帧被当作底图"才是被真实代码路径验证的。
    paste_back_frame = LipReal.paste_back_frame

    def __init__(self, default_frames=150, gesture_frames=6, avatar_id='Teacher'):
        self.opt = SimpleNamespace(fps=25, batch_size=2, sessionid='s1', avatar_id=avatar_id)
        self.sample_rate = 16000
        self.chunk = 320
        self.sessionid = 's1'
        self.batch_size = self.opt.batch_size
        self.speaking = False
        self.speech_request_id = 0
        self.custom_audiotype = 0
        self.custom_img_cycle = {}
        self.custom_audio_cycle = {}
        self.custom_audio_index = {}
        self.custom_index = {}
        self.res_frame_queue = Queue(self.batch_size * 3)
        # 与真实 avatar 一致：既有既有的三件套属性，也把它们绑定为默认帧源
        self.frame_list_cycle = make_frames(default_frames, 10)
        self.face_list_cycle = make_frames(default_frames, 11, size=(4, 4))
        self.coord_list_cycle = [(1, 2, 3, 4)] * default_frames
        self._init_gesture_state()
        self._bind_default_frames(
            self.frame_list_cycle, self.face_list_cycle, self.coord_list_cycle)
        # 贴回逻辑用到的口型参数（与 wav2lip 的默认值一致）
        self.mouth_alpha = 0.9
        self.mouth_offset_y = 0.0
        self.mouth_offset_x = 0.0
        self.mouth_region_frac = (0.55, 0.87, 0.22, 0.78)
        self.gesture_frames = make_frames(gesture_frames, 99)
        self.gesture_faces = make_frames(gesture_frames, 98, size=(4, 4))
        self.gesture_coords = [(5, 6, 7, 8)] * gesture_frames
        # 记录磁盘读取次数，用来断言缓存命中不重复读盘
        self.load_calls = []

    # ── 替身 ──────────────────────────────────────────────────────────────
    def _load_gesture_payload(self, name):
        self.load_calls.append(name)
        if name == 'broken':
            raise RuntimeError('素材被删了')
        if name == 'other':
            return make_frames(3, 77), make_frames(3, 76, size=(4, 4)), [(0, 1, 0, 1)] * 3
        return self.gesture_frames, self.gesture_faces, self.gesture_coords

    def gesture_payload(self):
        return self.gesture_frames, self.gesture_faces, self.gesture_coords

    def activate_now(self, name):
        """同步走一遍"请求 → 后台加载 → 激活"，避免测试依赖线程时序。"""
        self.request_gesture(name)
        self.join_loaders()

    def join_loaders(self):
        deadline = time.time() + 5
        while time.time() < deadline:
            with self._gesture_lock:
                pending = set(self._gesture_loading)
            if not pending:
                return
            time.sleep(0.002)
        raise AssertionError('后台加载未在 5 秒内结束')

    def join_loaders_with_gate(self, gate):
        """等后请求的手势先激活，再放行被挡住的加载，最后等所有加载结束。"""
        deadline = time.time() + 5
        while time.time() < deadline and self._current_gesture != 'b':
            time.sleep(0.002)
        gate.set()
        self.join_loaders()


def run_inference(avatar, iterations, gesture=None, on_put=None):
    """用假 ASR 队列跑若干轮 inference，返回实际交给渲染队列的帧索引序列。

    `on_put(item, seen)` 在每次入队后被调用，用来模拟"推理正在跑的时候帧源被切换"
    （真实场景里切换由渲染线程在段首发起）。
    """
    target = iterations * avatar.batch_size
    avatar.asr = SimpleNamespace(feat_queue=Queue(maxsize=5), output_queue=Queue())
    for _ in range(target * 2):
        avatar.asr.output_queue.put(
            AudioFrameData(data=np.zeros(320, dtype=np.float32), type=0))
    for _ in range(iterations):
        avatar.asr.feat_queue.put([[0.0]])

    if gesture is not None:
        avatar._activate(gesture, avatar.gesture_payload())

    # 无界队列：单测里没有渲染线程消费，有界队列会在第二轮 put 时永久阻塞
    avatar.res_frame_queue = Queue()
    seen = []
    real_put = avatar.res_frame_queue.put
    quit_event = Event()

    def spy(item, **kwargs):
        # 真实实现走 utils.queue_guard.put_capped，会带 timeout 关键字参数。
        _res_frame, _audio, index = item
        seen.append(index)
        if on_put is not None:
            on_put(item, seen)
        if len(seen) >= target:
            quit_event.set()
        return real_put(item)

    avatar.res_frame_queue.put = spy
    avatar.inference_batch = lambda index, feats: [np.zeros((4, 4, 3), dtype=np.uint8)] * avatar.batch_size

    def watchdog():
        if not quit_event.wait(15):
            quit_event.set()

    Thread(target=watchdog, daemon=True).start()
    avatar.inference(quit_event)
    return seen


class FrameSourceTestCase(unittest.TestCase):
    """3.1 未使用手势时 `_active_*` 与默认三件套同源，三条取帧路径都读 `_active_*`。"""

    def test_active_sequences_alias_default_after_binding(self):
        avatar = FakeAvatar()
        self.assertIs(avatar._active_frames, avatar.frame_list_cycle)
        self.assertIs(avatar._active_faces, avatar.face_list_cycle)
        self.assertIs(avatar._active_coords, avatar.coord_list_cycle)

    def test_accessors_follow_active_sequences(self):
        avatar = FakeAvatar()
        avatar._active_frames = avatar.gesture_frames
        avatar._active_faces = avatar.gesture_faces
        avatar._active_coords = avatar.gesture_coords
        self.assertIs(avatar._frame_at(0), avatar.gesture_frames[0])
        self.assertIs(avatar._face_at(0), avatar.gesture_faces[0])
        self.assertIs(avatar._coord_at(0), avatar.gesture_coords[0])

    def test_accessors_fall_back_to_model_attributes_when_unbound(self):
        avatar = FakeAvatar()
        avatar._active_frames = None
        self.assertIs(avatar._frame_at(3), avatar.frame_list_cycle[3])
        self.assertEqual(avatar.get_avatar_length(), 150)

    def test_paste_back_frame_uses_active_frame(self):
        avatar = FakeAvatar()
        avatar._active_frames = avatar.gesture_frames
        avatar._active_coords = avatar.gesture_coords
        avatar._active_faces = avatar.gesture_faces
        out = avatar.paste_back_frame(np.zeros((4, 4, 3), dtype=np.uint8), 1)
        # 手势帧亮度 99、默认帧 10：读错帧源就会看到 10
        self.assertEqual(int(out[0, 0, 0]), 99)

    def test_silence_and_fill_branches_read_active_frames(self):
        """静音分支与补帧分支依赖渲染线程，无法在单元层执行，因此断言其接线。"""
        source = inspect.getsource(BaseAvatar.process_frames)
        # 两个分支都按 `frame_idx` 取帧；手势期间 `frame_idx` 由渲染线程自己的
        # 进度决定（`_gesture_frames_played`），不是推理线程传来的 `idx`。
        self.assertIn('self._frame_at(frame_idx)', source)
        self.assertIn('frame_idx = self._gesture_frames_played', source)
        self.assertIn('self._frame_at(silence_idx)', source)
        self.assertNotIn('self.frame_list_cycle[', source)

    def test_no_avatar_code_indexes_default_sequences_directly(self):
        for module in (base_avatar,):
            source = inspect.getsource(module)
            self.assertNotIn('self.face_list_cycle[idx]', source)
            self.assertNotIn('self.coord_list_cycle[idx]', source)
            self.assertNotIn('self.frame_list_cycle[idx]', source)


class GestureAfterNotificationTestCase(unittest.TestCase):
    def test_gesture_after_starts_only_on_audio_end_notification(self):
        avatar = BaseAvatar.__new__(BaseAvatar)
        requested = []
        avatar.request_gesture = requested.append

        avatar.notify({'status': 'start', 'gesture_after': 'wave_one'})
        self.assertEqual(requested, [])
        avatar.notify({'status': 'end', 'gesture_after': 'wave_one'})
        self.assertEqual(requested, ['wave_one'])


class SwitchIndexTestCase(unittest.TestCase):
    """3.2 切换后从手势第 0 帧开始，且 length 随帧源长度更新。"""

    def test_length_follows_active_frames(self):
        avatar = FakeAvatar(default_frames=150, gesture_frames=6)
        self.assertEqual(avatar.get_avatar_length(), 150)
        avatar._activate('explain', avatar.gesture_payload())
        self.assertEqual(avatar.get_avatar_length(), 6)

    def test_generation_increments_on_every_activation(self):
        avatar = FakeAvatar()
        start = avatar._frame_source_gen
        avatar._activate('explain', avatar.gesture_payload())
        self.assertEqual(avatar._frame_source_gen, start + 1)
        avatar._activate(None)
        self.assertEqual(avatar._frame_source_gen, start + 2)

    def test_inference_restarts_index_at_zero_after_switch(self):
        avatar = FakeAvatar(default_frames=150, gesture_frames=121)
        before = run_inference(avatar, iterations=3)
        # 切换到 121 帧的手势素材：帧源代数变化，索引必须回到 0
        after = run_inference(avatar, iterations=2, gesture='explain')
        self.assertEqual(before, [0, 1, 2, 3, 4, 5])
        self.assertEqual(after, [0, 1, 2, 3])

    def test_inference_restarts_index_when_switch_happens_mid_run(self):
        """切换发生在推理正在跑的时候（真实场景：渲染线程在段首触发），下一批必须从 0 开始。"""
        avatar = FakeAvatar(default_frames=150, gesture_frames=121)

        def switch_during_second_batch(item, seen):
            if len(seen) == 2:
                avatar._activate('explain', avatar.gesture_payload())

        seen = run_inference(avatar, iterations=3, on_put=switch_during_second_batch)
        # 每批产出 batch_size=2 帧：第 1 批 [0,1] 之后切换，第 2 批必须重新从 0 开始。
        # 不重置索引的话会一路推到 [0,1,2,3,4,5]。
        self.assertEqual(seen, [0, 1, 0, 1, 2, 3])

    def test_inference_wraps_at_gesture_length_not_default_length(self):
        # 手势只有 5 帧：第 6 个产出帧的索引必须是 0（若仍用默认的 150 帧会是 5）
        avatar = FakeAvatar(default_frames=150, gesture_frames=5)
        seen = run_inference(avatar, iterations=3, gesture='explain')
        self.assertEqual(seen, [0, 1, 2, 3, 4, 0])


class PlayOnceTestCase(unittest.TestCase):
    """3.3 只完整播一遍；计数与 index 解耦。"""

    def test_returns_to_default_after_one_full_pass(self):
        avatar = FakeAvatar(gesture_frames=4)
        avatar._activate('explain', avatar.gesture_payload())
        avatar.advance_gesture(2)
        self.assertEqual(avatar._current_gesture, 'explain')
        avatar.advance_gesture(2)
        self.assertIs(avatar._active_frames, avatar.frame_list_cycle)
        self.assertIsNone(avatar._current_gesture)

    def test_counter_is_independent_of_index(self):
        avatar = FakeAvatar(gesture_frames=4)
        avatar._activate('explain', avatar.gesture_payload())
        avatar.advance_gesture(3)
        avatar.index = 0            # 人为重置 index，模拟"静音→说话"边界
        avatar.advance_gesture(1)
        self.assertIsNone(avatar._current_gesture)

    def test_repeat_keeps_gesture_looping(self):
        """闲时动作用 repeat=True 请求：播满一遍从头再来，不清回默认素材。"""
        avatar = FakeAvatar(gesture_frames=4)
        avatar.request_gesture('idle_sway', repeat=True)
        avatar._activate('idle_sway', avatar.gesture_payload())
        avatar.advance_gesture(4)
        self.assertEqual(avatar._current_gesture, 'idle_sway')
        # 索引必须落回第一遍内的合法范围，否则取帧会越界
        self.assertLess(avatar._gesture_frames_played, 4)
        avatar.advance_gesture(3)
        self.assertEqual(avatar._current_gesture, 'idle_sway')

    def test_repeat_flag_resets_when_back_to_default(self):
        avatar = FakeAvatar(gesture_frames=4)
        avatar.request_gesture('idle_sway', repeat=True)
        avatar._activate('idle_sway', avatar.gesture_payload())
        avatar._activate(None)
        self.assertFalse(avatar._gesture_repeat)

    def test_progress_is_advanced_by_render_thread_only(self):
        """进度只由渲染线程按**实际输出的视频帧**推进。

        推理产出的帧要经队列才上屏，按批量计数必然超前于画面：切换时机会早于
        最后几帧动作真正播出，现场表现正好是"动作快结束时突然收回去/卡一下"。
        """
        infer_source = inspect.getsource(BaseAvatar.inference)
        self.assertNotIn('self.advance_gesture(', infer_source)
        render_source = inspect.getsource(BaseAvatar.process_frames)
        self.assertGreaterEqual(render_source.count('self.advance_gesture('), 2)
        # 暂停期间动作要停住，不推进进度
        self.assertIn('if not self.playback_paused:', render_source)

    def test_advance_is_noop_without_gesture(self):
        avatar = FakeAvatar()
        avatar.advance_gesture(100)
        self.assertEqual(avatar._gesture_frames_played, 0)

    def test_inference_does_not_switch_source_back(self):
        """推理跑多少轮都不该自己把手势切掉——切源时机归渲染线程所有。"""
        avatar = FakeAvatar(gesture_frames=2)
        run_inference(avatar, iterations=3, gesture='explain')
        self.assertIs(avatar._active_frames, avatar.gesture_frames)
        # 渲染线程输出满 2 帧后才回默认素材
        avatar.advance_gesture(2)
        self.assertIs(avatar._active_frames, avatar.frame_list_cycle)


class IndexSafetyTestCase(unittest.TestCase):
    """3.4 两套素材帧数不同时，切换窗口内不越界。"""

    def setUp(self):
        self.avatar = FakeAvatar(default_frames=150, gesture_frames=121)
        self.avatar._active_frames = self.avatar.gesture_frames
        self.avatar._active_faces = self.avatar.gesture_faces
        self.avatar._active_coords = self.avatar.gesture_coords

    def test_stale_index_with_shorter_source_is_wrapped(self):
        # 150 帧时代的旧索引
        self.assertIs(self.avatar._frame_at(149), self.avatar.gesture_frames[149 % 121])
        self.assertIs(self.avatar._face_at(149), self.avatar.gesture_faces[149 % 121])
        self.assertIs(self.avatar._coord_at(149), self.avatar.gesture_coords[149 % 121])

    def test_paste_back_frame_does_not_raise_on_stale_index(self):
        out = self.avatar.paste_back_frame(np.zeros((4, 4, 3), dtype=np.uint8), 149)
        self.assertEqual(out.shape, self.avatar.gesture_frames[0].shape)

    def test_none_index_is_tolerated(self):
        avatar = FakeAvatar()
        self.assertIs(avatar._frame_at(None), avatar.frame_list_cycle[0])

    def test_empty_sequence_returns_none_instead_of_raising(self):
        avatar = FakeAvatar()
        avatar._active_frames = []
        self.assertIsNone(avatar._frame_at(3))


class CacheTestCase(unittest.TestCase):
    """3.5 后台加载与 LRU 缓存；预热不改变当前画面。"""

    def setUp(self):
        self.avatar = FakeAvatar()

    def test_second_request_reuses_cache(self):
        self.avatar.activate_now('explain')
        self.avatar.activate_now('explain')
        self.assertEqual(self.avatar.load_calls.count('explain'), 1)
        self.assertEqual(self.avatar._current_gesture, 'explain')

    def test_preheat_does_not_activate(self):
        self.avatar.preheat_gestures(['explain'])
        self.avatar.join_loaders()
        self.assertIsNone(self.avatar._current_gesture)
        self.assertIs(self.avatar._active_frames, self.avatar.frame_list_cycle)
        self.assertIn('explain', self.avatar._gesture_cache)

    def test_preheated_request_activates_from_cache(self):
        self.avatar.preheat_gestures(['explain'])
        self.avatar.join_loaders()
        self.avatar.activate_now('explain')
        self.assertEqual(self.avatar._current_gesture, 'explain')
        self.assertEqual(self.avatar.load_calls.count('explain'), 1)

    def test_concurrent_preheat_decodes_serially(self):
        """预热多套手势时，解码必须串行。

        一套手势 = 上百张 538x1080 的 PNG，解出来常驻内存约 200MB。预热会为每个
        手势各起一个线程，若并发解码，内存峰值会叠加到几百 MB～1GB，在内存吃紧的
        机器上直接把 cv2.imread 打成 "(-4:Insufficient memory) Failed to allocate"，
        手势加载整体失败并静默降级成默认素材（表现为"动作播不出来"）。
        """
        names = ['a', 'b', 'c', 'd']
        counter = {'active': 0, 'peak': 0}
        guard = Lock()

        def slow_load(name):
            with guard:
                counter['active'] += 1
                counter['peak'] = max(counter['peak'], counter['active'])
            try:
                time.sleep(0.02)
                return (self.avatar.gesture_frames, self.avatar.gesture_faces,
                        self.avatar.gesture_coords)
            finally:
                with guard:
                    counter['active'] -= 1

        self.avatar._load_gesture_payload = slow_load
        self.avatar.preheat_gestures(names)
        self.avatar.join_loaders()
        self.assertEqual(counter['peak'], 1, '手势解码必须串行，否则内存峰值会叠加')

    def test_preheat_covers_every_gesture_in_script(self):
        """预热必须覆盖讲稿用到的**全部**手势，而不是前几套。

        以前只预热缓存容量那么多套，讲到后面的动作才现读盘 —— 那一下顿挫很明显
        （实测触发到真正切换晚了近 2 秒）。全帧已按需解码，单套只占几 MB，
        一讲课的动作全留下是可行的。
        """
        names = ['a', 'b', 'c', 'd', 'a']
        self.avatar.preheat_gestures(names)
        self.avatar.join_loaders()
        self.assertEqual(sorted(self.avatar.load_calls), ['a', 'b', 'c', 'd'])
        self.assertEqual(len(self.avatar._gesture_cache), 4)
        self.assertLessEqual(len(self.avatar._gesture_cache), GESTURE_CACHE_LIMIT)

    def test_preheat_dedupes_repeated_names(self):
        self.avatar.preheat_gestures(['a', 'a', 'a', None])
        self.avatar.join_loaders()
        self.assertEqual(self.avatar.load_calls, ['a'])

    def test_lru_evicts_oldest_beyond_limit(self):
        with mock.patch.object(base_avatar, 'GESTURE_CACHE_LIMIT', 2):
            self.avatar.activate_now('a')
            self.avatar.activate_now('b')
            self.assertEqual(len(self.avatar._gesture_cache), 2)
            # 再触发第三套，最久未用的 a 被淘汰
            self.avatar._activate(None)
            self.avatar.activate_now('c')
            self.assertEqual(list(self.avatar._gesture_cache.keys()), ['b', 'c'])
            self.avatar.activate_now('a')
            self.assertEqual(self.avatar.load_calls.count('a'), 2)

    def test_currently_playing_gesture_survives_eviction(self):
        self.avatar.activate_now('a')
        self.avatar.activate_now('b')
        self.avatar.activate_now('c')
        self.assertIn('c', self.avatar._gesture_cache)

    def test_clear_releases_cache(self):
        self.avatar.activate_now('explain')
        self.avatar.clear_gestures()
        self.assertEqual(len(self.avatar._gesture_cache), 0)
        self.assertIsNone(self.avatar._current_gesture)
        self.assertIs(self.avatar._active_frames, self.avatar.frame_list_cycle)

    def test_slow_load_does_not_hijack_a_newer_request(self):
        """先请求的手势加载慢、后请求的加载快时，画面必须是后者（不能被迟到的加载抢走）。"""
        gate = Event()
        real_load = self.avatar._load_gesture_payload

        def gated_load(name):
            if name == 'a':
                gate.wait(5)
            return real_load(name)

        self.avatar._load_gesture_payload = gated_load
        self.avatar.request_gesture('a')   # 加载 a：被挡住
        self.avatar.request_gesture('b')   # 紧接着请求 b：先加载完并激活
        self.avatar.join_loaders_with_gate(gate)
        self.assertEqual(self.avatar._current_gesture, 'b')
        self.assertIs(self.avatar._active_frames, self.avatar.gesture_frames)

    def test_loading_threads_are_daemon(self):
        created = []
        real_thread = base_avatar.Thread

        class SpyThread(real_thread):
            def __init__(self, *args, **kwargs):
                created.append(kwargs.get('daemon'))
                super().__init__(*args, **kwargs)

        with mock.patch.object(base_avatar, 'Thread', SpyThread):
            self.avatar.request_gesture('explain')
            self.avatar.join_loaders()
        self.assertTrue(created)
        self.assertTrue(all(flag for flag in created))


class DegradeTestCase(unittest.TestCase):
    """3.6 素材不可用时降级为默认素材并记录警告，渲染线程不退出。"""

    def test_load_failure_keeps_default_and_warns(self):
        avatar = FakeAvatar()
        with self.assertLogs('utils.logger', level='WARNING') as captured:
            avatar.activate_now('broken')
        self.assertIs(avatar._active_frames, avatar.frame_list_cycle)
        self.assertIsNone(avatar._current_gesture)
        self.assertTrue(any('broken' in line for line in captured.output), captured.output)

    def test_failure_rolls_back_to_default(self):
        avatar = FakeAvatar()
        avatar.activate_now('explain')
        avatar.activate_now('broken')
        self.assertIs(avatar._active_frames, avatar.frame_list_cycle)

    def test_failure_in_background_thread_does_not_raise(self):
        avatar = FakeAvatar()
        avatar.request_gesture('broken')
        avatar.join_loaders()
        self.assertIsNone(avatar._current_gesture)


class SessionIsolationTestCase(unittest.TestCase):
    """手势状态按会话隔离：两个会话各播各的。"""

    def test_two_sessions_do_not_interfere(self):
        first = FakeAvatar(avatar_id='A')
        second = FakeAvatar(avatar_id='B')
        first.activate_now('explain')
        self.assertEqual(first._current_gesture, 'explain')
        self.assertIsNone(second._current_gesture)
        second.advance_gesture(100)
        self.assertEqual(first._gesture_frames_played, 0)
        self.assertIsNot(first._gesture_cache, second._gesture_cache)
        self.assertIs(second._active_frames, second.frame_list_cycle)


class PasteBackFrameToleranceTestCase(unittest.TestCase):
    """贴回步骤必须容忍非整数人脸框。

    实测缺陷：手势素材归一化后如果留下浮点坐标，`cv2.resize` 会因为 dsize 非整数
    在**每一帧**抛错，表现成"手势期间嘴不动"，只留一条 warning。

    注意：`paste_back_frame` 取的是**当前帧源**（`_active_coords`），不是
    `coord_list_cycle`；直接改后者不会影响贴回，用例会变成空跑。
    """

    def _avatar_with_coords(self, coords, count=4):
        avatar = FakeAvatar(default_frames=count)
        avatar._active_coords = list(coords)
        return avatar

    def test_float_coords_do_not_break_paste_back(self):
        # 非整数浮点：OpenCV 5.x 对任何浮点 dsize 都报错，包括 5.0 这种"看着像整数"的
        avatar = self._avatar_with_coords([(1.0, 5.4, 1.0, 6.7)] * 4)
        pred = np.zeros((4, 4, 3), dtype=np.uint8)
        out = avatar.paste_back_frame(pred, 0)
        self.assertEqual(out.shape, avatar._frame_at(0).shape)

    def test_float_coords_do_not_log_a_paste_back_error(self):
        """贴回失败会被吞成一条 warning 并回退到原帧——形状断言抓不住，必须看日志。"""
        import logging

        records = []

        class Collect(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Collect()
        logger = logging.getLogger('utils.logger')
        logger.addHandler(handler)
        try:
            avatar = self._avatar_with_coords([(1.0, 5.4, 1.0, 6.7)] * 4)
            avatar.paste_back_frame(np.zeros((4, 4, 3), dtype=np.uint8), 0)
        finally:
            logger.removeHandler(handler)
        messages = [r.getMessage() for r in records]
        self.assertEqual([m for m in messages if 'error' in m.lower()], [],
                         '贴回不应因坐标类型而失败：%s' % messages)

    def test_normalized_gesture_coords_round_trip(self):
        """归一化路径产出的坐标必须能直接贴回，不需要调用方再转换。

        坐标是**非整数浮点**（这正是归一化的产物），且必须落在替身帧的 8×8 范围内。
        """
        avatar = self._avatar_with_coords([(0.6, 6.4, 0.2, 5.8)] * 4)
        pred = np.zeros((4, 4, 3), dtype=np.uint8)
        out = avatar.paste_back_frame(pred, 0)
        self.assertEqual(out.shape, avatar._frame_at(0).shape)

    def test_degenerate_box_skips_without_raising(self):
        avatar = self._avatar_with_coords([(10.0, 10.0, 10.0, 10.0)] * 4)
        pred = np.zeros((4, 4, 3), dtype=np.uint8)
        out = avatar.paste_back_frame(pred, 0)
        self.assertEqual(out.shape, avatar._frame_at(0).shape)


if __name__ == '__main__':
    unittest.main()
