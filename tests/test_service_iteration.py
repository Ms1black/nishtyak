from __future__ import annotations

import io
import json
import sys
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nishtyak_core import ArchiveReader, ReproducibilityService
from nishtyak_core.advisors import ProjectAdvisoryResult, SafeProjectContext
from nishtyak_core.jobs import AnalysisJobResult
from nishtyak_core.locking import DependencyLock, _lock_id, resolution_input_id
from nishtyak_core.models import AnalysisOptions
from nishtyak_service.api import create_app
from nishtyak_service.config import Settings
from nishtyak_service.project_advice_jobs import ProjectAdviceJobRunner
from nishtyak_service.service import LogicService
from nishtyak_service.storage import SQLiteJobRepository

TOKEN = "iteration-test-" + "a" * 40
HEADERS = {"Authorization": "Bearer " + TOKEN, "X-Owner-ID": "alice"}


@pytest.fixture
def project(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", api_token=TOKEN, runner="subprocess")
    repository = SQLiteJobRepository(settings)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("experiment.py", "from helpers import compute\nprint(compute())\n")
        archive.writestr("helpers.py", "def compute():\n    return 42\n")
    snapshot = ArchiveReader().from_bytes(stream.getvalue())
    record = repository.submit("alice", stream.getvalue(), AnalysisOptions())
    repository.claim()
    report = ReproducibilityService().analyze(snapshot)
    repository.finish(record.job_id, AnalysisJobResult("succeeded", report))
    logic = LogicService(settings, repository)
    with TestClient(create_app(settings, logic)) as client:
        yield client, logic, record.project_id, report, snapshot


def test_default_flow_needs_no_main_decision(project):
    client, _, identifier, report, _ = project
    prefix = f"/v1/projects/{identifier}"
    preparation = client.get(prefix + "/preparation", headers=HEADERS)
    assert preparation.status_code == 200
    plan = preparation.json()["plan"]
    assert plan["run_spec"]["target"] == "experiment.py"
    assert plan["run_spec_origin"] == "inferred"
    assert plan["decisions"]["run_spec"] is None
    assert (
        client.post(prefix + "/plan", headers=HEADERS).json()["report"]["analysis_id"]
        == report.analysis_id
    )
    graph = client.get(prefix + "/graph", headers=HEADERS)
    assert graph.status_code == 200 and "helpers.py" in graph.text
    bundle = client.post(prefix + "/bundle", json={}, headers=HEADERS)
    assert bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        readme = archive.read("README.md").decode()
        assert "experiment.py" in readme and "inferred" in readme.lower()
        assert "project/helpers.py" in archive.namelist()


def test_locks_persist_and_reanalysis_invalidates_them(project, monkeypatch):
    client, logic, identifier, report, _ = project
    prefix = f"/v1/projects/{identifier}"

    def resolve(resolver, plan, target):
        input_id = resolution_input_id(plan, target)
        return DependencyLock(_lock_id(input_id, ""), report.analysis_id, input_id, target, "", ())

    monkeypatch.setattr("nishtyak_service.service.VersionResolver.resolve", resolve)
    result = client.post(
        prefix + "/resolve",
        headers=HEADERS,
        json={"target": {"python_version": "3.11", "platform": "linux-x86_64"}},
    )
    assert result.status_code == 200, result.text
    lock = result.json()
    assert client.get(prefix + "/lock", headers=HEADERS).json() == lock
    reopened = SQLiteJobRepository(logic.settings)
    assert reopened.read_lock("alice", identifier).id == lock["id"]
    bundle = client.post(prefix + "/bundle", headers=HEADERS, json={"lock_id": lock["id"]})
    assert bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert "requirements.lock.txt" in archive.namelist()
        assert json.loads(archive.read("dependency-lock.json"))["id"] == lock["id"]
    replacement = client.post(
        prefix + "/resolve",
        headers=HEADERS,
        json={"target": {"python_version": "3.11", "platform": "macos-arm64"}},
    )
    assert replacement.status_code == 200
    stale = client.post(prefix + "/bundle", headers=HEADERS, json={"lock_id": lock["id"]})
    assert stale.status_code == 409 and stale.json()["code"] == "LOCK_STALE"
    client.post(prefix + "/analysis", headers=HEADERS, json={"options": {}})
    assert not (reopened.archive_path(identifier).parent / "dependency-lock.json").exists()


def test_other_owner_cannot_read_graph_or_lock_or_run_advisor(project):
    client, _, identifier, _, _ = project
    headers = {**HEADERS, "X-Owner-ID": "mallory"}
    prefix = f"/v1/projects/{identifier}"
    for suffix in ("preparation", "graph", "lock"):
        assert client.get(prefix + "/" + suffix, headers=headers).status_code == 404
    response = client.post(
        prefix + "/project-advice",
        headers=headers,
        json={"feature": "entrypoint", "share_source": True},
    )
    assert response.status_code == 404


def test_project_advice_requires_explicit_sharing_and_no_key_is_honest(project, monkeypatch):
    client, _, identifier, _, _ = project
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    path = f"/v1/projects/{identifier}/project-advice"
    assert client.post(path, headers=HEADERS, json={}).status_code == 422
    response = client.post(
        path, headers=HEADERS, json={"feature": "entrypoint", "share_source": True}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["suggestions"] == []


class Probe(ProjectAdviceJobRunner):
    def __init__(self, result, timeout=1):
        super().__init__(timeout)
        self.result = result

    def _command(self, destination):
        return [sys.executable, "-I", "-c", self.result, str(destination)]


def test_project_advice_subprocess_checks_context_and_deadline(project, monkeypatch):
    _, _, _, report, snapshot = project
    monkeypatch.setenv("DEEPSEEK_API_KEY", "a-test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    context = SafeProjectContext.from_project(report, snapshot)
    result = ProjectAdvisoryResult(report.analysis_id, "explain", "ok").to_dict()
    code = (
        "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(" + repr(json.dumps(result)) + ")"
    )
    assert Probe(code).run(context)["status"] == "ok"
    bad = replace(context, analysis_id="wrong-analysis")
    assert Probe(code).run(bad)["status"] == "unavailable"
    assert Probe("import time; time.sleep(20)", 0.1).run(context)["status"] == "unavailable"


@pytest.mark.parametrize("explicit,expected", [(None, "3.12"), ("3.10", "3.10")])
def test_upload_uses_author_profile_unless_explicitly_selected(project, explicit, expected):
    client, logic, identifier, _, _ = project
    options = {} if explicit is None else {"python_version": explicit}
    response = client.post(
        "/v1/projects",
        headers=HEADERS,
        files={"file": ("fixture.zip", logic.repository.archive_path(identifier).read_bytes())},
        data={
            "options": json.dumps(options),
            "author_environment": json.dumps(
                {
                    "python_version": "3.12.1",
                    "platform": "Linux",
                    "distributions": [],
                }
            ),
        },
    )
    assert response.status_code == 202
    stored = logic.repository.get_project("alice", response.json()["project_id"])
    assert stored.options.python_version == expected
