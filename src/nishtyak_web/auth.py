from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from .config import WebSettings


@dataclass(frozen=True)
class WorkspaceIdentity:
    user: str
    workspace: str
    mode: str

    @property
    def scope(self) -> str:
        digest = hashlib.sha256((self.mode + ":" + self.workspace).encode()).hexdigest()
        return "web:" + digest


class GatewayBoundary:
    """Authenticate before receiving a bounded body; never trust browser owner headers."""

    def __init__(self, app, settings: WebSettings):
        self.app, self.settings = app, settings
        self._active = 0

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not (
            scope["path"] == "/api" or scope["path"].startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return
        headers: dict[bytes, bytes] = {}
        for key, value in scope.get("headers", []):
            key = key.lower()
            if key in headers and key in {
                b"host",
                b"origin",
                b"content-length",
                b"x-nishtyak-proxy-secret",
                b"x-nishtyak-user",
                b"x-nishtyak-workspace",
            }:
                await self._error(scope, receive, send, 400, "INVALID_HEADERS")
                return
            headers[key] = value
        if self.settings.mode == "local":
            try:
                host = urlsplit("http://" + headers.get(b"host", b"").decode("latin1")).hostname
                safe_host = host == "localhost" or bool(
                    host and ipaddress.ip_address(host).is_loopback
                )
            except ValueError:
                safe_host = False
            if not safe_host:
                await self._error(scope, receive, send, 403, "HOST_FORBIDDEN")
                return
        origin = headers.get(b"origin", b"").decode("latin1")
        if origin and origin not in self.settings.origins:
            await self._error(scope, receive, send, 403, "ORIGIN_FORBIDDEN")
            return
        if headers.get(b"sec-fetch-site") == b"cross-site":
            await self._error(scope, receive, send, 403, "ORIGIN_FORBIDDEN")
            return
        if scope["path"] == "/api/healthz":
            identity = None
        else:
            identity = self._identity(scope, headers)
            if identity is None:
                await self._error(scope, receive, send, 401, "AUTHENTICATION_REQUIRED")
                return
        scope.setdefault("state", {})["identity"] = identity
        if self._active >= 8:
            await self._error(scope, receive, send, 429, "GATEWAY_BUSY")
            return
        self._active += 1
        try:
            maximum = (
                11 * 1024**2
                if b"multipart/form-data" in headers.get(b"content-type", b"")
                else 262144
            )
            body = bytearray()
            try:
                async with asyncio.timeout(15):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        body.extend(message.get("body", b""))
                        if len(body) > maximum:
                            await self._error(scope, receive, send, 413, "REQUEST_TOO_LARGE")
                            return
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                await self._error(scope, receive, send, 408, "REQUEST_TIMEOUT")
                return
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            async def safe_send(message):
                if message["type"] == "http.response.start":
                    message["headers"] = [
                        *message.get("headers", []),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                    ]
                await send(message)

            await self.app(scope, bounded_receive, safe_send)
        finally:
            self._active -= 1

    def _identity(self, scope, headers) -> WorkspaceIdentity | None:
        if self.settings.mode == "local":
            try:
                address = ipaddress.ip_address(scope.get("client", ("", 0))[0])
            except ValueError:
                return None
            return WorkspaceIdentity("local", "local", "local") if address.is_loopback else None
        secret = headers.get(b"x-nishtyak-proxy-secret", b"")
        if not hmac.compare_digest(secret, self.settings.proxy_secret.encode("ascii")):
            return None
        user = headers.get(b"x-nishtyak-user", b"").decode("latin1")
        workspace = headers.get(b"x-nishtyak-workspace", b"").decode("latin1")
        if not all(re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", part) for part in (user, workspace)):
            return None
        return WorkspaceIdentity(user, workspace, "proxy")

    @staticmethod
    async def _error(scope, receive, send, status, code) -> None:
        await JSONResponse(
            {"code": code, "message": "Browser gateway rejected the request"},
            status,
            {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )(scope, receive, send)
