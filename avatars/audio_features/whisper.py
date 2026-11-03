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
#  Whisper 音频特征提取 — 用于 MuseTalk
#  迁移自 museasr.py
#

import time
import numpy as np

import queue
from queue import Queue
from utils.logger import logger
from utils.queue_guard import DropReporter, put_capped
from avatars.audio_features.base_asr import BaseASR
from avatars.musetalk.whisper.audio2feature import Audio2Feature

# 特征/输出队列单次写入等待上限（秒）。二者都在 render() 主循环的调用路径上，
# 无界 put 会在断线（推理线程不再消费）时把整条线程链一起卡死。
FEAT_PUSH_TIMEOUT = 0.5

class WhisperASR(BaseASR):
    def __init__(self, opt, parent, audio_processor:Audio2Feature):
        super().__init__(opt, parent)
        self.audio_processor = audio_processor
    
    def _feature2chunks(self,feature_array,batch_size,audio_feat_win=[8,8],start=0,feature_idx_multiplier=1.0):
        """
        :param feature_array: 
        :param batch_size: batch大小
        :param audio_feat_win: 音频特征窗口大小，通常为 [左侧窗口大小, 右侧窗口大小]，单位为视频帧数
        :param start: 起始帧索引，通常为 stride_left_size/2
        :param feature_idx_multiplier: 用于将视频帧索引转换为特征索引的乘数，通常为 (特征提取的宽度 / 视频帧率)
        :return: 
        """
        feature_chunks = []
        #start += 10
        #feature_idx_multiplier = 50./fps 
        for i in range(batch_size):
            # start_idx = int(i * whisper_idx_multiplier)
            # if start_idx>=len(feature_array):
            #     break
            selected_feature,selected_idx = self._get_sliced_feature(
                feature_array=feature_array, vid_idx=i+start,
                audio_feat_win=audio_feat_win, feature_idx_multiplier=feature_idx_multiplier)
            #print(f"i:{i},selected_idx {selected_idx},feature_idx_multiplier:{feature_idx_multiplier}")
            feature_chunks.append(selected_feature.reshape(-1, 384))
        return feature_chunks

    def run_step(self):
        ############################################## extract audio feature ##############################################
        start_time = time.time()
        # 段边界检测：slide_id 变化时清空帧缓冲区，防止旧段尾部混入新段
        if self._segment_changed:
            self.frames = []
            self._segment_changed = False
        # 段间清帧：上一段音频尾部不再混入当前段的特征提取
        if self._need_clear_frames:
            self.frames = []
            self._need_clear_frames = False
        audioframes = []
        for _ in range(self.batch_size*2):
            audio_frame = self.get_audio_frame()
            self.frames.append(audio_frame.data)
            audioframes.append(audio_frame)
        
        # 保存本地引用，防止 flush_talk() 在另一个线程中清空 self.frames 导致竞态条件
        frames = self.frames
        if len(frames) <= self.stride_left_size + self.stride_right_size:
            return
        
        # 有界写入：连接断开后推理线程不再消费 output_queue，
        # 无界 put 会把 render() 主循环（run_step 的调用方）永久卡住。
        if self._output_drop_reporter is None:
            self._output_drop_reporter = DropReporter('asr-output', logger, every=200)
        for audioframe in audioframes:
            put_capped(self.output_queue, audioframe, timeout=FEAT_PUSH_TIMEOUT,
                       on_drop=self._output_drop_reporter)
        
        inputs = np.concatenate(frames) # [N * chunk]
        whisper_feature = self.audio_processor.audio2feat(inputs)
        # 取特征窗口必须跟着帧率走：whisper 特征固定 50 帧/秒，而每帧视频要取
        # 10 个特征（叠 5 层 → 50 tokens，正是 unet 期望的序列长度）。
        # 25fps 时 multiplier=2、窗口 [0,5]、start=5，与上游写法完全一致（不改行为）；
        # 换成 20fps 则 multiplier=2.5、窗口 [0,4]、start=4，保证每帧仍是 10 个特征。
        # 写死 2 的话，一旦 --fps 不是 25 就会取到 9 或 11 个特征 → 口型逐渐错位。
        _multiplier = 50.0 / float(self.fps)
        _win = 10.0 / _multiplier
        whisper_chunks = self._feature2chunks(feature_array=whisper_feature,batch_size=self.batch_size,
                                              audio_feat_win = [0, _win],start=_win,
                                              feature_idx_multiplier=_multiplier)
        # 有界写入：feat_queue 只有 5 格，推理线程卡住时无界 put 会让主循环回不到 quit_event 判断。
        if self._feat_drop_reporter is None:
            self._feat_drop_reporter = DropReporter('asr-feat', logger, every=200)
        put_capped(self.feat_queue, self.fit_feature_batch(whisper_chunks),
                   timeout=FEAT_PUSH_TIMEOUT, on_drop=self._feat_drop_reporter)
        # discard the old part to save memory
        self.frames = frames[-(self.stride_left_size + self.stride_right_size):]
