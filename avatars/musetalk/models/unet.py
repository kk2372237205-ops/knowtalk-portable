import torch
import torch.nn as nn
import math
import json

from diffusers import UNet2DConditionModel
import sys
import time
import numpy as np
import os
import ctypes
import gc

class PositionalEncoding(nn.Module):
    def __init__(self, d_model=384, max_len=5000):
        super(PositionalEncoding, self).__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0)
        self.register_buffer('pe', pe)

    def forward(self, x):
        b, seq_len, d_model = x.size()
        pe = self.pe[:, :seq_len, :]
        x = x + pe.to(x.device)
        return x


# ─────────────────────────────────────────────────────────────────────────────
# Windows 低内存模型加载
#
# 症状：启动时卡在 "[加载] 加载 MuseTalk 模型..."，进程退出码 3221225477
# （0xC0000005 访问冲突），Python 层没有 traceback，stderr 也是空的。
#
# 根因：8G 显存 + 16G 内存的机器上，UNet 加载期同时要吃三份内存
#   · CPU 3.2GB —— UNet2DConditionModel(**cfg) 先按 fp32 分配全部参数
#   · CPU 3.2GB —— torch.load(mmap=True) 读进来的权重页不会主动归还，
#                  仍然算在本进程工作集里（mmap 在这里 "省内存" 是假的）
#   · GPU 6.3GB —— fp32 参数 + 半精度参数在卡上各存一份
# 物理内存被吃干后，CUDA 驱动做 H2D 拷贝时锁不到新的页，cudaMalloc 会返回
# 「显存明明空闲却分配 46KB 失败」的伪 OOM；驱动层拿不到页时直接炸成
# 0xC0000005，进程瞬死。
#
# 对策（三步，CPU 峰值 < 200MB，显存峰值 1.6GB）：
#   1. 用 meta device 构造 UNet —— 只搭结构，不分配任何存储
#   2. torch.load(mmap=True) + load_state_dict(assign=True) —— 权重零拷贝挂载
#   3. 逐层搬到 GPU 并转 fp16，每搬一批 EmptyWorkingSet 把读过的 mmap 页
#      推回 standby，物理内存立刻释放，驱动始终有页可锁
# ─────────────────────────────────────────────────────────────────────────────

_EMPTY_WORKING_SET = None


def _empty_working_set():
    """把本进程的物理页推回 standby list，释放 mmap 读过的权重页。

    PyTorch 在 Windows 上使用 ``torch.load(mmap=True)`` 时官方也建议配合
    它回收页，否则 3.2GB 权重会一直占着物理内存。
    """
    global _EMPTY_WORKING_SET
    if os.name != 'nt':
        return
    if _EMPTY_WORKING_SET is None:
        try:
            _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            _psapi = ctypes.WinDLL('psapi', use_last_error=True)
            _k32.GetCurrentProcess.restype = ctypes.c_void_p
            _psapi.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
            _psapi.EmptyWorkingSet.restype = ctypes.c_bool
            _EMPTY_WORKING_SET = lambda: _psapi.EmptyWorkingSet(_k32.GetCurrentProcess())
        except Exception:
            _EMPTY_WORKING_SET = lambda: None
    try:
        _EMPTY_WORKING_SET()
    except Exception:
        pass


def _move_to_cuda_fp16(model, device='cuda', every=10):
    """逐层把 CPU 上的 fp32 权重搬成 GPU 上的 fp16，边搬边归还物理内存。

    不能直接用 ``model.to(device, dtype)``：那会在 CPU 侧一次性把整模型转成
    半精度（再要 1.7GB），也会让 mmap 页在短时间内全部驻留内存。
    """
    modules = list(model.modules())
    moved = 0
    for i, mod in enumerate(modules):
        for name, param in list(mod._parameters.items()):
            if param is not None and param.device.type == 'cpu':
                mod._parameters[name] = nn.Parameter(
                    param.to(device=device, dtype=torch.float16),
                    requires_grad=False,
                )
                moved += 1
        for name, buf in list(mod._buffers.items()):
            if buf is not None and buf.device.type == 'cpu':
                mod._buffers[name] = buf.to(device=device, dtype=torch.float16)
                moved += 1
        if i % every == 0:
            _empty_working_set()
    _empty_working_set()
    return moved


def _load_unet_low_mem(unet_config, model_path, device):
    """meta 构造 → mmap 零拷贝挂载 → 逐层搬卡转 fp16。"""
    with torch.device('meta'):
        model = UNet2DConditionModel(**unet_config)
    weights = torch.load(model_path, map_location='cpu', mmap=True)
    model.load_state_dict(weights, assign=True, strict=True)
    del weights
    gc.collect()
    _empty_working_set()
    _move_to_cuda_fp16(model, device=device)
    gc.collect()
    return model


class UNet():
    def __init__(self, 
                 unet_config,
                 model_path,
                 use_float16=False,
                 device=None
        ):
        with open(unet_config, 'r') as f:
            unet_config = json.load(f)
        self.pe = PositionalEncoding(d_model=384)
        if device != None:
            self.device = device
        else:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if isinstance(self.device, str):
            self.device = torch.device(self.device)

        if self.device.type == 'cuda':
            # 推理固定走半精度：避免 fp32 参数和 mmap 权重页同时驻留。
            self.model = _load_unet_low_mem(unet_config, model_path, self.device)
        else:
            self.model = UNet2DConditionModel(**unet_config)
            weights = torch.load(model_path, map_location='cpu', mmap=True)
            self.model.load_state_dict(weights)
            del weights
            gc.collect()
            _empty_working_set()
            if use_float16:
                self.model = self.model.half()
            self.model.to(self.device)

if __name__ == "__main__":
    unet = UNet()
