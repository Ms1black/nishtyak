from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nishtyak_client import LogicClientError
from nishtyak_web.auth import WorkspaceIdentity
from nishtyak_web.config import WebSettings
from nishtyak_web.service import BrowserService

PROJECT_ID = "a" * 32
JOB_ID = "b" * 32


@pytest.fixture
def listing(tmp_path):
    identity = WorkspaceIdentity("author", "team", "local")
    job = {"project_id": PROJECT_ID, "job_id": JOB_ID, "status": "queued"}
    core = SimpleNamespace(job=Mock(), project_state=Mock())
    service = BrowserService(
        WebSettings(data_dir=tmp_path, service_token="test-token-" + "x" * 32), lambda scope: core
    )
    source = b"original source archive"
    service.store.create(identity.scope, job, "experiment.zip", "zip", len(source), source)
    return service, identity, core, job, source


@pytest.mark.parametrize("status", [404, 429, 502, 503, 504])
def test_backend_refresh_failure_preserves_project_and_files(listing, status):
    service, identity, core, job, source = listing
    core.job.side_effect = LogicClientError(status, "RESOURCE_NOT_FOUND", "Backend unavailable")
    core.project_state.side_effect = LogicClientError(404, "RESOURCE_NOT_FOUND", "Missing")
    artifact = b"previously prepared archive"
    service.store.save_archive(identity.scope, PROJECT_ID, json.dumps({}), artifact)

    for _ in range(2):
        projects = service.list(identity, identity.workspace)
        assert [project["id"] for project in projects] == [PROJECT_ID]
        assert json.loads(service.store.get(identity.scope, PROJECT_ID)["job"]) == job
        assert (service.store.root / PROJECT_ID / "source.zip").read_bytes() == source
        assert (service.store.root / PROJECT_ID / "bundle.zip").read_bytes() == artifact


@pytest.mark.parametrize("status", [401, 403])
def test_backend_authorization_failure_is_not_hidden(listing, status):
    service, identity, core, _, _ = listing
    core.job.side_effect = LogicClientError(status, "UNAUTHORIZED", "Unauthorized")
    with pytest.raises(LogicClientError) as error:
        service.list(identity, identity.workspace)
    assert error.value.status == status
    assert service.store.get(identity.scope, PROJECT_ID)["id"] == PROJECT_ID


def test_unavailable_backend_is_not_retried_for_every_cached_project(listing):
    service, identity, core, job, source = listing
    other = {**job, "project_id": "c" * 32, "job_id": "d" * 32}
    service.store.create(identity.scope, other, "second.zip", "zip", len(source), source)
    core.job.side_effect = LogicClientError(503, "BACKEND_UNAVAILABLE", "Unavailable")
    assert len(service.list(identity, identity.workspace)) == 2
    assert core.job.call_count == 1


def test_list_refreshes_completed_jobs_with_current_effective_status(listing):
    service, identity, core, job, _ = listing
    report = {
        "analysis_id": "current-analysis",
        "status": "complete",
        "dependencies": [{"module": "cv2", "category": "unresolved"}],
        "files": [],
    }
    old = {**job, "status": "succeeded", "analysis": report}
    service.store.update_job(identity.scope, PROJECT_ID, old)
    core.job.return_value = {
        **old,
        "preparation_summary": {
            "report": {"analysis_id": report["analysis_id"]},
            "state": "ready",
            "findings": [],
        },
    }
    assert service.list(identity, identity.workspace)[0]["analysis"] == "full"
    core.job.assert_called_once_with(JOB_ID)
    assert service.project(identity, PROJECT_ID)["project"]["analysis"] == "full"


def test_explicit_concurrent_deletion_does_not_reappear_in_cached_list(listing):
    service, identity, core, _, _ = listing

    def refresh(job_id):
        service.store.delete(identity.scope, PROJECT_ID)
        raise LogicClientError(404, "RESOURCE_NOT_FOUND", "Deleted explicitly")

    core.job.side_effect = refresh
    core.project_state.side_effect = LogicClientError(404, "RESOURCE_NOT_FOUND", "Deleted")
    assert service.list(identity, identity.workspace) == []
