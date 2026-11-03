###############################################################################
#  WebRTC 连接管理 + RTC 音频/视频接收
###############################################################################

import json
import asyncio
import random
import copy
from typing import Dict, Optional
import queue

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, RTCConfiguration
from aiortc.rtcrtpsender import RTCRtpSender

from utils.logger import logger


# def _rand_session_id(n: int = 6) -> int:
#     """生成 N 位随机 session ID"""
#     return random.randint(10 ** (n - 1), 10 ** n - 1)


from server.session_manager import session_manager

# ── 页面主动声明的"已释放"会话 ─────────────────────────────────────────
# 刷新/关页时浏览器用 sendBeacon 上报，服务端据此判定该会话无人观看。
# 没有这个信号只能看 PeerConnection 状态，而刷新后的旧 PC 在服务端常常
# 长时间保持 connected（日志实测：刷新后 8 秒仍判"仍被占用"），
# 于是提问编排永远迁不到新会话——表现为"刷新后不再提问"。
import time as _time

_RELEASED_SESSIONS: Dict[str, float] = {}
RELEASE_TTL = 600.0


def mark_session_released(sessionid) -> bool:
    """记下"该会话的页面已经走了"。仅用于迁移判定，不改动会话本身。"""
    sid = str(sessionid or '').strip()
    if not sid:
        return False
    _RELEASED_SESSIONS[sid] = _time.time()
    logger.info('[提问] 页面声明释放会话 %s', sid)
    return True


def is_session_released(sessionid, ttl=None) -> bool:
    sid = str(sessionid or '').strip()
    ts = _RELEASED_SESSIONS.get(sid)
    if ts is None:
        return False
    if _time.time() - ts > (RELEASE_TTL if ttl is None else ttl):
        _RELEASED_SESSIONS.pop(sid, None)
        return False
    return True


def clear_session_released(sessionid) -> None:
    """该会话重新建立连接：释放标记作废，否则会被误判成"没人看"。"""
    _RELEASED_SESSIONS.pop(str(sessionid or '').strip(), None)


class RTCManager:
    """
    WebRTC 连接管理器。
    
    管理 PeerConnection 生命周期、音视频轨道收发、DataChannel。
    """

    def __init__(self, opt):
        """
        Args:
            opt: 全局配置
        """
        self.opt = opt
        self.pcs: set = set()
        # sessionid -> PeerConnection 映射，用于踢出会话时关闭对应连接。
        # 仅靠 pcs 集合无法反查连接属于哪个会话。
        self._session_pcs: Dict[str, RTCPeerConnection] = {}

    async def close_session(self, sessionid: str) -> bool:
        """关闭某个会话对应的 PeerConnection。返回是否确实关闭了连接。"""
        pc = self._session_pcs.pop(sessionid, None)
        if pc is None:
            return False
        try:
            await pc.close()
            logger.info("已关闭会话 %s 的 PeerConnection", sessionid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭会话 %s 的 PeerConnection 失败: %s", sessionid, exc)
        self.pcs.discard(pc)
        return True

    # 页面刷新时旧连接往往还没被服务端判定断开，所以"未迁移成功"要退避重试。
    # 3 次（6 秒）不够：实测刷新后旧 PC 在服务端能保持 connected 十几秒以上，
    # 放大到 10 次（20 秒）覆盖"没收到释放信号"的兜底场景。
    QUIZ_CARRYOVER_RETRIES = 10
    QUIZ_CARRYOVER_DELAY = 2.0

    def _register_session_pc(self, sessionid, pc):
        """登记会话的 PeerConnection，并撤销该会话的"已释放"标记。

        撤销是必须的：同一会话重新建连（页面重连而非刷新）时若还留着释放标记，
        迁移判定会把它当成"没人看"，编排器可能被后来者抢走。
        """
        self.pcs.add(pc)
        self._session_pcs[sessionid] = pc
        clear_session_released(sessionid)

    def _session_in_use(self, sessionid):
        """该会话是否仍被某个页面占用。

        两重判据，任一为真才算"有人看"：
        1. 页面**没有**声明释放（`POST /api/quiz/release`）——这是刷新场景的关键，
           刷新后的旧 PeerConnection 在服务端仍可能长期是 connected，
           只看连接状态会把刷新误判成"仍在用"，提问永远迁不过去；
        2. 存在活跃的 PeerConnection（另一块展示页真的连着）。

        注意也不能只看 ``session_manager.has_session``：刷新时旧页面已经关掉连接，
        但服务端要等 ICE 判定超时才会把会话摘掉（`disconnected` 甚至不会触发清理），
        用 has_session 判定就会一直"以为旧页面还在"，导致刷新后提问丢失。
        """
        if is_session_released(sessionid):
            return False
        pc = getattr(self, '_session_pcs', {}).get(sessionid)
        if pc is None:
            return False
        state = getattr(pc, 'connectionState', None)
        return state in ('new', 'connecting', 'connected')

    def _migrate_quiz_session(self, old_sessionid, new_sessionid, speaker, retries=None):
        """把旧会话上的提问编排搬到新会话（页面刷新/重连）。

        只在**旧页面确实不再占用**时迁移：另一块展示页还连着就抢走它的提问，
        会让那一边突然失声。迁移失败不得影响 /offer 本身。
        """
        old = str(old_sessionid or '').strip()
        if not old or old == new_sessionid:
            return False
        try:
            from server.quiz_routes import registry as quiz_registry
            if quiz_registry.get(old) is None:
                return False                       # 旧会话上没有提问，无事可做
            if self._session_in_use(old):
                # 旧页面可能只是还没被判定断开（刷新时很常见）：稍后再试一次。
                logger.info('[提问] 会话 %s 仍被占用，稍后重试迁移提问编排', old)
                self._schedule_quiz_carryover(old, new_sessionid, speaker, retries)
                return False
            if quiz_registry.migrate(old, new_sessionid, speaker):
                logger.info('[提问] 页面重连：提问编排 %s -> %s', old, new_sessionid)
                return True
        except Exception:  # noqa: BLE001
            logger.exception('[提问] 迁移提问编排失败：%s -> %s', old, new_sessionid)
        return False

    def _schedule_quiz_carryover(self, old_sessionid, new_sessionid, speaker, retries=None):
        """退避重试迁移：刷新时旧连接要过一会儿才会被服务端判定断开。"""
        remaining = self.QUIZ_CARRYOVER_RETRIES if retries is None else retries
        if remaining <= 0:
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False                           # 没有事件循环（单测）时只做立即尝试
        loop.call_later(
            self.QUIZ_CARRYOVER_DELAY,
            self._migrate_quiz_session,
            old_sessionid, new_sessionid, speaker, remaining - 1,
        )
        return True

    async def handle_offer(self, request):
        """处理 WebRTC offer 信令"""
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        if False: # 不再由 RTCManager 控制 max_session，让业务逻辑或SessionManager 控制
            logger.info('reach max session')
            return web.Response(
                content_type="application/json",
                text=json.dumps({"code": -1, "msg": "reach max session"}),
            )

        #sessionid = _rand_session_id()

        # 每条 WebRTC 连接必须拥有独立的 Avatar session。
        # 多个 HumanPlayer 共享一个 session 时，output._player 会互相覆盖，
        # 造成音频队列欠载、重复播放和电流声。sessionid 仅作为前端上下文传递，
        # 不再用于绑定新的 WebRTC 连接。
        request_sessionid = params.get('sessionid')
        sessionid = await session_manager.create_session(params)
        if request_sessionid and session_manager.has_session(request_sessionid):
            logger.info('new sessionid=%s for WebRTC connection; requested sessionid=%s remains isolated',
                        sessionid, request_sessionid)
        else:
            logger.info('offer sessionid=%s', sessionid)
        avatar_session = session_manager.get_session(sessionid)

        # 页面刷新/重连会新建会话：把旧会话上的提问编排迁移过来，
        # 否则刷新一次提问模式就断（展示页再也查不到 in_quiz）。
        self._migrate_quiz_session(params.get('carryover_sessionid'), sessionid, avatar_session)

        # 创建 PeerConnection
        ice_server = RTCIceServer(urls='stun:stun.freeswitch.org:3478')
        pc = RTCPeerConnection(
            configuration=RTCConfiguration(iceServers=[ice_server])
        )
        self._register_session_pc(sessionid, pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info("Connection state is %s", pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                await pc.close()
                self.pcs.discard(pc)
                self._session_pcs.pop(sessionid, None)
                session_manager.remove_session(sessionid)

        # 添加发送轨道
        from server.webrtc import HumanPlayer
        player = HumanPlayer(avatar_session)
        pc.addTrack(player.audio)
        pc.addTrack(player.video)

        # 设置编解码器偏好
        capabilities = RTCRtpSender.getCapabilities("video")
        preferences = list(filter(lambda x: x.name == "H264", capabilities.codecs))
        preferences += list(filter(lambda x: x.name == "VP8", capabilities.codecs))
        preferences += list(filter(lambda x: x.name == "rtx", capabilities.codecs))
        transceiver = pc.getTransceivers()[1]
        transceiver.setCodecPreferences(preferences)

        await pc.setRemoteDescription(offer)

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return web.Response(
            content_type="application/json",
            text=json.dumps({
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
                "sessionid": sessionid,
            }),
        )

    async def handle_rtcpush(self, push_url, sessionid: str):
        """RTCPush 模式：主动推流"""
        import aiohttp
        await session_manager.create_session({}, sessionid)
        avatar_session = session_manager.get_session(sessionid)

        pc = RTCPeerConnection()
        self.pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info("Connection state is %s", pc.connectionState)
            if pc.connectionState == "failed":
                await pc.close()
                self.pcs.discard(pc)

        from server.webrtc import HumanPlayer
        player = HumanPlayer(avatar_session)
        pc.addTrack(player.audio)
        pc.addTrack(player.video)

        await pc.setLocalDescription(await pc.createOffer())

        async with aiohttp.ClientSession() as session:
            async with session.post(push_url, data=pc.localDescription.sdp) as response:
                answer_sdp = await response.text()

        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer_sdp, type='answer')
        )

    async def shutdown(self):
        """关闭所有 PeerConnection"""
        coros = [pc.close() for pc in self.pcs]
        await asyncio.gather(*coros)
        self.pcs.clear()
