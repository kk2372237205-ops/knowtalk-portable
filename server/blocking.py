# -*- coding: utf-8 -*-
"""把阻塞调用挪出事件循环。

`aiohttp` 的协程里直接调用同步阻塞函数（大模型判分、Whisper 转写、磁盘大文件）
会把整个事件循环冻住：同一条循环上的 `aiortc` 音视频收发、提问编排的 `tick`
以及所有轮询接口会一起停摆。表现就是"判分很慢、画面卡住、状态不动"。

这里统一用默认线程池卸载，调用方保持原有的同步写法，不需要改状态机。
"""

import asyncio
import functools


async def run_blocking(fn, *args, **kwargs):
    """在线程池中执行同步函数并等待结果。

    异常会原样透出到调用方协程，调用方可以照旧用 try/except 处理。
    """
    loop = asyncio.get_running_loop()
    call = functools.partial(fn, *args, **kwargs) if kwargs else functools.partial(fn, *args)
    return await loop.run_in_executor(None, call)
