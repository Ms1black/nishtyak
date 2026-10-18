import io
import zipfile
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from nishtyak_core import ReproducibilityService, SnapshotBuilder
from nishtyak_core.jobs import AnalysisJobResult
from nishtyak_core.models import AnalysisOptions, UserDecisions
from nishtyak_service.api import create_app
from nishtyak_service.config import Settings
from nishtyak_service.dto import JobDTO
from nishtyak_service.errors import ServiceError
from nishtyak_service.service import LogicService
from nishtyak_web.mapping import analysis_status


def completed_project(tmp_path, source="import research_lib\n", *, automatic=False):
    settings = Settings(
        data_dir=tmp_path,
        api_token="summary-test-" + "x" * 32,
        automatic_resolution=automatic,
    )
    logic = LogicService(settings)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("main.py", source)
    options = AnalysisOptions(python_version="3.11")
    record = logic.repository.submit("alice", stream.getvalue(), options)
    logic.repository.claim()
    snapshot = SnapshotBuilder().from_mapping({"main.py": source})
    report = ReproducibilityService().analyze(snapshot, options)
    logic.repository.finish(record.job_id, AnalysisJobResult("succeeded", report))
    return logic, logic.repository.get_job("alice", record.job_id), report


def test_job_summary_uses_current_saved_dependency_decisions(tmp_path):
    logic, record, report = completed_project(tmp_path)
    initial = logic.get_job("alice", record.job_id)
    assert analysis_status(report.to_dict(), initial["status"], job=initial) == "partial"
    assert {"code": "DEPENDENCY_UNRESOLVED"} in initial["preparation_summary"]["findings"]

    decisions = UserDecisions(report.analysis_id, packages={"research_lib": "research-dist"})
    logic.repository.save_decisions(
        "alice", record.project_id, report.analysis_id, 0, asdict(decisions)
    )
    current = logic.get_job("alice", record.job_id)
    summary = current["preparation_summary"]
    assert summary["report"] == {"analysis_id": report.analysis_id}
    assert {"code": "DEPENDENCY_UNRESOLVED"} not in summary["findings"]
    assert analysis_status(report.to_dict(), current["status"], job=current) == "full"
    assert set(summary) == {"report", "state", "findings"}
    assert all(set(item) == {"code"} for item in summary["findings"])
    assert len(summary["findings"]) == len({item["code"] for item in summary["findings"]})
    JobDTO.model_validate(current)


def test_job_summary_includes_cached_verified_automatic_mapping(tmp_path):
    logic, record, report = completed_project(tmp_path)
    logic.repository.save_review(
        record,
        report.analysis_id,
        {
            "status": "ready",
            "packages": {
                "research_lib": {
                    "package": "research-dist",
                    "origin": "model_verification",
                    "verification": {"method": "published_wheel_directory"},
                }
            },
        },
    )
    job = logic.get_job("alice", record.job_id)
    assert analysis_status(report.to_dict(), job["status"], job=job) == "full"
    assert job["analysis"]["dependencies"][0]["category"] == "unresolved"


def test_job_summary_does_not_spend_plan_quota_or_trigger_network_work(tmp_path, monkeypatch):
    logic, record, _ = completed_project(tmp_path, automatic=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("Reading a job must not initiate preparation or network work")

    monkeypatch.setattr(logic, "plan", forbidden)
    monkeypatch.setattr(logic.automatic_review, "ensure", forbidden)
    monkeypatch.setattr(logic.automatic_resolution, "ensure", forbidden)
    monkeypatch.setattr(logic.provider_discovery, "ensure", forbidden)
    monkeypatch.setattr("nishtyak_core.locking.VersionResolver.resolve", forbidden)
    monkeypatch.setattr("nishtyak_core.package_safety.PackageSafetyClient.check", forbidden)
    for _ in range(30):
        logic.repository.consume_rate("alice", "plan", 30)
    for _ in range(35):
        assert logic.get_job("alice", record.job_id)["preparation_summary"]
    with pytest.raises(ServiceError) as error:
        logic.repository.consume_rate("alice", "plan", 30)
    assert error.value.code == "RATE_LIMIT"


def test_job_http_response_exposes_typed_summary_without_source_or_prose(tmp_path):
    logic, record, report = completed_project(tmp_path, 'print("private source text")\n')
    headers = {"Authorization": "Bearer " + logic.settings.api_token, "X-Owner-ID": "alice"}
    with TestClient(create_app(logic.settings, logic)) as client:
        response = client.get(f"/v1/jobs/{record.job_id}", headers=headers)
    assert response.status_code == 200, response.text
    summary = response.json()["preparation_summary"]
    assert summary["report"] == {"analysis_id": report.analysis_id}
    assert not {"files", "description", "source", "requirements", "decisions"} & summary.keys()
    assert all(set(item) == {"code"} for item in summary["findings"])


def test_job_summary_rechecks_access_after_read_only_preparation(tmp_path, monkeypatch):
    logic, record, _ = completed_project(tmp_path)
    prepare = logic._prepare

    def delete_after_preparing(*args, **kwargs):
        plan = prepare(*args, **kwargs)
        logic.repository.delete("alice", record.project_id)
        return plan

    monkeypatch.setattr(logic, "_prepare", delete_after_preparing)
    with pytest.raises(ServiceError) as error:
        logic.get_job("alice", record.job_id)
    assert error.value.code == "RESOURCE_NOT_FOUND"


def test_queued_and_failed_jobs_do_not_prepare_a_summary(tmp_path, monkeypatch):
    logic = LogicService(Settings(data_dir=tmp_path))
    record = logic.repository.submit("alice", b"source", AnalysisOptions())

    def forbidden(*args, **kwargs):
        raise AssertionError("Only succeeded jobs have an effective preparation")

    monkeypatch.setattr(logic, "_prepare", forbidden)
    assert "preparation_summary" not in logic.get_job("alice", record.job_id)
    logic.repository.claim()
    logic.repository.finish(record.job_id, AnalysisJobResult("failed", code="ARCHIVE_INVALID"))
    failed = logic.get_job("alice", record.job_id)
    assert failed["status"] == "failed"
    assert "preparation_summary" not in failed
