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
    origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    )
    ttl_seconds: int = 3600
    max_storage_bytes: int = 2 * 1024**3
    max_workspace_bytes: int = 256 * 1024**2
    max_workspace_projects: int = 100

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir).absolute())
        if self.mode not in {"local", "proxy"}:
            raise ValueError("Gateway mode must be local or proxy")
        for name in ("service_token", "proxy_secret"):
            value = getattr(self, name)
            if name == "proxy_secret" and self.mode == "local" and not value:
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
        for name in (
            "ttl_seconds",
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
            origins=tuple(
                part.strip()
                for part in os.environ.get("NISHTYAK_WEB_ORIGINS", ",".join(cls.origins)).split(",")
                if part.strip()
            ),
        )
