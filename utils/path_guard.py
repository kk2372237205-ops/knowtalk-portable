# -*- coding: utf-8 -*-
"""路径包含性校验与文件名净化。

审计（`docs/audit/2026-10-12-project-audit.md` 第 3.5 节）登记的注入面都源于
同一个模式：请求参数里的字符串被直接拼进文件系统路径。本模块提供三处共用的
判定原语，避免每个调用点各自实现导致判定口径漂移。

判定口径（对 Windows 与 Linux 同时成立）：

1. 两侧都先用 ``realpath`` 归一化，符号链接在比较前就被解开；
2. 再用 ``normcase`` 统一大小写与分隔符（Windows 小写化，Linux 原样）；
3. 用 ``commonpath`` 判包含，而不是字符串前缀——``data/voices2`` 不会被误判为
   在 ``data/voices`` 之内；
4. ``commonpath`` 跨盘符时抛 ``ValueError``，一律视为"不在之内"。

本模块只做判定，不做任何文件系统写入。
"""

import os
import re

# 文件名中一律不允许出现的字符：路径分隔符、Windows 保留字符、控制字符。
_FORBIDDEN_FILENAME_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


class PathGuardError(ValueError):
    """请求给出的路径越出允许范围，或文件名不合法。"""


def _norm(path):
    """归一化为可比较形式：绝对化 -> 解开符号链接 -> 统一大小写与分隔符。"""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def is_within(root, candidate):
    """``candidate`` 解析后是否落在 ``root`` 之内（含 ``root`` 自身）。

    任何解析失败都返回 ``False``——判定函数不抛异常，便于调用点直接写守卫。
    """
    if not root or candidate is None:
        return False
    text = str(candidate)
    if not text.strip():
        return False
    try:
        root_norm = _norm(root)
        candidate_norm = _norm(text)
    except (TypeError, ValueError, OSError):
        return False
    try:
        common = os.path.commonpath([root_norm, candidate_norm])
    except ValueError:
        # 跨盘符（Windows）、混合绝对/相对路径 —— 一律不在之内。
        return False
    # 必须等于 root 本身：startswith 式的前缀比较会把 data/voices2 放进来。
    return common == root_norm


def resolve_within(root, candidate):
    """返回 ``candidate`` 的绝对真实路径；不在 ``root`` 之内时抛 ``PathGuardError``。

    对"目标尚不存在"的创建场景同样适用：``realpath`` 会解开父目录上的符号链接，
    再把不存在的末段原样保留，因此不会因为文件还没落盘而漏判。
    """
    if not root:
        raise PathGuardError('未配置允许目录')
    if candidate is None or not str(candidate).strip():
        raise PathGuardError('路径为空')
    if not is_within(root, candidate):
        raise PathGuardError('路径越出允许目录')
    return os.path.realpath(os.path.abspath(str(candidate)))


def resolve_within_any(roots, candidate):
    """候选路径落在 ``roots`` 中**任一**根之内即返回其绝对真实路径。

    用于"允许根可以由部署方配置成多个"的场景（如 ``AVATAR_VIDEO_ROOTS``）。
    ``roots`` 为空或全部不匹配时抛 ``PathGuardError``。
    """
    if candidate is None or not str(candidate).strip():
        raise PathGuardError('路径为空')
    for root in roots or ():
        if is_within(root, candidate):
            return os.path.realpath(os.path.abspath(str(candidate)))
    raise PathGuardError('路径越出允许目录')


def sanitize_filename(name, fallback='upload'):
    """把任意输入收敛为一个安全的单段文件名。

    去掉目录成分（同时处理 ``/`` 与 ``\\``）、去掉不允许的字符、拒绝 ``.`` 与
    ``..``。无法收敛出可用名字时返回 ``fallback``。本函数**不抛异常**，用于
    "尽力保留用户原名"的场景（如上传落盘）。
    """
    if name is None:
        return fallback
    text = str(name).strip()
    if not text:
        return fallback
    # 请求可能来自任意平台，两种分隔符都要当分隔符处理；结尾的分隔符先去掉，
    # 否则 "dir/" 会取到空段而丢掉用户本意中的 "dir"。
    text = text.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
    text = text.strip()
    if not text or text in ('.', '..'):
        return fallback
    text = _FORBIDDEN_FILENAME_RE.sub('', text).strip()
    # Windows 会静默丢弃结尾的点和空格，导致实际落盘名与返回值不一致。
    text = text.rstrip('. ')
    if not text or text in ('.', '..'):
        return fallback
    return text


def validate_filename(name):
    """校验请求给出的名字是否可作为单段文件名直接落盘。

    与 ``sanitize_filename`` 的区别：本函数**拒绝**而不是改写——用于"用户给的
    名字会直接决定落盘路径、改写会让用户拿到一个他没要求的名字"的场景。
    不合法时抛 ``PathGuardError``。
    """
    if name is None:
        raise PathGuardError('名称为空')
    text = str(name).strip()
    if not text:
        raise PathGuardError('名称为空')
    if text in ('.', '..'):
        raise PathGuardError('名称不能是 . 或 ..')
    if _FORBIDDEN_FILENAME_RE.search(text):
        raise PathGuardError('名称不能包含路径分隔符、控制字符或保留字符')
    return text
