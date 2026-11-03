###############################################################################
#  Copyright (C) 2024 KnowTalking@lipku https://github.com/lipku/KnowTalking
#  Copyright (C) 2026 KnowTalk Contributors
#  email: lipku@foxmail.com
# 
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  
#       http://www.apache.org/licenses/LICENSE-2.0
# 
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

import asyncio
import json
import logging
import threading
import time
from typing import Tuple, Dict, Optional, Set, Union
import queue
from collections import deque
from av.frame import Frame
from av.packet import Packet
from av import AudioFrame
import fractions
import numpy as np

from utils.queue_guard import DropReporter, put_capped

AUDIO_PTIME = 0.020  # 20ms audio packetization
VIDEO_CLOCK_RATE = 90000
VIDEO_PTIME = 0.040 #1 / 25  # 30fps
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)
SAMPLE_RATE = 16000
AUDIO_TIME_BASE = fractions.Fraction(1, SAMPLE_RATE)

# MuseTalk 的视频推理和音频打包在不同线程中运行。消费端严格每 20ms 索取一帧，
# 生产端偶发一次 CPU/GPU 调度抖动就会被误判成缺帧并塞入静音。首个语音包先积累
# 120ms，可吸收正常抖动；朗读途中则额外等待至多 80ms。两者均小于人能察觉的
# 对话延迟，但能显著减少“说话中间被补静音”造成的断续感。
AUDIO_PREROLL_PACKETS = 6
AUDIO_PREROLL_WAIT = 0.160
AUDIO_UNDERRUN_WAIT = 0.080

# 拆除会话时等待渲染线程退出（秒）。
# 必须有界：该 join 发生在 aiohttp 事件循环线程里（aiortc 轨道 stop 的调用路径
# 和 remove_session 都在 loop 上），无界等待会让一个已死的会话拖死整个服务。
TEARDOWN_JOIN_TIMEOUT = 2.0

# 音频队列单次写入的等待上限（秒）。超时后丢最旧一帧腾位。
AUDIO_PUSH_TIMEOUT = 0.5
# 视频队列同理。视频丢帧比音频更可容忍，等待上限可以更短。
VIDEO_PUSH_TIMEOUT = 0.2

#from aiortc.contrib.media import MediaPlayer, MediaRelay
#from aiortc.rtcrtpsender import RTCRtpSender
from aiortc import (
    MediaStreamTrack,
)

logging.basicConfig()
logger = logging.getLogger(__name__)
from utils.logger import logger as mylogger


class PlayerStreamTrack(MediaStreamTrack):
    """
    A video track that returns an animated flag.
    """

    def __init__(self, player, kind):
        super().__init__()  # don't forget this!
        self.kind = kind
        self._player = player
        # 音频不能像视频一样丢旧帧，否则会产生明显的断音和电流声。
        # 100 帧约 2 秒缓冲，兼顾 TTS 合成抖动和端到端延迟。
        self._queue = queue.Queue(maxsize=100 if kind == 'audio' else 30)
        self.timelist = [] #记录最近包的时间戳
        self.current_frame_count = 0
        # "播放中断粮"：只在**会话正在朗读**时发生的空转才计入——那才是会在语音
        # 中间插入静音、听得出断续的情形。
        self.audio_underruns = 0
        # 空闲空转：尚未开始朗读（或已朗读完）时的空转。它只是维持实时轨时钟，
        # 不是故障，因此单独计数、不以"欠载/中断"措辞告警。
        self.audio_idle_stalls = 0
        self._produced_real_audio = False
        # 首个带 ``status=start`` 的实际语音包不会立刻交给 RTP；先在这里留出
        # 一小段抖动缓冲。只保留音频包对象，不复制 PCM，内存占用极小。
        self._audio_preroll = deque()
        self.audio_prerolls = 0
        if self.kind == 'video':
            self.framecount = 0
            self.lasttime = time.perf_counter()
            self.totaltime = 0
    
    _start: float
    _timestamp: int

    async def next_timestamp(self) -> Tuple[int, fractions.Fraction]:
        if self.readyState != "live":
            raise Exception

        if self.kind == 'video':
            if hasattr(self, "_timestamp"):
                #self._timestamp = (time.time()-self._start) * VIDEO_CLOCK_RATE
                self._timestamp += int(VIDEO_PTIME * VIDEO_CLOCK_RATE)
                self.current_frame_count += 1
                wait = self._start + self.current_frame_count * VIDEO_PTIME - time.perf_counter()
                # wait = self.timelist[0] + len(self.timelist)*VIDEO_PTIME - time.time()               
                if wait>0:
                    await asyncio.sleep(wait)
                # if len(self.timelist)>=100:
                #     self.timelist.pop(0)
                # self.timelist.append(time.time())
            else:
                self._start = time.perf_counter()
                self._timestamp = 0
                self.timelist.append(self._start)
                mylogger.info('video start:%f',self._start)
            return self._timestamp, VIDEO_TIME_BASE
        else: #audio
            if hasattr(self, "_timestamp"):
                #self._timestamp = (time.time()-self._start) * SAMPLE_RATE
                self._timestamp += int(AUDIO_PTIME * SAMPLE_RATE)
                self.current_frame_count += 1
                wait = self._start + self.current_frame_count * AUDIO_PTIME - time.perf_counter()
                # wait = self.timelist[0] + len(self.timelist)*AUDIO_PTIME - time.time()
                if wait>0:
                    await asyncio.sleep(wait)
                # if len(self.timelist)>=200:
                #     self.timelist.pop(0)
                #     self.timelist.pop(0)
                # self.timelist.append(time.time())
            else:
                self._start = time.perf_counter()
                self._timestamp = 0
                self.timelist.append(self._start)
                mylogger.info('audio start:%f',self._start)
            return self._timestamp, AUDIO_TIME_BASE

    def _player_speaking(self):
        """会话当前是否正在朗读。

        返回 True/False；**无法判定时返回 None**，调用方按"可能中断"处理以保留旧信号。
        """
        checker = getattr(self._player, 'is_speaking', None)
        if checker is None:
            return None
        try:
            return bool(checker())
        except Exception:
            return None

    @staticmethod
    def _is_speech_start(eventpoint) -> bool:
        return isinstance(eventpoint, dict) and eventpoint.get('status') == 'start'

    async def _wait_for_audio_item(self, timeout: float):
        """等待生产线程交付一个音频包，超时返回 ``None``。

        ``queue.Queue`` 由渲染线程写、aiohttp 事件循环读，不能在这里做阻塞
        ``get``；使用短暂 ``asyncio.sleep`` 既让出事件循环，也不改变 RTP 时钟。
        """
        deadline = time.perf_counter() + max(0.0, timeout)
        while True:
            try:
                return self._queue.get_nowait()
            except queue.Empty:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return None
                await asyncio.sleep(min(0.002, remaining))

    async def _next_audio_item(self):
        """取下一包实际音频，并在一段语音开头建立小型抖动缓冲。"""
        if self._audio_preroll:
            return self._audio_preroll.popleft()

        item = await self._wait_for_audio_item(0.0)
        if item is None:
            # 朗读过程中，给渲染线程最多 80ms 的调度余量。此前这里只有 20ms，
            # GPU 一次正常的 VAE 波动就会直接变成可闻的静音洞。
            grace = AUDIO_UNDERRUN_WAIT if self._player_speaking() is not False else AUDIO_PTIME
            return await self._wait_for_audio_item(grace)

        _, eventpoint = item
        if not self._is_speech_start(eventpoint):
            return item

        self._audio_preroll.append(item)
        deadline = time.perf_counter() + AUDIO_PREROLL_WAIT
        while len(self._audio_preroll) < AUDIO_PREROLL_PACKETS:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            item = await self._wait_for_audio_item(remaining)
            if item is None:
                break
            self._audio_preroll.append(item)

        self.audio_prerolls += 1
        mylogger.info(
            '音频预缓冲就绪：第 %d 段，缓存 %d 包（%.0fms）',
            self.audio_prerolls,
            len(self._audio_preroll),
            len(self._audio_preroll) * AUDIO_PTIME * 1000,
        )
        return self._audio_preroll.popleft()

    async def recv(self) -> Union[Frame, Packet]:
        # frame = self.frames[self.counter % 30]            
        self._player._start(self)
        if self.kind == 'audio':
            item = await self._next_audio_item()
            if item is not None:
                frame, eventpoint = item
                if not self._produced_real_audio:
                    self._produced_real_audio = True
                    if self.audio_idle_stalls:
                        mylogger.info(
                            '音频轨开始出帧（此前空转 %d 帧，约 %.2f 秒）',
                            self.audio_idle_stalls,
                            self.audio_idle_stalls * AUDIO_PTIME)
                if self.audio_underruns:
                    # 音频恢复，计数重置，让下一次独立中断能被单独识别。
                    self.audio_underruns = 0
            else:
                if self._player_speaking() is False:
                    # 还没开始朗读（或已朗读完）：插入静音只是维持实时轨时钟，
                    # 不是故障，不计入中断粮、也不以“欠载”措辞告警。
                    self.audio_idle_stalls += 1
                else:
                    self.audio_underruns += 1
                    if self.audio_underruns == 1 or self.audio_underruns % 25 == 0:
                        mylogger.warning(
                            'audio queue underrun: count=%d, queued=%d',
                            self.audio_underruns,
                            self._queue.qsize(),
                        )
                audio = np.zeros((1, 320), dtype=np.int16)
                frame = AudioFrame.from_ndarray(audio, layout='mono', format='s16')
                frame.sample_rate = 16000
                eventpoint = None
        else:
            # 视频轨道：等待数据
            while True:
                try:
                    frame, eventpoint = self._queue.get_nowait()
                    break
                except queue.Empty:
                    await asyncio.sleep(0.005)
                
        pts, time_base = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = time_base
        if eventpoint and self._player is not None:
            self._player.notify(eventpoint)
        if frame is None:
            self.stop()
            raise Exception
        if self.kind == 'video':
            self.totaltime += (time.perf_counter() - self.lasttime)
            self.framecount += 1
            self.lasttime = time.perf_counter()
            if self.framecount==100:
                mylogger.info(f"------actual avg final fps:{self.framecount/self.totaltime:.4f}")
                self.framecount = 0
                self.totaltime=0
        return frame
    
    def stop(self):
        super().stop()
        # Drain & delete remaining frames
        self._audio_preroll.clear()
        while not self._queue.empty():
            item = self._queue.get_nowait()
            del item
        if self._player is not None:
            self._player._stop(self)
            self._player = None

def player_worker_thread(
    quit_event,
    container
):
    container.render(quit_event)

class HumanPlayer:

    def __init__(
        self, avatar_session, format=None, options=None, timeout=None, loop=False, decode=True
    ):
        self.__thread: Optional[threading.Thread] = None
        self.__thread_quit: Optional[threading.Event] = None

        # examine streams
        self.__started: Set[PlayerStreamTrack] = set()
        self.__audio: Optional[PlayerStreamTrack] = None
        self.__video: Optional[PlayerStreamTrack] = None

        self.__audio = PlayerStreamTrack(self, kind="audio")
        self.__video = PlayerStreamTrack(self, kind="video")

        self.__container = avatar_session
        if hasattr(self.__container, 'output'):
            self.__container.output._player = self

        # 音频/视频队列的丢弃告警限频器（首次必报，之后按次数抽样）
        self._audio_drop_reporter: Optional[DropReporter] = None
        self._video_drop_reporter: Optional[DropReporter] = None
        # BaseAvatar 在不同输出帧率下会给出不同长度的 PCM 块：20fps 时每块
        # 是 400 samples（25ms），而 WebRTC/Opus 固定要 320 samples（20ms）。
        # 余数必须跨调用保留，不能截掉；截掉会直接变成周期性断音。
        self._audio_remainder = np.empty(0, dtype=np.int16)

    def is_speaking(self) -> bool:
        """当前会话是否正在朗读。

        供音频轨区分"还没开始说话"与"说话途中断粮"：只有后者会在语音中间插入
        静音、听得出断续；前者只是空闲，插入静音是为了维持实时轨时钟。
        """
        try:
            return bool(self.__container.is_speaking())
        except Exception:
            return False

    def push_video(self, frame):
        from av import VideoFrame
        if getattr(self.__video, 'readyState', 'live') == 'ended':
            return
        new_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        # 视频队列满了丢弃旧帧，避免阻塞（put_capped 同样丢最旧，
        # 但最多只等一个 timeout，不会像裸 put 那样无限挂起）。
        if self._video_drop_reporter is None:
            self._video_drop_reporter = DropReporter(
                'webrtc-video', mylogger, every=200)
        put_capped(
            self.__video._queue, (new_frame, None),
            timeout=VIDEO_PUSH_TIMEOUT,
            on_drop=self._video_drop_reporter,
        )

    def push_audio(self, frame, eventpoint=None):
        from av import AudioFrame
        # 轨道已停止（连接断开/会话拆除）：消费端不会再取，直接丢弃，
        # 免得在这里白等一个 timeout。
        if getattr(self.__audio, 'readyState', 'live') == 'ended':
            return
        # 所有 TTS 引擎最终统一成单声道 16kHz、20ms（320 samples）音频帧。
        # 不可再用 ``samples[:320]``：20fps 的输入块有 400 samples，硬截会
        # 每秒丢 4000 个采样，听感就是持续卡顿。这里累积余数后精确切包。
        samples = np.asarray(frame, dtype=np.int16)
        if samples.ndim > 1:
            samples = samples.reshape(-1)
        if self._audio_remainder.size:
            samples = np.concatenate((self._audio_remainder, samples))
        status = (eventpoint or {}).get('status') if isinstance(eventpoint, dict) else None
        packets = []
        while samples.shape[0] >= 320:
            packets.append(samples[:320])
            samples = samples[320:]
        # 语句的末块不足 20ms 时补零后立即发出；否则尾音会滞留在余数里，
        # 下一句才被拼进去，既截尾又让“结束”事件提前。
        if status == 'end' and samples.shape[0]:
            packets.append(np.pad(samples, (0, 320 - samples.shape[0])))
            samples = np.empty(0, dtype=np.int16)
        self._audio_remainder = np.ascontiguousarray(samples, dtype=np.int16)
        if not packets:
            return
        # 音频必须保序，不能主动丢新帧；但**绝不能无限阻塞**——连接断开后
        # aiortc 不再消费这个队列，无界 put 会把渲染线程永久卡住，进而经
        # 推理/特征队列把 render() 一起拖死（见 utils/queue_guard 顶部说明）。
        # 因此仅当队列持续满时丢最旧的一帧腾位置，并在日志里限频告警。
        if self._audio_drop_reporter is None:
            self._audio_drop_reporter = DropReporter(
                'webrtc-audio', mylogger, every=200)
        # “开始”绑首包，“结束”绑末包，气泡和手势的时机才与实际声音一致。
        event_index = len(packets) - 1 if status == 'end' else 0
        for index, packet in enumerate(packets):
            new_frame = AudioFrame.from_ndarray(
                packet.reshape(1, 320), format='s16', layout='mono')
            new_frame.sample_rate = 16000
            put_capped(
                self.__audio._queue,
                (new_frame, eventpoint if index == event_index else None),
                timeout=AUDIO_PUSH_TIMEOUT,
                on_drop=self._audio_drop_reporter,
            )

    def get_buffer_size(self) -> int:
        # 只返回视频队列大小，不影响音频
        return self.__video._queue.qsize()

    def get_audio_buffer_size(self) -> int:
        return self.__audio._queue.qsize()

    def clear_audio_buffer(self) -> None:
        """清理尚未发送的音频，避免打断后继续播放旧 PCM。"""
        self._audio_remainder = np.empty(0, dtype=np.int16)
        while True:
            try:
                self.__audio._queue.get_nowait()
            except queue.Empty:
                break

    def notify(self,eventpoint):
        if self.__container is not None:
            self.__container.notify(eventpoint)

    @property
    def audio(self) -> MediaStreamTrack:
        """
        A :class:`aiortc.MediaStreamTrack` instance if the file contains audio.
        """
        return self.__audio

    @property
    def video(self) -> MediaStreamTrack:
        """
        A :class:`aiortc.MediaStreamTrack` instance if the file contains video.
        """
        return self.__video

    def _start(self, track: PlayerStreamTrack) -> None:
        self.__started.add(track)
        if self.__thread is None:
            self.__log_debug("Starting worker thread")
            self.__thread_quit = threading.Event()
            self.__thread = threading.Thread(
                name="media-player",
                target=player_worker_thread,
                args=(
                    self.__thread_quit,
                    self.__container
                ),
                # 守护线程：即使某次拆除超时留下残线程，也不能拖住进程退出。
                daemon=True,
            )
            self.__thread.start()

    def _stop(self, track: PlayerStreamTrack) -> None:
        self.__started.discard(track)

        if not self.__started and self.__thread is not None:
            self.__log_debug("Stopping worker thread")
            self.__thread_quit.set()
            # 有界 join：本方法可能在 aiohttp 事件循环线程里被调用
            # （aiortc 轨道 stop / remove_session 都跑在 loop 上）。
            # 渲染线程若因队列拥塞没能按时退出，只能放弃等待并告警，
            # 绝不能把整个事件循环一起拖死——那样服务就彻底不响应 HTTP 了。
            self.__thread.join(timeout=TEARDOWN_JOIN_TIMEOUT)
            if self.__thread.is_alive():
                mylogger.warning(
                    "渲染线程未在 %.1fs 内退出，放弃等待（线程仍在运行，"
                    "不再阻塞事件循环）", TEARDOWN_JOIN_TIMEOUT)
            self.__thread = None

        if not self.__started and self.__container is not None:
            #self.__container.close()
            self.__container = None

    def __log_debug(self, msg: str, *args) -> None:
        mylogger.debug(f"HumanPlayer {msg}", *args)
