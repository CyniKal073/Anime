"""连接层：IPv6 优先的地址选择。"""

from __future__ import annotations

import socket

import pytest

from src.net import IPv6FirstAdapter, IPv6FirstHTTPSConnection, build_session


def test_build_session_mounts_ipv6_first_adapter_when_direct():
    """直连时才挂自定义适配器（DNS 可能被污染，需要自己挑地址族）。"""
    session = build_session(proxy="")
    assert isinstance(session.get_adapter("https://nekobt.to"), IPv6FirstAdapter)
    # HTTP 不走这个适配器
    assert not isinstance(session.get_adapter("http://example.com"), IPv6FirstAdapter)


def test_build_session_skips_custom_adapter_when_using_proxy():
    """走代理时不挂自定义适配器——它会破坏代理隧道（实测 SSLEOFError）。

    而且走代理时 DNS 由代理解析，地址族选择本来就不归我们管。
    """
    session = build_session(proxy="http://127.0.0.1:10809")
    assert not isinstance(session.get_adapter("https://api.assrt.net"), IPv6FirstAdapter)
    assert session.proxies["https"] == "http://127.0.0.1:10809"
    assert session.proxies["http"] == "http://127.0.0.1:10809"


def test_connection_tries_ipv6_before_ipv4(monkeypatch):
    """DNS 把被污染的 IPv4 排在前面时，客户端应当先连 IPv6。"""
    attempted = []

    class FakeSock:
        def __init__(self, family, *_args):
            self.family = family

        def setsockopt(self, *_args):
            pass

        def settimeout(self, _timeout):
            pass

        def connect(self, address):
            attempted.append((self.family, address[0]))

        def close(self):
            pass

    fake_infos = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("45.67.223.32", 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:3030::6815:258c", 443, 0, 0)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: fake_infos)
    monkeypatch.setattr(socket, "socket", lambda family, *_args: FakeSock(family))

    conn = IPv6FirstHTTPSConnection("nekobt.to", 443)
    conn._new_conn()

    assert attempted, "应当尝试连接"
    assert attempted[0][0] == socket.AF_INET6
    assert attempted[0][1] == "2606:4700:3030::6815:258c"


def test_connection_falls_back_to_ipv4_when_ipv6_fails(monkeypatch):
    attempted = []

    class FlakySock:
        def __init__(self, family, *_args):
            self.family = family

        def setsockopt(self, *_args):
            pass

        def settimeout(self, _timeout):
            pass

        def connect(self, address):
            attempted.append((self.family, address[0]))
            if self.family == socket.AF_INET6:
                raise OSError("network unreachable")

        def close(self):
            pass

    fake_infos = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("45.67.223.32", 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:3030::6815:258c", 443, 0, 0)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: fake_infos)
    monkeypatch.setattr(socket, "socket", lambda family, *_args: FlakySock(family))

    conn = IPv6FirstHTTPSConnection("nekobt.to", 443)
    conn._new_conn()

    assert [item[0] for item in attempted] == [socket.AF_INET6, socket.AF_INET]


def test_ssl_error_message_is_actionable():
    from src.nekobt.client import NekoBTError, _describe_error

    exc = Exception(
        "HTTPSConnectionPool(host='nekobt.to', port=443): "
        "SSLError(CertificateError(\"hostname 'nekobt.to' doesn't match '45.67.223.32'\"))"
    )
    message = _describe_error(exc)
    assert "DNS" in message
    assert "remote_ip" in message

    assert _describe_error(ValueError("boom")) == "boom"
    assert NekoBTError is not None
