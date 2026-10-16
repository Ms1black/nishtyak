from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nishtyak_core import ArchiveReader, ReproducibilityService
from nishtyak_core.advisors import ProjectAdvisoryResult
from nishtyak_core.advisors.project_agent import ProjectAdvisorySuggestion
from nishtyak_core.jobs import AnalysisJobResult
from nishtyak_core.models import AnalysisOptions, UserDecisions
from nishtyak_service.api import create_app
from nishtyak_service.automatic_review import AutomaticProjectReview
from nishtyak_service.config import Settings
from nishtyak_service.errors import ServiceError
from nishtyak_service.project_advice_jobs import ProjectAdviceJobRunner
from nishtyak_service.service import LogicService
from nishtyak_service.storage import SQLiteJobRepository

TOKEN = "automatic-review-test-" + "x" * 32
HEADERS = {"Authorization": "Bearer " + TOKEN, "X-Owner-ID": "alice"}
DESCRIPTION = "experiment.py prints the result returned by helpers.compute()."


def create_project(repository, owner="alice"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("experiment.py", "from helpers import compute\nprint(compute())\n")
        archive.writestr("helpers.py", "def compute():\n    return 42\n")
    source = stream.getvalue()
    record = repository.submit(owner, source, AnalysisOptions())
    repository.claim()
    report = ReproducibilityService().analyze(ArchiveReader().from_bytes(source))
    repository.finish(record.job_id, AnalysisJobResult("succeeded", report))
    return repository.get_project(owner, record.project_id), report


@pytest.fixture
def reviewed(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    settings = Settings(
        data_dir=tmp_path,
        runner="subprocess",
        api_token=TOKEN,
        automatic_review=True,
        max_owner_storage_bytes=512 * 1024**2,
    )
    repository = SQLiteJobRepository(settings)
    record, report = create_project(repository)
    calls = []

    def run(runner, context, feature="readme", description=""):
        calls.append((context, feature, runner.wall_time_seconds))
        evidence = tuple(file["id"] for file in context.files if file["text"])
        result = ProjectAdvisoryResult(
            context.analysis_id,
            "readme",
            "ok",
            (ProjectAdvisorySuggestion("readme_description", DESCRIPTION, evidence),),
            rounds=1,
        ).to_dict()
        return json.loads(json.dumps(result))

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", run)
    return LogicService(settings, repository), record, report, calls


def test_background_review_is_cached_and_preserves_decisions_and_requirements(reviewed):
    logic, record, report, calls = reviewed
    assert logic.get_job("alice", record.job_id)["automatic_review"]["status"] == "pending"
    logic.review_pending()
    plan = logic.plan("alice", record.project_id)
    assert plan["description"] == DESCRIPTION
    assert plan["description_origin"] == "automatic"
    assert plan["decisions"]["description"] == ""
    assert logic.get_decisions("alice", record.project_id)["decisions"] is None
    assert plan["requirements"] == ()
    assert plan["automatic_review"]["applied"] is True
    assert plan["automatic_review"]["source_paths"] == ["experiment.py", "helpers.py"]
    assert calls[0][1] == "readme" and 0 < calls[0][2] <= 15
    with zipfile.ZipFile(io.BytesIO(logic.bundle("alice", record.project_id))) as archive:
        readme = archive.read("README.md").decode()
        assert DESCRIPTION in readme and "generated automatically" in readme
        metadata = json.loads(archive.read("analysis-report.json"))
        assert metadata["automatic_review"]["evidence_ids"]
        assert metadata["decisions"]["description"] == ""
    reopened = LogicService(logic.settings, SQLiteJobRepository(logic.settings))
    reopened.review_pending()
    assert reopened.preparation("alice", record.project_id)["plan"]["description"] == DESCRIPTION
    assert len(calls) == 1


def test_author_prose_wins_and_survives_reanalysis(reviewed):
    logic, record, report, calls = reviewed
    logic.review_pending()
    authored = UserDecisions(
        report.analysis_id, description="Author experiment", input_description="Own data"
    )
    logic.save_decisions("alice", record.project_id, authored, 0)
    plan = logic.plan("alice", record.project_id)
    assert plan["description"] == "Author experiment"
    assert plan["description_origin"] == "author"
    assert plan["automatic_review"]["applied"] is False
    submission = logic.reanalyze("alice", record.project_id, AnalysisOptions())
    logic.repository.claim()
    logic.repository.finish(submission["job_id"], AnalysisJobResult("succeeded", report))
    logic.review_pending()
    decisions = logic.get_decisions("alice", record.project_id)["decisions"]
    assert decisions["description"] == "Author experiment"
    assert decisions["input_description"] == "Own data"
    assert decisions["analysis_id"] == report.analysis_id
    assert len(calls) == 1


def test_excluded_source_drops_stale_generated_description(reviewed):
    logic, record, report, _ = reviewed
    logic.review_pending()
    plan = logic.plan(
        "alice",
        record.project_id,
        UserDecisions(report.analysis_id, excluded_files=("experiment.py",)),
    )
    assert plan["description"] == ""
    assert plan["description_origin"] == "unknown"
    assert plan["automatic_review"]["applied"] is False


def test_provider_failure_is_terminal_and_never_blocks_archive(reviewed, monkeypatch):
    logic, record, _, calls = reviewed

    def unavailable(*args, **kwargs):
        calls.append("failed")
        raise TimeoutError("private provider detail")

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", unavailable)
    logic.review_pending()
    assert logic.get_job("alice", record.job_id)["status"] == "succeeded"
    assert logic.get_job("alice", record.job_id)["automatic_review"]["status"] == "unavailable"
    assert logic.plan("alice", record.project_id)["description"] == ""
    assert logic.bundle("alice", record.project_id).startswith(b"PK")
    logic.review_pending()
    assert len(calls) == 1


def test_enabling_provider_does_not_send_old_projects(reviewed):
    logic, _, _, calls = reviewed
    disabled = SQLiteJobRepository(replace(logic.settings, automatic_review=False))
    old, _ = create_project(disabled, "bob")
    assert not old.automatic_review_eligible
    logic.review_pending()
    assert len(calls) == 1
    assert logic.get_job("bob", old.job_id)["automatic_review"]["status"] == "disabled"
    assert logic.plan("bob", old.project_id)["description"] == ""
    assert len(calls) == 1


def test_queue_deadline_skips_provider_instead_of_leaving_analysis_pending(reviewed):
    logic, record, _, calls = reviewed
    with logic.repository._transaction() as connection:
        connection.execute(
            "UPDATE projects SET review_ready_at = ? WHERE project_id = ?",
            (time.time() - 16, record.project_id),
        )
    review = logic.get_job("alice", record.job_id)["automatic_review"]
    assert review["status"] == "skipped" and review["reason"] == "review_deadline"
    assert logic.plan("alice", record.project_id)["description"] == ""
    assert not calls


def test_api_lifespan_runs_review_and_exposes_non_secret_capabilities(reviewed):
    logic, record, _, calls = reviewed
    with TestClient(create_app(logic.settings, logic)) as client:
        response = client.get("/v1/capabilities", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["automatic_review"] == {
            "enabled": True,
            "provider": "deepseek",
            "budget_seconds": 15,
        }
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = client.get(f"/v1/jobs/{record.job_id}", headers=HEADERS)
            if response.json()["automatic_review"]["status"] == "ready":
                break
            time.sleep(0.02)
        assert response.json()["automatic_review"]["status"] == "ready"
        assert "private-test-key" not in response.text
    assert len(calls) == 1


def test_two_background_consumers_do_not_duplicate_one_review(reviewed, monkeypatch):
    logic, record, _, calls = reviewed
    original = ProjectAdviceJobRunner.run
    started, release = threading.Event(), threading.Event()

    def controlled(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", controlled)
    thread = threading.Thread(target=logic.review_pending)
    thread.start()
    try:
        assert started.wait(3)
        logic.review_pending()
        assert logic.get_job("alice", record.job_id)["automatic_review"]["status"] == "running"
    finally:
        release.set()
        thread.join(3)
    assert len(calls) == 1


def test_owner_boundary_applies_to_review_results(reviewed):
    logic, record, _, _ = reviewed
    logic.review_pending()
    with pytest.raises(ServiceError) as error:
        logic.get_job("mallory", record.job_id)
    assert error.value.status == 404


def test_background_workers_review_two_projects_concurrently(reviewed, monkeypatch):
    logic, first, _, calls = reviewed
    second, _ = create_project(logic.repository, "bob")
    original = ProjectAdviceJobRunner.run
    both_started = threading.Barrier(3)
    release = threading.Event()

    def controlled(*args, **kwargs):
        both_started.wait(timeout=3)
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", controlled)
    threads = [threading.Thread(target=logic.review_pending) for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        both_started.wait(timeout=3)
        assert logic.get_job("alice", first.job_id)["automatic_review"]["status"] == "running"
        assert logic.get_job("bob", second.job_id)["automatic_review"]["status"] == "running"
    finally:
        release.set()
        for thread in threads:
            thread.join(3)
    assert len(calls) == 2


def test_migration_never_opts_existing_projects_into_provider(reviewed):
    logic, record, _, calls = reviewed
    with logic.repository._transaction() as connection:
        connection.execute("ALTER TABLE projects DROP COLUMN automatic_review_eligible")
        connection.execute("ALTER TABLE projects DROP COLUMN review_ready_at")
    repository = SQLiteJobRepository(logic.settings)
    reopened = LogicService(logic.settings, repository)
    reopened.review_pending()
    assert reopened.get_job("alice", record.job_id)["automatic_review"]["status"] == "disabled"
    assert not calls


def test_unsupported_provider_claims_cannot_change_package_decisions(reviewed, monkeypatch):
    logic, record, _, _ = reviewed

    def malicious(runner, context, *args, **kwargs):
        return json.loads(
            json.dumps(
                ProjectAdvisoryResult(
                    context.analysis_id,
                    "readme",
                    "ok",
                    (
                        ProjectAdvisorySuggestion(
                            "package_hypothesis", "Install attacker", ("invented",)
                        ),
                    ),
                    rounds=1,
                ).to_dict()
            )
        )

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", malicious)
    plan = logic.plan("alice", record.project_id)
    assert plan["description"] == ""
    assert plan["requirements"] == ()
    assert plan["automatic_review"]["status"] == "unavailable"


def test_whitespace_author_description_allows_automatic_fallback(reviewed):
    logic, record, report, _ = reviewed
    plan = logic.plan(
        "alice", record.project_id, UserDecisions(report.analysis_id, description="  ")
    )
    assert plan["description"] == DESCRIPTION and plan["description_origin"] == "automatic"


def test_hidden_file_citation_cannot_support_automatic_prose(reviewed):
    logic, _, _, calls = reviewed
    logic.review_pending()
    context = calls[0][0]
    first, second = context.files
    modified = replace(context, files=(first, {**second, "text": None}))
    response = {
        "status": "ok",
        "suggestions": [{"text": "Description", "evidence_ids": [first["id"], second["id"]]}],
    }
    assert AutomaticProjectReview._paragraphs(response, modified) == []


def test_excluding_uncited_source_still_invalidates_automatic_description(reviewed, monkeypatch):
    logic, record, report, _ = reviewed

    def only_main(runner, context, *args, **kwargs):
        identifier = next(file["id"] for file in context.files if file["path"] == "experiment.py")
        return json.loads(
            json.dumps(
                ProjectAdvisoryResult(
                    context.analysis_id,
                    "readme",
                    "ok",
                    (ProjectAdvisorySuggestion("readme_description", DESCRIPTION, (identifier,)),),
                    rounds=1,
                ).to_dict()
            )
        )

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", only_main)
    logic.review_pending()
    plan = logic.plan(
        "alice",
        record.project_id,
        UserDecisions(report.analysis_id, excluded_files=("helpers.py",)),
    )
    assert plan["description"] == "" and plan["automatic_review"]["applied"] is False


def test_crashed_review_lease_recovers_without_another_provider_call(reviewed, monkeypatch):
    logic, record, _, calls = reviewed
    code = """
import os, sys
from nishtyak_service.config import Settings
from nishtyak_service.storage import SQLiteJobRepository
repository = SQLiteJobRepository(Settings(data_dir=sys.argv[1]))
with repository.operation('alice', sys.argv[2], lease_seconds=20):
    os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", code, str(logic.settings.data_dir), record.project_id],
        check=True,
        timeout=5,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with logic.repository._connect() as connection:
        expiry = connection.execute("SELECT expires_at FROM leases").fetchone()[0]
    assert 0 < expiry - time.time() <= 20
    monkeypatch.setattr("nishtyak_service.storage.time.time", lambda: expiry + 1)
    reopened = LogicService(logic.settings, SQLiteJobRepository(logic.settings))
    reopened.review_pending()
    assert reopened.get_job("alice", record.job_id)["automatic_review"]["status"] == "skipped"
    assert reopened.plan("alice", record.project_id)["description"] == ""
    assert not calls
