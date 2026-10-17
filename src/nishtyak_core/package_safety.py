from __future__ import annotations

import http.client
import json
import math
import re
import socket
import ssl
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import islice
from typing import Literal, Protocol

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

OSV_ENDPOINT = "https://api.osv.dev/v1/querybatch"
SafetyStatus = Literal["checked", "blocked", "unavailable"]
_PACKAGE_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?\Z")
_ADVISORY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]{1,126}[A-Za-z0-9]\Z")


@dataclass(frozen=True)
class PackageVersion:
    name: str
    version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _PACKAGE_NAME.fullmatch(self.name):
            raise ValueError("Expected a public PyPI package name")
        object.__setattr__(self, "name", canonicalize_name(self.name))
        if self.version is None:
            return
        if (
            not isinstance(self.version, str)
            or not 1 <= len(self.version) <= 128
            or not self.version.isascii()
            or any(character.isspace() for character in self.version)
        ):
            raise ValueError("Expected an exact package version")
        try:
            parsed = Version(self.version)
            if parsed.local is not None:
                raise ValueError("Expected a public package version")
            version = str(parsed)
        except InvalidVersion:
            raise ValueError("Expected an exact package version") from None
        object.__setattr__(self, "version", version)


@dataclass(frozen=True)
class PackageSafetyResult:
    name: str
    version: str | None
    status: SafetyStatus
    advisory_ids: tuple[str, ...]
    malicious_ids: tuple[str, ...]
    source: str
    checked_at: str
    reason: str | None = None

    @property
    def scope(self) -> str:
        return "version" if self.version is not None else "package"

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "advisory_ids": list(self.advisory_ids),
            "malicious_ids": list(self.malicious_ids),
            "scope": self.scope,
        }


@dataclass(frozen=True)
class PackageSafetyReport:
    status: SafetyStatus
    packages: tuple[PackageSafetyResult, ...]
    source: str
    checked_at: str

    @property
    def results(self) -> tuple[PackageSafetyResult, ...]:
        return self.packages

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "packages": [package.to_dict() for package in self.packages],
            "source": self.source,
            "checked_at": self.checked_at,
        }


class PackageSafetyLookupError(ValueError):
    pass


class SafetyTransport(Protocol):
    def post(self, payload: bytes, *, deadline: float, max_bytes: int) -> bytes: ...


class _OSVTransport:
    """A fixed HTTPS POST endpoint, without redirects, ambient proxies or credentials."""

    def __init__(self, connection_factory=None):
        self.connection_factory = connection_factory or http.client.HTTPSConnection

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PackageSafetyLookupError("safety_lookup_timeout")
        return remaining

    def post(self, payload: bytes, *, deadline: float, max_bytes: int) -> bytes:
        connection = self.connection_factory(
            "api.osv.dev",
            port=443,
            timeout=self._remaining(deadline),
            context=ssl.create_default_context(),
        )
        connected = []

        def expire():
            active_socket = connected[0] if connected else connection.sock
            if active_socket is not None:
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()

        timer = threading.Timer(self._remaining(deadline), expire)
        timer.daemon = True
        timer.start()
        try:
            connection.connect()
            connected.append(connection.sock)
            connection.sock.settimeout(self._remaining(deadline))
            connection.request(
                "POST",
                "/v1/querybatch",
                body=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            )
            connection.sock.settimeout(self._remaining(deadline))
            response = connection.getresponse()
            if response.status != 200:
                raise PackageSafetyLookupError("safety_service_unavailable")
            if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                raise PackageSafetyLookupError("safety_response_encoding")
            length = response.getheader("Content-Length")
            if length is not None and (
                not length.isascii() or not length.isdecimal() or int(length) > max_bytes
            ):
                raise PackageSafetyLookupError("safety_response_limit")
            content = bytearray()
            while True:
                connected[0].settimeout(self._remaining(deadline))
                chunk = response.read1(min(65536, max_bytes + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise PackageSafetyLookupError("safety_response_limit")
            self._remaining(deadline)
            if length is not None and len(content) != int(length):
                raise PackageSafetyLookupError("safety_response_incomplete")
            return bytes(content)
        finally:
            timer.cancel()
            connection.close()


class PackageSafetyClient:
    """Check OSV records for exact versions or all known releases of a package.

    ``checked`` means that the bounded query completed. It is never a safety
    guarantee. Normal vulnerabilities are reported; active MAL records block.
    Pagination and failures stay explicitly unavailable, rather than clean.
    """

    max_batch_size = 100
    max_packages = 2000
    max_response_bytes = 1024**2

    def __init__(
        self,
        transport: SafetyTransport | None = None,
        *,
        timeout: float = 5,
        cache_ttl: float = 600,
        max_cache_entries: int = 1024,
    ):
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= 5
            or isinstance(cache_ttl, bool)
            or not isinstance(cache_ttl, int | float)
            or not math.isfinite(cache_ttl)
            or not 0 <= cache_ttl <= 600
            or type(max_cache_entries) is not int
            or not 1 <= max_cache_entries <= 1024
        ):
            raise ValueError("Invalid package advisory limits")
        self.transport = transport if transport is not None else _OSVTransport()
        self.timeout, self.cache_ttl, self.max_cache_entries = timeout, cache_ttl, max_cache_entries
        self._cache: OrderedDict[PackageVersion, tuple[float, PackageSafetyResult]] = OrderedDict()
        self._cache_lock = threading.Lock()

    def check(
        self, packages: Iterable[PackageVersion], *, deadline: float | None = None
    ) -> PackageSafetyReport:
        requested = list(islice(packages, self.max_packages + 1))
        if len(requested) > self.max_packages or any(
            not isinstance(item, PackageVersion) for item in requested
        ):
            raise ValueError("Expected at most 2000 validated package queries")
        if deadline is not None and (
            isinstance(deadline, bool)
            or not isinstance(deadline, int | float)
            or not math.isfinite(deadline)
        ):
            raise ValueError("Invalid package advisory deadline")
        deadline = (
            min(deadline, time.monotonic() + self.timeout)
            if deadline is not None
            else time.monotonic() + self.timeout
        )
        results = {}
        unique = tuple(dict.fromkeys(requested))
        for package in unique:
            cached = self._cached(package)
            if cached is not None:
                results[package] = cached
        pending = [package for package in unique if package not in results]
        remaining_bytes = self.max_response_bytes
        for offset in range(0, len(pending), self.max_batch_size):
            batch = pending[offset : offset + self.max_batch_size]
            checked_at = self._timestamp()
            try:
                if time.monotonic() >= deadline:
                    raise PackageSafetyLookupError("safety_lookup_timeout")
                if remaining_bytes <= 0:
                    raise PackageSafetyLookupError("safety_response_limit")
                payload = json.dumps(
                    {"queries": [self._query(item) for item in batch]}, separators=(",", ":")
                ).encode("ascii")
                content = self.transport.post(payload, deadline=deadline, max_bytes=remaining_bytes)
                if not isinstance(content, bytes) or len(content) > remaining_bytes:
                    raise PackageSafetyLookupError("safety_response_limit")
                remaining_bytes -= len(content)
                if time.monotonic() >= deadline:
                    raise PackageSafetyLookupError("safety_lookup_timeout")
                responses = self._response(content, len(batch))
                for package, response in zip(batch, responses, strict=True):
                    try:
                        result = self._result(package, response, checked_at)
                    except (ValueError, TypeError, OverflowError):
                        result = self._unavailable(package, checked_at, "safety_response_invalid")
                    results[package] = result
                    self._remember(package, result)
            except Exception as error:
                reason = (
                    str(error)
                    if isinstance(error, PackageSafetyLookupError)
                    else "safety_service_unavailable"
                )
                for package in batch:
                    results[package] = self._unavailable(package, checked_at, reason)
        ordered = tuple(results[package] for package in requested)
        status: SafetyStatus = (
            "blocked"
            if any(item.status == "blocked" for item in ordered)
            else "unavailable"
            if any(item.status == "unavailable" for item in ordered)
            else "checked"
        )
        return PackageSafetyReport(
            status,
            ordered,
            OSV_ENDPOINT,
            min((item.checked_at for item in ordered), default=self._timestamp()),
        )

    @staticmethod
    def _query(package: PackageVersion) -> dict:
        query = {"package": {"name": package.name, "ecosystem": "PyPI"}}
        if package.version is not None:
            query["version"] = package.version
        return query

    @staticmethod
    def _response(content: bytes, expected: int) -> list:
        def object_pairs(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise PackageSafetyLookupError("safety_response_invalid")
                value[key] = item
            return value

        def invalid_constant(value):
            raise PackageSafetyLookupError("safety_response_invalid")

        try:
            document = json.loads(
                content, object_pairs_hook=object_pairs, parse_constant=invalid_constant
            )
        except (ValueError, RecursionError):
            raise PackageSafetyLookupError("safety_response_invalid") from None
        if not isinstance(document, dict) or set(document) != {"results"}:
            raise PackageSafetyLookupError("safety_response_invalid")
        results = document["results"]
        if not isinstance(results, list) or len(results) != expected:
            raise PackageSafetyLookupError("safety_response_invalid")
        return results

    def _result(self, package: PackageVersion, response, checked_at: str) -> PackageSafetyResult:
        if not isinstance(response, dict) or set(response) - {"vulns", "next_page_token"}:
            raise ValueError("Invalid advisory result")
        token = response.get("next_page_token", "")
        if not isinstance(token, str) or len(token) > 4096:
            raise ValueError("Invalid advisory pagination")
        vulnerabilities = response.get("vulns", [])
        if not isinstance(vulnerabilities, list) or len(vulnerabilities) > 1000:
            raise ValueError("Invalid advisory count")
        identifiers = set()
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict) or set(vulnerability) - {
                "id",
                "modified",
                "withdrawn",
            }:
                raise ValueError("Invalid advisory")
            identifier = vulnerability.get("id")
            if (
                not isinstance(identifier, str)
                or not _ADVISORY_ID.fullmatch(identifier)
                or "-" not in identifier
            ):
                raise ValueError("Invalid advisory identifier")
            if identifier.lower().startswith("mal-") and not identifier.startswith("MAL-"):
                raise ValueError("Invalid malicious advisory identifier")
            if "modified" in vulnerability:
                self._validate_timestamp(vulnerability["modified"])
            if vulnerability.get("withdrawn") is not None:
                self._validate_timestamp(vulnerability["withdrawn"])
                continue
            identifiers.add(identifier)
        advisories = tuple(sorted(identifiers))
        malicious = tuple(identifier for identifier in advisories if identifier.startswith("MAL-"))
        status: SafetyStatus = "blocked" if malicious else "unavailable" if token else "checked"
        return PackageSafetyResult(
            package.name,
            package.version,
            status,
            advisories,
            malicious,
            OSV_ENDPOINT,
            checked_at,
            "safety_response_incomplete" if token else None,
        )

    @staticmethod
    def _validate_timestamp(value):
        if not isinstance(value, str) or not 1 <= len(value) <= 64:
            raise ValueError("Invalid advisory timestamp")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Invalid advisory timestamp")

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _unavailable(package, checked_at, reason):
        return PackageSafetyResult(
            package.name, package.version, "unavailable", (), (), OSV_ENDPOINT, checked_at, reason
        )

    def _cached(self, package):
        with self._cache_lock:
            cached = self._cache.get(package)
            if cached is None:
                return None
            if cached[0] <= time.monotonic():
                self._cache.pop(package)
                return None
            self._cache.move_to_end(package)
            return cached[1]

    def _remember(self, package, result):
        if result.status == "unavailable" or self.cache_ttl == 0:
            return
        if len(json.dumps(result.to_dict())) > 4096:
            return
        with self._cache_lock:
            self._cache[package] = (time.monotonic() + self.cache_ttl, result)
            self._cache.move_to_end(package)
            while len(self._cache) > self.max_cache_entries:
                self._cache.popitem(last=False)


_default_client: PackageSafetyClient | None = None
_default_client_lock = threading.Lock()


def get_package_safety_client() -> PackageSafetyClient:
    global _default_client
    with _default_client_lock:
        if _default_client is None:
            _default_client = PackageSafetyClient()
        return _default_client
