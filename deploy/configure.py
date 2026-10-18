"""Create a private deployment environment without exposing credentials."""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import secrets
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class DeploymentConfig:
    origin: str
    docker_socket: Path = Path("/var/run/docker.sock")
    docker_gid: int | None = None
    domain: str = ""

    def validate(self) -> None:
        try:
            parsed = urlsplit(self.origin)
            port = parsed.port
        except ValueError as error:
            raise ValueError("Origin must be an exact HTTPS origin") from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.path
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.netloc.lower() != parsed.netloc
            or any(char.isspace() for char in self.origin)
        ):
            raise ValueError("Origin must be an exact HTTPS origin without a path")
        hostname = parsed.hostname
        is_address = True
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            is_address = False
            if not self._valid_domain(hostname):
                raise ValueError(
                    "Origin hostname must be a valid ASCII domain or IP address"
                ) from None
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Origin port must be between 1 and 65535")
        if self.domain and (
            not self._valid_domain(self.domain)
            or is_address
            or "." not in self.domain
            or self.domain != hostname
            or port not in {None, 443}
        ):
            raise ValueError("Edge domain must match the HTTPS origin on the standard port")
        if (
            not self.docker_socket.is_absolute()
            or re.search(r"[^/A-Za-z0-9_.-]", str(self.docker_socket))
            or ".." in self.docker_socket.parts
        ):
            raise ValueError("Docker socket must be an absolute path without special characters")
        if self.docker_gid is not None and not 0 <= self.docker_gid <= 2**32 - 2:
            raise ValueError("Docker socket group id must be a valid nonnegative integer")

    @staticmethod
    def _valid_domain(value: str) -> bool:
        return len(value) <= 253 and all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in value.split(".")
        )

    def socket_gid(self) -> int:
        if self.docker_gid is not None:
            return self.docker_gid
        try:
            socket = self.docker_socket.stat()
        except OSError as error:
            raise ValueError(
                "Docker socket is unavailable; run this on the Linux Docker host"
            ) from error
        if not stat.S_ISSOCK(socket.st_mode):
            raise ValueError("Configured Docker socket is not a Unix socket")
        return socket.st_gid

    def render(self, environment: dict[str, str]) -> str:
        self.validate()
        api_key = environment.get("DEEPSEEK_API_KEY", "")
        model = environment.get("DEEPSEEK_MODEL", "deepseek-flash")
        if api_key and re.fullmatch(r"[A-Za-z0-9._-]{8,512}", api_key) is None:
            raise ValueError("DEEPSEEK_API_KEY must be a plain API token")
        if re.fullmatch(r"[A-Za-z0-9._:/-]{1,120}", model) is None:
            raise ValueError("DEEPSEEK_MODEL must be a plain model identifier")
        storage_bytes = environment.get("NISHTYAK_MAX_STORAGE_BYTES", "2147483648")
        if re.fullmatch(r"[1-9][0-9]{0,18}", storage_bytes) is None:
            raise ValueError("NISHTYAK_MAX_STORAGE_BYTES must be a positive byte count")
        values = {
            "NISHTYAK_WEB_ORIGINS": self.origin,
            "NISHTYAK_DOMAIN": self.domain,
            "NISHTYAK_SERVICE_TOKEN": secrets.token_urlsafe(48),
            "NISHTYAK_DOCKER_SOCKET": str(self.docker_socket),
            "NISHTYAK_DOCKER_GID": str(self.socket_gid()),
            "NISHTYAK_AUTOMATIC_REVIEW": "1" if api_key else "0",
            "DEEPSEEK_API_KEY": api_key,
            "DEEPSEEK_MODEL": model,
            "NISHTYAK_ACCOUNT_SESSION_SECONDS": "604800",
            "NISHTYAK_MAX_STORAGE_BYTES": storage_bytes,
            "NISHTYAK_MAX_OWNER_STORAGE_BYTES": "268435456",
        }
        return "".join(f"{key}={value}\n" for key, value in values.items())


class EnvironmentWriter:
    def write(self, destination: Path, content: str) -> None:
        destination = destination.absolute()
        if destination.exists() or destination.is_symlink():
            raise ValueError(
                "Environment file already exists; preserve or move it before configuring"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".nishtyak-env-", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError as error:
                raise ValueError(
                    "Environment file already exists; credentials were not replaced"
                ) from error
            parent = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            Path(temporary).unlink(missing_ok=True)


def check_runtime(socket: Path) -> None:
    environment = dict(os.environ, DOCKER_HOST=f"unix://{socket}")
    for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DEEPSEEK_API_KEY"):
        environment.pop(name, None)
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}}"],
            env=environment,
            capture_output=True,
            timeout=10,
            check=False,
        )
        compose = subprocess.run(
            ["docker", "compose", "version", "--short"],
            env=environment,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Docker Engine and Docker Compose v2 or newer are required") from error
    if info.returncode or info.stdout.strip() != b"linux":
        raise ValueError("A reachable Linux Docker Engine is required")
    version = re.match(rb"v?(\d+)\.", compose.stdout.strip())
    if compose.returncode or version is None or int(version.group(1)) < 2:
        raise ValueError("Docker Compose v2 or newer is required")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True, help="Exact public HTTPS origin without a path")
    parser.add_argument("--domain", default="", help="Matching public DNS name for optional Caddy")
    parser.add_argument("--docker-socket", type=Path, default=Path("/var/run/docker.sock"))
    parser.add_argument(
        "--docker-gid", type=int, help="Explicit socket group id for offline preparation"
    )
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument(
        "--skip-runtime-check",
        action="store_true",
        help="Prepare configuration before copying to a VM",
    )
    arguments = parser.parse_args()
    config = DeploymentConfig(
        arguments.origin, arguments.docker_socket, arguments.docker_gid, arguments.domain
    )
    try:
        config.validate()
        if not arguments.skip_runtime_check:
            check_runtime(config.docker_socket)
        EnvironmentWriter().write(arguments.output, config.render(dict(os.environ)))
    except (ValueError, OSError) as error:
        parser.exit(2, f"Configuration failed: {error}\n")
    print(f"Created {arguments.output.absolute()} with mode 0600. Credentials were not printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
