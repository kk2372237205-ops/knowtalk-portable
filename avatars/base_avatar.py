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
#
#  Avatar 基类 — 合并自 basereal.py，集成到 Async Pipeline
#

import math
from numpy.typing import NDArray
import torch
import numpy as np
import subprocess
import os
import time
import cv2
import glob
import resampy
import queue
from queue import Queue
from threading import Thread, Event, Lock
from collections import OrderedDict
from io import BytesIO
import soundfile as sf
import asyncio
from enum import Enum
import json
import importlib
import registry

import torch.multiprocessing as mp
from dataclasses import dataclass, field

from av import AudioFrame, VideoFrame
from fractions import Fraction

from utils.logger import logger
from utils.image import read_imgs,mirror_index
from utils import gestures as gesture_utils
from utils.queue_guard import DropReporter, put_capped

# 一个会话内可同时持有的手势素材套数上限。
#
# 全帧改成按需解码后（`utils.gestures.LazyFrames`），一套动作常驻只剩人脸裁剪、
# 遮罩与 latent 约 25MB，因此上限放到 8 套：一讲课常见 6~8 种动作，全部留在缓存里
# 才不会出现"讲到中间又把以前用过的动作淘汰掉、下一段再读盘"的卡顿。
GESTURE_CACHE_LIMIT = 8

# 推理结果队列单次写入等待上限（秒）。连接断开后没有消费者，无界 put 会把
# 推理线程永久卡住，再顺着 feat_queue 把 render() 主循环一起拖死（见 queue_guard）。
RES_FRAME_PUSH_TIMEOUT = 0.5

# 推理线程等待音频的最长时间（秒）。超时按静音补齐，保证 quit_event 能及时生效。
AUDIO_WAIT_TIMEOUT = 0.5

# 拆除会话时等待工作线程退出的上限（秒）。必须有界，否则拆除会把
# 调用方（通常是 aiohttp 事件循环线程）一起拖住。
THREAD_JOIN_TIMEOUT = 2.0


def _indexed(seq, idx):
    """按序列长度取模取用，保证切换帧源的窗口内不会索引越界。

    `idx` 由推理线程算出、经队列传到渲染线程，而帧源可能已经切换，两者长度不同
    （如默认 150 帧、手势 121 帧）。取模把最坏情况限制为"显示另一个合法帧"，
    而不是抛出异常打断渲染线程。
    """
    if not seq:
        return None
    return seq[(idx or 0) % len(seq)]


def splice_face(new_frame, prev_frame, rect):
    """把手势新帧的人脸区域换成上一张已合成画面的那块（手臂动、嘴型不动）。

    用途见 `process_frames` 的补帧分支：推理只有约 19fps、输出 20fps，必然有缺帧。
    缺帧时如果直接冻结上一帧，手臂会一顿一顿；如果直接换成手势的原始帧，嘴会
    在"张开↔闭合"之间闪跳。动作素材的硬约束是**头部零位移**，因此两张图在同一
    人脸矩形内除了嘴完全一致——把上一帧的脸贴回来即可两全。

    返回新数组（不修改入参，入参可能是按需解码的缓存帧）。
    """
    frame = np.array(new_frame, copy=True)
    if prev_frame is None or not rect:
        return frame
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in rect)
    x1, x2 = max(0, min(x1, x2)), min(width, max(x1, x2))
    y1, y2 = max(0, min(y1, y2)), min(height, max(y1, y2))
    if x2 <= x1 or y2 <= y1:
        return frame
    if prev_frame.shape[:2] != frame.shape[:2]:
        return frame
    frame[y1:y2, x1:x2] = prev_frame[y1:y2, x1:x2]
    return frame


# class State(Enum):
#     INIT=0
#     WAIT=1
#     QUESTION=2
#     ANSWER=3

@dataclass
class AudioFrameData:
    data: NDArray[np.float32]
    type: int = 0  # 默认值
    userdata: dict = field(default_factory=dict)

class BaseAvatar:
    def __init__(self, opt):
        self.opt = opt
        self.sample_rate = 16000
        self.chunk = self.sample_rate // (opt.fps*2) # 320 samples per chunk (20ms)
        self.sessionid = self.opt.sessionid

        self.speaking = False
        self.speech_request_id = 0
        self.llm_text = ''
        self.llm_done = False
        # 直接朗读的整句（欢迎语/菜单介绍），屏幕对话气泡用。
        # 与 llm_text 分开存：两条链路谁后发生谁覆盖，见 /llm_status。
        self.say_text = ''
        self.say_at = 0.0
        self.llm_started_at = 0.0
        self.recording = False
        self._record_video_pipe = None
        self._record_audio_pipe = None
        self.width = self.height = 0

        self.custom_audiotype = 0 # 0: normal, 1: sinlence, >1: custom audio
        self.custom_img_cycle = {}
        self.custom_audio_cycle = {}
        self.custom_audio_index = {}
        self.custom_index = {}
        # self.custom_opt = {}
        self.__loadcustom()

        self.batch_size = opt.batch_size
        self.res_frame_queue = Queue(self.batch_size*3)
        self.render_event = Event()

        _tts_modules = {
            'edgetts': 'tts.edge',
            'sapi': 'tts.sapi',
            'gpt-sovits': 'tts.sovits',
            'xtts': 'tts.xtts',
            'cosyvoice': 'tts.cosyvoice',
            'fishtts': 'tts.fish',
            'tencent': 'tts.tencent',
            'doubao': 'tts.doubao',
            'azuretts': 'tts.azure',
            'qwentts': 'tts.qwentts'
        }

        if opt.tts in _tts_modules:
            importlib.import_module(_tts_modules[opt.tts])
            self.tts = registry.create("tts", opt.tts, opt=opt, parent=self)
        else:
            logger.error(f"TTS module {opt.tts} not found.")

        _output_modules = {
            'webrtc': 'streamout.webrtc',
            'rtcpush': 'streamout.webrtc',
            'rtmp': 'streamout.rtmp',
            'virtualcam': 'streamout.virtualcam'
        }

        # 初始化 Output 模块
        if opt.transport in _output_modules:
            try:
                importlib.import_module(_output_modules[opt.transport])
                self.output = registry.create("streamout", opt.transport, opt=opt, parent=self)
            except ModuleNotFoundError:
                logger.error(f"Output transport module {_output_modules[opt.transport]} not found.")
        else:
            logger.error(f"Output transport {opt.transport} not found in map.")

        # 手势编排的会话级状态（当前帧源引用、切换代数、素材缓存、播放进度）
        self._init_gesture_state()

        # 背景合成：data/settings/background.json 配了背景才会启用，没配时整个
        # 热路径零开销（_frame_at 里只是一次 None 判断）。
        try:
            from utils.avatar_background import AvatarBackground
            self._bg = AvatarBackground(self._avatar_id() or "")
        except Exception:  # noqa: BLE001 - 背景是可选能力，失败不能拖垮开播
            logger.exception("[背景] 初始化失败，按原背景输出")
            self._bg = None

        # 播放暂停态：由 POST /api/playback 设置。为真时输出画面回到基础素材帧
        # （闭嘴静止），避免定格在张嘴的推理帧上。
        self.playback_paused = False

    # 如果系统没有使用 pipeline，或者为了向后兼容原来的 ttsreal.py
    def put_msg_txt(self, msg, datainfo:dict={}):
        # 记一份"直接让数字人说的整句"（欢迎语、菜单介绍、固定应答）。
        # LLM 那条链路自带 llm_text 逐片累加，这里**不能**覆盖它，否则一轮对话
        # 的中间插一句欢迎语就会把已经生成的回答冲掉；用 `_speech_request_id`
        # 区分两条链路。
        if not datainfo.get('_speech_request_id'):
            self.say_text = str(msg or '')
            self.say_at = time.time()
        if hasattr(self, 'tts'):
            self.tts.put_msg_txt(msg, datainfo)
    
    def put_audio_frame(self, audio_chunk:NDArray[np.float32], datainfo:dict={}): # 16khz 20ms pcm
        if hasattr(self, 'asr'):
            self.asr.put_audio_frame(audio_chunk, datainfo)

    def put_audio_file(self, filebyte, datainfo:dict={}): 
        input_stream = BytesIO(filebyte)
        stream = self.__create_bytes_stream(input_stream)
        streamlen = stream.shape[0]
        idx = 0
        first = True
        while streamlen >= self.chunk:
            eventpoint = {}
            if first:
                eventpoint = {'status': 'start'}
                first = False
            if streamlen - self.chunk < self.chunk:
                eventpoint = {'status': 'end'}
            eventpoint.update(**datainfo) 
            self.put_audio_frame(stream[idx:idx+self.chunk], eventpoint)
            streamlen -= self.chunk
            idx += self.chunk

    def put_audio_filepath(self, filepath, datainfo:dict={}): 
        stream = self.__create_bytes_stream(filepath)
        streamlen = stream.shape[0]
        idx = 0
        first = True
        while streamlen >= self.chunk:
            eventpoint = {}
            if first:
                eventpoint = {'status': 'start'}
                first = False
            if streamlen - self.chunk < self.chunk:
                eventpoint = {'status': 'end'}
            eventpoint.update(**datainfo) 
            self.put_audio_frame(stream[idx:idx+self.chunk], eventpoint)
            streamlen -= self.chunk
            idx += self.chunk
    
    def __create_bytes_stream(self, byte_stream):
        stream, sample_rate = sf.read(byte_stream) # [T*sample_rate,] float64
        logger.info(f'[INFO]put audio stream {sample_rate}: {stream.shape}')
        stream = stream.astype(np.float32)

        if stream.ndim > 1:
            logger.info(f'[WARN] audio has {stream.shape[1]} channels, only use the first.')
            stream = stream[:, 0]
    
        if sample_rate != self.sample_rate and stream.shape[0] > 0:
            logger.info(f'[WARN] audio sample rate is {sample_rate}, resampling into {self.sample_rate}.')
            stream = resampy.resample(x=stream, sr_orig=sample_rate, sr_new=self.sample_rate)

        return stream

    def flush_talk(self):
        # 使正在生成或等待入队的旧请求失效。
        self.speech_request_id += 1
        if hasattr(self, 'tts') and hasattr(self.tts, 'flush_talk'):
            self.tts.flush_talk()
        if hasattr(self, 'asr') and hasattr(self.asr, 'flush_talk'):
            self.asr.flush_talk()
        # 清除推理结果队列，防止旧帧残留导致说话乱跳
        if hasattr(self, 'res_frame_queue'):
            while not self.res_frame_queue.empty():
                try:
                    self.res_frame_queue.get_nowait()
                except:
                    break
        if hasattr(self, 'output') and hasattr(self.output, 'clear_audio_buffer'):
            self.output.clear_audio_buffer()
        self.custom_audiotype = 0  

    def flush_asr_pipeline(self):
        """清除 ASR 帧缓冲区（段间调用，不触碰 TTS 队列）"""
        # 设置清帧标记，run_step 检测到段边界时自动清空 frames 缓冲区
        if hasattr(self, 'asr') and hasattr(self.asr, 'clear_frames'):
            self.asr.clear_frames()
        # 注意：不清理 input/output/feat/res_frame 队列，避免丢弃音频或视频帧

    # def flush(self):
    #     self.flush_talk()

    def is_speaking(self) -> bool:
        return self.speaking
    
    def __loadcustom(self):
        if not hasattr(self.opt, 'customopt') or not self.opt.customopt:
            return
        for item in self.opt.customopt:
            logger.info(item)
            input_img_list = glob.glob(os.path.join(item['imgpath'], '*.[jpJP][pnPN]*[gG]'))
            input_img_list = sorted(input_img_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
            self.custom_img_cycle[item['audiotype']] = read_imgs(input_img_list)
            if item.get('audiopath'):
                self.custom_audio_cycle[item['audiotype']], sample_rate = sf.read(item['audiopath'], dtype='float32')
                self.custom_audio_index[item['audiotype']] = 0
            self.custom_index[item['audiotype']] = 0
            # self.custom_opt[item['audiotype']] = item

    def init_customindex(self):
        self.custom_audiotype = 0
        for key in self.custom_audio_index:
            self.custom_audio_index[key] = 0
        for key in self.custom_index:
            self.custom_index[key] = 0

    def notify(self, eventpoint:dict):
        if eventpoint and eventpoint.get('status'):
            logger.info("notify:%s", eventpoint)

    def start_recording(self):
        if self.recording:
            return
        command = ['ffmpeg',
                    '-y', '-an',
                    '-f', 'rawvideo',
                    '-vcodec','rawvideo',
                    '-pix_fmt', 'bgr24',
                    '-s', "{}x{}".format(self.width, self.height),
                    '-r', str(25),
                    '-i', '-',
                    '-pix_fmt', 'yuv420p', 
                    '-vcodec', "h264",
                    f'temp{self.opt.sessionid}.mp4']
        self._record_video_pipe = subprocess.Popen(command, shell=False, stdin=subprocess.PIPE)

        acommand = ['ffmpeg',
                    '-y', '-vn',
                    '-f', 's16le',
                    '-ac', '1',
                    '-ar', '16000',
                    '-i', '-',
                    '-acodec', 'aac',
                    f'temp{self.opt.sessionid}.aac']
        self._record_audio_pipe = subprocess.Popen(acommand, shell=False, stdin=subprocess.PIPE)

        self.recording = True
    
    def record_video_data(self, image):
        if self.width == 0:
            self.height, self.width, _ = image.shape
        if self.recording:
            self._record_video_pipe.stdin.write(image.tostring())

    def record_audio_data(self, frame):
        if self.recording:
            self._record_audio_pipe.stdin.write(frame.tostring())
		
    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False 
        self._record_video_pipe.stdin.close()
        self._record_video_pipe.wait()
        self._record_audio_pipe.stdin.close()
        self._record_audio_pipe.wait()
        
        record_path = os.path.join('data', 'record')
        os.makedirs(record_path, exist_ok=True)
        output_file = os.path.join(record_path, f"{self.opt.sessionid}.mp4")
        
        temp_aac = f"temp{self.opt.sessionid}.aac"
        temp_mp4 = f"temp{self.opt.sessionid}.mp4"
        
        cmd_combine_audio = f"ffmpeg -y -i {temp_aac} -i {temp_mp4} -c:v copy -c:a copy {output_file}"
        os.system(cmd_combine_audio)
        
        # 删除临时文件
        try:
            os.remove(temp_aac)
            os.remove(temp_mp4)
        except Exception as e:
            logger.error(f"Error removing temp files: {e}")

    # def mirror_index(self, size, index):
    #     turn = index // size
    #     res = index % size
    #     if turn % 2 == 0:
    #         return res
    #     else:
    #         return size - res - 1 
    
    def get_custom_audio_stream(self, audiotype):
        idx = self.custom_audio_index[audiotype]
        stream = self.custom_audio_cycle[audiotype][idx:idx+self.chunk]
        self.custom_audio_index[audiotype] += self.chunk
        if self.custom_audio_index[audiotype] >= self.custom_audio_cycle[audiotype].shape[0]:
            self.custom_audiotype = 1
        return stream
    
    def set_custom_state(self, audiotype, reinit=True):
        print('set_custom_state:', audiotype)
        if self.custom_audio_index.get(audiotype) is None:
            return
        self.custom_audiotype = audiotype
        if reinit:
            self.custom_audio_index[audiotype] = 0
            self.custom_index[audiotype] = 0

    # ========================== 手势编排（帧源切换） ==========================
    #
    # 手势不是"停下说话做动作"，而是把整个帧源（全帧、人脸裁剪、人脸框）换成
    # 另一套素材，因此必须与语音时间线并行：不插静音、不截断、不改变段落顺序。
    # 语义由 openspec/specs/gesture-orchestration 定义，这里只负责实现。

    def _init_gesture_state(self):
        """初始化手势编排的会话级状态（每个会话一个 avatar 实例，天然按会话隔离）。"""
        # 默认素材三件套与当前生效的三件套。未绑定默认素材时（非 wav2lip 结构），
        # 取帧会回退到 `frame_list_cycle`，行为与引入手势前一致。
        self._default_frames = None
        self._default_faces = None
        self._default_coords = None
        self._active_frames = None
        self._active_faces = None
        self._active_coords = None
        # 模型专属资产：MuseTalk 的 {latents, masks, mask_coords, coords}；wav2lip 为 None
        self._active_extra = None
        # 该模型是否**必须**有 MuseTalk 侧手势资产才能播动作（MuseReal 里置 True）。
        # 为真时缺资产会抛错并走「改用默认素材继续朗读」的降级分支。
        self._requires_muse_assets = False

        self._current_gesture = None        # 当前已激活的手势名
        self._requested_gesture = None      # 最近一次段首请求的手势名
        self._gesture_repeat = False        # 当前手势是否循环（闲时动作要一直播）
        self._gesture_frames_played = 0     # 本遍已产出的帧数（与 index 解耦）
        self._frame_source_gen = 0          # 帧源代数：每次切换 +1，推理线程据此重置索引
        self._gesture_cache = OrderedDict()  # 手势名 -> (frames, faces, coords)，LRU
        self._gesture_loading = set()       # 正在后台加载的手势名
        self._gesture_lock = Lock()
        # 手势素材的**解码**串行闸门：一套手势 = 上百张 538x1080 的 PNG，
        # 解出来常驻内存约 200MB。预热会为每个手势各起一个线程，若并发解码，
        # 峰值会叠加到几百 MB～1GB，在内存吃紧的机器上直接把 OpenCV 的
        # cv2.imread 打成 "(-4:Insufficient memory) Failed to allocate"，
        # 手势加载整体失败并静默降级成默认素材（表现为"动作播不出来"）。
        # 用独立的闸门把解码串行化：峰值内存降到单套手势的量级。
        # 闸门与 _gesture_lock 分开，避免持锁期间再去抢 _gesture_lock 造成死锁。
        self._gesture_load_slot = Lock()

    def _bind_default_frames(self, frames, faces, coords):
        """绑定该形象的默认素材三件套，并把它作为当前生效帧源。"""
        self._default_frames = frames
        self._default_faces = faces
        self._default_coords = coords
        self._active_frames = frames
        self._active_faces = faces
        self._active_coords = coords

    def _sequence(self, active, fallback_name):
        """当前生效的序列；未绑定时回退到该模型自己的属性（行为与改动前一致）。"""
        if active is not None:
            return active
        return getattr(self, fallback_name, None)

    def _frame_at(self, idx):
        """按当前（可能已切到手势的）帧源取全帧，必要时合成到自定义背景。

        合成放在这里而不是推流前：推理侧的 `ori_frame` 也从这里取，脸部贴回去
        之后整帧自然就是新背景，不必在多处重复合成（重复合成会让边缘变硬）。
        """
        frame = _indexed(self._sequence(self._active_frames, 'frame_list_cycle'), idx)
        comp = getattr(self, '_bg', None)
        if comp is not None and frame is not None:
            return comp.compose(frame, self._current_gesture, idx)
        return frame

    def _base_frame_at(self, idx=0):
        """取**形象自身**默认素材的帧，不受当前帧源影响。

        暂停时 MUST 用它，不能用 `_frame_at`：动作播放期间暂停若取当前帧源，画面
        会停在"手臂抬起"的动作帧上，而不是形象的默认姿态——嘴虽闭着，人却是僵在
        半截动作里的。
        """
        frame = _indexed(self._sequence(self._default_frames, 'frame_list_cycle'), idx)
        comp = getattr(self, '_bg', None)
        if comp is not None and frame is not None:
            return comp.compose(frame, None, idx)
        return frame

    def _face_at(self, idx):
        """按当前帧源取人脸裁剪（口型模型的输入）。"""
        return _indexed(self._sequence(self._active_faces, 'face_list_cycle'), idx)

    def _coord_at(self, idx):
        """按当前帧源取人脸框。"""
        return _indexed(self._sequence(self._active_coords, 'coord_list_cycle'), idx)

    def _face_rect(self, idx):
        """当前帧源的人脸框，统一成 `(x1, y1, x2, y2)`。

        两个模型的 `coords.pkl` 元素序不同（wav2lip `(y1,y2,x1,x2)`、MuseTalk
        `(x1,y1,x2,y2)`），且 MuseTalk 手势的框在 `_active_extra['coords']` 里。
        补帧拼接需要"人脸的矩形范围"，这里一次性抹平差异，别让调用方自己判别型。
        """
        extra = self._active_extra
        coords = (extra or {}).get('coords')
        if coords:
            x1, y1, x2, y2 = (int(v) for v in coords[(idx or 0) % len(coords)])
            return x1, y1, x2, y2
        box = self._coord_at(idx)
        if box is None:
            return None
        y1, y2, x1, x2 = (int(v) for v in box)
        return x1, y1, x2, y2

    def _activate(self, name, payload=None):
        """原子切换帧源。

        `name=None` 表示回到默认素材。切换会 +1 帧源代数，推理线程下一轮据此把
        `index` 重置为 0——这是"手势从第 0 帧开始播"的实现方式（`index` 是推理
        线程的局部变量，跨线程改不了）。
        """
        if name is None or payload is None:
            self._active_frames = self._sequence(self._default_frames, 'frame_list_cycle')
            self._active_faces = self._sequence(self._default_faces, 'face_list_cycle')
            self._active_coords = self._sequence(self._default_coords, 'coord_list_cycle')
            self._active_extra = None
            self._current_gesture = None
            self._gesture_repeat = False
        else:
            # 三元组是 wav2lip 手势（帧/人脸裁剪/框）；四元组末位是模型专属资产
            # （MuseTalk 的 latents / mask / mask_coords / 框），wav2lip 为 None。
            frames, faces, coords = payload[0], payload[1], payload[2]
            self._active_frames = frames
            self._active_faces = faces
            self._active_coords = coords
            self._active_extra = payload[3] if len(payload) > 3 else None
            self._current_gesture = name
        self._gesture_frames_played = 0
        self._frame_source_gen += 1

    def _avatar_id(self):
        return getattr(self.opt, 'avatar_id', None)

    def _default_sizes(self):
        """默认素材的 (全帧尺寸, 人脸尺寸)，用于把手势素材归一化到同一规格。"""
        def size_of(frames):
            if not frames:
                return None
            frame = frames[0]
            return (int(frame.shape[1]), int(frame.shape[0]))

        return size_of(self._default_frames), size_of(self._default_faces)

    def request_gesture(self, name, repeat=False):
        """段首请求某手势：已缓存则立即切换，否则后台加载、加载完成后切换。

        加载期间继续输出当前素材，因此不会出现黑帧或掉帧；最坏情况是首次触发时
        动作晚几十到几百毫秒开始。

        `repeat=True` 表示该手势播满一遍后**从头再来**而不是清回默认素材：
        闲时动作要一直挂在画面上（没人站在舱前时数字人也不能定住不动）。
        """
        self._requested_gesture = name
        self._gesture_repeat = bool(repeat)
        if name is None:
            self._activate(None)
            return

        cached = self._gesture_cache.get(name)
        if cached is not None:
            self._gesture_cache.move_to_end(name)
            self._activate(name, cached)
            return

        with self._gesture_lock:
            if name in self._gesture_loading:
                return
            self._gesture_loading.add(name)
        Thread(target=self._load_then_activate, args=(name,), daemon=True).start()

    def preheat_gestures(self, names):
        """受理阶段的后台预热：只加载进缓存，不改变当前画面。

        首帧切换时机由段首决定（规格要求"该段音频开始播放的时刻切换"），因此预热
        MUST NOT 抢先激活——它只让首次触发赶上段首，避免"动作从半途开始"。

        讲稿出现的**全部**手势都在这里预热，且由**单线程顺序**完成（见 `_preheat_worker`）：
        - 讲到一半才去读盘解码 = 画面明显顿一下（动作还会晚上 1 秒才开始），
          而讲稿的手势集合在受理时就已确定，没有理由等到段首才加载；
        - 并发预热会各自解上百张 PNG 抢 CPU，把推理线程的帧率压下去（越预热越卡）。
        全帧已是按需解码（见 `utils.gestures.LazyFrames`），单套素材只占几 MB，
        一整讲课的动作全留下也不会撑爆内存。
        """
        todo = []
        for name in dict.fromkeys(names or ()):  # 去重且保序
            if not name or name in self._gesture_cache:
                continue
            with self._gesture_lock:
                if name in self._gesture_loading:
                    continue
                self._gesture_loading.add(name)
            todo.append(name)
        if todo:
            Thread(target=self._preheat_worker, args=(todo,), daemon=True).start()

    def _preheat_worker(self, names):
        """顺序预热若干手势；单个失败只记 warning，不影响其它。"""
        logger.info('[手势] 开始预热 %d 套动作：%s', len(names), ', '.join(names))
        for name in names:
            self._load_then_activate(name, activate=False)
        logger.info('[手势] 预热结束，当前缓存 %d 套', len(self._gesture_cache))

    def _load_then_activate(self, name, activate=True):
        """后台加载手势素材；失败时降级为默认素材，不影响朗读与渲染线程。"""
        try:
            # 解码串行化：同一时刻只有一套手势素材在解码，避免内存峰值叠加。
            with self._gesture_load_slot:
                payload = self._load_gesture_payload(name)
        except Exception as exc:  # noqa: BLE001
            logger.warning('[手势] %s 加载失败（%s），改用默认素材继续', name, exc)
            if activate and self._requested_gesture == name:
                self._activate(None)
            return
        finally:
            with self._gesture_lock:
                self._gesture_loading.discard(name)

        with self._gesture_lock:
            self._gesture_cache[name] = payload
            self._gesture_cache.move_to_end(name)
            self._evict_gestures()
        if activate and self._requested_gesture == name:
            self._activate(name, payload)

    def _load_gesture_payload(self, name):
        ref_size, ref_face_size = self._default_sizes()
        # 输出帧率：素材按自己的原始帧率（导出视频，通常 24fps）采样，播放端却"每输出一帧
        # 前进一帧"。不按输出帧率重采样的话动作会变速（24→20fps 就是 0.83 倍速、慢半拍）。
        target_fps = getattr(self.opt, 'fps', None)
        frames, faces, coords = gesture_utils.load_gesture(
            self._avatar_id(), name,
            ref_size=ref_size, ref_face_size=ref_face_size, target_fps=target_fps)
        # MuseTalk 需要人脸参考（latent）与遮罩。wav2lip 形象没有这套资产，返回 None 即可；
        # 但 MuseTalk 形象缺资产时必须抛错——由 `_load_then_activate` 走降级分支
        # （记 warning、改用默认素材继续朗读），绝不能拿基础 latent 去配手势帧，
        # 那会把动作帧和别的嘴型混在一起。
        #
        # 这套资产取自**形象自身**，长度恒为 1：动作素材只贡献身体与姿态，人脸永远
        # 由形象驱动——生成出来的动作帧脸型会漂移（实测比形象胖一圈），用它自己的
        # 脸做参考，动作期间就会"换一张脸"。
        extra = gesture_utils.load_muse_assets(self._avatar_id(), name, target_fps=target_fps)
        if extra is None and self._requires_muse_assets:
            raise gesture_utils.GestureError(
                '手势 %s 缺少 MuseTalk 动作资产（需补齐 musetalk/ 下的 latents 与遮罩）' % name)
        return frames, faces, coords, extra

    def _evict_gestures(self):
        """超出上限时淘汰最久未使用的一套（跳过当前正在播的那套）。调用方持锁。"""
        while len(self._gesture_cache) > GESTURE_CACHE_LIMIT:
            victim = next((key for key in self._gesture_cache if key != self._current_gesture), None)
            if victim is None:
                return
            self._gesture_cache.pop(victim, None)
            logger.info('[手势] 缓存已满，淘汰最久未使用的手势 %s', victim)

    def clear_gestures(self):
        """释放本会话持有的手势素材（会话结束或打断时调用）。"""
        with self._gesture_lock:
            self._gesture_cache.clear()
            self._gesture_loading.clear()
        self._requested_gesture = None
        self._activate(None)

    def advance_gesture(self, frames_produced):
        """按实际产出的帧数推进"只播一遍"的进度，播满即回默认素材。

        计数 MUST 与 `index` 解耦：`index` 会在"静音→说话"边界被重置，用它计数会
        算错；静音与说话两条分支都要累加，因为静音期间手势照常推进帧序。

        调用方 MUST 只是**渲染线程**，且只在视频帧真正推给接收端之后调用。
        推理线程 MUST NOT 调用：它产出的帧要先经队列才上屏，按批量计数必然超前
        于画面，结果是最后几帧动作还没播就被切回默认素材（等价于"动作被砍掉了
        尾巴"，观感上正好是"快结束时卡一下/突然收回去"）。
        """
        if self._current_gesture is None:
            return
        self._gesture_frames_played += frames_produced
        total = len(self._active_frames or ())
        if total and self._gesture_frames_played >= total:
            if self._gesture_repeat:
                # 循环动作：留一点尾巴重叠，直接从第 0 帧续上（首末帧对齐的
                # 预设动作因此看不出接缝），不清回默认素材。
                self._gesture_frames_played %= total
                return
            logger.info('[手势] %s 已完整播放一遍（%d 帧），回到默认素材',
                        self._current_gesture, total)
            self._activate(None)

    # ========================== 核心渲染及 Pipeline 桥接 ==========================
    def get_avatar_length(self):
        frames = self._sequence(self._active_frames, 'frame_list_cycle')
        return len(frames) if frames else 1
        
    def inference(self, quit_event):
        index = 0
        count = 0
        counttime = 0
        last_speaking = False
        seen_frame_source_gen = self._frame_source_gen
        drop_reporter = DropReporter('res_frame', logger, every=200)

        def put_res(item):
            """把推理结果放进有界队列，绝不无限阻塞。

            连接断开后 process_frames 会先卡住（音频队列没人消费），
            res_frame_queue 随即塞满；这里若用无界 put，推理线程会被永久卡住，
            再经 feat_queue 把 render() 主循环一起拖死（2026-10-21 现场故障）。
            """
            put_capped(self.res_frame_queue, item,
                       timeout=RES_FRAME_PUSH_TIMEOUT,
                       should_abort=quit_event.is_set,
                       on_drop=drop_reporter)

        # syncnet_T = 12  # 时间步
        # weight_dtype = torch.float16  # 数据类型
        # infernum = 0
        logger.info('start inference')
        while not quit_event.is_set():
            starttime = time.perf_counter()
            # 帧源长度每轮重取：手势切换后必须立刻生效（长度会变），
            # 在循环外取一次会让切换后的索引按旧长度推进甚至越界。
            length = self.get_avatar_length()
            # 帧源被切换（手势激活/恢复默认）：手势必须从第 0 帧开始播。
            if self._frame_source_gen != seen_frame_source_gen:
                seen_frame_source_gen = self._frame_source_gen
                logger.info('帧源已切换，索引重置为 0（当前手势：%s）', self._current_gesture)
                index = 0
            audiofeat_batch = []
            try:
                audiofeat_batch = self.asr.feat_queue.get(block=True, timeout=1)
            except queue.Empty:
                # 段切换/清帧导致音频特征暂缺，用静音帧填充保持输出帧率，避免掉帧
                audio_frames = []
                for _ in range(self.batch_size * 2):
                    try:
                        audioframe = self.asr.output_queue.get(block=False)
                    except queue.Empty:
                        audioframe = AudioFrameData(
                            data=np.zeros(self.chunk, dtype=np.float32), type=1, userdata={}
                        )
                    audio_frames.append(audioframe)
                for i in range(self.batch_size):
                    idx = mirror_index(length, index)
                    put_res((None, audio_frames[i*2:i*2+2], idx))
                    index += 1
                continue
                
            is_all_silence = True
            audio_frames: list[AudioFrameData] = []
            for _ in range(self.batch_size * 2):
                # 限时等待 + 检查 quit_event：无超时的 get 会让推理线程在
                # 音频断流时无法响应拆除，进而让 render() 的 join 超时。
                try:
                    audioframe:AudioFrameData = self.asr.output_queue.get(
                        block=True, timeout=AUDIO_WAIT_TIMEOUT)
                except queue.Empty:
                    if quit_event.is_set():
                        break
                    audioframe = AudioFrameData(
                        data=np.zeros(self.chunk, dtype=np.float32), type=1, userdata={}
                    )
                if audioframe.type == 0:
                    is_all_silence = False               
                audio_frames.append(audioframe)
            if len(audio_frames) < self.batch_size * 2:
                # 拆除中：丢掉本轮，直接回到 while 判断退出
                continue

             # 检测状态变化
            current_speaking = not is_all_silence

            if is_all_silence: #全为静音数据，只需要取fullimg，不需要推理
                for i in range(self.batch_size):
                    idx = mirror_index(length, index)
                    put_res((None, audio_frames[i*2:i*2+2], idx))
                    index = index + 1
            else:
                if current_speaking and not last_speaking and self.custom_index.get(1) is not None: #从静音到说话切换,并且有自定义静态视频
                    index = 0
                t = time.perf_counter()

                try:
                    pred = self.inference_batch(index, audiofeat_batch)
                except RuntimeError as exc:
                    message = str(exc)
                    if 'out of memory' not in message and 'bad allocation' not in message:
                        raise
                    # 一次显存不足不该让整个推理线程永久退出——现场表现就是
                    # 「连上了、有声音、但画面不动」。清掉 CUDA 缓存后丢弃本批，
                    # 线程继续跑，下一批通常能恢复。
                    logger.error('推理显存不足，已清 CUDA 缓存并跳过本批（index=%s, batch=%s）：%s',
                                 index, self.batch_size,
                                 message.splitlines()[0] if message else '')
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    continue

                counttime += (time.perf_counter() - t)
                count += self.batch_size
                if count >= 100:
                    logger.info(f"------actual avg infer fps:{count/counttime:.4f}")
                    count = 0
                    counttime = 0
                has_output = hasattr(self, 'output') and self.output
                for i, res_frame in enumerate(pred):
                    # 说话时用简单正向循环（index % length），避免 mirror_index 来回播放
                    # 看起来像"倒带重启"
                    put_res((res_frame, audio_frames[i*2:i*2+2], index % length))
                    index = index + 1
                    # 手势进度不在这里推进：推理产出的帧要经队列才上屏，按批量计数
                    # 必然超前于画面，切换时机会早于最后几帧动作真正播出——现场就是
                    # "动作快结束时突然收回去"。进度只由渲染线程按实际输出帧推进。
                    
            if current_speaking != last_speaking:
                logger.info(f"inference 状态切换：{'说话' if last_speaking else '静音'} → {'说话' if current_speaking else '静音'}")
                last_speaking = current_speaking         
        logger.info('baseavatar inference thread stop')

    def process_frames(self,quit_event):
        enable_transition = False  # 设置为False禁用过渡效果，True启用
        
        _last_speaking = False
        _transition_start = time.time()
        if enable_transition:
            _transition_duration = 0.1  # 过渡时间
            _last_silent_frame = None  # 静音帧缓存
            _last_speaking_frame = None  # 说话帧缓存

        self.output.start()
        
        _frame_interval = 1.0 / self.opt.fps  # 40ms
        _next_frame_time = time.perf_counter()  # 下一帧的预定输出时刻

        # 音频平滑状态：消除静音帧(全0)与语音帧之间突变产生的爆音/电流声
        _prev_audio_type = 1  # 上一音频帧类型（1=静音，0=语音）
        _fade_samples = 160   # 淡入淡出长度（10ms @16kHz），降低 PCM 边界爆音
        _proc_idx = 0         # 缺帧补静音时使用的滚动索引
        _last_out_frame = None  # 上一张已输出画面，缺帧时沿用，避免跳回原始闭嘴帧
        _fill_count = 0       # 补帧计数（缺帧补位），用于量化"卡不卡"
        _out_count = 0
        # `_last_out_frame` 属于哪一代帧源。切换帧源后上一帧画面来自**另一套素材**，
        # 继续沿用它会把上一套素材的姿态钉在屏幕上：动作播完切回默认素材时，
        # 画面会停在（或回抽到）手势的某一帧上，直到下一帧推理到达才跳走。
        _last_out_gen = None

        def _note_frame(is_fill):
            """统计补帧率：每 200 帧打一条，用来判断"卡顿"是推理跟不上还是别的原因。"""
            nonlocal _fill_count, _out_count
            _fill_count += 1 if is_fill else 0
            _out_count += 1
            if _out_count >= 200:
                logger.info('[平滑] 近 %d 帧中补帧 %d 帧（%.1f%%）',
                            _out_count, _fill_count, 100.0 * _fill_count / _out_count)
                _fill_count = 0
                _out_count = 0

        while not quit_event.is_set():
            try:
                audio_frames: list[AudioFrameData]
                res_frame,audio_frames,idx = self.res_frame_queue.get(block=True, timeout=_frame_interval)
            except queue.Empty:
                # 推理短暂缺帧时补一帧画面和两帧静音音频，
                # 保持 WebRTC 音频队列连续，避免欠载造成的卡顿与电流声。
                #
                # 补的是「上一张已生成画面」而不是原始闭嘴帧：推理实测约 19fps
                # （VAE 解码是硬成本）低于播放 25fps，必然周期性缺帧。原先回到
                # full_imgs 原始帧会让嘴在「说话 ↔ 闭合」之间来回跳，肉眼就是
                # 一卡一卡；沿用上一帧则表现为动作连续、节奏稍慢。
                try:
                    # 这一帧取自哪一代帧源：必须在 `advance_gesture` 之前记下来，
                    # 因为"播满一遍"会在那里把帧源切回默认，而本帧内容仍属旧素材。
                    frame_gen = self._frame_source_gen
                    if self.playback_paused:
                        # 暂停时队列往往已空，这里也必须回**形象自身的**基础素材帧，
                        # 否则会一直重复 `_last_out_frame`（通常是张嘴的说话帧）。
                        frame_to_push = self._base_frame_at(0)
                    elif self._current_gesture is not None:
                        # 手势期间推理晚到：画面要**继续往前走**，不能冻结上一帧。
                        # 推理约 19fps、输出 20fps，必然周期性缺帧；重复上一帧会让
                        # 手臂每 0.5 秒顿一下——这正是"带动作时卡顿"的直接观感。
                        # 但直接用动作原始帧会让嘴闪回闭合，所以把人脸矩形换成上一帧
                        # 已合成的那块：手臂继续动、嘴型保持不动（头部是锁死的）。
                        #
                        # 帧索引用渲染线程自己的进度（`_gesture_frames_played`），
                        # 不用 `_proc_idx`：后者会被下一帧推理结果按它自己的 idx
                        # 回写，补位推进的帧就被"倒回去"，画面上是动作回抽一下——
                        # 这也是结尾那一下顿挫的来源之一。
                        played = self._gesture_frames_played
                        rect = self._face_rect(played)
                        frame_to_push = self._frame_at(played)
                        self.advance_gesture(1)
                        if frame_to_push is None:
                            # 素材为空/读取失败：宁可冻住上一张画面，也不推空帧。
                            frame_to_push = _last_out_frame
                        elif _last_out_frame is not None:
                            frame_to_push = splice_face(frame_to_push, _last_out_frame, rect)
                    elif _last_out_frame is not None and _last_out_gen == self._frame_source_gen:
                        # 同一帧源内缺帧：沿用上一帧，嘴型连续、不闪。
                        frame_to_push = _last_out_frame
                    else:
                        # 没有可沿用的上一帧，或者帧源**刚切换**（上一帧来自另一套
                        # 素材）。后者必须按当前帧源取帧，否则上一套素材的姿态会
                        # 被钉在屏幕上——动作播完切回默认素材时，画面会停住半拍
                        # 再跳到默认姿态，这就是"结尾那一下顿"的观感来源。
                        # 同时把上一帧的人脸矩形贴回来，避免切源瞬间嘴型闪回闭合。
                        length = self.get_avatar_length()
                        silence_idx = mirror_index(length, _proc_idx)
                        _proc_idx += 1
                        frame_to_push = self._frame_at(silence_idx)
                        if frame_to_push is not None and _last_out_frame is not None:
                            frame_to_push = splice_face(
                                frame_to_push, _last_out_frame,
                                self._face_rect(silence_idx))
                    if frame_to_push is None:
                        frame_to_push = _last_out_frame
                    self.output.push_video_frame(frame_to_push)
                    _last_out_frame = frame_to_push
                    _last_out_gen = frame_gen
                    silence_audio = np.zeros(self.chunk, dtype=np.int16)
                    for _ in range(2):
                        self.output.push_audio_frame(silence_audio, None)
                    _note_frame(True)
                except Exception:
                    pass
                now = time.perf_counter()
                wait = _next_frame_time - now
                if wait > 0:
                    time.sleep(wait)
                    _next_frame_time += _frame_interval
                else:
                    _next_frame_time = now + _frame_interval
                continue

            if idx is not None and self._current_gesture is None:
                _proc_idx = idx + 1

            # 推理线程可能在 flush_talk() 清队列后完成一个旧批次。
            # 在最终输出前再次校验请求编号，防止旧 PCM 混入新语音。
            current_request_id = self.speech_request_id
            stale_audio = any(
                frame.userdata
                and frame.userdata.get('_speech_request_id') is not None
                and frame.userdata.get('_speech_request_id') != current_request_id
                for frame in audio_frames
            )
            if stale_audio:
                audio_frames = [
                    AudioFrameData(
                        data=np.zeros(self.chunk, dtype=np.float32),
                        type=1,
                        userdata={},
                    )
                    for _ in audio_frames
                ]
                res_frame = None

            # 检查音频帧中的幻灯片切换指令（PPT 标签分段）
            # 在音频实际开始播放时才切换幻灯片，避免切换过快
            # 只在 slide_id 实际变化时才记录日志，避免每帧都刷屏
            # slide_id 会附着在该段全部音频帧上，但切页只应在段首执行一次。
            # 同时检查这一对音频帧，避免段首刚好落在第二帧时被漏掉。
            userdata = next((
                frame.userdata for frame in audio_frames
                if frame.userdata
                and frame.userdata.get('status') == 'start'
                and frame.userdata.get('slide_id') is not None
            ), None)
            if userdata:
                from utils import material_manager as mat_mgr
                slide_id = userdata['slide_id']
                current_slide = mat_mgr.get_current_slide(self.sessionid)
                if slide_id != current_slide:
                    mat_mgr.set_current_slide(self.sessionid, slide_id)
                    logger.info(f"[PPT标签] 音频播放切换到幻灯片 → id={slide_id}")

            # 手势触发与切页同一时刻发生：音频真正开始播放时才做动作，
            # 避免"还没说到那一段，手已经动完了"。
            gesture_userdata = next((
                frame.userdata for frame in audio_frames
                if frame.userdata
                and frame.userdata.get('status') == 'start'
                and frame.userdata.get('gesture')
            ), None)
            if gesture_userdata:
                gesture_name = gesture_userdata['gesture']
                logger.info(f"[手势标签] 音频播放触发手势 → {gesture_name}")
                self.request_gesture(gesture_name)

            # 帧索引必须在**手势切换之后**确定，否则刚触发动作的这一帧还会按
            # 切换前的旧索引取帧（动作首帧被跳过 1 帧）。
            #
            # 手势期间索引完全由渲染线程按"已输出帧数"决定：推理线程传来的 `idx`
            # 属于它自己的节奏，而补位帧已经把画面往前推过，再按推理的 idx 回写会
            # 让画面倒退一帧（动作末尾最明显）。手势素材头部零位移、逐帧对齐，
            # 按输出帧严格 1:1 播放才是对的，嘴型贴回也用同一个索引取框与遮罩。
            if self._current_gesture is not None:
                frame_idx = self._gesture_frames_played
            else:
                frame_idx = idx

            # 检测状态变化
            current_speaking = not (audio_frames[0].type!=0 and audio_frames[1].type!=0)
            if current_speaking != _last_speaking:
                logger.info(f"状态切换：{'说话' if _last_speaking else '静音'} → {'说话' if current_speaking else '静音'}")
                _transition_start = time.time()
            _last_speaking = current_speaking

            if audio_frames[0].type!=0 and audio_frames[1].type!=0: #全为静音数据，只需要取fullimg
                self.speaking = False
                audiotype = audio_frames[0].type
                if self.custom_index.get(audiotype) is not None: #有自定义视频
                    mirindex = mirror_index(len(self.custom_img_cycle[audiotype]),self.custom_index[audiotype])
                    target_frame = self.custom_img_cycle[audiotype][mirindex]
                    self.custom_index[audiotype] += 1
                else:
                    target_frame = self._frame_at(frame_idx)
                
                if enable_transition:
                    # 说话→静音过渡
                    if time.time() - _transition_start < _transition_duration and _last_speaking_frame is not None:
                        alpha = min(1.0, (time.time() - _transition_start) / _transition_duration)
                        combine_frame = cv2.addWeighted(_last_speaking_frame, 1-alpha, target_frame, alpha, 0)
                    else:
                        combine_frame = target_frame
                    # 缓存静音帧
                    _last_silent_frame = combine_frame.copy()
                else:
                    combine_frame = target_frame
            else:
                self.speaking = True
                try:
                    current_frame = self.paste_back_frame(res_frame,frame_idx)
                except Exception as e:
                    # 贴回失败 MUST NOT 丢掉这一帧：丢帧会同时断掉这一帧的音频，
                    # 接收端随即欠载（audio queue underrun 刷屏），观感比"贴回质量
                    # 下降"严重得多。退化为输出当前帧源的合法画面继续播放。
                    logger.warning(f"paste_back_frame error: {e}")
                    current_frame = None
                if current_frame is None:
                    current_frame = self._frame_at(frame_idx)
                if current_frame is None:
                    current_frame = _last_out_frame
                if current_frame is None:
                    # 帧源彻底为空（不该发生）：跳过本帧，绝不给下游推空帧。
                    continue
                if enable_transition:
                    # 静音→说话过渡
                    if time.time() - _transition_start < _transition_duration and _last_silent_frame is not None:
                        alpha = min(1.0, (time.time() - _transition_start) / _transition_duration)
                        combine_frame = cv2.addWeighted(_last_silent_frame, 1-alpha, current_frame, alpha, 0)
                    else:
                        combine_frame = current_frame
                    # 缓存说话帧
                    _last_speaking_frame = combine_frame.copy()
                else:
                    combine_frame = current_frame

            # 暂停播放：画面回到**形象自身的**基础素材帧（闭嘴静止），不要定格在
            # 张嘴的推理帧上，也不要停在动作素材的某一帧。
            # 只换画面，不改朗读队列与进度，恢复后从下一帧继续。
            if self.playback_paused:
                # 固定取第 0 帧：多帧形象（wav2lip 素材常有一百多帧）若按 idx 取，
                # 画面会跟着推理进度继续循环——嘴虽闭着，身体却还在动。
                paused_frame = self._base_frame_at(0)
                if paused_frame is not None:
                    combine_frame = paused_frame.copy()

            if combine_frame is None:
                combine_frame = _last_out_frame
            if combine_frame is None:
                continue

            # 这一帧取自哪一代帧源（`advance_gesture` 可能在本帧之后切源）
            frame_gen = self._frame_source_gen

            # 使用统一输出接口推送视频帧
            self.output.push_video_frame(combine_frame)
            _last_out_frame = combine_frame
            _last_out_gen = frame_gen
            # 手势进度按**实际输出的视频帧**推进，且这一帧已经推给接收端了：
            # 播满一遍时切回默认素材，正好落在末帧之后，收尾动作一定播完。
            # 暂停期间不推进——暂停了动作也该停住。
            if not self.playback_paused:
                self.advance_gesture(1)
            self.record_video_data(combine_frame)
            _note_frame(False)

            for audio_frame in audio_frames:
                #frame,type,eventpoint = audio_frame
                data = np.asarray(audio_frame.data, dtype=np.float32).copy()
                cur_type = audio_frame.type

                # 静音→语音切换：开头淡入，避免从 0 突变到语音波形产生爆音
                if cur_type == 0 and _prev_audio_type != 0:
                    n = min(_fade_samples, data.shape[0])
                    data[:n] *= np.linspace(0.0, 1.0, n, dtype=np.float32)
                # 语音→静音切换：结尾淡出，避免语音波形突变到 0 产生爆音
                elif cur_type != 0 and _prev_audio_type == 0:
                    n = min(_fade_samples, data.shape[0])
                    data[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)
                _prev_audio_type = cur_type

                # 防止 NaN/Inf 或浮点溢出进入 WebRTC，避免出现爆音和电流声。
                data = np.nan_to_num(data, nan=0.0, posinf=1.0, neginf=-1.0)
                frame = np.clip(data, -1.0, 1.0)
                frame = np.clip(frame * 32767, -32768, 32767).astype(np.int16)

                # 使用统一输出接口推送音频帧
                self.output.push_audio_frame(frame, audio_frame.userdata)
                self.record_audio_data(frame)
                
            # 帧率控制：基于固定时钟，确保每帧严格 40ms 间隔
            # 不依赖处理耗时，避免帧间隔抖动导致画面卡顿
            now = time.perf_counter()
            wait = _next_frame_time - now
            if wait > 0:
                time.sleep(wait)
                _next_frame_time += _frame_interval
            else:
                # 落后于预定时间，放弃追赶，从当前时刻重新开始
                _next_frame_time = now + _frame_interval

        self.output.stop()
        logger.info('baseavatar process_frames thread stop') 

    def render(self,quit_event):
        self.quit_event = quit_event
        
        self.init_customindex()
        self.tts.render(quit_event)

        infer_quit_event = mp.Event()
        # 守护线程：拆除超时后残线程不得阻止进程退出
        infer_thread = Thread(target=self.inference, args=(infer_quit_event,), daemon=True)
        infer_thread.start()
        
        process_quit_event = Event()
        process_thread = Thread(target=self.process_frames, args=(process_quit_event,), daemon=True)
        process_thread.start()

        count=0
        totaltime=0
        _starttime=time.perf_counter()
        _totalframe=0
        while not quit_event.is_set():
            self.asr.run_step()
            # 1ms yield：防止 CPU 空转，主背压由 feat_queue.put（阻塞）提供
            time.sleep(0.001)
        logger.info('baseavatar render thread stop')

        infer_quit_event.set()
        # 有界 join：render 由 HumanPlayer 的 media-player 线程执行，而
        # HumanPlayer._stop() 会在事件循环线程里等这条线程——这里若无界等待
        # 推理线程，等于把整个服务拖死（2026-10-21 现场故障）。
        infer_thread.join(timeout=THREAD_JOIN_TIMEOUT)
        if infer_thread.is_alive():
            logger.warning('推理线程未在 %.1fs 内退出，放弃等待', THREAD_JOIN_TIMEOUT)

        process_quit_event.set()
        process_thread.join(timeout=THREAD_JOIN_TIMEOUT)
        if process_thread.is_alive():
            logger.warning('帧输出线程未在 %.1fs 内退出，放弃等待', THREAD_JOIN_TIMEOUT)
