from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from nishtyak_client import LogicClientError

from .config import WebSettings
from .preview import PreviewAccess


@dataclass(frozen=True)
class WorkspaceIdentity:
    user: str
    workspace: str
    mode: str
    role: str = "owner"

    @property
    def scope(self) -> str:
        digest = hashlib.sha256((self.mode + ":" + self.workspace).encode()).hexdigest()
        return "web:" + digest


class GatewayBoundary:
    """Authenticate before receiving a bounded body; never trust browser owner headers."""

    def __init__(
        self,
        app,
        settings: WebSettings,
        preview: PreviewAccess | None = None,
        accounts=None,
        projects=None,
    ):
        self.app, self.settings = app, settings
        self.preview = preview
        self.accounts, self.projects = accounts, projects
        self._active = 0

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not (
            scope["path"] == "/api"
            or scope["path"].startswith("/api/")
            or scope["path"] in {"/preview/access", "/preview/logout"}
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
                b"cookie",
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
        if self.settings.mode in {"preview", "accounts"}:
            host = headers.get(b"host", b"").decode("latin1").lower()
            if scope["path"] != "/api/healthz" and host not in {
                urlsplit(value).netloc.lower() for value in self.settings.origins
            }:
                await self._error(scope, receive, send, 403, "HOST_FORBIDDEN")
                return
            if scope["method"] not in {"GET", "HEAD", "OPTIONS"} and not origin:
                await self._error(scope, receive, send, 403, "ORIGIN_REQUIRED")
                return
        if origin and origin not in self.settings.origins:
            await self._error(scope, receive, send, 403, "ORIGIN_FORBIDDEN")
            return
        if headers.get(b"sec-fetch-site") == b"cross-site":
            await self._error(scope, receive, send, 403, "ORIGIN_FORBIDDEN")
            return
        public_account = self.settings.mode == "accounts" and scope["path"] in {
            "/api/auth/register",
            "/api/auth/login",
            "/api/auth/logout",
            "/api/invites/preview",
        }
        if scope["path"] == "/api/healthz" or scope["path"] == "/preview/access" or public_account:
            identity = None
        else:
            try:
                identity = await run_in_threadpool(self._identity, scope, headers)
            except LogicClientError as error:
                await self._error(scope, receive, send, error.status, error.code)
                return
            if identity is None:
                code = (
                    "PREVIEW_AUTH_REQUIRED"
                    if self.settings.mode == "preview"
                    else "AUTHENTICATION_REQUIRED"
                )
                await self._error(scope, receive, send, 401, code)
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
            if scope["path"].startswith(("/preview/", "/api/auth/", "/api/invites/")):
                maximum = 8192
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
                        (b"referrer-policy", b"no-referrer"),
                    ]
                await send(message)

            await self.app(scope, bounded_receive, safe_send)
        finally:
            self._active -= 1

    def _identity(self, scope, headers) -> WorkspaceIdentity | None:
        if self.settings.mode == "accounts":
            user_id = self.accounts.authenticate(headers.get(b"cookie", b"").decode("latin1"))
            if not user_id:
                return None
            parts = scope["path"].strip("/").split("/")
            team_id = parts[2] if len(parts) >= 3 and parts[1] == "teams" else None
            project_scope = None
            if len(parts) >= 3 and parts[1] == "projects":
                project_scope = self.projects.scope_for(parts[2])
            membership = self.accounts.workspace(user_id, team_id, project_scope)
            if membership["role"] == "viewer" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                read_plan = (
                    len(parts) == 4
                    and parts[1] == "projects"
                    and parts[3] == "plan"
                    and scope["method"] == "POST"
                )
                project_write = len(parts) >= 3 and parts[1] == "projects"
                team_upload = len(parts) == 4 and parts[1] == "teams" and parts[3] == "projects"
                if (project_write or team_upload) and not read_plan:
                    raise LogicClientError(403, "ROLE_FORBIDDEN", "Viewers cannot change projects")
            return WorkspaceIdentity(user_id, membership["team_id"], "accounts", membership["role"])
        if self.settings.mode == "preview":
            valid = self.preview and self.preview.valid_cookie(
                headers.get(b"cookie", b"").decode("latin1")
            )
            return WorkspaceIdentity("reviewer", "preview", "preview") if valid else None
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
