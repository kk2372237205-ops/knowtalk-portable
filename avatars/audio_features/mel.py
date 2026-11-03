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
#  Mel 频谱音频特征提取 — 用于 Wav2Lip
#  迁移自 lipasr.py
#

print("[mel.py] 开始导入模块...", flush=True)

import time
print("[mel.py] 导入 time 完成", flush=True)
import torch
print("[mel.py] 导入 torch 完成", flush=True)
import numpy as np
print("[mel.py] 导入 numpy 完成", flush=True)

import queue
from queue import Queue

print("[mel.py] 正在导入 BaseASR...", flush=True)
from utils.logger import logger
from utils.queue_guard import DropReporter, put_capped
from avatars.audio_features.base_asr import BaseASR
print("[mel.py] 导入 BaseASR 完成", flush=True)
print("[mel.py] 正在导入 avatars.wav2lip.audio...", flush=True)
from avatars.wav2lip import audio
print("[mel.py] 导入 audio 完成", flush=True)

# 特征/输出队列单次写入等待上限（秒）。这两个队列都在 render() 主循环的
# 调用路径上，无界 put 会让整条线程链一起卡死。
FEAT_PUSH_TIMEOUT = 0.5

class MelASR(BaseASR):

    def run_step(self):
        ############################################## extract audio feature ##############################################
        # 段边界检测：slide_id 变化时清空帧缓冲区，防止旧段尾部混入新段
        if self._segment_changed:
            self.frames = []
            self._segment_changed = False
        # 段间清帧：上一段音频尾部不再混入当前段的特征提取
        if self._need_clear_frames:
            self.frames = []
            self._need_clear_frames = False
        # get a frame of audio
        audioframes = []
        first_slide = None
        last_slide = None
        for _ in range(self.batch_size*2):
            audioframe = self.get_audio_frame()
            self.frames.append(audioframe.data)
            audioframes.append(audioframe)
            sid = audioframe.userdata.get('slide_id') if audioframe.userdata else None
            if first_slide is None:
                first_slide = sid
            last_slide = sid
        if first_slide != last_slide:
            logger.info(f'[mel run_step] 批次内 slide_id 变化: {first_slide} → {last_slide}, 共{len(audioframes)}帧')
        # 保存本地引用，防止 flush_talk() 在另一个线程中清空 self.frames 导致竞态条件
        frames = self.frames
        # context not enough, do not run network.
        if len(frames) <= self.stride_left_size + self.stride_right_size:
            return
        
        # 缓冲满了才推入 output_queue，避免初始 silence 帧导致黑脸
        # 有界写入：连接断开后推理线程不再消费 output_queue，无界 put 会把
        # render() 主循环（run_step 的调用方）永久卡住。
        if self._output_drop_reporter is None:
            self._output_drop_reporter = DropReporter('asr-output', logger, every=200)
        for audioframe in audioframes:
            put_capped(self.output_queue, audioframe, timeout=FEAT_PUSH_TIMEOUT,
                       on_drop=self._output_drop_reporter)
        
        inputs = np.concatenate(frames) # [N * chunk]
        mel = audio.melspectrogram(inputs)
        #print(mel.shape[0],mel.shape,len(mel[0]),len(frames))
        # cut off stride
        left = max(0, self.stride_left_size*80/50)
        right = min(len(mel[0]), len(mel[0]) - self.stride_right_size*80/50)
        mel_idx_multiplier = 80./self.fps # 80 is the hop length of mel spectrogram, so that each 40ms corresponds to 3.2 mel frames. (80/25=3.2)
        mel_step_size = 16
        i = 0
        mel_chunks = []
        while i < (len(frames)-self.stride_left_size-self.stride_right_size)/2:
            start_idx = int(left + i * mel_idx_multiplier)
            #print(start_idx)
            if start_idx + mel_step_size > len(mel[0]):
                mel_chunks.append(mel[:, len(mel[0]) - mel_step_size:])
            else:
                mel_chunks.append(mel[:, start_idx : start_idx + mel_step_size])
            i += 1
        
        if not mel_chunks:
            logger.warning('[mel] mel_chunks is empty, skipping feat_queue.put (race condition with flush_talk)')
            return

        # 清帧后本批可能只产生少量 mel chunk；补齐到固定 batch，
        # 否则 inference 会消费 32 个音频帧却只输出 6 个视频帧，剩余音频会丢失。
        # 有界写入：feat_queue 只有 5 格，推理线程卡住时这里无界 put 会让
        # render() 主循环再也回不到 quit_event 判断（2026-10-21 现场故障）。
        if self._feat_drop_reporter is None:
            self._feat_drop_reporter = DropReporter('asr-feat', logger, every=200)
        put_capped(self.feat_queue, self.fit_feature_batch(mel_chunks),
                   timeout=FEAT_PUSH_TIMEOUT, on_drop=self._feat_drop_reporter)
        
        # discard the old part to save memory
        self.frames = frames[-(self.stride_left_size + self.stride_right_size):]
