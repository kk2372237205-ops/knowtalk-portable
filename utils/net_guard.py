# -*- coding: utf-8 -*-
"""对外信任边界：回调地址的主机允许列表与跨域来源允许列表。

审计（`docs/audit/2026-10-12-project-audit.md` 第 3.5 节）登记了两处"服务按请求方
的意愿对外发起动作"的问题：

- `notifyurl` 由请求方任意指定，服务会向其 POST —— SSRF；
- CORS 通配来源且允许凭据 —— 任意站点可带凭据读取管理数据。

本模块把「信任谁」集中成两条允许列表，并把解析与判定做成纯函数，便于单元测试。

已知边界（如实记录，不假装不存在）：回调的主机匹配**按主机名做字符串比对，不解析
DNS、不校验解析后的 IP**，因此挡不住 DNS rebinding。它挡的是"任意指定内网地址"
这一主要利用方式。允许列表只应填入可信的回调主机。
"""

from urllib.parse import urlsplit

ALLOWED_NOTIFY_SCHEMES = ('http', 'https')

DEFAULT_PORTS = {'http': 80, 'https': 443}

WILDCARD = '*'


class NetGuardError(ValueError):
    """取出的外部地址/来源不被信任。"""


def _split_entries(value):
    """把逗号分隔的配置值切成去空白、去空条目、保持顺序的列表。"""
    if value is None:
        return []
    text = value if isinstance(value, str) else str(value)
    return [item.strip() for item in text.split(',') if item.strip()]


def parse_allowed_origins(value):
    """解析 ``CORS_ALLOWED_ORIGINS``：逗号分隔的完整来源。

    归一化规则：去掉首尾空白与结尾的 ``/``；协议与主机名小写化（``aiohttp_cors``
    对 ``Origin`` 头做**精确字符串比对**，而浏览器发出的 Origin 里协议与主机本就是
    小写，因此小写化只会提高命中率，不会放宽信任）；去重且保持原顺序。

    ``*`` 明确拒绝：通配来源配合 ``allow_credentials`` 正是本次要消除的问题，
    不接受它作为"允许列表"的取值。调用方应当捕获本异常并退化为"不信任任何来源"。
    """
    origins = []
    for item in _split_entries(value):
        if item == WILDCARD:
            raise NetGuardError(
                'CORS_ALLOWED_ORIGINS 不接受通配来源 *，请逐个列出可信来源'
            )
        item = item.rstrip('/')
        if not item:
            continue
        try:
            parts = urlsplit(item)
            port = parts.port
        except ValueError:
            raise NetGuardError('来源格式非法：{0}'.format(item))
        if parts.scheme and parts.netloc and parts.hostname:
            netloc = parts.hostname.lower()
            if port is not None:
                netloc = '{0}:{1}'.format(netloc, port)
            item = '{0}://{1}'.format(parts.scheme.lower(), netloc)
        if item not in origins:
            origins.append(item)
    return tuple(origins)


def parse_allowed_hosts(value):
    """解析 ``AVATAR_NOTIFY_ALLOWED_HOSTS``：逗号分隔的 ``host`` 或 ``host:port``。

    统一小写化以便大小写不敏感比对。``*`` 同样拒绝——允许列表的意义就是逐项列出。
    """
    hosts = []
    for item in _split_entries(value):
        if item == WILDCARD:
            raise NetGuardError(
                'AVATAR_NOTIFY_ALLOWED_HOSTS 不接受通配主机 *，请逐个列出可信主机'
            )
        host, _, port_text = item.rpartition(':')
        if host and port_text.isdigit():
            normalized = '{0}:{1}'.format(host.lower(), int(port_text))
        else:
            normalized = item.lower()
        if normalized not in hosts:
            hosts.append(normalized)
    return tuple(hosts)


def host_allowed(host, port, allowed_hosts):
    """``host``/``port`` 是否落在允许列表内。

    条目为 ``host`` 时匹配该主机的任意端口；条目为 ``host:port`` 时要求端口一致。
    """
    if not host or not allowed_hosts:
        return False
    host = str(host).lower()
    for entry in allowed_hosts:
        entry_host, _, entry_port = entry.rpartition(':')
        if entry_host and entry_port.isdigit():
            if host == entry_host and int(port) == int(entry_port):
                return True
        elif host == entry:
            return True
    return False


def validate_notify_url(url, allowed_hosts):
    """校验回调地址是否可信；不可信时抛 ``NetGuardError``。

    ``url`` 为空视为"没有回调需求"，直接放行（返回 ``None``）。协议只允许
    ``http``/``https``，主机必须落在允许列表内。允许列表为空时一律拒绝。
    """
    if url is None:
        return None
    text = str(url).strip()
    if not text:
        return None
    if not allowed_hosts:
        raise NetGuardError(
            '未配置 AVATAR_NOTIFY_ALLOWED_HOSTS，回调地址一律被拒绝'
        )
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise NetGuardError('回调地址格式非法')
    scheme = (parts.scheme or '').lower()
    if scheme not in ALLOWED_NOTIFY_SCHEMES:
        raise NetGuardError(
            '回调地址协议必须是 http 或 https，当前为 {0!r}'.format(parts.scheme)
        )
    host = parts.hostname
    if not host:
        raise NetGuardError('回调地址缺少主机名')
    if port is None:
        port = DEFAULT_PORTS[scheme]
    if not host_allowed(host, port, allowed_hosts):
        raise NetGuardError(
            '回调主机 {0!r} 不在 AVATAR_NOTIFY_ALLOWED_HOSTS 允许列表内'.format(host)
        )
    return text
