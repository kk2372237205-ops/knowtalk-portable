# -*- coding: utf-8 -*-
"""平台能力判定（唯一来源）。

为什么要单独一个模块：离线 TTS 语音能力只在 Windows 上存在（系统语音 SAPI），
而"默认备用引擎"这个默认值必须跟着平台走。如果 `config.py`、`settings_store.py`、
`tts/sapi.py` 各写一份 `sys.platform.startswith("win")`，三处迟早会漂移，
表现为"设置页显示已启用离线兜底、实际不回退"这类很难查的不一致。
"""

import sys


def default_tts_fallback(platform=None):
    """未显式配置备用引擎时使用的默认值。

    空串表示"该平台没有可用的离线兜底"：在非 Windows 上指向 SAPI 只会制造一个
    必然失败的配置项，把"无兜底"变成"有兜底但必然失败"，反而更难看懂。
    """
    if platform is None:
        platform = sys.platform
    return OFFLINE_TTS_ENGINE if str(platform).startswith("win") else ""


# 离线引擎名（Windows 系统语音），与 tts/sapi.py 的注册名一致
OFFLINE_TTS_ENGINE = "sapi"

IS_WINDOWS = sys.platform.startswith("win")

# 离线 TTS 语音能力是否可用。目前只有 Windows 提供。
HAS_OFFLINE_TTS = IS_WINDOWS

DEFAULT_TTS_FALLBACK = default_tts_fallback()
