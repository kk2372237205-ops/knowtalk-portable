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
#  MuseTalk 数字人 — 迁移自 musereal.py + museasr.py
#

import math
import torch
import numpy as np

import subprocess
import os
import time
import torch.nn.functional as F
import cv2
import glob
import pickle
import copy

import queue
from queue import Queue
from threading import Thread, Event
import torch.multiprocessing as mp

from avatars.musetalk.utils.utils import get_file_type,get_video_fps,datagen
from avatars.musetalk.myutil import get_image_blending
from avatars.musetalk.utils.utils import load_all_model
from avatars.musetalk.whisper.audio2feature import Audio2Feature

from avatars.audio_features.whisper import WhisperASR
import asyncio
from av import AudioFrame, VideoFrame
from avatars.base_avatar import BaseAvatar, _indexed

from tqdm import tqdm
from utils.logger import logger
from utils.image import read_imgs, mirror_index
from utils.device import initialize_device
from registry import register

device = initialize_device()
logger.info('Using {} for inference.'.format(device))

def load_model():
    # load model weights
    vae, unet, pe = load_all_model()
    #device = torch.device("cuda" if torch.cuda.is_available() else ("mps" if (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()) else "cpu"))
    timesteps = torch.tensor([0], device=device)
    pe = pe.half().to(device)
    vae.vae = vae.vae.half().to(device)
    unet.model = unet.model.half().to(device)
    # 加载期 VAE 会先在 GPU 上放一份 fp32 权重再转半精度；释放出来的缓存块会一直占着，
    # 在 8G 卡上与桌面/Photoshop 同跑时容易把显存顶满，这里主动还给驱动。
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    # Initialize audio processor and Whisper model
    audio_processor = Audio2Feature(model_path="./models/whisper")
    return vae, unet, pe, timesteps, audio_processor

def load_avatar(avatar_id):
    avatar_path = f"./data/avatars/{avatar_id}"
    full_imgs_path = f"{avatar_path}/full_imgs" 
    coords_path = f"{avatar_path}/coords.pkl"
    latents_out_path= f"{avatar_path}/latents.pt"
    video_out_path = f"{avatar_path}/vid_output/"
    mask_out_path =f"{avatar_path}/mask"
    mask_coords_path =f"{avatar_path}/mask_coords.pkl"
    avatar_info_path = f"{avatar_path}/avator_info.json"

    input_latent_list_cycle = torch.load(latents_out_path)
    with open(coords_path, 'rb') as f:
        coord_list_cycle = pickle.load(f)
    frame_list_cycle = None
    input_img_list = glob.glob(os.path.join(full_imgs_path, '*.[jpJP][pnPN]*[gG]'))
    input_img_list = sorted(input_img_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    frame_list_cycle = read_imgs(input_img_list)
    with open(mask_coords_path, 'rb') as f:
        mask_coords_list_cycle = pickle.load(f)
    input_mask_list = glob.glob(os.path.join(mask_out_path, '*.[jpJP][pnPN]*[gG]'))
    input_mask_list = sorted(input_mask_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    mask_list_cycle = read_imgs(input_mask_list)
    return frame_list_cycle,mask_list_cycle,coord_list_cycle,mask_coords_list_cycle,input_latent_list_cycle

@torch.no_grad()
def warm_up(batch_size,model):
    # 预热函数
    print('warmup model...')
    vae, unet, pe, timesteps, audio_processor = model
    whisper_batch = np.ones((batch_size, 50, 384), dtype=np.uint8)
    latent_batch = torch.ones(batch_size, 8, 32, 32).to(unet.device)

    audio_feature_batch = torch.from_numpy(whisper_batch)
    audio_feature_batch = audio_feature_batch.to(device=unet.device, dtype=unet.model.dtype)
    audio_feature_batch = pe(audio_feature_batch)
    latent_batch = latent_batch.to(dtype=unet.model.dtype)
    pred_latents = unet.model(latent_batch,
                              timesteps,
                              encoder_hidden_states=audio_feature_batch).sample
    vae.decode_latents(pred_latents)    

@register("avatar", "musetalk")
class MuseReal(BaseAvatar):
    @torch.no_grad()
    def __init__(self, opt, model, avatar):
        super().__init__(opt)

        # MuseTalk 播动作必须有 musetalk/ 下的人脸参考资产（形象自己的一份 latent
        # 与遮罩）；缺了就降级为「不播动作、照常朗读」，而不是拿基础 latent 硬配手势帧。
        self._requires_muse_assets = True

        #self.fps = opt.fps # 20 ms per frame

        # self.batch_size = opt.batch_size
        # self.idx = 0
        # self.res_frame_queue = mp.Queue(self.batch_size*2)

        self.vae, self.unet, self.pe, self.timesteps, self.audio_processor = model

        self.frame_list_cycle,self.mask_list_cycle,self.coord_list_cycle,self.mask_coords_list_cycle, self.input_latent_list_cycle = avatar

        self.asr = WhisperASR(opt,self,self.audio_processor)
        self.asr.warm_up()
    

    @torch.no_grad()
    def inference_batch(self, index, audiofeat_batch):
        # 这里的 index 是针对当前 avatar 的索引
        # 返回一个 batch 的推理结果，batch 大小由 self.batch_size 决定
        #
        # 手势期间帧源会切到动作素材，latent 也必须跟着切——否则会拿基础形象的
        # latent 去配手势帧，出来的嘴型与画面完全不匹配。
        extra = getattr(self, '_active_extra', None)
        latents_seq = extra['latents'] if extra else self.input_latent_list_cycle
        length = len(latents_seq)
        whisper_batch = np.stack(audiofeat_batch)
        latent_batch = []
        for i in range(self.batch_size):
            idx = mirror_index(length, index + i)
            latent = latents_seq[idx]
            latent_batch.append(latent)
        latent_batch = torch.cat(latent_batch, dim=0)
        
        audio_feature_batch = torch.from_numpy(whisper_batch)
        audio_feature_batch = audio_feature_batch.to(device=self.unet.device,
                                                        dtype=self.unet.model.dtype)
        audio_feature_batch = self.pe(audio_feature_batch)
        latent_batch = latent_batch.to(dtype=self.unet.model.dtype)

        pred_latents = self.unet.model(latent_batch, 
                                    self.timesteps, 
                                    encoder_hidden_states=audio_feature_batch).sample
        pred = self.vae.decode_latents(pred_latents)
        return pred

    def paste_back_frame(self,pred_frame,idx:int):
        # 手势期间帧源已切到动作素材：原帧、人脸框、遮罩都必须跟着切，
        # 否则动作帧永远贴不上去（这正是此前「MuseTalk 下动作不生效」的根因）。
        #
        # 所有序列一律按各自长度取模（`_indexed`），MUST NOT 用裸下标：
        # 帧源切换是跨线程的，渲染线程拿到的 `idx` 可能属于**切换前**那套素材。
        # 默认形象往往只有 1 帧（musetalk_ke 的 full_imgs / mask / coords 各 1 个），
        # 而手势素材有 89 帧——动作播完切回默认素材时，队列里还压着一批
        # idx=1..88 的在途帧，裸下标会**逐帧越界**。越界异常被渲染线程的
        # `except` 吞成一条 warning 后，该帧的**视频与音频一起被丢弃**，
        # 现场表现就是"每个动作快结束时卡一下"外加一串 audio queue underrun。
        extra = getattr(self, '_active_extra', None)
        if extra:
            ori_frame = copy.deepcopy(self._frame_at(idx))
            bbox = tuple(int(v) for v in _indexed(extra['coords'], idx))
            mask = _indexed(extra['masks'], idx)
            mask_crop_box = _indexed(extra['mask_coords'], idx)
        else:
            bbox = _indexed(self.coord_list_cycle, idx)
            # MUST 走 `_frame_at` 而不是裸 `frame_list_cycle`：后者拿不到背景合成，
            # 说话（非手势）时画面就会退回原始白底——这正是"说话时背景还是白的"根因。
            ori_frame = copy.deepcopy(self._frame_at(idx))
            mask = _indexed(self.mask_list_cycle, idx)
            mask_crop_box = _indexed(self.mask_coords_list_cycle, idx)

        if ori_frame is None or bbox is None:
            logger.warning('[口型] 帧源为空（idx=%s），本帧跳过贴回', idx)
            return None

        x1, y1, x2, y2 = bbox

        res_frame = cv2.resize(pred_frame.astype(np.uint8),(x2-x1,y2-y1))
        combine_frame = get_image_blending(ori_frame,res_frame,bbox,mask,mask_crop_box)
        return combine_frame
            
