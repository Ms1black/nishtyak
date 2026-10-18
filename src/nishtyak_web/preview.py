from __future__ import annotations

import hashlib
import hmac
import html
import re
import secrets
import threading
import time
from collections import deque
from http.cookies import CookieError, SimpleCookie
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from .config import WebSettings

COOKIE_NAME = "__Host-nishtyak_preview"
HASH_ITERATIONS = 600_000


def hash_passphrase(passphrase: str) -> str:
    """Create the server-side credential; the passphrase is never kept by the gateway."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, HASH_ITERATIONS)
    return f"pbkdf2-sha256${HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def validate_password_hash(value: str) -> None:
    parts = value.split("$")
    if (
        len(parts) != 4
        or parts[0] != "pbkdf2-sha256"
        or not parts[1].isdigit()
        or not 100_000 <= int(parts[1]) <= 2_000_000
        or not re.fullmatch(r"[a-f0-9]{32,128}", parts[2])
        or len(parts[2]) % 2
        or not re.fullmatch(r"[a-f0-9]{64}", parts[3])
    ):
        raise ValueError("Expected a PBKDF2-SHA256 preview password hash")


class PreviewAccess:
    """One shared review workspace, deliberately separate from account authentication."""

    def __init__(self, settings: WebSettings):
        self.settings = settings
        self._attempts: deque[float] = deque(maxlen=10)
        self._lock = threading.Lock()

    def authenticate(self, passphrase: str) -> str:
        # A global budget also works behind a tunnel; forwarded addresses are untrusted.
        with self._lock:
            now = time.monotonic()
            while self._attempts and self._attempts[0] <= now - 60:
                self._attempts.popleft()
            if len(self._attempts) >= 10:
                raise PreviewLoginError(429, "PREVIEW_LOGIN_LIMIT")
            self._attempts.append(now)
        if len(passphrase.encode()) > 1024:
            raise PreviewLoginError(401, "PREVIEW_PASSWORD_INVALID")
        _, iterations, salt, expected = self.settings.preview_password_hash.split("$")
        actual = hashlib.pbkdf2_hmac(
            "sha256", passphrase.encode(), bytes.fromhex(salt), int(iterations)
        ).hex()
        if not hmac.compare_digest(actual, expected):
            raise PreviewLoginError(401, "PREVIEW_PASSWORD_INVALID")
        expires = int(time.time()) + self.settings.preview_session_seconds
        payload = f"v1.{expires}.{secrets.token_hex(16)}"
        signature = hmac.new(
            self.settings.preview_cookie_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return payload + "." + signature

    def valid_cookie(self, header: str) -> bool:
        if len(header) > 8192 or header.count(COOKIE_NAME + "=") != 1:
            return False
        try:
            cookie: SimpleCookie = SimpleCookie()
            cookie.load(header)
            value = cookie[COOKIE_NAME].value
        except (CookieError, KeyError):
            return False
        match = re.fullmatch(r"(v1\.([0-9]{1,12})\.[a-f0-9]{32})\.([a-f0-9]{64})", value)
        if match is None:
            return False
        now = int(time.time())
        if not now < int(match[2]) <= now + self.settings.preview_session_seconds:
            return False
        expected = hmac.new(
            self.settings.preview_cookie_secret.encode(), match[1].encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, match[3])


class PreviewLoginError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def safe_next(value: str) -> str:
    parsed = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(c) < 32 for c in value)
        or parsed.scheme
        or parsed.netloc
        or parsed.path.startswith(("/preview/", "/api/"))
    ):
        return "/team/projects"
    return value[:2048]


def access_page(next_path: str) -> str:
    target = html.escape(safe_next(next_path), quote=True)
    return f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ништяк — доступ к проверке</title>
<style>body{{font:17px system-ui;background:#101015;color:#f5f5fa;margin:0}}
main{{max-width:460px;margin:15vh auto;padding:28px}}input,button{{box-sizing:border-box;
width:100%;padding:13px;margin:10px 0;font:inherit;border-radius:8px}}button{{cursor:pointer}}
p{{line-height:1.6;color:#bcbcc7}}</style><main><h1>Проверка сервиса</h1>
<p>Введите общий код доступа. Проекты доступны всем участникам этой проверки.
Регистрацию, команды и приглашения разрабатываем отдельно.</p>
<form method="post" action="/preview/access"><input type="hidden" name="next" value="{target}">
<label>Код доступа<input type="password" name="passphrase" required maxlength="1024"
autocomplete="current-password"></label><button>Открыть приложение</button></form></main></html>"""
