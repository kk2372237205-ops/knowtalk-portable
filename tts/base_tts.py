from threading import Thread
import queue
from queue import Queue
from io import BytesIO
from enum import Enum

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from avatars.base_avatar import BaseAvatar

from utils.logger import logger

class State(Enum):
    RUNNING = 0
    PAUSE = 1


# 主引擎连续失败达到该次数、且备用引擎已成功接管后，本会话不再尝试主引擎。
#
# 取 2 而不是 1：一次失败可能只是网络抖动，而一旦短路就要用备用引擎（音质更差）
# 讲完整堂课，代价明显大于多等一次失败。一次存疑、两次即证实。
#
# 为什么需要短路：实测在线引擎被服务端拒绝时，单段失败要等待约 3-4 秒；
# 一段 12 页的讲稿若每段都重等一次，累计约 40 秒的空白停顿。
PRIMARY_FAILURE_THRESHOLD = 2


class TTSSynthesisError(RuntimeError):
    """TTS 引擎合成失败。

    引擎 MUST 用它上报失败，而不是记录日志后静默返回——静默返回会让课堂
    表现为"数字人突然不说话"，而使用者看不到任何原因。

    `hint` 用于给出可操作的排查建议（例如"该引擎依赖外部服务，请改用离线引擎"）。
    """

    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint


class BaseTTS:
    def __init__(self, opt, parent: "BaseAvatar"):
        self.opt = opt
        self.parent = parent

        #self.fps = opt.fps # 20 ms per frame
        self.sample_rate = 16000
        self.chunk = self.sample_rate // (opt.fps*2) # 320 samples per chunk (20ms * 16000 / 1000)
        self.input_stream = BytesIO()

        self.msgqueue = Queue()
        self.state = State.RUNNING
        self.processing = False  # 标记 TTS 是否正在处理音频

        # 备用引擎（至多回退一次）；None 表示未启用
        self._fallback = None
        self._fallback_name = getattr(opt, 'TTS_FALLBACK', '') or ''
        self._fallback_tried = False
        self.last_error = None          # 最近一次失败的 (message, hint)
        self.fallback_count = 0         # 实际发生回退的次数
        self._primary_fail_streak = 0   # 主引擎连续失败次数；成功一次即归零
        self._skip_primary = False      # 主引擎已被反复证实不可用：本会话直接走备用引擎

    def flush_talk(self):
        # 逐项清空队列，避免直接操作底层 deque 导致并发状态不一致。
        while True:
            try:
                self.msgqueue.get_nowait()
            except queue.Empty:
                break
        self.state = State.PAUSE

    def put_msg_txt(self, msg: str, datainfo: dict = {}): 
        if len(msg) > 0:
            self.msgqueue.put((msg, datainfo))

    def render(self, quit_event):
        if hasattr(self, '_process_thread') and self._process_thread.is_alive():
            logger.warning('[TTS] process_tts 线程已在运行，跳过重复创建')
            return
        logger.info('[TTS] 创建 process_tts 线程')
        self._process_thread = Thread(target=self.process_tts, args=(quit_event,), daemon=True)
        self._process_thread.start()

    def _ensure_fallback(self):
        """懒加载备用引擎。返回实例或 None，并记录不可用的原因。"""
        if self._fallback is not None:
            return self._fallback
        name = self._fallback_name
        if not name:
            return None
        if name == getattr(self.opt, 'tts', None):
            # 备用与主引擎同名是无意义配置：直接原样重试会形成自环。
            # 用 WARNING 而非 ERROR：默认兜底生效后，"主引擎=sapi 且默认兜底=sapi"
            # 属于正常配置（使用者只是选了离线引擎），不该报成错误。
            logger.warning('[TTS] 备用引擎与主引擎同为 %s，无需回退（视为未配置）', name)
            self._fallback_name = ''
            return None
        try:
            import importlib
            import registry
            module = _TTS_MODULES.get(name)
            if not module:
                logger.error('[TTS] 备用引擎 %s 未注册，可用: %s', name, ', '.join(sorted(_TTS_MODULES)))
                self._fallback_name = ''
                return None
            importlib.import_module(module)
            # 备用引擎复用同一个 parent 与 opt，保证产出与下游契约一致
            self._fallback = registry.create("tts", name, opt=self.opt, parent=self.parent)
            logger.info('[TTS] 备用引擎已就绪: %s', name)
            return self._fallback
        except Exception:  # noqa: BLE001
            logger.exception('[TTS] 备用引擎 %s 加载失败，将不执行回退', name)
            self._fallback_name = ''
            return None

    def _synthesize_with_fallback(self, msg):
        """合成一段文本。返回是否成功产出。

        三条规则：

        1. 默认优先主引擎（好音质）；
        2. 主引擎失败时至多回退一次到备用引擎；
        3. 主引擎被反复证实不可用、且备用引擎已接管后，本会话后续合成直接使用
           备用引擎——不再为每段文本重复等待同一个失败。短路状态只存在于本实例
           （一个会话），新建会话会重新尝试主引擎，使外部服务恢复后自动回到好音质。

        无论走哪条路，文本都必须被合成；MUST NOT 静默丢弃。
        """
        if self._skip_primary:
            fallback = self._ensure_fallback()
            if fallback is not None:
                return self._speak_with(fallback, msg, short_circuited=True)
            # 备用引擎已不可用，短路的前提不成立：解除后按常规路径重试主引擎
            self._exit_short_circuit('备用引擎不可用')

        try:
            self.txt_to_audio(msg)
            self.last_error = None
            self._primary_fail_streak = 0
            return True
        except TTSSynthesisError as exc:
            self.last_error = (exc.message, exc.hint)
            logger.error('[TTS] 合成失败: %s%s', exc.message,
                         ('（%s）' % exc.hint) if exc.hint else '')
        except Exception as exc:  # noqa: BLE001
            self.last_error = ('%s: %s' % (type(exc).__name__, exc), '')
            logger.exception('[TTS] 合成出现未预期错误')

        self._primary_fail_streak += 1

        fallback = self._ensure_fallback()
        if fallback is None:
            if self._fallback_name:
                logger.error('[TTS] 未启用可用的备用引擎，本段文本未能发声')
            return False

        logger.warning('[TTS] 主引擎失败，改用备用引擎 %s 重试（同一段文本，仅此一次）',
                       self._fallback_name)
        return self._speak_with(fallback, msg, short_circuited=False)

    def _speak_with(self, engine, msg, short_circuited):
        """用备用引擎合成。失败时若处于短路状态则解除——短路的前提已不成立。"""
        try:
            engine.txt_to_audio(msg)
        except Exception as exc:  # noqa: BLE001
            logger.error('[TTS] 备用引擎 %s 失败: %s，本段文本未能发声（不再尝试其它引擎）',
                         self._fallback_name, exc)
            if short_circuited:
                self._exit_short_circuit('备用引擎失败')
            return False

        self.fallback_count += 1
        if short_circuited:
            return True

        logger.warning('[TTS] 已回退到备用引擎 %s 并成功发声', self._fallback_name)
        if self._primary_fail_streak >= PRIMARY_FAILURE_THRESHOLD:
            self._enter_short_circuit()
        return True

    def _enter_short_circuit(self):
        if self._skip_primary:
            return
        self._skip_primary = True
        logger.warning(
            '[TTS] 主引擎 %s 连续 %d 次合成失败，且备用引擎 %s 已成功接管；'
            '本会话后续合成将直接使用备用引擎，不再重复等待主引擎（新建会话会重新尝试主引擎）',
            getattr(self.opt, 'tts', '?'), self._primary_fail_streak, self._fallback_name)

    def _exit_short_circuit(self, reason):
        if not self._skip_primary:
            return
        self._skip_primary = False
        self._primary_fail_streak = 0
        logger.warning('[TTS] 解除主引擎短路（%s），下一次合成将重新尝试 %s',
                       reason, getattr(self.opt, 'tts', '?'))

    def process_tts(self, quit_event):        
        while not quit_event.is_set():
            try:
                msg: tuple[str, dict] = self.msgqueue.get(block=True, timeout=1)
                self.state = State.RUNNING
                self.processing = True
            except queue.Empty:
                continue
            slide_id = msg[1].get('slide_id', '无')
            logger.info(f'[TTS队列] 开始处理段: slide_id={slide_id}, text={msg[0][:40]}...')
            self._synthesize_with_fallback(msg)
            self.processing = False
            logger.info(f'[TTS队列] 完成处理段: slide_id={slide_id}')
            # 段间清空 ASR 帧缓冲区，防止上一段音频尾部混入下一段
            if hasattr(self.parent, 'flush_asr_pipeline'):
                self.parent.flush_asr_pipeline()
        self.stop_tts()
        logger.info('ttsreal thread stop')
    
    def txt_to_audio(self, msg: tuple[str, dict]):
        pass

    def stop_tts(self):
        pass


# 引擎名 -> 模块路径。与 avatars/base_avatar.py 的 _tts_modules 保持一致，
# 备用引擎需要按模块路径自行导入注册。
_TTS_MODULES = {
    'gpt-sovits': 'tts.sovits',
    'cosyvoice': 'tts.cosyvoice',
    'fishtts': 'tts.fish',
    'xtts': 'tts.xtts',
    'edgetts': 'tts.edge',
    'tencent': 'tts.tencent',
    'doubao': 'tts.doubao',
    'azuretts': 'tts.azure',
    'qwentts': 'tts.qwentts',
    'sapi': 'tts.sapi',
}
