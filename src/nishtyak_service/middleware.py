from __future__ import annotations

import asyncio
import hmac
import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBoundaryMiddleware:
    """Authenticate and bound incoming bodies before routing or multipart parsing."""

    _owner = re.compile(rb"[A-Za-z0-9_.:-]{1,128}\Z")

    def __init__(
        self,
        app: ASGIApp,
        *,
        api_token: str,
        max_archive_bytes: int,
        max_options_bytes: int = 65536,
        json_request_bytes: int = 262144,
        max_http_requests: int = 8,
        body_timeout_seconds: float = 30.0,
    ) -> None:
        self.app = app
        self._token = api_token.encode("ascii")
        self._upload_limit = max_archive_bytes + max_options_bytes + 262144 + 65536
        self._json_limit = json_request_bytes
        self._capacity = max_http_requests
        self._timeout = body_timeout_seconds
        self._active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["path"] == "/healthz" and scope["method"] == "GET":
            await self.app(scope, receive, send)
            return
        headers: dict[bytes, list[bytes]] = {}
        for key, value in scope.get("headers", []):
            headers.setdefault(key.lower(), []).append(value)
        authorization = headers.get(b"authorization", [])
        if len(authorization) != 1 or not self._authorized(authorization[0]):
            await self._error(
                scope, receive, send, 401, "UNAUTHORIZED", "Invalid service credentials"
            )
            return
        if scope["path"].startswith("/v1/"):
            owners = headers.get(b"x-owner-id", [])
            if len(owners) != 1 or not self._owner.fullmatch(owners[0]):
                await self._error(
                    scope, receive, send, 400, "OWNER_REQUIRED", "Invalid owner header"
                )
                return
            scope.setdefault("state", {})["owner_id"] = owners[0].decode("ascii")
        if self._active >= self._capacity:
            await self._error(scope, receive, send, 429, "HTTP_BUSY", "Too many active requests")
            return
        self._active += 1
        try:
            limit = (
                self._upload_limit
                if scope["method"] == "POST"
                and (
                    scope["path"] == "/v1/projects"
                    or re.fullmatch(r"/v1/projects/[a-f0-9]{32}/source", scope["path"])
                )
                else self._json_limit
            )
            lengths = headers.get(b"content-length", [])
            if lengths and (len(lengths) != 1 or not re.fullmatch(rb"[0-9]{1,20}", lengths[0])):
                await self._error(
                    scope, receive, send, 400, "INVALID_LENGTH", "Invalid content length"
                )
                return
            if lengths and int(lengths[0]) > limit:
                await self._error(
                    scope, receive, send, 413, "BODY_TOO_LARGE", "Request body too large"
                )
                return
            try:
                body = await asyncio.wait_for(
                    self._read_body(receive, limit), timeout=self._timeout
                )
            except TimeoutError:
                await self._error(
                    scope, receive, send, 408, "BODY_TIMEOUT", "Request body timed out"
                )
                return
            except BodyTooLarge:
                await self._error(
                    scope, receive, send, 413, "BODY_TOO_LARGE", "Request body too large"
                )
                return
            if body is None:
                return
            sent_body = False

            async def bounded_receive() -> Message:
                nonlocal sent_body
                if not sent_body:
                    sent_body = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            await self.app(scope, bounded_receive, send)
        finally:
            self._active -= 1

    def _authorized(self, header: bytes) -> bool:
        scheme, separator, token = header.partition(b" ")
        return bool(
            separator and scheme.lower() == b"bearer" and hmac.compare_digest(token, self._token)
        )

    async def _read_body(self, receive: Receive, limit: int) -> bytes | None:
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                raise BodyTooLarge()
            body.extend(chunk)
            if not message.get("more_body", False):
                return bytes(body)

    async def _error(
        self, scope: Scope, receive: Receive, send: Send, status: int, code: str, message: str
    ) -> None:
        headers = {"Cache-Control": "no-store"}
        if status == 401:
            headers["WWW-Authenticate"] = "Bearer"
        if status == 429:
            headers["Retry-After"] = "2"
        await JSONResponse({"code": code, "message": message}, status, headers)(
            scope, receive, send
        )


class BodyTooLarge(Exception):
    pass
