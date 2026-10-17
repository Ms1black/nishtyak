from __future__ import annotations

import json
import math
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

PACKAGE_SAFETY_SOURCE = "https://api.osv.dev/v1/querybatch"


class LogicClientError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.code, self.field = status, code, field


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LogicClient:
    """Synchronous internal-backend client. Never expose its token to a browser.

    Mutations are never retried automatically. The caller derives owner_id from its
    authenticated user, and can move blocking requests into its thread pool.
    """

    def __init__(self, base_url: str, token: str, owner_id: str, timeout: float = 15):
        url = urlsplit(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Expected an internal HTTP(S) service URL")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", owner_id):
            raise ValueError("Invalid owner identity")
        if (
            not isinstance(token, str)
            or not 32 <= len(token) <= 512
            or not token.isascii()
            or any(ord(char) < 33 or ord(char) > 126 for char in token)
        ):
            raise ValueError("Invalid service token")
        if not 0 < timeout <= 120:
            raise ValueError("Invalid request timeout")
        self.base_url, self.owner_id, self.timeout = base_url.rstrip("/"), owner_id, timeout
        self._token = token
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def capabilities(self) -> dict:
        return self._request("GET", "/v1/capabilities")

    def submit(
        self,
        archive: bytes | Path,
        options: dict | None = None,
        author_environment: dict | None = None,
    ) -> dict:
        if isinstance(archive, Path):
            with archive.open("rb") as stream:
                archive = stream.read(10 * 1024 * 1024 + 1)
        if not isinstance(archive, bytes) or not 0 < len(archive) <= 10 * 1024 * 1024:
            raise ValueError("Expected a ZIP of at most 10 MiB")
        boundary = "nishtyak-" + uuid.uuid4().hex
        parts = []
        for name, value, maximum in (
            ("options", options, 65536),
            ("author_environment", author_environment, 262144),
        ):
            if value is not None:
                payload = self._json(value, maximum)
                parts.append(
                    (
                        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                    ).encode()
                    + payload
                    + b"\r\n"
                )
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                'filename="project.zip"\r\nContent-Type: application/zip\r\n\r\n'
            ).encode()
            + archive
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        return self._request(
            "POST", "/v1/projects", b"".join(parts), f"multipart/form-data; boundary={boundary}"
        )

    def job(self, job_id: str) -> dict:
        return self._request("GET", "/v1/jobs/" + self._id(job_id))

    def wait(self, job_id: str, timeout: float = 60, interval: float = 1) -> dict:
        if not 0 < timeout <= 3600 or not 0.1 <= interval <= 30:
            raise ValueError("Invalid polling interval or timeout")
        deadline = time.monotonic() + timeout
        while True:
            job = self.job(job_id)
            automation = job.get("automation")
            preparing = (
                automation.get("phase") in {"static", "assisting", "validating", "finalizing"}
                if automation is not None
                else any(
                    (job.get(key) or {}).get("status") in {"pending", "running"}
                    for key in ("automatic_review", "automatic_resolution")
                )
            )
            if job["status"] not in {"queued", "running"} and not preparing:
                return job
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Analysis is still pending; resume polling the same job")
            time.sleep(min(interval, remaining))

    def project(self, project_id: str) -> dict:
        return self._request("GET", self._project_path(project_id))

    def project_state(self, project_id: str) -> dict:
        return self._request("GET", self._project_path(project_id) + "/state")

    def source(self, project_id: str) -> bytes:
        return self._request("GET", self._project_path(project_id) + "/source", binary=True)

    def reanalyze(
        self, project_id: str, options: dict, author_environment: dict | None = None
    ) -> dict:
        body = {"options": options}
        if author_environment is not None:
            body["author_environment"] = author_environment
        return self._request("POST", self._project_path(project_id) + "/analysis", self._json(body))

    def decisions(self, project_id: str) -> dict:
        return self._request("GET", self._project_path(project_id) + "/decisions")

    def apply_target(
        self, project_id: str, python_version: str, platform: str, expected_revision: int
    ) -> dict:
        body = {
            "target": {"python_version": python_version, "platform": platform},
            "expected_revision": expected_revision,
        }
        return self._request("POST", self._project_path(project_id) + "/target", self._json(body))

    def replace_source(self, project_id: str, archive: bytes, expected_revision: int) -> dict:
        if not isinstance(archive, bytes) or not 0 < len(archive) <= 10 * 1024**2:
            raise ValueError("Expected a ZIP of at most 10 MiB")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Invalid decision revision")
        boundary = "nishtyak-" + uuid.uuid4().hex
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="expected_revision"\r\n\r\n'
                f"{expected_revision}\r\n--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; '
                'filename="project.zip"\r\nContent-Type: application/zip\r\n\r\n'
            ).encode()
            + archive
            + f"\r\n--{boundary}--\r\n".encode()
        )
        return self._request(
            "POST",
            self._project_path(project_id) + "/source",
            body,
            f"multipart/form-data; boundary={boundary}",
        )

    def save_decisions(self, project_id: str, decisions: dict, expected_revision: int) -> dict:
        body = {"decisions": decisions, "expected_revision": expected_revision}
        return self._request("PUT", self._project_path(project_id) + "/decisions", self._json(body))

    def plan(self, project_id: str, decisions: dict | None = None) -> dict:
        return self._request(
            "POST",
            self._project_path(project_id) + "/plan",
            self._json(decisions) if decisions else None,
        )

    def submit_clarifications(
        self,
        project_id: str,
        questionnaire_id: str,
        decisions: dict,
        expected_revision: int,
        notes: str = "",
    ) -> dict:
        body = {
            "questionnaire_id": questionnaire_id,
            "expected_revision": expected_revision,
            "decisions": decisions,
            "notes": notes,
        }
        return self._request(
            "POST", self._project_path(project_id) + "/clarifications", self._json(body)
        )

    def preparation(self, project_id: str) -> dict:
        return self._request("GET", self._project_path(project_id) + "/preparation")

    def graph(self, project_id: str) -> dict:
        return self._request("GET", self._project_path(project_id) + "/graph")

    def project_advice(
        self,
        project_id: str,
        feature: str = "explain",
        description: str = "",
        *,
        share_source: bool = False,
    ) -> dict:
        if share_source is not True:
            raise ValueError("Explicit source sharing is required for project advice")
        body = {"feature": feature, "description": description, "share_source": True}
        return self._request(
            "POST",
            self._project_path(project_id) + "/project-advice",
            self._json(body),
            timeout=max(self.timeout, 30),
        )

    def resolve(
        self, project_id: str, python_version: str, platform: str, decisions: dict | None = None
    ) -> dict:
        body = {
            "target": {"python_version": python_version, "platform": platform},
            "decisions": decisions,
        }
        return self._request(
            "POST",
            self._project_path(project_id) + "/resolve",
            self._json(body),
            timeout=max(self.timeout, 60),
        )

    def lock(self, project_id: str) -> dict:
        return self._request("GET", self._project_path(project_id) + "/lock")

    def bundle(
        self,
        project_id: str,
        decisions: dict | None = None,
        include_dockerfile: bool | None = None,
        lock_id: str | None = None,
    ) -> bytes:
        body = {"decisions": decisions, "include_dockerfile": include_dockerfile}
        if lock_id is not None:
            body["lock_id"] = lock_id
        return self._request(
            "POST", self._project_path(project_id) + "/bundle", self._json(body), binary=True
        )

    def package_safety(self, project_id: str, lock_id: str | None = None) -> dict:
        path = self._project_path(project_id) + "/package-safety"
        if lock_id is not None and (
            not isinstance(lock_id, str) or not re.fullmatch(r"[a-f0-9]{64}", lock_id)
        ):
            raise ValueError("Invalid dependency lock ID")
        try:
            report = self._request("POST", path, self._json({"lock_id": lock_id}))
        except LogicClientError as error:
            if error.status == 409 and error.code == "PACKAGE_MALICIOUS":
                raise LogicClientError(
                    409,
                    "PACKAGE_MALICIOUS",
                    "Обнаружена известная вредоносная зависимость. Выдача архива заблокирована.",
                ) from None
            raise LogicClientError(
                503,
                "PACKAGE_SAFETY_UNAVAILABLE",
                "Проверка зависимостей по OSV не завершена. Повторите попытку позже.",
            ) from None
        return self.validate_package_safety(report, project_id, lock_id)

    @staticmethod
    def validate_package_safety(report: object, project_id: str, lock_id: str | None) -> dict:
        def timestamp(value: object) -> bool:
            if not isinstance(value, str) or len(value) > 64:
                return False
            try:
                parsed = datetime.fromisoformat(
                    value[:-1] + "+00:00" if value.endswith("Z") else value
                )
                return parsed.utcoffset() == timedelta(0) and math.isfinite(parsed.timestamp())
            except (ValueError, OverflowError, OSError):
                return False

        def identifiers(value: object) -> bool:
            return (
                isinstance(value, list)
                and len(value) <= 2000
                and all(
                    isinstance(item, str)
                    and 0 < len(item) <= 200
                    and item.isascii()
                    and all(33 <= ord(char) <= 126 for char in item)
                    for item in value
                )
            )

        def valid() -> bool:
            if not isinstance(report, dict) or report.get("status") not in (
                "checked",
                "blocked",
                "unavailable",
            ):
                return False
            if (
                report.get("project_id") != project_id
                or "lock_id" not in report
                or report["lock_id"] != lock_id
                or report.get("source") != PACKAGE_SAFETY_SOURCE
                or not timestamp(report.get("checked_at"))
            ):
                return False
            packages = report.get("packages")
            if not isinstance(packages, list) or len(packages) > 2000:
                return False
            for package in packages:
                if not isinstance(package, dict):
                    return False
                name = package.get("name")
                version = package.get("version")
                status = package.get("status")
                if (
                    not isinstance(name, str)
                    or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,198}[A-Za-z0-9])?", name)
                    or (
                        version is not None
                        and (not isinstance(version, str) or not 0 < len(version) <= 200)
                    )
                    or "version" not in package
                    or status not in ("checked", "blocked", "unavailable")
                    or package.get("source") != PACKAGE_SAFETY_SOURCE
                    or not timestamp(package.get("checked_at"))
                    or not identifiers(package.get("advisory_ids"))
                    or not identifiers(package.get("malicious_ids"))
                ):
                    return False
                if report["status"] == "checked" and (
                    status != "checked" or package["malicious_ids"]
                ):
                    return False
            return True

        if not valid():
            raise LogicClientError(
                503,
                "PACKAGE_SAFETY_UNAVAILABLE",
                "Проверка зависимостей по OSV не завершена. Повторите попытку позже.",
            )
        return report

    def delete(self, project_id: str) -> None:
        self._request("DELETE", self._project_path(project_id))

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        content_type: str = "application/json",
        binary: bool = False,
        timeout: float | None = None,
    ) -> Any:
        headers = {
            "Authorization": "Bearer " + self._token,
            "X-Owner-ID": self.owner_id,
            "Accept": "application/zip" if binary else "application/json",
        }
        if body is not None:
            headers["Content-Type"] = content_type
        request = Request(self.base_url + path, body, headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout or self.timeout) as response:
                maximum = 80 * 1024 * 1024 if binary else 32 * 1024 * 1024
                payload = response.read(maximum + 1)
                if len(payload) > maximum:
                    raise LogicClientError(
                        502, "RESPONSE_LIMIT", "Service response exceeds its limit"
                    )
                if binary:
                    return payload
                if not payload:
                    return None
                value = json.loads(payload)
                if not isinstance(value, dict):
                    raise ValueError("Expected a service response object")
                return value
        except HTTPError as error:
            try:
                value = json.loads(error.read(16384))
                code, message, field = value["code"], value["message"], value.get("field")
                if not isinstance(code, str) or not isinstance(message, str):
                    raise ValueError("Invalid error")
            except (ValueError, KeyError, TypeError):
                code, message, field = "HTTP_ERROR", "Logic service request failed", None
            finally:
                error.close()
            raise LogicClientError(error.code, code, message, field) from None
        except (URLError, OSError):
            raise LogicClientError(
                503, "SERVICE_UNAVAILABLE", "Logic service is unavailable"
            ) from None
        except (ValueError, TypeError):
            raise LogicClientError(
                502, "RESPONSE_INVALID", "Invalid logic service response"
            ) from None

    @staticmethod
    def _json(value: dict, maximum: int = 262144) -> bytes:
        payload = json.dumps(
            value, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode("ascii")
        if len(payload) > maximum:
            raise ValueError("Request JSON exceeds its byte limit")
        return payload

    @staticmethod
    def _id(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
            raise ValueError("Invalid service object ID")
        return value

    def _project_path(self, project_id: str) -> str:
        return "/v1/projects/" + self._id(project_id)
