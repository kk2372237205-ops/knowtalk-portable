# -*- coding: utf-8 -*-
"""MiniMax H3 图生视频的最小封装：建任务 → 轮询 → 下载。

只覆盖「首帧图生视频」这一种用法（`content[]` 里一个 text + 一个
`image_url` 且 `role=first_frame`）。接口是异步的：POST 拿到 task_id，
之后 GET 查询列表里按 id 找状态，成功后 `content.url` 是带签名的 mp4 地址。

密钥、基址、模型名全部来自运行期设置（`minimax.*`），不写死在这里。
"""

import base64
import os

from utils.logger import logger
from utils.settings_store import get_store

DEFAULT_TIMEOUT = 15 * 60
POLL_SECONDS = 10
_REQUEST_TIMEOUT = 60

# 上游状态词：文档写 Success/Fail，实测返回 succeeded/running，统一按前缀判
_DONE = ('success', 'succeeded', 'succeed', 'done')
_FAILED = ('fail', 'failed', 'error', 'cancel')


class MinimaxError(RuntimeError):
    """调用 MiniMax 失败（含上游返回的错误信息）。"""


class MinimaxNotConfigured(MinimaxError):
    """没配密钥——调用方据此给出「先去设置页填 Key」的提示。"""


def settings():
    store = get_store()
    return {
        'api_key': (store.get('minimax.api_key') or '').strip(),
        'base_url': (store.get('minimax.base_url') or 'https://api.minimax.cn').rstrip('/'),
        'model': (store.get('minimax.video_model') or 'MiniMax-H3').strip(),
        'resolution': (store.get('minimax.resolution') or '768P').strip(),
        'duration': int(store.get('minimax.duration') or 5),
    }


def is_configured():
    return bool(settings()['api_key'])


def _headers(api_key):
    return {'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'}


def image_data_uri(path):
    """把本地图片编成 data URI。H3 的 image_url 接受 URL；本地文件走 data URI 最省事。"""
    with open(path, 'rb') as handle:
        raw = handle.read()
    suffix = os.path.splitext(path)[1].lower().lstrip('.') or 'png'
    mime = {'jpg': 'jpeg'}.get(suffix, suffix)
    return 'data:image/%s;base64,%s' % (mime, base64.b64encode(raw).decode('ascii'))


def create_task(prompt, image_path, duration=None, resolution=None):
    """建一个图生视频任务，返回 task_id。"""
    conf = settings()
    if not conf['api_key']:
        raise MinimaxNotConfigured('还没配置 MiniMax API Key（设置页 → 动作制作）')

    payload = {
        'model': conf['model'],
        'content': [
            {'type': 'text', 'text': prompt},
            {'type': 'image_url', 'image_url': {'url': image_data_uri(image_path)},
             'role': 'first_frame'},
        ],
        'duration': int(duration or conf['duration']),
        'resolution': resolution or conf['resolution'],
    }
    url = '%s/v2/video_generation' % conf['base_url']
    import requests

    try:
        resp = requests.post(url, headers=_headers(conf['api_key']),
                             json=payload, timeout=_REQUEST_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - 网络异常统一转成可读错误
        raise MinimaxError('连接 MiniMax 失败：%s' % exc)
    if resp.status_code != 200:
        raise MinimaxError('MiniMax 拒绝请求（HTTP %d）：%s'
                           % (resp.status_code, _short(resp.text)))
    data = resp.json()
    task_id = data.get('task_id') or data.get('id')
    if not task_id:
        raise MinimaxError('MiniMax 没返回任务号：%s' % _short(resp.text))
    logger.info('[动作制作] 已建 MiniMax 任务 %s（%s，%s）',
                task_id, payload['duration'], payload['resolution'])
    return str(task_id)


def query_task(task_id):
    """查一个任务：返回 {status, url, reason}。状态统一成小写字符串。"""
    conf = settings()
    if not conf['api_key']:
        raise MinimaxNotConfigured('还没配置 MiniMax API Key（设置页 → 动作制作）')
    url = '%s/v2/query/video_generation' % conf['base_url']
    import requests

    try:
        resp = requests.get(url, headers=_headers(conf['api_key']),
                            params={'task_id': task_id}, timeout=_REQUEST_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        raise MinimaxError('查询 MiniMax 任务失败：%s' % exc)
    if resp.status_code != 200:
        raise MinimaxError('查询 MiniMax 任务失败（HTTP %d）：%s'
                           % (resp.status_code, _short(resp.text)))
    payload = resp.json()
    item = _pick_task(payload, task_id)
    status = str(item.get('status') or '').strip().lower()
    content = item.get('content') or {}
    reason = item.get('error_message') or item.get('error') or ''
    return {'status': status, 'url': content.get('url') or item.get('download_url') or '',
            'reason': reason if isinstance(reason, str) else str(reason)}


def wait_for_video(task_id, timeout=DEFAULT_TIMEOUT, on_poll=None):
    """轮询到终态，返回可下载的 mp4 地址。超时/失败抛 :class:`MinimaxError`。"""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        info = query_task(task_id)
        status = info['status']
        if info['url'] and any(status.startswith(w) for w in _DONE):
            return info['url']
        if any(status.startswith(w) for w in _FAILED):
            raise MinimaxError('MiniMax 出片失败：%s' % (info['reason'] or status or '未知原因'))
        if on_poll:
            on_poll(status)
        time.sleep(POLL_SECONDS)
    raise MinimaxError('等待 MiniMax 出片超时（%d 分钟）' % (timeout // 60))


def download(url, dest):
    """把成片下到 dest（先写 .part 再改名，避免半截文件被当成成片）。"""
    import requests

    os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)
    part = dest + '.part'
    try:
        with requests.get(url, stream=True, timeout=180) as stream:
            stream.raise_for_status()
            with open(part, 'wb') as handle:
                for chunk in stream.iter_content(1 << 16):
                    handle.write(chunk)
    except Exception as exc:  # noqa: BLE001
        if os.path.exists(part):
            os.remove(part)
        raise MinimaxError('下载成片失败：%s' % exc)
    os.replace(part, dest)
    size = os.path.getsize(dest)
    if size <= 0:
        os.remove(dest)
        raise MinimaxError('下载到的成片是空文件')
    logger.info('[动作制作] 成片已下载 %s（%d 字节）', dest, size)
    return dest


def _pick_task(payload, task_id):
    """查询接口返回 {"items": [...]}；按 id 找，找不到就退回第一条。"""
    items = payload.get('items')
    if isinstance(items, list) and items:
        for item in items:
            if str(item.get('id')) == str(task_id):
                return item
        return items[0]
    if isinstance(payload, dict) and ('status' in payload or 'content' in payload):
        return payload
    return {}


def _short(text, limit=200):
    text = (text or '').strip().replace('\n', ' ')
    return text[:limit] + ('…' if len(text) > limit else '')
