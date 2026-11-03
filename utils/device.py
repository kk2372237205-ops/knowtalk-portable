import torch
import warnings

from utils.logger import logger


def cuda_build_installed() -> bool:
    """当前安装的 torch 是否为 CUDA 构建（而不是 CPU-only 构建）。"""
    return bool(getattr(torch.version, "cuda", None))


def initialize_device() -> torch.device:
    """选择推理设备。

    优先级：CUDA → MPS → CPU。

    当安装的是 CUDA 构建却落到 CPU 时给出明确警告：这种情况通常意味着驱动、
    显存或设备可见性有问题，若不提示，使用者会以为自己在用 GPU 而实际很慢
    （历史上本项目就长期处于"有显卡但装的是 CPU-only 构建"的状态）。
    """
    if torch.cuda.is_available():
        device = torch.device('cuda')
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        device = torch.device('mps')
    else:
        device = torch.device('cpu')
        if cuda_build_installed():
            logger.warning(
                "已安装 CUDA 构建的 PyTorch (%s)，但 CUDA 当前不可用，推理将运行在 CPU 上。"
                "请检查显卡驱动、显存是否被占满，或设备是否被显式禁用。",
                torch.version.cuda,
            )
            warnings.warn(
                "torch 是 CUDA 构建但 CUDA 不可用，当前落到 CPU；请核对声明与实际的设备一致性。",
                stacklevel=2,
            )

    logger.info("设备初始化完成: %s", device.type)
    return device
