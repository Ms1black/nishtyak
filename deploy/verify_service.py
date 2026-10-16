#!/usr/bin/env python3
"""Repeatable HTTP smoke test against an already running internal logic service."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass

SOURCES = {
    "main.py": (
        b"from statistics import mean\n"
        b"from helpers import load_values\n"
        b"print(f'mean={mean(load_values()):.2f}')\n"
    ),
    "helpers.py": (
        b"import csv\n"
        b"from pathlib import Path\n\n"
        b"def load_values():\n"
        b"    with Path(__file__).with_name('data.csv').open() as source:\n"
        b"        return [float(row['value']) for row in csv.DictReader(source)]\n"
    ),
    "data.csv": b"value\n2\n4\n6\n",
}


class VerificationFailed(Exception):
    pass


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


@dataclass(frozen=True)
class Response:
    status: int
    content: bytes
    content_type: str

    def json(self) -> dict:
        try:
            value = json.loads(self.content)
        except (ValueError, UnicodeError, RecursionError):
            raise VerificationFailed("response_json_invalid") from None
        if not isinstance(value, dict):
            raise VerificationFailed("response_json_invalid")
        return value


class ServiceVerifier:
    def __init__(self, base_url: str, token: str) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise VerificationFailed("url_invalid")
        if not re.fullmatch(r"[!-~]{32,512}", token):
            raise VerificationFailed("service_token_required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.owner = f"http-smoke-{uuid.uuid4().hex}"
        self.foreign_owner = f"http-smoke-{uuid.uuid4().hex}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirects())
        self.projects: list[tuple[str, str]] = []
        self.checks: dict[str, bool] = {}
        self.failures: list[str] = []
        self.polls: dict[str, int] = {}

    def check(self, name: str, passed: bool) -> None:
        self.checks[name] = bool(passed)
        if not passed:
            raise VerificationFailed(name)

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: bytes | dict | None = None,
        owner: str | None = None,
        authenticated: bool = True,
        content_type: str | None = None,
        max_bytes: int = 8 * 1024**2,
        timeout: float = 8,
    ) -> Response:
        headers = {}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.token}"
        if owner is not None:
            headers["X-Owner-ID"] = owner
        if isinstance(payload, dict):
            payload = json.dumps(payload).encode()
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            self.base_url + path, data=payload, headers=headers, method=method
        )
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (OSError, ValueError):
            raise VerificationFailed("http_request_failed") from None
        try:
            content = response.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise VerificationFailed("http_response_limit")
            return Response(response.status, content, response.headers.get_content_type())
        finally:
            response.close()

    def run(self) -> dict:
        try:
            self.verify_health()
            self.verify_project()
            self.verify_invalid_archive()
        except VerificationFailed as error:
            self.failures.append(str(error))
        except (KeyboardInterrupt, SystemExit):
            self.failures.append("interrupted")
        except Exception:
            self.failures.append("verification_error")
        finally:
            self.cleanup()
        return {
            "schema_version": 1,
            "status": "passed" if not self.failures else "failed",
            "checks": self.checks,
            "polls": self.polls,
            "failures": self.failures,
        }

    def verify_health(self) -> None:
        health = self.request("GET", "/healthz", authenticated=False)
        self.check("health", health.status == 200 and health.json() == {"status": "ok"})
        unauthorized = self.request("GET", "/readyz", authenticated=False)
        self.check(
            "authentication",
            unauthorized.status == 401 and unauthorized.json().get("code") == "UNAUTHORIZED",
        )
        ready = self.request("GET", "/readyz")
        self.check("ready", ready.status == 200 and ready.json() == {"status": "ready"})

    def submit(self, archive: bytes) -> tuple[str, str]:
        boundary = f"nishtyak-smoke-{uuid.uuid4().hex}"
        body = (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="options"\r\n\r\n'
                '{"python_version":"3.11"}\r\n'
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="trusted-smoke.zip"\r\n'
                "Content-Type: application/zip\r\n\r\n"
            ).encode()
            + archive
            + f"\r\n--{boundary}--\r\n".encode()
        )
        response = self.request(
            "POST",
            "/v1/projects",
            payload=body,
            owner=self.owner,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        self.check("upload_accepted", response.status == 202)
        project = response.json()
        project_id, job_id = project.get("project_id"), project.get("job_id")
        self.check(
            "server_identifiers",
            isinstance(project_id, str)
            and isinstance(job_id, str)
            and re.fullmatch(r"[a-f0-9]{32}", project_id) is not None
            and re.fullmatch(r"[a-f0-9]{32}", job_id) is not None,
        )
        self.projects.append((project_id, job_id))
        self.check("upload_status", project.get("status") in {"queued", "running"})
        return project_id, job_id

    def wait_for_job(self, job_id: str, label: str) -> dict:
        deadline = time.monotonic() + 30
        for attempt in range(1, 31):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            response = self.request(
                "GET", f"/v1/jobs/{job_id}", owner=self.owner, timeout=min(8, remaining)
            )
            self.polls[label] = attempt
            self.check(f"{label}_poll", response.status == 200)
            result = response.json()
            if result.get("status") not in {"queued", "running"}:
                return result
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        raise VerificationFailed(f"{label}_poll_timeout")

    def verify_project(self) -> None:
        source = io.BytesIO()
        with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in SOURCES.items():
                archive.writestr(name, content)
        project_id, job_id = self.submit(source.getvalue())
        foreign = self.request("GET", f"/v1/jobs/{job_id}", owner=self.foreign_owner)
        self.check("foreign_job_hidden", foreign.status == 404)
        result = self.wait_for_job(job_id, "analysis")
        self.check("analysis_succeeded", result.get("status") == "succeeded")
        report = result.get("analysis")
        self.check("analysis_report", isinstance(report, dict))
        dependencies = report.get("dependencies", [])
        self.check(
            "local_helper",
            any(
                item.get("module") == "helpers" and item.get("category") == "local"
                for item in dependencies
            ),
        )
        stdlib = {item.get("module") for item in dependencies if item.get("category") == "stdlib"}
        self.check("stdlib_dependencies", {"statistics", "csv", "pathlib"} <= stdlib)
        project_url = f"/v1/projects/{project_id}"
        foreign = self.request("GET", project_url, owner=self.foreign_owner)
        self.check("foreign_project_hidden", foreign.status == 404)
        saved = self.request("GET", project_url, owner=self.owner)
        self.check("saved_report", saved.status == 200 and saved.json().get("analysis") == report)
        decisions = {
            "analysis_id": report["analysis_id"],
            "run_spec": {"kind": "script", "target": "main.py"},
            "python_version": "3.11",
            "data_confirmed": True,
            "description": "Trusted HTTP verification fixture",
            "input_description": "data.csv contains the values 2, 4, 6",
            "expected_output": "mean=4.00",
        }
        plan = self.request("POST", project_url + "/plan", owner=self.owner, payload=decisions)
        self.check(
            "plan_ready", plan.status == 200 and plan.json().get("state") == "ready_to_verify"
        )
        stale_id = "0" * 20 if report["analysis_id"] != "0" * 20 else "f" * 20
        stale = self.request(
            "POST",
            project_url + "/plan",
            owner=self.owner,
            payload={**decisions, "analysis_id": stale_id},
        )
        self.check(
            "stale_decisions", stale.status == 409 and stale.json().get("code") == "STALE_ANALYSIS"
        )
        bundle = self.request(
            "POST",
            project_url + "/bundle",
            owner=self.owner,
            payload={"decisions": decisions, "include_dockerfile": False},
            max_bytes=16 * 1024**2,
        )
        self.check(
            "bundle_downloaded", bundle.status == 200 and bundle.content_type == "application/zip"
        )
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as exported:
            self.check(
                "bundle_limits",
                len(exported.infolist()) <= 128
                and sum(item.file_size for item in exported.infolist()) <= 32 * 1024**2,
            )
            for name, content in SOURCES.items():
                info = exported.getinfo(f"project/{name}")
                self.check(
                    f"bundle_{name}",
                    info.file_size == len(content) and exported.read(info) == content,
                )
            readme = exported.getinfo("README.md")
            self.check("bundle_readme_limit", readme.file_size <= 1024**2)
            self.check("bundle_expected_output", b"mean=4.00" in exported.read(readme))

    def verify_invalid_archive(self) -> None:
        project_id, job_id = self.submit(b"This is not a ZIP archive")
        result = self.wait_for_job(job_id, "invalid_archive")
        error = result.get("error") or {}
        self.check(
            "invalid_archive_failed",
            result.get("status") == "failed" and error.get("code") == "ARCHIVE_INVALID",
        )
        project = self.request("GET", f"/v1/projects/{project_id}", owner=self.owner)
        self.check("failed_project_unavailable", project.status == 409)

    def cleanup(self) -> None:
        for index, (project_id, job_id) in enumerate(reversed(self.projects), start=1):
            try:
                deleted = self.request("DELETE", f"/v1/projects/{project_id}", owner=self.owner)
                self.check(f"cleanup_{index}", deleted.status == 204)
                project = self.request("GET", f"/v1/projects/{project_id}", owner=self.owner)
                job = self.request("GET", f"/v1/jobs/{job_id}", owner=self.owner)
                self.check(f"revoked_project_{index}", project.status == 404)
                self.check(f"revoked_job_{index}", job.status == 404)
            except VerificationFailed as error:
                self.failures.append(str(error))
            except Exception:
                self.failures.append("cleanup_failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    arguments = parser.parse_args()
    try:
        summary = ServiceVerifier(arguments.url, os.environ.get("NISHTYAK_SERVICE_TOKEN", "")).run()
    except VerificationFailed as error:
        summary = {"schema_version": 1, "status": "failed", "checks": {}, "failures": [str(error)]}
    except Exception:
        summary = {
            "schema_version": 1,
            "status": "failed",
            "checks": {},
            "failures": ["configuration_error"],
        }
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
