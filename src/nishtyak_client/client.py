from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


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

    def reanalyze(
        self, project_id: str, options: dict, author_environment: dict | None = None
    ) -> dict:
        body = {"options": options}
        if author_environment is not None:
            body["author_environment"] = author_environment
        return self._request("POST", self._project_path(project_id) + "/analysis", self._json(body))

    def decisions(self, project_id: str) -> dict:
        return self._request("GET", self._project_path(project_id) + "/decisions")

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
        include_dockerfile: bool = False,
        lock_id: str | None = None,
    ) -> bytes:
        body = {"decisions": decisions, "include_dockerfile": include_dockerfile}
        if lock_id is not None:
            body["lock_id"] = lock_id
        return self._request(
            "POST", self._project_path(project_id) + "/bundle", self._json(body), binary=True
        )

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
