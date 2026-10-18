import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nishtyak_service.api import create_app
from nishtyak_service.config import Settings
from nishtyak_service.dispatcher import Dispatcher
from nishtyak_service.execution import SubprocessExecutor
from nishtyak_service.service import LogicService
from nishtyak_service.storage import SQLiteJobRepository


@pytest.fixture
def running_service(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    settings = Settings(data_dir=tmp_path / "data", api_token="service-test-token-" + "x" * 32)
    repository = SQLiteJobRepository(settings)
    dispatcher = Dispatcher(repository, SubprocessExecutor(settings.job_policy), poll_interval=0.01)
    dispatcher.start()
    headers = {"Authorization": f"Bearer {settings.api_token}", "X-Owner-ID": "team-one"}
    try:
        with TestClient(create_app(settings, LogicService(settings, repository))) as client:
            yield client, headers, repository
    finally:
        dispatcher.stop()


def source_zip(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def wait_for_job(client, headers, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/v1/jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(0.04)
    raise AssertionError("The analysis worker did not complete")


def test_full_http_project_lifecycle(running_service):
    client, headers, repository = running_service
    source = Path(__file__).parent / "fixtures/local_project"
    archive = source_zip(
        {
            path.relative_to(source).as_posix(): path.read_bytes()
            for path in source.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
    )
    submitted = client.post(
        "/v1/projects", headers=headers, files={"file": ("project.zip", archive, "application/zip")}
    )
    assert submitted.status_code == 202, submitted.text
    project = submitted.json()
    other = {**headers, "X-Owner-ID": "other-team"}
    assert client.get(f"/v1/jobs/{project['job_id']}", headers=other).status_code == 404
    job = wait_for_job(client, headers, project["job_id"])
    assert job["status"] == "succeeded", job
    assert any(
        dependency["module"] == "helpers" and dependency["category"] == "local"
        for dependency in job["analysis"]["dependencies"]
    )
    project_url = f"/v1/projects/{project['project_id']}"
    assert client.get(project_url, headers=headers).json()["analysis"] == job["analysis"]
    decisions = {
        "analysis_id": job["analysis"]["analysis_id"],
        "run_spec": {"kind": "script", "target": "main.py"},
        "python_version": "3.11",
        "data_confirmed": True,
        "input_description": "data.csv: 2, 4, 6",
        "expected_output": "mean=4.00",
    }
    planned = client.post(project_url + "/plan", headers=headers, json=decisions)
    assert planned.status_code == 200, planned.text
    assert planned.json()["state"] == "ready_to_verify"
    bundle = client.post(project_url + "/bundle", headers=headers, json={"decisions": decisions})
    assert bundle.status_code == 200, bundle.text
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as exported:
        assert exported.read("project/main.py") == (source / "main.py").read_bytes()
        assert "mean=4.00" in exported.read("README.md").decode()
        assert "project/helpers.py" in exported.namelist()
    assert list(repository.work_directory(project["project_id"]).iterdir()) == []
    stale = client.post(
        project_url + "/plan", headers=headers, json={**decisions, "analysis_id": "0" * 20}
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "STALE_ANALYSIS"
    unavailable = client.post(project_url + "/advice", headers=headers, json={"feature": "explain"})
    assert unavailable.status_code == 200
    assert unavailable.json()["status"] == "unavailable"
    assert client.delete(project_url, headers=other).status_code == 404
    assert client.delete(project_url, headers=headers).status_code == 204
    assert client.get(project_url, headers=headers).status_code == 404
    assert client.get(f"/v1/jobs/{project['job_id']}", headers=headers).status_code == 404
    assert not repository.archive_path(project["project_id"]).exists()


def test_invalid_archive_is_a_persistent_failed_job(running_service):
    client, headers, repository = running_service
    response = client.post("/v1/projects", headers=headers, files={"file": ("bad.zip", b"not ZIP")})
    assert response.status_code == 202
    project = response.json()
    job = wait_for_job(client, headers, project["job_id"])
    assert job["status"] == "failed"
    assert job["error"]["code"] == "ARCHIVE_INVALID"
    reopened = SQLiteJobRepository(repository.settings)
    assert reopened.get_job("team-one", project["job_id"]).status == "failed"
    assert client.get(f"/v1/projects/{project['project_id']}", headers=headers).status_code == 409


def test_uploaded_source_is_not_executed_and_secret_blocks_export(running_service, tmp_path):
    client, headers, _ = running_service
    marker = tmp_path / "must-not-exist"
    credential = "sk-abcdefghijklmnopqrstuvwxyz012345"
    source = f"from pathlib import Path\nPath({str(marker)!r}).touch()\napi_key = {credential!r}\n"
    submitted = client.post(
        "/v1/projects",
        headers=headers,
        files={"file": ("secret.zip", source_zip({"main.py": source}))},
    ).json()
    job = wait_for_job(client, headers, submitted["job_id"])
    assert job["status"] == "succeeded", job
    assert not marker.exists()
    assert credential not in json.dumps(job)
    assert any(finding["code"] == "SECRET_DETECTED" for finding in job["analysis"]["findings"])
    response = client.post(
        f"/v1/projects/{submitted['project_id']}/bundle",
        headers=headers,
        json={"decisions": {"analysis_id": job["analysis"]["analysis_id"]}},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "PLAN_BLOCKED"
