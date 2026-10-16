from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from nishtyak_core.jobs import JobPolicy


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("var/nishtyak")
    api_token: str = field(default="", repr=False)
    runner: str = "docker"
    analyzer_image: str = "nishtyak-analyzer:0.1.0"
    max_running: int = 2
    max_queued: int = 32
    max_owner_active: int = 2
    max_storage_bytes: int = 2 * 1024**3
    max_owner_storage_bytes: int = 256 * 1024**2
    max_archive_bytes: int = 10 * 1024**2
    max_report_bytes: int = 8 * 1024**2
    max_bundle_bytes: int = 80 * 1024**2
    max_options_bytes: int = 64 * 1024
    json_request_bytes: int = 256 * 1024
    max_http_requests: int = 8
    ttl_seconds: int = 3600
    operation_lease_seconds: int = 180

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir).absolute())
        if self.runner not in {"docker", "subprocess"}:
            raise ValueError("Runner must be docker or subprocess")
        if self.api_token and not re.fullmatch(r"[!-~]{32,512}", self.api_token):
            raise ValueError("Service token must contain 32 to 512 printable ASCII characters")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}", self.analyzer_image):
            raise ValueError("Invalid analyzer image")
        for name, value in vars(self).items():
            if name in {"data_dir", "api_token", "runner", "analyzer_image"}:
                continue
            if type(value) is not int or value <= 0:
                raise ValueError("Service limits must be positive integers")
        if self.max_running > 2:
            raise ValueError("This service supports at most two simultaneous operations")
        if self.max_archive_bytes > 10 * 1024**2 or self.max_report_bytes > 8 * 1024**2:
            raise ValueError("Service limits cannot raise the worker input/output limits")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_archive_bytes

    @property
    def job_policy(self) -> JobPolicy:
        return JobPolicy(
            max_result_bytes=self.max_report_bytes,
            max_concurrent_jobs=self.max_running,
        )

    @classmethod
    def from_env(cls) -> Settings:
        values: dict = {
            "data_dir": Path(os.environ.get("NISHTYAK_DATA_DIR", "var/nishtyak")),
            "api_token": os.environ.get("NISHTYAK_SERVICE_TOKEN", ""),
            "runner": os.environ.get("NISHTYAK_RUNNER", "docker"),
            "analyzer_image": os.environ.get("NISHTYAK_ANALYZER_IMAGE", "nishtyak-analyzer:0.1.0"),
        }
        for name in cls.__dataclass_fields__:
            if name in values:
                continue
            raw = os.environ.get(f"NISHTYAK_{name.upper()}")
            if raw is not None:
                try:
                    values[name] = int(raw)
                except ValueError as error:
                    raise ValueError(f"NISHTYAK_{name.upper()} must be an integer") from error
        return cls(**values)
