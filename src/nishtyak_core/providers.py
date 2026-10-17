from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import PurePosixPath
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from .resolver_proxy import RestrictedIndexProxy


class ProviderLookupError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderEvidence:
    package: str
    modules: tuple[str, ...]
    namespaces: tuple[str, ...]
    method: str
    observed_release: str
    wheel: str
    sha256: str
    artifact_hash_verified: bool = False
    metadata_sha256: str | None = None

    def provides(self, module: str) -> bool:
        return any(name.split(".", 1)[0] == module for name in (*self.modules, *self.namespaces))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _IndexTransport:
    """Bounded HTTPS reads; range responses must identify the exact requested bytes."""

    def __init__(self, deadline: float):
        self.deadline = deadline
        self.proxy = RestrictedIndexProxy(
            max_channels=1, max_bytes=4 * 1024**2, connection_deadline=self.remaining()
        )

    def __enter__(self):
        self.proxy.__enter__()
        try:
            self.opener = build_opener(
                ProxyHandler({"https": self.proxy.url, "http": self.proxy.url}), _NoRedirect()
            )
        except Exception:
            self.proxy.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        return self.proxy.__exit__(*args)

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderLookupError("provider_lookup_timeout")
        return remaining

    def read(self, url: str, limit: int, *, start: int | None = None, total: int | None = None):
        headers = {"Accept-Encoding": "identity", "Accept": "application/json, */*"}
        if start is not None:
            headers["Range"] = f"bytes={start}-{start + limit - 1}"
        try:
            with self.opener.open(Request(url, headers=headers), timeout=self.remaining()) as reply:
                if start is not None:
                    expected = f"bytes {start}-{start + limit - 1}/{total}"
                    if reply.status != 206 or reply.headers.get("Content-Range") != expected:
                        raise ProviderLookupError("provider_range_unsupported")
                elif reply.status != 200:
                    raise ProviderLookupError("provider_index_unavailable")
                content = reply.read(limit + 1)
                if len(content) > limit or start is not None and len(content) != limit:
                    raise ProviderLookupError("provider_response_limit")
                self.remaining()
                return content
        except HTTPError as error:
            raise ProviderLookupError(
                "provider_not_found" if error.code == 404 else "provider_index_unavailable"
            ) from None
        except (OSError, TimeoutError) as error:
            raise ProviderLookupError("provider_index_unavailable") from error


class _RemoteWheel(io.RawIOBase):
    def __init__(self, transport, url: str, size: int):
        self.transport, self.url, self.size = transport, url, size
        self.position = 0
        self.transferred = 0
        self.cache: list[tuple[int, bytes]] = []

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        if whence not in {0, 1, 2}:
            raise ValueError("Invalid seek mode")
        position = (
            offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        )
        if not 0 <= position <= self.size:
            raise ProviderLookupError("provider_wheel_invalid")
        self.position = position
        return position

    def read(self, size=-1):
        if size < 0:
            size = self.size - self.position
        size = min(size, self.size - self.position)
        if size == 0:
            return b""
        if size > 1024**2 or self.transferred + size > 2 * 1024**2:
            raise ProviderLookupError("provider_directory_limit")
        for start, content in self.cache:
            if start <= self.position and self.position + size <= start + len(content):
                offset = self.position - start
                self.position += size
                return content[offset : offset + size]
        content = self.transport.read(self.url, size, start=self.position, total=self.size)
        self.cache.append((self.position, content))
        self.transferred += len(content)
        self.position += len(content)
        return content


class PublishedDistributionInspector:
    """Read import-provider evidence without downloading or executing a large wheel.

    Core-metadata hashes authenticate metadata only. HTTPS range inspection does
    not verify the whole wheel digest, and records that distinction explicitly.
    """

    name_pattern = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?\Z")
    max_metadata_bytes = 256 * 1024
    max_directory_bytes = 1024**2
    max_members = 20_000
    max_artifact_bytes = 2 * 1024**3

    def __init__(self, transport_factory=None):
        self.transport_factory = transport_factory or _IndexTransport

    def inspect(self, package: str, *, specifier: str = "", timeout: float = 5) -> ProviderEvidence:
        if not self.name_pattern.fullmatch(package) or not 0 < timeout <= 10:
            raise ProviderLookupError("provider_name_invalid")
        name = canonicalize_name(package)
        deadline = time.monotonic() + timeout
        try:
            constraint = SpecifierSet(specifier)
            with self.transport_factory(deadline) as transport:
                exact = {
                    item.version
                    for item in constraint
                    if item.operator in {"==", "==="} and "*" not in item.version
                }
                if len(exact) == 1:
                    version = Version(next(iter(exact)))
                    if (
                        version not in constraint
                        or version.is_prerelease
                        or version.is_devrelease
                        or version.local
                    ):
                        raise ProviderLookupError("provider_release_unavailable")
                else:
                    version = self._recent_version(transport, name, constraint)
                content = transport.read(f"https://pypi.org/pypi/{name}/{version}/json", 1024**2)
                document = json.loads(content)
                info = document["info"]
                if canonicalize_name(info["name"]) != name or Version(info["version"]) != version:
                    raise ProviderLookupError("provider_identity_mismatch")
                wheel = self._wheel(document["urls"], name, version)
                sidecar = self._metadata_sidecar(transport, wheel, name, version)
                if sidecar is not None:
                    modules, namespaces, metadata_hash = sidecar
                    return self._evidence(
                        info["name"],
                        version,
                        wheel,
                        modules,
                        namespaces,
                        "published_core_metadata",
                        metadata_hash,
                    )
                modules = self._wheel_modules(transport, wheel, name, version)
                return self._evidence(
                    info["name"], version, wheel, modules, (), "published_wheel_directory"
                )
        except ProviderLookupError:
            raise
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            RecursionError,
            zipfile.BadZipFile,
        ) as error:
            raise ProviderLookupError("provider_evidence_unavailable") from error

    @staticmethod
    def _recent_version(transport, name, constraint):
        # The project JSON includes every historical wheel and can be enormous.
        # A bounded official release feed gives a version for the release-specific API.
        content = transport.read(f"https://pypi.org/rss/project/{name}/releases.xml", 128 * 1024)
        if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
            raise ProviderLookupError("provider_feed_invalid")
        try:
            feed = ET.fromstring(content)
        except ET.ParseError as error:
            raise ProviderLookupError("provider_feed_invalid") from error
        versions = []
        for item in feed.findall("./channel/item")[:100]:
            link = urlsplit(item.findtext("link", ""))
            parts = link.path.strip("/").split("/")
            if link.scheme != "https" or link.netloc != "pypi.org" or len(parts) != 3:
                continue
            if (
                parts[0] != "project"
                or canonicalize_name(parts[1]) != name
                or not re.fullmatch(r"[A-Za-z0-9.!_-]{1,80}", parts[2])
            ):
                continue
            try:
                version = Version(parts[2])
            except InvalidVersion:
                continue
            if (
                not version.is_prerelease
                and not version.is_devrelease
                and not version.local
                and version in constraint
            ):
                versions.append(version)
        if not versions:
            raise ProviderLookupError("provider_release_unavailable")
        return max(versions)

    def _wheel(self, files, name, version):
        candidates = []
        for item in files[:10_000]:
            if item.get("packagetype") != "bdist_wheel" or item.get("yanked") is not False:
                continue
            size = item.get("size")
            if type(size) is not int or not 0 < size <= self.max_artifact_bytes:
                continue
            filename = item.get("filename", "")
            wheel_name, wheel_version, _, _ = parse_wheel_filename(filename)
            if canonicalize_name(wheel_name) != name or wheel_version != version:
                continue
            parsed = urlsplit(item.get("url", ""))
            path = PurePosixPath(unquote(parsed.path))
            if (
                parsed.scheme != "https"
                or parsed.netloc != "files.pythonhosted.org"
                or parsed.query
                or parsed.fragment
                or not parsed.path.startswith("/packages/")
                or path.name != filename
                or ".." in path.parts
            ):
                continue
            if not re.fullmatch(r"[a-f0-9]{64}", item.get("digests", {}).get("sha256", "")):
                continue
            candidates.append(item)
        if not candidates:
            raise ProviderLookupError("provider_wheel_unavailable")
        return min(
            candidates,
            key=lambda item: (not item["filename"].endswith("-none-any.whl"), item["size"]),
        )

    def _metadata_sidecar(self, transport, wheel, name, version):
        hashes = wheel.get("core-metadata", wheel.get("dist_info_metadata"))
        if not isinstance(hashes, dict) or not re.fullmatch(
            r"[a-f0-9]{64}", hashes.get("sha256", "")
        ):
            return None
        content = transport.read(wheel["url"] + ".metadata", self.max_metadata_bytes)
        digest = hashlib.sha256(content).hexdigest()
        if digest != hashes["sha256"]:
            raise ProviderLookupError("provider_metadata_checksum_mismatch")
        fields = self._metadata(content, name, version)
        imports = fields.get_all("Import-Name")
        namespaces = fields.get_all("Import-Namespace")
        if imports is None and namespaces is None:
            return None
        if Version(fields.get("Metadata-Version", "0")) < Version("2.5"):
            return None
        modules = self._import_names(imports or (), allow_empty=True)
        namespace_names = self._import_names(namespaces or (), allow_empty=False)
        if set(modules) & set(namespace_names):
            raise ProviderLookupError("provider_metadata_ambiguous")
        return modules, namespace_names, digest

    @staticmethod
    def _import_names(values, *, allow_empty):
        names = set()
        for value in values[:500]:
            name, _, modifier = value.partition(";")
            name = name.strip()
            if not name and allow_empty:
                continue
            if modifier.strip() not in {"", "private"} or not all(
                part.isidentifier() for part in name.split(".")
            ):
                raise ProviderLookupError("provider_metadata_invalid")
            names.add(name)
        return tuple(sorted(names))

    @staticmethod
    def _metadata(content, name, version):
        fields = BytesParser().parsebytes(content, headersonly=True)
        if (
            canonicalize_name(fields.get("Name", "")) != name
            or Version(fields.get("Version", "")) != version
        ):
            raise ProviderLookupError("provider_identity_mismatch")
        return fields

    def _wheel_modules(self, transport, wheel, name, version):
        stream = _RemoteWheel(transport, wheel["url"], wheel["size"])
        stream.seek(max(0, wheel["size"] - 65557))
        tail = stream.read()
        end = tail.rfind(b"PK\x05\x06")
        if end < 0 or len(tail) - end < 22:
            raise ProviderLookupError("provider_wheel_invalid")
        _, disk, central_disk, disk_count, count, size, offset, comment = struct.unpack(
            "<4s4H2LH", tail[end : end + 22]
        )
        if (
            disk
            or central_disk
            or disk_count != count
            or count > self.max_members
            or size > self.max_directory_bytes
            or offset + size > wheel["size"]
            or end + 22 + comment != len(tail)
        ):
            raise ProviderLookupError("provider_directory_limit")
        with zipfile.ZipFile(stream) as archive:
            members = archive.infolist()
            if len(members) != count:
                raise ProviderLookupError("provider_wheel_invalid")
            metadata = [item for item in members if item.filename.endswith(".dist-info/METADATA")]
            if (
                len(metadata) != 1
                or metadata[0].file_size > self.max_metadata_bytes
                or metadata[0].compress_size > self.max_metadata_bytes
            ):
                raise ProviderLookupError("provider_metadata_limit")
            if metadata[0].compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ProviderLookupError("provider_wheel_invalid")
            with archive.open(metadata[0]) as source:
                content = source.read(self.max_metadata_bytes + 1)
            if len(content) > self.max_metadata_bytes:
                raise ProviderLookupError("provider_metadata_limit")
            self._metadata(content, name, version)
            modules = set()
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
                if not parts or not member.filename.endswith((".py", ".so", ".pyd")):
                    continue
                module = parts[0] if len(parts) > 1 else parts[0].split(".")[0]
                if module.isidentifier():
                    modules.add(module)
            if len(modules) > 500:
                raise ProviderLookupError("provider_modules_limit")
            return tuple(sorted(modules))

    @staticmethod
    def _evidence(package, version, wheel, modules, namespaces, method, metadata_hash=None):
        return ProviderEvidence(
            package,
            tuple(modules),
            tuple(namespaces),
            method,
            str(version),
            wheel["filename"],
            wheel["digests"]["sha256"],
            metadata_sha256=metadata_hash,
        )
