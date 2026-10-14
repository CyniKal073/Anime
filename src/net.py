"""连接层：IPv6 优先的 HTTPS 连接。

背景（实测）：某些网络环境下 ``nekobt.to`` 的 A 记录会被 DNS 污染成
45.67.223.32，而该地址返回的证书是签给它自己的，于是报
``CertificateError: hostname 'nekobt.to' doesn't match '45.67.223.32'``；
同一时刻 AAAA 记录解析到 Cloudflare 是正常的。

Python 的 socket 会按 ``getaddrinfo`` 返回顺序取第一个地址，正好是坏的那个，
所以稳定失败；curl 优先 IPv6 因此不受影响。

这里的做法是：**主机名仍然用于 SNI 和证书校验（不降低安全性）**，只把 TCP
连接的目标地址改成 IPv6 优先，连不上再回落到 IPv4。
"""

from __future__ import annotations

import os
import socket
import threading
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.poolmanager import PoolManager

#: 本机地址不经过代理
NO_PROXY_HOSTS = "localhost,127.0.0.1,::1"

#: Windows 系统代理的注册表位置
_WININET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def detect_system_proxy() -> Optional[str]:
    """读取 Windows 系统代理（IE/WinINET 设置）。

    为什么需要它：很多代理客户端（v2rayN、Clash 等）工作在**系统代理**模式下，
    浏览器会自动走它，但 requests / curl **不读注册表**，只看环境变量。
    结果就是「浏览器能打开、程序连不上」——实测 rutracker、蜜柑、api.bgm.tv
    直连全部不可达，走系统代理后全部 200。
    """
    # 显式覆盖优先：.env 里的 ANIME_PROXY 可以指定别的代理；
    # 设成空字符串就表示强制直连。
    override = os.environ.get("ANIME_PROXY")
    if override is not None:
        return override.strip() or None

    try:
        import winreg  # type: ignore
    except ImportError:  # pragma: no cover - 非 Windows
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WININET_KEY) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return None
    if not enabled or not server:
        return None

    server = str(server).strip()
    # 形如 "http=host:port;https=host:port" 时优先取 https
    if "=" in server:
        parts = dict(item.split("=", 1) for item in server.split(";") if "=" in item)
        server = parts.get("https") or parts.get("http") or ""
    if not server:
        return None
    if not server.startswith(("http://", "https://", "socks5://", "socks4://")):
        server = "http://" + server
    return server


def apply_proxy(session: Any, proxy: Optional[str]) -> None:
    """给会话装上代理，并显式忽略环境变量。

    忽略环境变量是必要的：沙箱会给进程注入一个黑洞代理（127.0.0.1:9），
    不关掉的话请求会被导去那里。
    """
    session.trust_env = False
    session.proxies = {"http": proxy, "https": proxy} if proxy else {}

#: 记住每个主机实际可用的地址族，避免每次请求都先等一遍不可用的那个。
#: 实测有些网络下 IPv6 是通的（nekobt.to），有些反过来，所以不能写死。
_PREFERRED_FAMILY: dict = {}
_FAMILY_LOCK = threading.Lock()

#: 单个地址的连接超时上限（秒）。不做限制的话，一个不通的地址会把整体拖到
#: 请求超时（实测出现过单次桥接 12 秒的情况）。
CONNECT_TIMEOUT_CAP = 5.0


def _order(infos, host: str):
    with _FAMILY_LOCK:
        preferred = _PREFERRED_FAMILY.get(host, socket.AF_INET6)
    return sorted(infos, key=lambda info: 0 if info[0] == preferred else 1)


def _remember(host: str, family: int) -> None:
    with _FAMILY_LOCK:
        _PREFERRED_FAMILY[host] = family


class IPv6FirstHTTPSConnection(HTTPSConnection):
    """只改地址选择顺序，证书校验与 SNI 仍然使用原主机名。"""

    def _new_conn(self):  # type: ignore[override]
        try:
            infos = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)
        except OSError as exc:
            raise OSError(f"无法解析主机 {self.host}：{exc}") from exc

        # 默认 IPv6 优先；连通过一次之后按该主机实际可用的地址族排序
        infos = _order(infos, self.host)

        try:
            connect_timeout = min(float(self.timeout), CONNECT_TIMEOUT_CAP)
        except (TypeError, ValueError):
            connect_timeout = CONNECT_TIMEOUT_CAP

        last_error: Optional[Exception] = None
        for family, socktype, proto, _canonname, sockaddr in infos:
            sock = None
            try:
                sock = socket.socket(family, socktype, proto)
                for option in self.socket_options or []:
                    sock.setsockopt(*option)
                sock.settimeout(connect_timeout)
                sock.connect(sockaddr)
                _remember(self.host, family)
                sock.settimeout(self.timeout)  # 连上后恢复请求本身的超时
                return sock
            except OSError as exc:
                last_error = exc
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
        raise last_error or OSError(f"无法连接到 {self.host}:{self.port}")


class IPv6FirstHTTPSConnectionPool(HTTPSConnectionPool):
    """HTTPS 连接池，底层换成 IPv6 优先的连接实现。"""

    ConnectionCls = IPv6FirstHTTPSConnection


class IPv6FirstAdapter(HTTPAdapter):
    """让 requests 的 HTTPS 连接走 IPv6 优先。

    注意：urllib3 1.26 的 ``PoolManager`` 不接受 ``connection_class`` 参数
    （会一路传到 PoolKey 里报 TypeError），所以这里改成替换该实例的
    https 连接池实现，不触碰全局。
    """

    def init_poolmanager(  # type: ignore[override]
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )
        self.poolmanager.pool_classes_by_scheme = {
            **self.poolmanager.pool_classes_by_scheme,
            "https": IPv6FirstHTTPSConnectionPool,
        }


def build_session(proxy: Optional[str] = None) -> requests.Session:
    """构造默认会话：自动走系统代理（若开着），HTTPS 地址族自适应。

    ``proxy`` 传空字符串表示「强制不使用代理」，传 None 表示自动探测。
    """
    session = requests.Session()
    effective = detect_system_proxy() if proxy is None else proxy
    if not effective:
        # 只有直连时才需要自己挑地址族：DNS 被污染时 IPv4 会解析到错误的 IP。
        # 走代理时这件事由代理负责，而且自定义连接会破坏代理隧道
        # （实测 api.assrt.net 走代理时报 SSLEOFError，去掉适配器就正常）。
        session.mount("https://", IPv6FirstAdapter())
    apply_proxy(session, effective)
    return session
