###############################################################################
#  全局会话管理器 (Session Manager)
###############################################################################

import asyncio
import time
import uuid
from typing import Dict, Optional
from utils.logger import logger
from avatars.base_avatar import BaseAvatar

def _rand_session_id() -> str:
    """生成 UUID session ID"""
    return str(uuid.uuid4())

class SessionManager:
    """
    全局数字人会话管理器。
    
    统一管理 avatar_sessions 生命周期，并在脱离 WebRTC 时依然保持服务可用。
    """
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "initialized"):
            self.sessions: Dict[str, BaseAvatar] = {}
            self.build_session_fn = None
            # sessionid -> 最后活动时间戳（用于空闲清理）
            self._last_active: Dict[str, float] = {}
            self.initialized = True

    def init_builder(self, build_session_fn):
        """配置用于构建 avatar_session 的工厂函数"""
        self.build_session_fn = build_session_fn

    def touch(self, sessionid: str):
        """刷新会话活动时间（被 get_session / 各类请求调用时触发）。"""
        if sessionid in self.sessions:
            self._last_active[sessionid] = time.time()

    def get_session(self, sessionid: str) -> Optional[BaseAvatar]:
        """获取已存活的会话"""
        s = self.sessions.get(sessionid)
        if s is not None:
            self.touch(sessionid)
        return s

    def has_session(self, sessionid: str) -> bool:
        """检查会话是否存在"""
        return sessionid in self.sessions and self.sessions[sessionid] is not None
        
    async def create_session(self, params: dict, sessionid: str = None) -> str:
        """
        在异步环境中创建一个新会话
        如果 sessionid 为 None，则自动生成。
        """
        if self.build_session_fn is None:
            raise Exception("SessionManager builder not initialized")
            
        if sessionid is None:
            sessionid = _rand_session_id()
            
        logger.info('Creating sessionid=%s, current session num=%d', sessionid, len(self.sessions))
        # 预先占位防止重复
        self.sessions[sessionid] = None
        self._last_active[sessionid] = time.time()

        # 在线程池中构建 session（加载模型非常耗时）
        avatar_session = await asyncio.get_event_loop().run_in_executor(
            None, self.build_session_fn, sessionid, params
        )
        self.sessions[sessionid] = avatar_session
        self._last_active[sessionid] = time.time()
        return sessionid
        
    def add_session(self, sessionid: str, avatar_session: BaseAvatar):
        """同步添加静态或外部管理的会话（供非服务端入口调用）"""
        self.sessions[sessionid] = avatar_session
        self._last_active[sessionid] = time.time()

    def remove_session(self, sessionid: str):
        """销毁会话资源"""
        if sessionid in self.sessions:
            logger.info(f"Removing session {sessionid}")
            avatar_session = self.sessions.pop(sessionid, None)
            self._last_active.pop(sessionid, None)
            if avatar_session is not None:
                try:
                    # 主动释放资源：设置 quit_event 停止所有线程
                    if hasattr(avatar_session, 'quit_event') and avatar_session.quit_event is not None:
                        avatar_session.quit_event.set()
                    # 顺序很重要：必须先清空队列把阻塞在 put 上的渲染/推理线程
                    # "叫醒"，再调 output.stop()（内部会 join 这些线程）。
                    # 反过来的话 join 会一直等一条还卡在队列上的线程，
                    # 而本方法跑在事件循环线程里 —— 那会把整个服务拖死。
                    if hasattr(avatar_session, 'flush_talk'):
                        try:
                            avatar_session.flush_talk()
                        except Exception as e:
                            logger.warning(f"Error flushing session {sessionid}: {e}")
                    if hasattr(avatar_session, 'output') and hasattr(avatar_session.output, 'stop'):
                        avatar_session.output.stop()
                    # 停止录音（如果正在进行）
                    if hasattr(avatar_session, 'recording') and avatar_session.recording:
                        avatar_session.stop_recording()
                    # 释放本会话持有的手势素材（每套约 200 MB，不能留给下个会话）
                    if hasattr(avatar_session, 'clear_gestures'):
                        avatar_session.clear_gestures()
                    # 讲稿留底跟着会话走：id 已经作废，留着只会让字典无限增长
                    try:
                        from utils import material_manager as mat_mgr
                        mat_mgr.forget_lecture(sessionid)
                    except Exception:  # noqa: BLE001 - 清理失败不影响释放流程
                        pass
                    logger.info(f"Session {sessionid} resources released")
                except Exception as e:
                    logger.warning(f"Error releasing session {sessionid}: {e}")

    def cleanup_idle(self, timeout_seconds: int = 1800) -> int:
        """
        回收空闲超时的会话（无 WebRTC 依赖、超过 timeout_seconds 未活动）。

        静态会话 '0'（virtualcam/rtmp 常驻）永远不清理。
        返回回收数量。
        """
        now = time.time()
        stale = []
        for sessionid, avatar_session in self.sessions.items():
            if sessionid == '0':
                continue  # 常驻会话，跳过
            last = self._last_active.get(sessionid, now)
            idle = now - last
            # 正在说话的会话不清理（即便轮询触发了 touch，也加一层 is_speaking 保护）
            if idle > timeout_seconds:
                try:
                    if hasattr(avatar_session, 'is_speaking') and avatar_session.is_speaking():
                        continue
                except Exception:
                    pass
                stale.append(sessionid)
        for sessionid in stale:
            logger.info(f"[会话清理] 会话 {sessionid} 空闲超时，开始回收")
            self.remove_session(sessionid)
        return len(stale)

# 单例抛出
session_manager = SessionManager()
