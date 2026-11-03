# -*- coding: utf-8 -*-
"""有界队列的安全写入：保证生产者线程永远不会被队列永久卡死。

现场故障（2026-10-21）：浏览器断开连接后，aiortc 不再消费音频队列，
`HumanPlayer.push_audio` 的 `put(block=True)` 永久阻塞，随后推理队列、
特征队列依次塞满，`render()` 主循环卡在 `mel.run_step()` 上再也回不到
`quit_event` 判断；而 `HumanPlayer._stop()` 又在事件循环线程里无超时
`join()` 这条线程——整个 aiohttp 事件循环被一个已死的会话拖住，
表现为「断开后再点开始连接连不上」，连静态文件都不响应。

`quit_event.set()` 对阻塞在 `put` 上的线程是无效的：它阻塞的是队列，
不是事件。因此写入必须有界，拆除时再配合清空队列把生产者放出来。
"""

import queue

# 单次限时等待上限。最坏情况总阻塞为 2 倍该值（丢一项腾位后再试一次）。
DEFAULT_TIMEOUT = 0.5


def put_capped(q, item, timeout=DEFAULT_TIMEOUT, should_abort=None, on_drop=None):
    """把 ``item`` 放入有界队列，绝不无限阻塞。

    策略：
    1. 限时等待写入（正常运行时队列几乎不会满，偶发拥塞可自愈）；
    2. 仍满则丢弃**最旧**一项腾出位置再试一次（实时流要的是最新帧）；
    3. 仍失败则丢弃本次写入。

    Args:
        q: 目标队列（有界）。
        item: 待写入对象。
        timeout: 单次等待上限（秒）。
        should_abort: 返回 True 时立即放弃写入（通常传 ``quit_event.is_set``）。
        on_drop: 丢弃/腾位时的回调 ``on_drop(reason)``，reason ∈
            ``'abort' | 'full' | 'drop'``；调用方用它做限频告警。

    Returns:
        bool: True 表示已写入，False 表示本次写入被放弃。
    """
    if should_abort is not None and should_abort():
        _notify(on_drop, "abort")
        return False

    try:
        q.put(item, timeout=timeout)
        return True
    except queue.Full:
        pass

    # 队列满：丢掉最旧的一项腾位置。实时音视频丢旧帧比卡死可接受得多。
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    except Exception:  # noqa: BLE001 - 队列实现异常不应让写入方崩溃
        pass
    _notify(on_drop, "full")

    try:
        q.put(item, timeout=timeout)
        return True
    except queue.Full:
        _notify(on_drop, "drop")
        return False


def _notify(callback, reason):
    """回调异常不得影响生产线程。"""
    if callback is None:
        return
    try:
        callback(reason)
    except Exception:  # noqa: BLE001
        pass


class DropReporter:
    """丢弃事件的限频记录器：首次必报，之后每 ``every`` 次报一次。"""

    def __init__(self, label, logger, every=100):
        self.label = label
        self.logger = logger
        self.every = max(1, int(every))
        self.count = 0
        self.dropped = 0

    def __call__(self, reason):
        self.count += 1
        if reason == "drop":
            self.dropped += 1
        if self.count == 1 or self.count % self.every == 0:
            try:
                self.logger.warning(
                    "[背压] %s 队列写入受阻（%s），累计 %d 次，其中丢弃 %d 次",
                    self.label, reason, self.count, self.dropped)
            except Exception:  # noqa: BLE001
                pass
