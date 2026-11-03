# -*- coding: utf-8 -*-
"""素材文件类型判定：区分「视频」与「图片」。

生成任务现在既收视频也收图片（单张图片生成静态背景的数字人形象），
需要一个不依赖 magic number、只看扩展名的轻量判定——扩展名由上传方控制、
且落盘前已经过 `path_guard.sanitize_filename` 归一，这里只做分类，不担安全职责。
"""

import os

VIDEO_EXTS = (".mp4", ".mkv", ".flv", ".avi", ".mov", ".webm", ".m4v", ".ts")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


def _ext(path: str) -> str:
    return os.path.splitext(str(path))[1].lower()


def is_video_file(path: str) -> bool:
    """``path`` 看起来是视频文件（按扩展名）。"""
    return _ext(path) in VIDEO_EXTS


def is_image_file(path: str) -> bool:
    """``path`` 看起来是图片文件（按扩展名）。"""
    return _ext(path) in IMAGE_EXTS


def is_media_file(path: str) -> bool:
    """视频或图片都算合法素材。"""
    ext = _ext(path)
    return ext in VIDEO_EXTS or ext in IMAGE_EXTS
