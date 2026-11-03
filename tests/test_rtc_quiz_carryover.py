# -*- coding: utf-8 -*-
"""`/offer` 上的提问续接（页面刷新不打断提问）。

`/offer` 每次都会新建独立会话，刷新一次课堂就换了 sessionid。迁移规则：

- 只在**旧页面确实不再占用**（没有活跃 PeerConnection）时迁移——另一块展示页还连着就抢走，
  会让那一边突然失声；
- **不能只看 `has_session`**：刷新时旧连接已关，但服务端要等 ICE 判定超时才摘掉会话，
  用 has_session 判定会一直"以为旧页面还在"，刷新后提问就丢了（已踩）；
- 旧连接可能只是还没被判定断开 → 退避重试；
- 迁移失败不得影响 /offer 本身（连接永远优先）。
"""

import asyncio
import unittest
from unittest import mock

from server import rtc_manager as rtc_module


class FakePC(object):
    def __init__(self, state):
        self.connectionState = state


def make_manager(pcs=None):
    # 只测迁移辅助方法，不需要真实的 opt/PeerConnection
    manager = rtc_module.RTCManager.__new__(rtc_module.RTCManager)
    manager._session_pcs = dict(pcs or {})
    return manager


def patch_registry(**kwargs):
    return mock.patch('server.quiz_routes.registry', **kwargs)


class OfferCarryoverTestCase(unittest.TestCase):
    def test_does_not_steal_orchestrator_from_live_session(self):
        manager = make_manager({'old': FakePC('connected')})
        with patch_registry() as registry, \
                mock.patch.object(manager, '_schedule_quiz_carryover') as schedule:
            moved = manager._migrate_quiz_session('old', 'new', object())
        self.assertFalse(moved, '旧页面还连着时不得迁移')
        registry.migrate.assert_not_called()
        self.assertTrue(schedule.called, '旧连接可能只是没被判定断开，必须安排重试')

    def test_migrates_when_old_session_is_gone(self):
        manager = make_manager()
        speaker = object()
        with patch_registry() as registry:
            registry.migrate.return_value = True
            moved = manager._migrate_quiz_session('old', 'new', speaker)
        self.assertTrue(moved)
        registry.migrate.assert_called_once_with('old', 'new', speaker)

    def test_refresh_migrates_even_if_old_session_object_survives(self):
        """刷新后 /offer 的回归点：旧会话对象还挂在 session_manager 里（ICE 未超时），
        但它的 PeerConnection 已经断了——此时必须迁移，否则展示页永远查不到提问。"""
        manager = make_manager({'old': FakePC('disconnected')})
        with patch_registry() as registry, \
                mock.patch.object(rtc_module, 'session_manager') as sm:
            sm.has_session.return_value = True      # 会话对象仍在，但已无人观看
            registry.migrate.return_value = True
            moved = manager._migrate_quiz_session('old', 'new', object())
        self.assertTrue(moved, '旧连接已断（disconnected）即视为可迁移')
        registry.migrate.assert_called_once()

    def test_retries_then_migrates(self):
        """第一次判定为"仍占用"→ 退避重试；连接断了以后第二次迁移成功。"""
        pc = FakePC('connected')
        manager = make_manager({'old': pc})
        with patch_registry() as registry:
            registry.migrate.return_value = True
            with mock.patch.object(manager, '_schedule_quiz_carryover') as schedule:
                self.assertFalse(manager._migrate_quiz_session('old', 'new', object()))
                self.assertEqual(schedule.call_args[0][3], None, '首次尝试不消耗重试次数')
            pc.connectionState = 'disconnected'
            self.assertTrue(manager._migrate_quiz_session('old', 'new', object(), 2))

    def test_schedule_stops_after_retries_exhausted(self):
        manager = make_manager({'old': FakePC('connected')})
        self.assertFalse(manager._schedule_quiz_carryover('old', 'new', object(), 0))

    def test_scheduled_retry_really_fires(self):
        """重试必须挂到真实事件循环上：刷新时旧连接断开的那一刻自动迁移。"""
        pc = FakePC('connected')
        manager = make_manager({'old': pc})
        manager.QUIZ_CARRYOVER_DELAY = 0.01
        with patch_registry() as registry:
            registry.migrate.return_value = True

            async def scenario():
                # 首次：旧连接还"连着" → 不迁移，但要排一次重试
                self.assertFalse(manager._migrate_quiz_session('old', 'new', object(), 2))
                # 旧页面在这一刻断开
                pc.connectionState = 'disconnected'
                await asyncio.sleep(0.2)

            asyncio.run(scenario())
            self.assertTrue(registry.migrate.called, '重试到点后必须真的完成迁移')

    def test_no_carryover_hint_does_nothing(self):
        manager = make_manager()
        with patch_registry() as registry:
            self.assertFalse(manager._migrate_quiz_session('', 'new', object()))
            self.assertFalse(manager._migrate_quiz_session(None, 'new', object()))
            self.assertFalse(manager._migrate_quiz_session('new', 'new', object()))
        registry.migrate.assert_not_called()

    def test_migration_failure_never_breaks_offer(self):
        manager = make_manager()
        with patch_registry() as registry:
            registry.get.return_value = object()
            registry.migrate.side_effect = RuntimeError('boom')
            moved = manager._migrate_quiz_session('old', 'new', object())
        self.assertFalse(moved, '迁移异常必须被吞掉并返回 False')

    def test_returns_false_when_registry_has_nothing(self):
        manager = make_manager()
        with patch_registry() as registry:
            registry.get.return_value = None
            self.assertFalse(manager._migrate_quiz_session('old', 'new', object()))
        registry.migrate.assert_not_called()


class SessionReleaseTestCase(unittest.TestCase):
    """刷新时的关键信号：页面主动声明释放。

    实测日志：刷新后旧 PeerConnection 在服务端仍报 connected，仅凭连接状态
    会连续判"仍被占用"，提问编排永远迁不到新会话（现象：刷新后不再提问）。
    """

    def setUp(self):
        rtc_module._RELEASED_SESSIONS.clear()

    def tearDown(self):
        rtc_module._RELEASED_SESSIONS.clear()

    def test_released_session_is_not_in_use_even_if_pc_connected(self):
        manager = make_manager({'old': FakePC('connected')})
        self.assertTrue(manager._session_in_use('old'), '未声明释放时按连接状态判定')

        rtc_module.mark_session_released('old')
        self.assertFalse(manager._session_in_use('old'),
                         '页面已声明释放：即使 PC 仍报 connected 也不得视为占用')

    def test_release_enables_immediate_migration(self):
        """刷新场景端到端：声明释放后第一次尝试就迁移成功，不必等重试。"""
        manager = make_manager({'old': FakePC('connected')})
        rtc_module.mark_session_released('old')
        with patch_registry() as registry, \
                mock.patch.object(manager, '_schedule_quiz_carryover') as schedule:
            registry.migrate.return_value = True
            moved = manager._migrate_quiz_session('old', 'new', object())
        self.assertTrue(moved, '有释放信号时必须立即迁移')
        registry.migrate.assert_called_once_with('old', 'new', mock.ANY)
        self.assertFalse(schedule.called, '已经迁移成功就不该再排队重试')

    def test_reconnect_clears_release_marker(self):
        """同一会话重新建连后，释放标记必须作废，否则会被误判成没人看。"""
        rtc_module.mark_session_released('sid')
        self.assertTrue(rtc_module.is_session_released('sid'))
        rtc_module.clear_session_released('sid')
        self.assertFalse(rtc_module.is_session_released('sid'))

    def test_registering_new_pc_clears_release_marker(self):
        """/offer 建连时必须走 _register_session_pc：登记 PC 同时撤销释放标记。"""
        manager = make_manager()
        manager.pcs = set()
        rtc_module.mark_session_released('sid')

        pc = FakePC('connected')
        manager._register_session_pc('sid', pc)

        self.assertFalse(rtc_module.is_session_released('sid'),
                         '重新建连后仍留着释放标记会被误判成没人看')
        self.assertIs(manager._session_pcs['sid'], pc)
        self.assertIn(pc, manager.pcs)

    def test_release_expires(self):
        rtc_module.mark_session_released('sid')
        self.assertFalse(rtc_module.is_session_released('sid', ttl=-1),
                         '过期的释放标记不得继续生效')

    def test_release_ignores_empty_sessionid(self):
        self.assertFalse(rtc_module.mark_session_released(''))
        self.assertFalse(rtc_module.mark_session_released(None))
        self.assertFalse(rtc_module.is_session_released(''))

    def test_retry_window_covers_slow_disconnect(self):
        """没有释放信号时的兜底：重试次数要够多（实测旧连接十几秒仍 connected）。"""
        self.assertGreaterEqual(rtc_module.RTCManager.QUIZ_CARRYOVER_RETRIES, 10)
        self.assertGreaterEqual(
            rtc_module.RTCManager.QUIZ_CARRYOVER_RETRIES * rtc_module.RTCManager.QUIZ_CARRYOVER_DELAY,
            15.0, '重试窗口至少要覆盖十几秒的连接滞留')


if __name__ == '__main__':
    unittest.main()
