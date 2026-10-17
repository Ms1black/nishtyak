from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class WebSettings:
    data_dir: Path = Path("var/nishtyak-web")
    logic_url: str = "http://127.0.0.1:8010"
    service_token: str = field(default="", repr=False)
    mode: str = "local"
    proxy_secret: str = field(default="", repr=False)
    preview_password_hash: str = field(default="", repr=False)
    preview_cookie_secret: str = field(default="", repr=False)
    preview_session_seconds: int = 4 * 3600
    account_session_seconds: int = 7 * 86400
    origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    )
    ttl_seconds: int = 0
    max_storage_bytes: int = 2 * 1024**3
    max_workspace_bytes: int = 256 * 1024**2
    max_workspace_projects: int = 100

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir).absolute())
        object.__setattr__(self, "ttl_seconds", 0)
        if self.mode not in {"local", "proxy", "preview", "accounts"}:
            raise ValueError("Gateway mode must be local, proxy, preview or accounts")
        for name in ("service_token", "proxy_secret"):
            value = getattr(self, name)
            if name == "proxy_secret" and self.mode != "proxy" and not value:
                continue
            if not 32 <= len(value) <= 512 or any(not 33 <= ord(c) <= 126 for c in value):
                raise ValueError(f"{name} must contain 32 to 512 printable ASCII characters")
        endpoint = urlsplit(self.logic_url)
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("Invalid internal logic URL")
        for origin in self.origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.path
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Expected exact HTTP(S) browser origins without paths")
        if self.mode == "preview":
            from .preview import validate_password_hash

            validate_password_hash(self.preview_password_hash)
            if not 32 <= len(self.preview_cookie_secret) <= 512 or any(
                not 33 <= ord(c) <= 126 for c in self.preview_cookie_secret
            ):
                raise ValueError("Preview cookie secret must contain 32 to 512 ASCII characters")
            if not self.origins or any(urlsplit(value).scheme != "https" for value in self.origins):
                raise ValueError("Preview mode requires exact HTTPS browser origins")
        if self.mode == "accounts" and (
            not self.origins or any(urlsplit(value).scheme != "https" for value in self.origins)
        ):
            raise ValueError("Accounts mode requires exact HTTPS browser origins")
        if (
            type(self.account_session_seconds) is not int
            or not 3600 <= self.account_session_seconds <= 30 * 86400
        ):
            raise ValueError("Account session lifetime must be between 1 hour and 30 days")
        if (
            type(self.preview_session_seconds) is not int
            or not 60 <= self.preview_session_seconds <= 86400
        ):
            raise ValueError("Preview session lifetime must be between 60 and 86400 seconds")
        for name in (
            "max_storage_bytes",
            "max_workspace_bytes",
            "max_workspace_projects",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError("Gateway limits must be positive integers")

    @classmethod
    def from_env(cls) -> WebSettings:
        return cls(
            data_dir=Path(os.environ.get("NISHTYAK_WEB_DATA_DIR", "var/nishtyak-web")),
            logic_url=os.environ.get("NISHTYAK_LOGIC_URL", "http://127.0.0.1:8010"),
            service_token=os.environ.get("NISHTYAK_SERVICE_TOKEN", ""),
            mode=os.environ.get("NISHTYAK_WEB_MODE", "local"),
            proxy_secret=os.environ.get("NISHTYAK_WEB_PROXY_SECRET", ""),
            preview_password_hash=os.environ.get("NISHTYAK_PREVIEW_PASSWORD_HASH", ""),
            preview_cookie_secret=os.environ.get("NISHTYAK_PREVIEW_COOKIE_SECRET", ""),
            preview_session_seconds=int(
                os.environ.get("NISHTYAK_PREVIEW_SESSION_SECONDS", "14400")
            ),
            account_session_seconds=int(
                os.environ.get("NISHTYAK_ACCOUNT_SESSION_SECONDS", "604800")
            ),
            origins=tuple(
                part.strip()
                for part in os.environ.get("NISHTYAK_WEB_ORIGINS", ",".join(cls.origins)).split(",")
                if part.strip()
            ),
        )
