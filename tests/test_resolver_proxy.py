from __future__ import annotations

import socket
import threading
import time
from urllib.parse import urlsplit

import pytest

from nishtyak_core.resolver_proxy import RestrictedIndexProxy, _ProxyError


def connect(proxy: RestrictedIndexProxy) -> socket.socket:
    address = urlsplit(proxy.url)
    stream = socket.create_connection((address.hostname, address.port), timeout=1)
    stream.settimeout(1)
    return stream


def request(proxy: RestrictedIndexProxy, text: bytes) -> bytes:
    with connect(proxy) as stream:
        stream.sendall(text)
        return stream.recv(1024)


@pytest.mark.parametrize(
    "authority",
    [
        "localhost:443",
        "127.0.0.1:443",
        "169.254.169.254:443",
        "[::1]:443",
        "pypi.org:80",
        "pypi.org:444",
        "pypi.org",
        "pypi.org.evil.test:443",
        "pypi.org@127.0.0.1:443",
        "user@pypi.org:443",
        "pypi.org.:443",
        "https://pypi.org:443",
        "PYPI.ORG:443",
        "pypi.org:0443",
    ],
)
def test_unapproved_authorities_cannot_trigger_dns(
    monkeypatch: pytest.MonkeyPatch, authority: str
) -> None:
    def forbidden(_host: str):
        pytest.fail("Rejected authority reached DNS")

    with RestrictedIndexProxy() as proxy:
        monkeypatch.setattr(proxy, "_resolve_public", forbidden)
        response = request(
            proxy, f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode()
        )
        assert response.startswith(b"HTTP/1.1 403")


@pytest.mark.parametrize(
    "payload,status",
    [
        (b"GET http://pypi.org/simple/numpy HTTP/1.1\r\n\r\n", 403),
        (b"POST http://127.0.0.1/ HTTP/1.1\r\n\r\n", 403),
        (b"CONNECT pypi.org:443 HTTP/1.1\r\nHost: evil.test:443\r\n\r\n", 403),
        (b"CONNECT pypi.org:443 HTTP/1.1\r\nHost: pypi.org:443\r\nHost: pypi.org:443\r\n\r\n", 403),
        (b"CONNECT pypi.org:443 HTTP/1.1\r\nContent-Length: 0\r\n\r\n", 400),
        (b"CONNECT pypi.org:443 HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n", 400),
        (b"CONNECT pypi.org:443 HTTP/1.1\r\n Bad: header\r\n\r\n", 400),
        (b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\nsmuggled", 400),
        (b"CONNECT pypi.org:443 HTTP/1.1\r\nX: " + b"a" * 8192 + b"\r\n\r\n", 431),
    ],
)
def test_plain_http_and_ambiguous_headers_rejected(payload: bytes, status: int) -> None:
    with RestrictedIndexProxy() as proxy:
        assert request(proxy, payload).startswith(f"HTTP/1.1 {status}".encode())


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "172.16.0.1",
        "192.168.1.1",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "2606:4700:4700::1111%eth0",
    ],
)
def test_dns_nonpublic_answers_rejected(monkeypatch: pytest.MonkeyPatch, ip: str) -> None:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(family, socket.SOCK_STREAM, 6, "", (ip, 443))],
    )
    with pytest.raises(_ProxyError):
        RestrictedIndexProxy._resolve_public("pypi.org")


def test_dns_mixed_public_private_answers_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ("8.8.8.8", "127.0.0.1")
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: answers)
    with pytest.raises(_ProxyError):
        RestrictedIndexProxy._resolve_public("pypi.org")


def test_public_dns_is_resolved_once_and_numeric_address_dialed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries = []
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    def resolve(*args, **kwargs):
        queries.append((args, kwargs))
        return answers

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    validated = RestrictedIndexProxy._resolve_public("pypi.org")
    assert validated == answers and len(queries) == 1
    assert queries[0][0] == ("pypi.org", 443)


def test_dial_uses_numeric_tuple_without_a_second_dns_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = []

    class NumericSocket:
        def settimeout(self, value):
            assert 0 < value <= 5

        def connect(self, address):
            connections.append(address)

    numeric = NumericSocket()
    monkeypatch.setattr(socket, "socket", lambda *args: numeric)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: pytest.fail("Second DNS lookup"))
    proxy = RestrictedIndexProxy()
    target = ("8.8.8.8", 443)
    result = proxy._dial(
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", target)], time.monotonic() + 1
    )
    assert result is numeric and connections == [target]
    proxy._untrack(numeric)


def install_tunnel(proxy: RestrictedIndexProxy, monkeypatch: pytest.MonkeyPatch) -> socket.socket:
    tunnel, upstream = socket.socketpair()
    upstream.settimeout(1)
    monkeypatch.setattr(proxy, "_resolve_public", lambda host: [host])
    monkeypatch.setattr(proxy, "_dial", lambda addresses, deadline: tunnel)
    return upstream


def test_connect_tunnel_preserves_tls_bytes_and_closes_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = RestrictedIndexProxy()
    with proxy:
        upstream = install_tunnel(proxy, monkeypatch)
        with upstream, connect(proxy) as client:
            client.sendall(
                b"CONNECT files.pythonhosted.org:443 HTTP/1.1\r\n"
                b"Host: files.pythonhosted.org:443\r\n\r\n"
            )
            assert client.recv(1024).startswith(b"HTTP/1.1 200")
            client.sendall(b"\x16\x03\x03opaque-client-tls")
            assert upstream.recv(1024) == b"\x16\x03\x03opaque-client-tls"
            upstream.sendall(b"\x17\x03\x03opaque-server-tls")
            assert client.recv(1024) == b"\x17\x03\x03opaque-server-tls"
        assert proxy.transferred_bytes == len(
            b"\x16\x03\x03opaque-client-tls\x17\x03\x03opaque-server-tls"
        )
    assert proxy._acceptor is not None and not proxy._acceptor.is_alive()
    assert not proxy._threads and not proxy._sockets
    with pytest.raises(RuntimeError):
        _ = proxy.url


def test_byte_cap_is_aggregate_and_closes_tunnel(monkeypatch: pytest.MonkeyPatch) -> None:
    with RestrictedIndexProxy(max_bytes=8) as proxy:
        upstream = install_tunnel(proxy, monkeypatch)
        with upstream, connect(proxy) as client:
            client.sendall(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
            assert client.recv(1024).startswith(b"HTTP/1.1 200")
            client.sendall(b"123456")
            assert upstream.recv(1024) == b"123456"
            upstream.sendall(b"789")
            assert client.recv(1024) == b""
        assert proxy.transferred_bytes == 6 and proxy._exhausted.is_set()
        assert request(proxy, b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 503")


def test_channel_cap_rejects_slow_header_clients() -> None:
    with RestrictedIndexProxy(max_channels=1) as proxy:
        with connect(proxy) as first:
            first.sendall(b"CONNECT")
            deadline = time.monotonic() + 1
            while not proxy._sockets and time.monotonic() < deadline:
                threading.Event().wait(0.01)
            assert proxy._sockets
            assert request(proxy, b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n").startswith(
                b"HTTP/1.1 503"
            )


def test_deadline_covers_slow_headers_and_tunnel(monkeypatch: pytest.MonkeyPatch) -> None:
    with RestrictedIndexProxy(connection_deadline=0.1) as proxy:
        with connect(proxy) as client:
            client.sendall(b"CONNECT")
            assert client.recv(1024).startswith(b"HTTP/1.1 504")
    with RestrictedIndexProxy(connection_deadline=0.1) as proxy:
        upstream = install_tunnel(proxy, monkeypatch)
        with upstream, connect(proxy) as client:
            client.sendall(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
            assert client.recv(1024).startswith(b"HTTP/1.1 200")
            assert client.recv(1024) == b""


def test_context_exit_interrupts_active_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = RestrictedIndexProxy()
    with proxy:
        upstream = install_tunnel(proxy, monkeypatch)
        client = connect(proxy)
        client.sendall(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
        assert client.recv(1024).startswith(b"HTTP/1.1 200")
    try:
        assert client.recv(1024) == b""
        assert not proxy._threads and not proxy._sockets
    finally:
        client.close()
        upstream.close()


@pytest.mark.parametrize(
    "options",
    [
        {"max_channels": 0},
        {"max_channels": 5},
        {"max_channels": True},
        {"max_bytes": 33 * 1024 * 1024},
        {"max_bytes": 0},
        {"connection_deadline": 46},
        {"connection_deadline": float("nan")},
    ],
)
def test_limits_cannot_be_relaxed(options: dict) -> None:
    with pytest.raises(ValueError):
        RestrictedIndexProxy(**options)
