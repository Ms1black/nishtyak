from __future__ import annotations

import ipaddress
import select
import socket
import socketserver
import threading
import time
from typing import Any


class _ProxyError(Exception):
    def __init__(self, status: int, reason: str):
        self.status = status
        self.reason = reason


class RestrictedIndexProxy:
    """A finite CONNECT tunnel to the two public PyPI download authorities only.

    TLS is end-to-end: the resolver still verifies certificates and host names.
    The proxy resolves the allowlisted authority once, checks every returned IP,
    and connects to a numeric address, avoiding a second DNS lookup when dialing.
    """

    authorities = frozenset({"pypi.org:443", "files.pythonhosted.org:443"})
    max_header_bytes = 8192

    def __init__(
        self,
        *,
        max_channels: int = 4,
        max_bytes: int = 32 * 1024 * 1024,
        connection_deadline: float = 45.0,
    ) -> None:
        if type(max_channels) is not int or not 1 <= max_channels <= 4:
            raise ValueError("Proxy channels must be between 1 and 4")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 32 * 1024 * 1024:
            raise ValueError("Proxy byte limit exceeds the supported bound")
        if (
            isinstance(connection_deadline, bool)
            or not isinstance(connection_deadline, (int, float))
            or not 0 < connection_deadline <= 45
        ):
            raise ValueError("Proxy connection deadline must be between 0 and 45 seconds")
        self.max_channels = max_channels
        self.max_bytes = max_bytes
        self.connection_deadline = connection_deadline
        self._server: _ProxyServer | None = None
        self._acceptor: threading.Thread | None = None
        self._slots = threading.BoundedSemaphore(max_channels)
        self._lock = threading.Lock()
        self._sockets: set[socket.socket] = set()
        self._threads: set[threading.Thread] = set()
        self._stopped = threading.Event()
        self._exhausted = threading.Event()
        self._transferred = 0
        self._used = False

    @property
    def url(self) -> str:
        if self._server is None or self._stopped.is_set():
            raise RuntimeError("Restricted proxy is not running")
        return "http://127.0.0.1:" + str(self._server.server_address[1])

    @property
    def transferred_bytes(self) -> int:
        with self._lock:
            return self._transferred

    def __enter__(self) -> RestrictedIndexProxy:
        if self._used:
            raise RuntimeError("Restricted proxy cannot be reused")
        self._used = True
        self._server = _ProxyServer(self)
        self._acceptor = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="nishtyak-index-proxy",
            daemon=True,
        )
        self._acceptor.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._stopped.set()
        with self._lock:
            sockets = tuple(self._sockets)
        for stream in sockets:
            self._close(stream)
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        deadline = time.monotonic() + 2.0
        if self._acceptor is not None:
            self._acceptor.join(timeout=max(0, deadline - time.monotonic()))
        with self._lock:
            threads = tuple(self._threads)
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))

    def _handle(self, client: socket.socket) -> None:
        remote = None
        connected = False
        deadline = time.monotonic() + self.connection_deadline
        self._track(client)
        try:
            if self._stopped.is_set() or self._exhausted.is_set():
                raise _ProxyError(503, "Unavailable")
            authority = self._read_authority(client, deadline)
            addresses = self._resolve_public(authority.removesuffix(":443"))
            if time.monotonic() >= deadline or self._stopped.is_set():
                raise _ProxyError(504, "Timeout")
            remote = self._dial(addresses, deadline)
            self._track(remote)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            connected = True
            self._tunnel(client, remote, deadline)
        except _ProxyError as error:
            if not connected:
                self._respond(client, error.status, error.reason)
        except (OSError, ValueError, UnicodeError):
            if not connected:
                self._respond(client, 502, "Unavailable")
        finally:
            for stream in (client, remote):
                if stream is not None:
                    self._untrack(stream)
                    self._close(stream)

    def _read_authority(self, client: socket.socket, deadline: float) -> str:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._stopped.is_set():
                raise _ProxyError(504, "Timeout")
            client.settimeout(min(1.0, remaining))
            try:
                chunk = client.recv(min(1024, self.max_header_bytes + 1 - len(data)))
            except TimeoutError:
                continue
            if not chunk:
                raise _ProxyError(400, "Invalid Request")
            data.extend(chunk)
            if len(data) > self.max_header_bytes:
                raise _ProxyError(431, "Header Too Large")
        head, extra = bytes(data).split(b"\r\n\r\n", 1)
        if extra:
            raise _ProxyError(400, "Invalid Request")
        lines = head.decode("ascii").split("\r\n")
        request = lines[0].split(" ")
        if (
            len(request) != 3
            or request[0] != "CONNECT"
            or request[2] not in {"HTTP/1.0", "HTTP/1.1"}
        ):
            raise _ProxyError(403, "Forbidden")
        authority = request[1]
        if authority not in self.authorities:
            raise _ProxyError(403, "Forbidden")
        host_seen = False
        for line in lines[1:]:
            key, separator, value = line.partition(":")
            if (
                not separator
                or not key
                or any(
                    char
                    not in (
                        "!#$%&'*+-.^_`|~0123456789"
                        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                    )
                    for char in key
                )
            ):
                raise _ProxyError(400, "Invalid Request")
            if any(ord(char) < 32 and char != "\t" for char in value):
                raise _ProxyError(400, "Invalid Request")
            if key.lower() in {"content-length", "transfer-encoding"}:
                raise _ProxyError(400, "Invalid Request")
            if key.lower() == "host":
                if host_seen or value.strip() != authority:
                    raise _ProxyError(403, "Forbidden")
                host_seen = True
        return authority

    @staticmethod
    def _resolve_public(host: str) -> list[tuple]:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not answers:
            raise _ProxyError(502, "Unavailable")
        validated = []
        for answer in answers:
            family, kind, _protocol, _canonical, address = answer
            if family not in {socket.AF_INET, socket.AF_INET6} or kind != socket.SOCK_STREAM:
                raise _ProxyError(403, "Forbidden")
            numeric = address[0]
            if "%" in numeric:
                raise _ProxyError(403, "Forbidden")
            ip = ipaddress.ip_address(numeric)
            if not ip.is_global or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
                raise _ProxyError(403, "Forbidden")
            if isinstance(ip, ipaddress.IPv6Address) and (
                ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None
            ):
                raise _ProxyError(403, "Forbidden")
            if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("192.0.0.0/24"):
                raise _ProxyError(403, "Forbidden")
            if answer not in validated:
                validated.append(answer)
        return validated

    def _dial(self, addresses: list[tuple], deadline: float) -> socket.socket:
        for family, kind, protocol, _canonical, numeric_address in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._stopped.is_set():
                break
            stream = socket.socket(family, kind, protocol)
            self._track(stream)
            try:
                stream.settimeout(min(5.0, remaining))
                stream.connect(numeric_address)
                return stream
            except OSError:
                self._untrack(stream)
                stream.close()
        raise _ProxyError(502, "Unavailable")

    def _tunnel(self, client: socket.socket, remote: socket.socket, deadline: float) -> None:
        streams = (client, remote)
        for stream in streams:
            stream.settimeout(0.5)
        while not self._stopped.is_set() and not self._exhausted.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            readable, _, failed = select.select(streams, [], streams, min(0.1, remaining))
            if failed:
                return
            for source in readable:
                data = source.recv(65_536)
                if not data:
                    return
                with self._lock:
                    if self._transferred + len(data) > self.max_bytes:
                        self._exhausted.set()
                        return
                    self._transferred += len(data)
                destination = remote if source is client else client
                destination.sendall(data)

    def _track(self, stream: socket.socket) -> None:
        with self._lock:
            if self._stopped.is_set():
                self._close(stream)
            else:
                self._sockets.add(stream)

    def _untrack(self, stream: socket.socket) -> None:
        with self._lock:
            self._sockets.discard(stream)

    @staticmethod
    def _respond(stream: socket.socket, status: int, reason: str) -> None:
        try:
            stream.settimeout(0.1)
            stream.sendall(
                (
                    f"HTTP/1.1 {status} {reason}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
                ).encode()
            )
        except OSError:
            pass

    @staticmethod
    def _close(stream: socket.socket) -> None:
        try:
            stream.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        stream.close()


class _ProxyServer(socketserver.TCPServer):
    allow_reuse_address = False
    request_queue_size = 4

    def __init__(self, proxy: RestrictedIndexProxy):
        self.proxy = proxy
        super().__init__(("127.0.0.1", 0), socketserver.BaseRequestHandler)

    def process_request(self, request: socket.socket, client_address: tuple) -> None:
        if self.proxy._stopped.is_set() or not self.proxy._slots.acquire(blocking=False):
            self.proxy._respond(request, 503, "Unavailable")
            self.shutdown_request(request)
            return

        def handle() -> None:
            try:
                self.proxy._handle(request)
            finally:
                self.proxy._slots.release()
                with self.proxy._lock:
                    self.proxy._threads.discard(threading.current_thread())

        thread = threading.Thread(target=handle, name="nishtyak-index-channel", daemon=True)
        with self.proxy._lock:
            self.proxy._threads.add(thread)
        try:
            thread.start()
        except RuntimeError:
            with self.proxy._lock:
                self.proxy._threads.discard(thread)
            self.proxy._slots.release()
            self.shutdown_request(request)
