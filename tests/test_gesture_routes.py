# -*- coding: utf-8 -*-
"""手势相关接口的测试（TDD：这些断言先于实现存在）。

对应 openspec/changes/add-gesture-module/tasks.md 第 4 组。
直接调用处理函数并传入最小请求替身，不启动 aiohttp 服务。
"""

import asyncio
import json
import pathlib
import unittest
from types import SimpleNamespace
from unittest import mock

import server.avatar_routes as avatar_routes
import server.routes as routes
import utils.gestures as gesture_utils
from server.session_manager import session_manager


class FakeRequest(object):
    def __init__(self, body=None, match=None, app=None):
        self._body = body
        self.match_info = match or {}
        self.app = app or {}

    async def json(self):
        if self._body is None:
            raise ValueError('请求体不是合法 JSON')
        return self._body


class FakeSession(object):
    def __init__(self, avatar_id='Teacher', preheat_error=None):
        self.opt = SimpleNamespace(avatar_id=avatar_id)
        self.speech_request_id = 0
        self.delivered = []
        self.preheat_calls = []
        self.preheat_error = preheat_error

    def put_msg_txt(self, text, datainfo=None):
        self.delivered.append((text, dict(datainfo or {})))

    def flush_talk(self):
        self.speech_request_id += 1

    def preheat_gestures(self, names):
        self.preheat_calls.append(list(names))
        if self.preheat_error is not None:
            raise self.preheat_error

    @property
    def texts(self):
        return [text for text, _ in self.delivered]


def call(handler, request):
    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(handler(request))
    finally:
        loop.close()
    return json.loads(response.text)


def gesture_payload(request):
    return json.loads(request.text)


class ListGesturesRouteTestCase(unittest.TestCase):
    """4.1 GET /api/avatars/{avatar_id}/gestures"""

    def setUp(self):
        self.root = pathlib.Path(avatar_routes.DATA_DIR)
        self.avatar_dir = self.root / 'Teacher'
        self.avatar_dir.mkdir(parents=True, exist_ok=True)
        self.patches = mock.patch.object(avatar_routes, 'DATA_DIR', str(self.root))
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.avatar_dir, ignore_errors=True)

    def test_lists_available_gestures(self):
        with mock.patch.object(gesture_utils, 'list_gestures',
                               return_value=[{'id': 'explain', 'label': '讲解手势'}]):
            body = call(avatar_routes.list_avatar_gestures,
                        FakeRequest(match={'avatar_id': 'Teacher'}))
        self.assertEqual(body['code'], 0)
        self.assertEqual(body['data']['gestures'],
                         [{'id': 'explain', 'label': '讲解手势'}])

    def test_avatar_without_gestures_returns_empty_list(self):
        with mock.patch.object(gesture_utils, 'list_gestures', return_value=[]):
            body = call(avatar_routes.list_avatar_gestures,
                        FakeRequest(match={'avatar_id': 'Teacher'}))
        self.assertEqual(body['code'], 0)
        self.assertEqual(body['data']['gestures'], [])

    def test_invalid_avatar_id_is_rejected_without_touching_filesystem(self):
        for bad_id in ('../etc', 'a b', 'a/b', ''):
            with mock.patch.object(gesture_utils, 'list_gestures') as spy:
                with mock.patch.object(pathlib.Path, 'is_dir',
                                       side_effect=AssertionError('不应读取文件系统')):
                    body = call(avatar_routes.list_avatar_gestures,
                                FakeRequest(match={'avatar_id': bad_id}))
            self.assertNotEqual(body['code'], 0, bad_id)
            spy.assert_not_called()

    def test_missing_avatar_returns_error(self):
        with mock.patch.object(gesture_utils, 'list_gestures') as spy:
            body = call(avatar_routes.list_avatar_gestures,
                        FakeRequest(match={'avatar_id': 'NoSuchAvatar'}))
        self.assertNotEqual(body['code'], 0)
        spy.assert_not_called()

    def test_gesture_route_is_registered(self):
        registered = []

        class FakeRouter(object):
            def add_get(self, path, handler):
                registered.append(path)

            def add_put(self, *args):
                pass

            def add_delete(self, *args):
                pass

            def add_post(self, *args):
                pass

        class FakeApp(object):
            router = FakeRouter()

        avatar_routes.setup_avatar_routes(FakeApp())
        self.assertIn('/api/avatars/{avatar_id}/gestures', registered)


class HumanEchoGestureTestCase(unittest.TestCase):
    """4.2 / 4.3 POST /human 的 echo 模式受理阶段校验与预热。"""

    def setUp(self):
        self.session = FakeSession()
        session_manager.sessions['sid'] = self.session
        self.addCleanup(lambda: session_manager.sessions.pop('sid', None))

    def post(self, text, sessionid='sid', **extra):
        body = {'sessionid': sessionid, 'type': 'echo', 'text': text}
        body.update(extra)
        return call(routes.human, FakeRequest(body=body))

    def test_available_gesture_is_delivered_to_renderer(self):
        with mock.patch.object(gesture_utils, 'list_gestures',
                               return_value=[{'id': 'explain', 'label': '讲解手势'}]):
            body = self.post('[GESTURE:explain] 我们来分析这个问题。')
        self.assertEqual(body['code'], 0)
        self.assertEqual(self.session.delivered[0][0], '我们来分析这个问题。')
        self.assertEqual(self.session.delivered[0][1]['gesture'], 'explain')

    def test_trailing_gesture_is_delivered_after_speech(self):
        with mock.patch.object(gesture_utils, 'list_gestures',
                               return_value=[{'id': 'wave_one', 'label': '挥手'}]):
            body = self.post('大家好，我是陈老师[GESTURE:wave_one]')
        self.assertEqual(body['code'], 0)
        self.assertEqual(self.session.texts, ['大家好，我是陈老师'])
        self.assertEqual(self.session.delivered[0][1]['gesture_after'], 'wave_one')
        self.assertNotIn('gesture', self.session.delivered[0][1])

    def test_unknown_gesture_is_dropped_but_text_still_reads(self):
        """不可用手势 MUST NOT 让整篇讲稿停下：只摘掉该段的手势，文字照常朗读。"""
        with mock.patch.object(gesture_utils, 'list_gestures',
                               return_value=[{'id': 'explain', 'label': '讲解手势'}]):
            body = self.post('[GESTURE:nosuch] 内容')
        self.assertEqual(body['code'], 0)
        self.assertEqual(self.session.texts, ['内容'])
        self.assertNotIn('gesture', self.session.delivered[0][1])

    def test_unknown_gesture_warning_names_it_and_lists_available(self):
        with mock.patch.object(gesture_utils, 'list_gestures',
                               return_value=[{'id': 'explain', 'label': '讲解手势'}]):
            body = self.post('[GESTURE:nosuch] 内容')
        warnings = (body.get('data') or {}).get('warnings') or []
        self.assertEqual(len(warnings), 1)
        message = warnings[0]['message']
        self.assertIn('nosuch', message)
        self.assertIn('explain', message)

    def test_unavailable_gesture_is_logged_as_warning(self):
        with mock.patch.object(gesture_utils, 'list_gestures', return_value=[]):
            with self.assertLogs('utils.logger', level='WARNING') as captured:
                self.post('[GESTURE:nosuch] 内容')
        self.assertTrue(any('nosuch' in line for line in captured.output),
                        '不可用手势必须记一条警告，否则等于静默降级')

    def test_all_segments_are_delivered_when_gesture_is_unavailable(self):
        with mock.patch.object(gesture_utils, 'list_gestures', return_value=[]):
            self.post('[GESTURE:nosuch] 第一段。[PPT:2] 第二段。')
        self.assertEqual(self.session.texts, ['第一段。', '第二段。'])
        for _text, datainfo in self.session.delivered:
            self.assertNotIn('gesture', datainfo)

    def test_avatar_without_any_gesture_is_reported_in_warning(self):
        with mock.patch.object(gesture_utils, 'list_gestures', return_value=[]):
            body = self.post('[GESTURE:explain] 内容')
        self.assertEqual(body['code'], 0)
        warnings = (body.get('data') or {}).get('warnings') or []
        self.assertEqual(len(warnings), 1)
        self.assertIn('explain', warnings[0]['message'])
        self.assertIn('没有', warnings[0]['message'])
        self.assertEqual(self.session.texts, ['内容'])

    def test_one_bad_gesture_does_not_block_the_good_one(self):
        with mock.patch.object(gesture_utils, 'list_gestures',
                               return_value=[{'id': 'explain', 'label': '讲解手势'}]):
            body = self.post('[GESTURE:explain] 第一段。[GESTURE:nosuch] 第二段。')
        self.assertEqual(body['code'], 0)
        self.assertEqual(self.session.texts, ['第一段。', '第二段。'])
        self.assertEqual(self.session.delivered[0][1]['gesture'], 'explain')
        self.assertNotIn('gesture', self.session.delivered[1][1])

    def test_preheat_only_covers_available_gestures(self):
        with mock.patch.object(gesture_utils, 'list_gestures',
                               return_value=[{'id': 'explain', 'label': '讲解手势'}]):
            self.post('[GESTURE:explain] 甲。[GESTURE:nosuch] 乙。')
        self.assertEqual(self.session.preheat_calls, [['explain']])

    def test_script_without_gesture_tags_is_unaffected(self):
        with mock.patch.object(gesture_utils, 'list_gestures') as spy:
            body = self.post('[PPT:1] 甲。[PPT:2] 乙。')
        self.assertEqual(body['code'], 0)
        spy.assert_not_called()
        self.assertEqual(self.session.texts, ['甲。', '乙。'])
        self.assertNotIn('gesture', self.session.delivered[0][1])

    def test_shorter_alias_gesture_tag_works(self):
        with mock.patch.object(gesture_utils, 'list_gestures',
                               return_value=[{'id': 'explain', 'label': '讲解手势'}]):
            body = self.post('[gesture:explain] 内容')
        self.assertEqual(body['code'], 0)
        self.assertEqual(self.session.delivered[0][1]['gesture'], 'explain')

    def test_gesture_names_are_preheated_once(self):
        with mock.patch.object(gesture_utils, 'list_gestures',
                               return_value=[{'id': 'explain', 'label': '讲解手势'},
                                             {'id': 'wave', 'label': '挥手'}]):
            body = self.post('[GESTURE:explain] 甲。[GESTURE:wave] 乙。[GESTURE:explain] 丙。')
        self.assertEqual(body['code'], 0)
        self.assertEqual(self.session.preheat_calls, [['explain', 'wave']])

    def test_preheat_failure_does_not_fail_the_request(self):
        self.session.preheat_error = RuntimeError('读盘失败')
        with mock.patch.object(gesture_utils, 'list_gestures',
                               return_value=[{'id': 'explain', 'label': '讲解手势'}]):
            body = self.post('[GESTURE:explain] 内容')
        self.assertEqual(body['code'], 0)
        self.assertEqual(self.session.texts, ['内容'])

    def test_missing_session_still_returns_error(self):
        body = self.post('[GESTURE:explain] 内容', sessionid='nosuch')
        self.assertNotEqual(body['code'], 0)

    def test_gesture_and_slide_datainfo_coexist(self):
        with mock.patch.object(gesture_utils, 'list_gestures',
                               return_value=[{'id': 'explain', 'label': '讲解手势'}]):
            body = self.post('[PPT:3] 甲。[GESTURE:explain] 乙。')
        self.assertEqual(body['code'], 0)
        self.assertNotIn('gesture', self.session.delivered[0][1])
        self.assertEqual(self.session.delivered[1][1]['gesture'], 'explain')
        self.assertEqual(self.session.delivered[1][1]['slide_id'],
                         routes._resolve_slide_id(3))


if __name__ == '__main__':
    unittest.main()
