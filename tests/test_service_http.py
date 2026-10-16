from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nishtyak_core.advisors.models import AdvisoryResult
from nishtyak_core.models import AnalysisOptions, AnalysisReport, PackagePlan, UserDecisions
from nishtyak_service.api import create_app
from nishtyak_service.config import Settings
from nishtyak_service.errors import ServiceError
from nishtyak_service.middleware import RequestBoundaryMiddleware

TOKEN = "test-service-token-" + "a" * 32
PROJECT = "a" * 32
JOB = "b" * 32
ANALYSIS = "c" * 20
HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-Owner-ID": "owner:42"}
REPORT = AnalysisReport(ANALYSIS, "f" * 64, AnalysisOptions(), ())


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.is_ready = True
        self.error: Exception | None = None
        self.called_in_threadpool = False

    def _call(self, *values: Any) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.called_in_threadpool = True
        self.calls.append(values)
        if self.error:
            raise self.error

    def ready(self) -> bool:
        self._call("ready")
        return self.is_ready

    def submit(self, owner: str, archive: bytes, options: AnalysisOptions) -> dict:
        self._call("submit", owner, archive, options)
        return {"project_id": PROJECT, "job_id": JOB, "status": "queued"}

    def get_job(self, owner: str, job_id: str) -> dict:
        self._call("get_job", owner, job_id)
        return {
            "project_id": PROJECT,
            "job_id": job_id,
            "status": "queued",
            "created_at": 1000.0,
            "expires_at": 4600.0,
        }

    def get_project(self, owner: str, project_id: str) -> dict:
        self._call("get_project", owner, project_id)
        return {"project_id": project_id, "analysis": REPORT.to_dict()}

    def delete(self, owner: str, project_id: str) -> None:
        self._call("delete", owner, project_id)

    def plan(self, owner: str, project_id: str, decisions: UserDecisions) -> dict:
        self._call("plan", owner, project_id, decisions)
        return asdict(PackagePlan(REPORT, (), decisions.run_spec, "3.11", (), (), (), "draft"))

    def bundle(
        self, owner: str, project_id: str, decisions: UserDecisions, include_dockerfile: bool
    ) -> bytes:
        self._call("bundle", owner, project_id, decisions, include_dockerfile)
        return b"PK-fake-bundle"

    def advice(self, owner: str, project_id: str, feature: str, description: str) -> dict:
        self._call("advice", owner, project_id, feature, description)
        return AdvisoryResult(ANALYSIS, feature, "ok").to_dict()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, api_token=TOKEN, runner="subprocess")


@pytest.fixture
def facade() -> FakeService:
    return FakeService()


@pytest.fixture
def client(settings, facade):
    with TestClient(create_app(settings, facade), raise_server_exceptions=False) as test_client:
        yield test_client


def test_health_is_minimal_and_unauthenticated(client, facade) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert not facade.calls


def test_readiness_requires_service_token_but_no_owner(client, facade) -> None:
    assert client.get("/readyz").status_code == 401
    response = client.get("/readyz", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.json() == {"status": "ready"}
    facade.is_ready = False
    assert client.get("/readyz", headers=HEADERS).status_code == 503
    assert facade.called_in_threadpool


@pytest.mark.parametrize("token", ["", "x" * 31])
def test_app_requires_valid_service_token(settings, token) -> None:
    with pytest.raises(ValueError, match="token"):
        create_app(replace(settings, api_token=token), FakeService())


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {TOKEN}"}],
)
def test_service_authentication(client, facade, headers) -> None:
    response = client.post("/v1/projects", headers=headers, content=b"invalid")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert not facade.calls


@pytest.mark.parametrize("owner", [None, "", "has space", "../other", "a" * 129])
def test_owner_header_is_required_and_bounded(client, facade, owner) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if owner is not None:
        headers["X-Owner-ID"] = owner
    assert client.get(f"/v1/jobs/{JOB}", headers=headers).status_code == 400
    assert not facade.calls


def test_duplicate_security_headers_are_rejected(client) -> None:
    response = client.get(
        f"/v1/jobs/{JOB}",
        headers=[("Authorization", f"Bearer {TOKEN}"), ("Authorization", f"Bearer {TOKEN}")],
    )
    assert response.status_code == 401
    response = client.get(
        f"/v1/jobs/{JOB}",
        headers=[
            ("Authorization", f"Bearer {TOKEN}"),
            ("X-Owner-ID", "one"),
            ("X-Owner-ID", "two"),
        ],
    )
    assert response.status_code == 400


def test_upload_ignores_filename_and_uses_typed_options(client, facade) -> None:
    response = client.post(
        "/v1/projects",
        headers=HEADERS,
        files={"file": ("../../etc/project.zip", b"zip-content", "application/zip")},
        data={"options": json.dumps({"source_roots": ["src"], "max_files": 20})},
    )
    assert response.status_code == 202
    assert response.json() == {"project_id": PROJECT, "job_id": JOB, "status": "queued"}
    name, owner, archive, options = facade.calls[0]
    assert (name, owner, archive) == ("submit", "owner:42", b"zip-content")
    assert options.source_roots == ("src",)
    assert options.max_files == 20
    assert facade.called_in_threadpool


def test_upload_defaults_options(client, facade) -> None:
    response = client.post("/v1/projects", headers=HEADERS, files={"file": ("p.zip", b"zip")})
    assert response.status_code == 202
    assert facade.calls[0][3] == AnalysisOptions(python_version_auto=True)


@pytest.mark.parametrize(
    "options",
    [
        "{bad json",
        '{"max_files": true}',
        '{"max_files": "10"}',
        '{"max_files": 1001}',
        '{"max_source_bytes": NaN}',
        '{"unknown": "private-user-value"}',
        '{"source_roots": ["' + "x" * 1025 + '"]}',
    ],
)
def test_upload_rejects_invalid_options_without_echo(client, facade, options) -> None:
    response = client.post(
        "/v1/projects",
        headers=HEADERS,
        files={"file": ("p.zip", b"zip")},
        data={"options": options},
    )
    assert response.status_code == 422
    assert "private-user-value" not in response.text
    assert not facade.calls


def test_upload_rejects_unknown_fields_and_multiple_files(client, facade) -> None:
    response = client.post(
        "/v1/projects",
        headers=HEADERS,
        files={"file": ("p.zip", b"zip")},
        data={"unexpected": "hello"},
    )
    assert response.status_code == 422
    response = client.post(
        "/v1/projects",
        headers=HEADERS,
        files=[("file", ("a.zip", b"a")), ("file", ("b.zip", b"b"))],
    )
    assert response.status_code == 400
    assert not facade.calls


def test_upload_requires_file(client, facade) -> None:
    response = client.post("/v1/projects", headers=HEADERS, data={"file": "plain-text"})
    assert response.status_code == 422
    assert not facade.calls


def test_upload_enforces_actual_archive_bytes(settings, facade) -> None:
    client = TestClient(create_app(replace(settings, max_archive_bytes=10), facade))
    response = client.post("/v1/projects", headers=HEADERS, files={"file": ("a.zip", b"x" * 11)})
    assert response.status_code == 413
    assert not facade.calls


def test_lookup_and_delete_pass_authenticated_owner(client, facade) -> None:
    assert client.get(f"/v1/projects/{PROJECT}", headers=HEADERS).status_code == 200
    assert client.get(f"/v1/jobs/{JOB}", headers=HEADERS).status_code == 200
    deleted = client.delete(f"/v1/projects/{PROJECT}", headers=HEADERS)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert facade.calls == [
        ("get_project", "owner:42", PROJECT),
        ("get_job", "owner:42", JOB),
        ("delete", "owner:42", PROJECT),
    ]


def test_plan_and_bundle_use_core_decisions(client, facade) -> None:
    decisions = {
        "analysis_id": ANALYSIS,
        "run_spec": {"target": "main.py", "args": [""]},
        "data_confirmed": True,
    }
    assert (
        client.post(f"/v1/projects/{PROJECT}/plan", headers=HEADERS, json=decisions).status_code
        == 200
    )
    assert isinstance(facade.calls[-1][3], UserDecisions)
    assert facade.calls[-1][3].run_spec.args == ("",)
    response = client.post(
        f"/v1/projects/{PROJECT}/bundle",
        headers=HEADERS,
        json={"decisions": decisions, "include_dockerfile": True},
    )
    assert response.content == b"PK-fake-bundle"
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert (
        response.headers["content-disposition"] == 'attachment; filename="reproducible-project.zip"'
    )
    assert facade.calls[-1][-1] is True


@pytest.mark.parametrize(
    "changes",
    [
        {"analysis_id": "wrong"},
        {"data_confirmed": 1},
        {"data_confirmed": "true"},
        {"description": "x" * 8193},
        {"packages": {"module": 123}},
        {"packages": {str(i): "v" for i in range(1001)}},
        {"excluded_files": ["x" * 1025]},
        {"run_spec": {"target": "main.py", "args": ["x"] * 65}},
        {"unexpected": "private-user-value"},
    ],
)
def test_decisions_are_strict_and_bounded(client, facade, changes) -> None:
    response = client.post(
        f"/v1/projects/{PROJECT}/plan",
        headers=HEADERS,
        json={"analysis_id": ANALYSIS, **changes},
    )
    assert response.status_code == 422
    assert "private-user-value" not in response.text
    assert not facade.calls


def test_invalid_identifiers_fail_before_service(client, facade) -> None:
    response = client.get("/v1/projects/not-an-id", headers=HEADERS)
    assert response.status_code == 422
    assert not facade.calls


def test_advice_has_bounded_known_features(client, facade) -> None:
    response = client.post(
        f"/v1/projects/{PROJECT}/advice",
        headers=HEADERS,
        json={"feature": "packages", "description": "A scientific project"},
    )
    assert response.status_code == 200
    assert facade.calls == [("advice", "owner:42", PROJECT, "packages", "A scientific project")]
    response = client.post(
        f"/v1/projects/{PROJECT}/advice", headers=HEADERS, json={"feature": "execute"}
    )
    assert response.status_code == 422


def test_facade_errors_have_safe_shapes_and_retry_header(client, facade) -> None:
    facade.error = ServiceError(429, "OWNER_LIMIT", "Owner quota reached")
    response = client.get(f"/v1/projects/{PROJECT}", headers=HEADERS)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "2"
    assert response.json() == {"code": "OWNER_LIMIT", "message": "Owner quota reached"}


def test_unavailable_errors_include_retry_delay(client, facade) -> None:
    facade.error = ServiceError(503, "STORAGE_UNAVAILABLE", "Storage is unavailable")
    response = client.get(f"/v1/projects/{PROJECT}", headers=HEADERS)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"


def test_unexpected_errors_do_not_expose_paths_or_tracebacks(client, facade) -> None:
    facade.error = RuntimeError("/private/path secret-token")
    response = client.get(f"/v1/projects/{PROJECT}", headers=HEADERS)
    assert response.status_code == 500
    assert response.json() == {"code": "INTERNAL_ERROR", "message": "Internal service error"}


def test_openapi_requires_token_and_describes_auth_upload_and_owner(client) -> None:
    assert client.get("/openapi.json").status_code == 401
    response = client.get("/openapi.json", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    schema = response.json()
    upload = schema["paths"]["/v1/projects"]["post"]
    assert upload["security"] == [{"ServiceToken": []}]
    assert upload["requestBody"]["content"]["multipart/form-data"]["schema"]["required"] == ["file"]
    assert any(parameter["name"] == "X-Owner-ID" for parameter in upload["parameters"])
    for path, method, model in [
        ("/v1/projects/{project_id}", "get", "ProjectReportDTO"),
        ("/v1/jobs/{job_id}", "get", "JobDTO"),
        ("/v1/projects/{project_id}/plan", "post", "PackagePlan"),
        ("/v1/projects/{project_id}/advice", "post", "AdvisoryResult"),
    ]:
        operation = schema["paths"][path][method]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{model}"
        }
        assert "507" in operation["responses"]
        assert "Retry-After" in operation["responses"]["503"]["headers"]
    assert client.get("/docs", headers=HEADERS).status_code == 404


def test_browser_cors_is_not_enabled(client) -> None:
    response = client.options(
        "/v1/projects", headers={**HEADERS, "Origin": "https://browser.example"}
    )
    assert "access-control-allow-origin" not in response.headers


def make_scope(path="/v1/projects/" + PROJECT, *, headers=None, method="POST") -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": method,
        "path": path,
        "headers": headers
        if headers is not None
        else [(b"authorization", f"Bearer {TOKEN}".encode()), (b"x-owner-id", b"owner:42")],
    }


async def finish_app(scope, receive, send) -> None:
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def test_authentication_precedes_any_body_read() -> None:
    async def scenario() -> None:
        called = False

        async def receive():
            nonlocal called
            called = True
            raise AssertionError("Unauthenticated body must not be read")

        messages = []

        async def send(message):
            messages.append(message)

        middleware = RequestBoundaryMiddleware(finish_app, api_token=TOKEN, max_archive_bytes=10)
        await middleware(make_scope(headers=[]), receive, send)
        assert messages[0]["status"] == 401
        assert not called

    asyncio.run(scenario())


@pytest.mark.parametrize("content_length", [None, b"1"])
def test_chunked_body_uses_actual_byte_limit(content_length) -> None:
    async def scenario() -> None:
        messages = []
        chunks = iter([b"1234", b"5678"])

        async def receive():
            return {"type": "http.request", "body": next(chunks), "more_body": True}

        async def send(message):
            messages.append(message)

        middleware = RequestBoundaryMiddleware(
            finish_app, api_token=TOKEN, max_archive_bytes=10, json_request_bytes=6
        )
        scope = make_scope()
        if content_length is not None:
            scope["headers"].append((b"content-length", content_length))
        await middleware(scope, receive, send)
        assert messages[0]["status"] == 413
        assert middleware._active == 0

    asyncio.run(scenario())


def test_body_timeout_is_bounded_and_releases_capacity() -> None:
    async def scenario() -> None:
        messages = []

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        middleware = RequestBoundaryMiddleware(
            finish_app, api_token=TOKEN, max_archive_bytes=10, body_timeout_seconds=0.01
        )
        await middleware(make_scope(), receive, send)
        assert messages[0]["status"] == 408
        assert middleware._active == 0

    asyncio.run(scenario())


def test_capacity_rejects_concurrent_request_without_reading_its_body() -> None:
    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def busy_app(scope, receive, send):
            started.set()
            await release.wait()
            await finish_app(scope, receive, send)

        async def empty_receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def forbidden_receive():
            raise AssertionError("Rejected request body must not be read")

        first_messages, second_messages = [], []

        async def first_send(message):
            first_messages.append(message)

        async def second_send(message):
            second_messages.append(message)

        middleware = RequestBoundaryMiddleware(
            busy_app, api_token=TOKEN, max_archive_bytes=10, max_http_requests=1
        )
        task = asyncio.create_task(middleware(make_scope(), empty_receive, first_send))
        await started.wait()
        await middleware(make_scope(), forbidden_receive, second_send)
        assert second_messages[0]["status"] == 429
        release.set()
        await task
        assert middleware._active == 0

    asyncio.run(scenario())
