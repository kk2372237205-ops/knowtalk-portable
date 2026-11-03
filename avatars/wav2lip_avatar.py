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
#  Wav2Lip 数字人 — 迁移自 lipreal.py + lipasr.py
#

print("[wav2lip_avatar] 开始导入模块...", flush=True)

import math
print("[wav2lip_avatar] 导入 math 完成", flush=True)
import torch
print("[wav2lip_avatar] 导入 torch 完成", flush=True)
import numpy as np
print("[wav2lip_avatar] 导入 numpy 完成", flush=True)

import os
import time
import cv2
print("[wav2lip_avatar] 导入 cv2 完成", flush=True)
import glob
import pickle
import copy

import queue
from queue import Queue
from threading import Thread, Event
import torch.multiprocessing as mp
print("[wav2lip_avatar] 导入 torch.multiprocessing 完成", flush=True)

from avatars.audio_features.mel import MelASR
print("[wav2lip_avatar] 导入 MelASR 完成", flush=True)
import asyncio
from av import AudioFrame, VideoFrame
from avatars.wav2lip.models import Wav2Lip
print("[wav2lip_avatar] 导入 Wav2Lip 完成", flush=True)
from avatars.base_avatar import BaseAvatar
print("[wav2lip_avatar] 导入 BaseAvatar 完成", flush=True)

from tqdm import tqdm
from utils.logger import logger
from utils.image import read_imgs, mirror_index
from utils.device import initialize_device
from registry import register
print("[wav2lip_avatar] 导入 registry 完成", flush=True)

device = initialize_device()
print(f"[wav2lip_avatar] 设备初始化完成: {device}", flush=True)
logger.info('Using {} for inference.'.format(device))

def _load(checkpoint_path):
    if device == 'cuda':
        checkpoint = torch.load(checkpoint_path)
    else:
        checkpoint = torch.load(checkpoint_path,
                                map_location=lambda storage, loc: storage)
    return checkpoint

def load_model(path):
    model = Wav2Lip()
    logger.info("Load checkpoint from: {}".format(path))
    checkpoint = _load(path)
    s = checkpoint["state_dict"]
    new_s = {}
    for k, v in s.items():
        new_s[k.replace('module.', '')] = v
    model.load_state_dict(new_s)

    model = model.to(device)
    return model.eval()

def load_avatar(avatar_id):
    avatar_path = f"./data/avatars/{avatar_id}"
    full_imgs_path = f"{avatar_path}/full_imgs" 
    face_imgs_path = f"{avatar_path}/face_imgs" 
    coords_path = f"{avatar_path}/coords.pkl"
    
    with open(coords_path, 'rb') as f:
        coord_list_cycle = pickle.load(f)
    frame_list_cycle = None
    input_img_list = glob.glob(os.path.join(full_imgs_path, '*.[jpJP][pnPN]*[gG]'))
    input_img_list = sorted(input_img_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    frame_list_cycle = read_imgs(input_img_list)
    input_face_list = glob.glob(os.path.join(face_imgs_path, '*.[jpJP][pnPN]*[gG]'))
    input_face_list = sorted(input_face_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    face_list_cycle = read_imgs(input_face_list)

    return frame_list_cycle,face_list_cycle,coord_list_cycle

@torch.no_grad()
def warm_up(batch_size,model,modelres):
    # 预热函数
    logger.info('warmup model...')
    img_batch = torch.ones(batch_size, 6, modelres, modelres).to(device)
    mel_batch = torch.ones(batch_size, 1, 80, 16).to(device)
    model(mel_batch, img_batch)

@register("avatar", "wav2lip")
class LipReal(BaseAvatar):
    @torch.no_grad()
    def __init__(self, opt, model, avatar):
        super().__init__(opt)

        #self.fps = opt.fps # 20 ms per frame
        
        # self.batch_size = opt.batch_size
        # self.idx = 0
        # self.res_frame_queue = Queue(self.batch_size*2)
        self.model = model

        self.frame_list_cycle,self.face_list_cycle,self.coord_list_cycle = avatar
        # 默认素材即"永远可用的帧源"；手势切换只是改这三个引用（见 base_avatar 的手势编排一节）
        self._bind_default_frames(self.frame_list_cycle, self.face_list_cycle, self.coord_list_cycle)

        # 口型幅度控制：0.0=不动嘴, 1.0=原始幅度
        self.mouth_alpha = getattr(opt, 'mouth_alpha', 0.90)
        self.mouth_offset_y = getattr(opt, 'mouth_offset_y', 0.0)
        self.mouth_offset_x = getattr(opt, 'mouth_offset_x', 0.0)
        # 加载时用模型自身定位真实嘴部运动区域 [my1, my2, mx1, mx2]（归一化坐标）
        self.mouth_region_frac = self._detect_mouth_region()
        logger.info(f'[口型控制] mouth_alpha={self.mouth_alpha}, offset_y={self.mouth_offset_y}, offset_x={self.mouth_offset_x}')
        logger.info(f'[口型控制] 嘴部区域(归一化) my1~my2={self.mouth_region_frac[0]:.2f}~{self.mouth_region_frac[1]:.2f}, mx1~mx2={self.mouth_region_frac[2]:.2f}~{self.mouth_region_frac[3]:.2f}')

        self.asr = MelASR(opt,self)
        self.asr.warm_up()

    def _detect_mouth_region(self):
        """加载时用模型自身定位真实嘴部运动区域（一次性，避免手工框定误差）。

        分别以静音和探针音频前向一次，两者输出差异即音频驱动的嘴部运动热区，
        天然只落在嘴唇/开口处，不包含下巴、额头、眼睛，且左右对称。
        """
        n = min(4, len(self.face_list_cycle))
        idxs = np.linspace(0, len(self.face_list_cycle) - 1, n).astype(int)
        faces = np.stack([self.face_list_cycle[i] for i in idxs]).astype(np.float32)
        H, W = faces.shape[1], faces.shape[2]

        img_masked = faces.copy()
        img_masked[:, H // 2:] = 0
        img_batch = np.concatenate((img_masked, faces), axis=3) / 255.
        img_batch = torch.from_numpy(np.ascontiguousarray(np.transpose(img_batch, (0, 3, 1, 2)))).float().to(device)

        def _mel(a):
            return torch.from_numpy(np.ascontiguousarray(a[:, None, :, :])).float().to(device)

        silence = np.zeros((n, 80, 16), np.float32)
        probe = np.zeros((n, 80, 16), np.float32)
        probe[:, 4:48, :] = 1.4  # 探针：低频满能量，模拟说话

        with torch.no_grad():
            pz = self.model(_mel(silence), img_batch)
            ps = self.model(_mel(probe), img_batch)

        move = (ps - pz).abs().mean(dim=1).mean(dim=0)  # [H, W]
        move = move.cpu().numpy()

        # 用热区质心定位嘴部中心，而非 min/max 取极值：
        # 极值框会被下巴/脸颊的零散响应拖大拖偏，忽上忽下。
        # 只在下半脸中间区域统计质心，排除边缘与下巴对重心的拖拽。
        y0, y1r = int(H * 0.45), int(H * 0.88)
        x0, x1r = int(W * 0.12), int(W * 0.88)
        roi = move[y0:y1r, x0:x1r]
        row = roi.sum(axis=1)
        col = roi.sum(axis=0)
        if row.sum() <= 1e-6:
            # 兜底：常见头部数字人嘴部位置
            return (0.55, 0.86, 0.28, 0.80)

        ys = np.arange(y0, y1r)
        xs = np.arange(x0, x1r)
        cy = float((row * ys).sum() / row.sum())  # 像素坐标
        cx = float((col * xs).sum() / col.sum())

        # 垂直中心 clamp 到嘴部合理区间，防止质心被下巴拉低
        cy = np.clip(cy, H * 0.60, H * 0.74) / H
        cx = cx / W

        # 固定嘴部框尺寸：高度覆盖张嘴的上下唇+口腔，宽度含完整唇宽
        MH, MW = 0.32, 0.56
        my1 = float(np.clip(cy - MH / 2, 0.0, 1.0 - MH))
        my2 = my1 + MH
        mx1 = float(np.clip(cx - MW / 2, 0.0, 1.0 - MW))
        mx2 = mx1 + MW

        logger.info(f'[口型定位] 热区质心 cx={cx:.2f} cy={cy:.2f}, 嘴部框 y=[{my1:.2f},{my2:.2f}] x=[{mx1:.2f},{mx2:.2f}]')
        return (my1, my2, mx1, mx2)
    
    def inference_batch(self, index, audiofeat_batch):
        # 这里的 index 是针对当前 avatar 的索引
        # 返回一个 batch 的推理结果，batch 大小由 self.batch_size 决定
        length = len(self._active_faces or self.face_list_cycle)
        img_batch = []
        for i in range(self.batch_size):
            idx = mirror_index(length, index + i)
            face = self._face_at(idx)
            img_batch.append(face)
        img_batch = np.asarray(img_batch)
        
        # 防御性检查：audiofeat_batch 为空时直接返回空白帧
        if len(audiofeat_batch) == 0:
            logger.warning('audiofeat_batch is empty, returning blank frames (race condition with flush_talk)')
            return np.zeros((self.batch_size, face.shape[0], face.shape[1], 3), dtype=np.uint8)
        
        audiofeat_batch = np.asarray(audiofeat_batch)

        # 确保 audiofeat_batch 是 3D [N, 80, 16]
        # 防止 mel_chunks 维度异常导致 reshape 越界
        if audiofeat_batch.ndim == 2:
            # 只有一个 mel chunk [80, 16]，扩展为 [1, 80, 16]
            logger.warning(f'audiofeat_batch is 2D {audiofeat_batch.shape}, expanding to 3D')
            audiofeat_batch = audiofeat_batch[np.newaxis, ...]
        elif audiofeat_batch.ndim == 1:
            # object 数组（mel_chunks 元素形状不一致），尝试堆叠
            logger.warning(f'audiofeat_batch is 1D {audiofeat_batch.shape}, trying to stack')
            try:
                audiofeat_batch = np.stack(list(audiofeat_batch))
            except Exception as e:
                logger.error(f'Failed to stack audiofeat_batch: {e}')
                return np.zeros((self.batch_size, face.shape[0], face.shape[1], 3), dtype=np.uint8)

        # 安全检查：确保 audiofeat_batch 和 img_batch 的 batch 维度一致
        n_img = img_batch.shape[0]
        n_aud = audiofeat_batch.shape[0]
        if n_aud != n_img:
            logger.warning(f'batch_size mismatch: audiofeat={n_aud}, img={n_img}, 截断到较小值 {min(n_img, n_aud)}')
            n = min(n_img, n_aud)
            img_batch = img_batch[:n]
            audiofeat_batch = audiofeat_batch[:n]

        img_masked = img_batch.copy()
        img_masked[:, face.shape[0]//2:] = 0

        img_batch = np.concatenate((img_masked, img_batch), axis=3) / 255.
        audiofeat_batch = np.reshape(audiofeat_batch, [len(audiofeat_batch), audiofeat_batch.shape[1], audiofeat_batch.shape[2], 1])

        # 使用 from_numpy 替代 FloatTensor，避免数据拷贝
        img_batch = torch.from_numpy(np.ascontiguousarray(np.transpose(img_batch, (0, 3, 1, 2)))).float().to(device)
        audiofeat_batch = torch.from_numpy(np.ascontiguousarray(np.transpose(audiofeat_batch, (0, 3, 1, 2)))).float().to(device)

        with torch.no_grad():
            pred = self.model(audiofeat_batch, img_batch)
        # 在 GPU 上完成 *255 计算，减少 CPU 负担
        pred = (pred * 255.).cpu().numpy().transpose(0, 2, 3, 1)
        return pred

    def paste_back_frame(self,pred_frame,idx:int):
        bbox = self._coord_at(idx)
        combine_frame = self._frame_at(idx).copy()
        # 第二道防线：人脸框理应是整数像素坐标，但 coords 来自磁盘上的 .pkl，可能
        # 带着浮点（例如历史手势素材或将来新增的缩放路径）。**必须在入口一次取整**：
        # 只给 cv2.resize 的 dsize 取整是不够的——下面的 numpy 切片
        # `combine_frame[y1:y2, x1:x2]` 同样会因为浮点索引抛 TypeError，
        # 只是把失败点从一处搬到另一处。那种失败被调用方吞成一条 warning，
        # 表现成"嘴不动"，几乎不可能从画面上反查出来。
        y1, y2, x1, x2 = (int(round(value)) for value in bbox)
        w, h = x2 - x1, y2 - y1
        if w < 1 or h < 1:
            logger.warning('[口型] 人脸框尺寸非法（w=%s, h=%s），跳过本帧贴回', w, h)
            return combine_frame

        # 高质量上采样（LANCZOS4），提升嘴部清晰度
        res_frame = cv2.resize(pred_frame.astype(np.uint8), (w, h), interpolation=cv2.INTER_LANCZOS4)

        if self.mouth_alpha < 1.0:
            orig_face = cv2.resize(self._face_at(idx), (w, h), interpolation=cv2.INTER_LANCZOS4)
            gen_face = res_frame.astype(np.float32)
            orig_face_f = orig_face.astype(np.float32)

            # 加载时用模型自身定位到的嘴部区域（含两角与张嘴开口）
            my1, my2, mx1, mx2 = self.mouth_region_frac
            mouth_y1 = int(np.clip(h * my1 + h * self.mouth_offset_y, 0, h - 1))
            mouth_y2 = int(np.clip(h * my2 + h * self.mouth_offset_y, mouth_y1 + 1, h))
            mouth_x1 = int(np.clip(w * mx1 + w * self.mouth_offset_x, 0, w - 1))
            mouth_x2 = int(np.clip(w * mx2 + w * self.mouth_offset_x, mouth_x1 + 1, w))
            m_slice = (slice(mouth_y1, mouth_y2), slice(mouth_x1, mouth_x2))

            # 颜色校正：局部高斯匹配，减少色差和逐帧抖动
            mh = mouth_y2 - mouth_y1
            mw = mouth_x2 - mouth_x1
            k = max(5, min(mh, mw) // 4)
            k = k if k % 2 == 1 else k + 1
            for c in range(3):
                g = gen_face[m_slice[0], m_slice[1], c]
                o = orig_face_f[m_slice[0], m_slice[1], c]
                g_blur = cv2.GaussianBlur(g, (k, k), 0)
                o_blur = cv2.GaussianBlur(o, (k, k), 0)
                g += (o_blur - g_blur)

            # 嘴部 alpha 混合（控制口型幅度）
            gen_mouth = gen_face[m_slice[0], m_slice[1]]
            orig_mouth = orig_face_f[m_slice[0], m_slice[1]]
            gen_face[m_slice[0], m_slice[1]] = (
                self.mouth_alpha * gen_mouth + (1.0 - self.mouth_alpha) * orig_mouth
            )

            # 高斯羽化 mask：平滑过渡嘴部边界，消除生硬接缝
            feather = max(3, min(mh, mw) // 20)
            mask = np.zeros((h, w), dtype=np.float32)
            mask[mouth_y1:mouth_y2, mouth_x1:mouth_x2] = 1.0
            mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=feather)
            mask = np.clip(mask, 0.0, 1.0)[..., None]

            res_frame = np.clip(gen_face * mask + orig_face_f * (1.0 - mask), 0, 255).astype(np.uint8)

        combine_frame[y1:y2, x1:x2] = res_frame
        return combine_frame

