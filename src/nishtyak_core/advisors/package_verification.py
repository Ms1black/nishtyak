from __future__ import annotations

import hashlib
import io
import json
import re
import time
import zipfile
from collections.abc import Callable
from email.parser import BytesParser
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from ..resolver_proxy import RestrictedIndexProxy


class PackageVerificationError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PublishedPackageVerifier:
    """Check a proposed provider against a bounded wheel, without installing it.

    A matching wheel establishes that the distribution provides the import name.
    It does not establish the author's intended package or a tested version.
    The subsequent resolver selects the actual target-compatible versions.
    """

    max_metadata_bytes = 1024 * 1024
    max_wheel_bytes = 4 * 1024 * 1024
    max_members = 20_000
    _name = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?\Z")

    def __init__(self, fetch: Callable[[str, int, float], bytes] | None = None):
        self.fetch = fetch

    def verify(self, module: str, package: str, *, timeout: float = 4) -> dict:
        if not module.isidentifier() or not self._name.fullmatch(package) or not 0 < timeout <= 15:
            raise PackageVerificationError("package_mapping_invalid")
        deadline = time.monotonic() + timeout
        if self.fetch is not None:
            return self._verify(module, package, deadline, self.fetch)
        with RestrictedIndexProxy(
            max_channels=1,
            max_bytes=self.max_wheel_bytes + self.max_metadata_bytes + 65536,
            connection_deadline=timeout,
        ) as proxy:
            opener = build_opener(
                ProxyHandler({"https": proxy.url, "http": proxy.url}), _NoRedirect()
            )

            def fetch(url: str, limit: int, remaining: float) -> bytes:
                request = Request(
                    url, headers={"Accept": "application/json, application/octet-stream"}
                )
                try:
                    with opener.open(request, timeout=remaining) as response:
                        content = response.read(limit + 1)
                except OSError as error:
                    raise PackageVerificationError("package_index_unavailable") from error
                if len(content) > limit:
                    raise PackageVerificationError("package_verification_limit")
                return content

            return self._verify(module, package, deadline, fetch)

    def _verify(self, module: str, package: str, deadline: float, fetch: Callable) -> dict:
        name = canonicalize_name(package)
        raw = fetch(
            f"https://pypi.org/pypi/{name}/json", self.max_metadata_bytes, self._remaining(deadline)
        )
        if len(raw) > self.max_metadata_bytes:
            raise PackageVerificationError("package_verification_limit")
        try:
            document = json.loads(raw)
            info = document["info"]
            if canonicalize_name(info["name"]) != name:
                raise ValueError("package identity mismatch")
            version = Version(info["version"])
            if version.is_prerelease or version.is_devrelease:
                raise ValueError("unverified release")
            wheels = [
                item
                for item in document["urls"]
                if item.get("packagetype") == "bdist_wheel"
                and item.get("yanked") is False
                and type(item.get("size")) is int
                and 0 < item["size"] <= self.max_wheel_bytes
            ]
            wheels.sort(
                key=lambda item: (not item["filename"].endswith("-none-any.whl"), item["size"])
            )
            if not wheels:
                raise PackageVerificationError("package_wheel_unavailable")
            wheel = wheels[0]
            filename = wheel["filename"]
            wheel_name, wheel_version, _, _ = parse_wheel_filename(filename)
            if canonicalize_name(wheel_name) != name or wheel_version != version:
                raise ValueError("wheel identity mismatch")
            digest = wheel["digests"]["sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("invalid artifact checksum")
            parsed = urlsplit(wheel["url"])
            if (
                parsed.scheme != "https"
                or parsed.netloc != "files.pythonhosted.org"
                or parsed.query
                or parsed.fragment
                or not parsed.path.startswith("/packages/")
                or PurePosixPath(unquote(parsed.path)).name != filename
                or ".." in PurePosixPath(unquote(parsed.path)).parts
            ):
                raise ValueError("unsupported artifact URL")
            content = fetch(wheel["url"], self.max_wheel_bytes, self._remaining(deadline))
            if len(content) != wheel["size"] or hashlib.sha256(content).hexdigest() != digest:
                raise ValueError("artifact checksum mismatch")
            self._verify_contents(content, name, version, module)
            self._remaining(deadline)
        except PackageVerificationError:
            raise
        except (
            KeyError,
            TypeError,
            ValueError,
            zipfile.BadZipFile,
            UnicodeError,
            RecursionError,
        ) as error:
            raise PackageVerificationError("package_mapping_unverified") from error
        return {
            "package": info["name"],
            "module": module,
            "method": "published_wheel_contents",
            "wheel": filename,
            "sha256": digest,
            "observed_release": str(version),
            "execution_verified": False,
        }

    def _verify_contents(self, content: bytes, name: str, version: Version, module: str) -> None:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > self.max_members:
                raise PackageVerificationError("package_verification_limit")
            metadata = [item for item in members if item.filename.endswith(".dist-info/METADATA")]
            if len(metadata) != 1 or metadata[0].file_size > 65536:
                raise ValueError("invalid wheel metadata")
            with archive.open(metadata[0]) as stream:
                fields = BytesParser().parsebytes(stream.read(65537), headersonly=True)
            if (
                canonicalize_name(fields.get("Name", "")) != name
                or Version(fields.get("Version", "")) != version
            ):
                raise ValueError("wheel metadata identity mismatch")
            for member in members:
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
                    continue
                parts = path.parts
                if (
                    len(parts) >= 3
                    and parts[0].endswith(".data")
                    and parts[1] in {"purelib", "platlib"}
                ):
                    parts = parts[2:]
                if not parts or any(part.endswith((".dist-info", ".data")) for part in parts[:-1]):
                    continue
                if (
                    len(parts) >= 2
                    and parts[0] == module
                    and not member.is_dir()
                    and member.filename.endswith((".py", ".so", ".pyd"))
                ) or (
                    len(parts) == 1
                    and parts[0].split(".", 1)[0] == module
                    and parts[0].endswith((".py", ".so", ".pyd"))
                ):
                    return
            raise PackageVerificationError("package_does_not_provide_module")

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PackageVerificationError("package_verification_timeout")
        return remaining
