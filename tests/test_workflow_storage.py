from __future__ import annotations

import io
import sqlite3
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from nishtyak_core import ReproducibilityService, SnapshotBuilder
from nishtyak_core.environment import AuthorEnvironmentSnapshot, InstalledDistribution
from nishtyak_core.jobs import AnalysisJobResult
from nishtyak_core.models import AnalysisOptions
from nishtyak_service.config import Settings
from nishtyak_service.errors import ServiceError
from nishtyak_service.storage import SQLiteJobRepository


@pytest.fixture
def repository(tmp_path):
    return SQLiteJobRepository(Settings(data_dir=tmp_path / "data"))


def environment(version="1.26.4"):
    return AuthorEnvironmentSnapshot(
        "3.11.9", "Linux-x86_64", (InstalledDistribution("numpy", version, ("numpy",)),)
    )


def source_zip():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("main.py", "import numpy\n")
    return stream.getvalue()


def submit(repository, owner="alice", author_environment=None):
    return repository.submit(owner, source_zip(), AnalysisOptions(), author_environment)


def complete(repository, record):
    claimed = repository.claim()
    assert claimed.job_id == record.job_id
    report = ReproducibilityService().analyze(
        SnapshotBuilder().from_mapping({"main.py": "import numpy\n"}),
        record.options,
        record.author_environment,
    )
    assert repository.finish(record.job_id, AnalysisJobResult("succeeded", report))
    return report


def assert_error(code, function, *args):
    with pytest.raises(ServiceError) as error:
        function(*args)
    assert error.value.code == code
    return error.value


def test_author_environment_survives_repository_restart_and_reanalysis(repository):
    record = submit(repository, author_environment=environment())
    reopened = SQLiteJobRepository(repository.settings)
    assert reopened.get_project("alice", record.project_id).author_environment == environment()
    complete(repository, record)
    replacement = reopened.reanalyze("alice", record.project_id, AnalysisOptions(project_root="."))
    assert replacement.author_environment == record.author_environment
    assert replacement.author_environment.id == record.author_environment.id
    complete(repository, replacement)
    updated = reopened.reanalyze("alice", record.project_id, AnalysisOptions(), environment("2.0"))
    assert updated.author_environment == environment("2.0")


def test_environment_storage_is_bounded_and_not_part_of_process_environment(repository):
    oversized = AuthorEnvironmentSnapshot(
        "3.11.9",
        "Linux",
        tuple(InstalledDistribution(f"package{index}", "1.0") for index in range(6000)),
    )
    assert_error("AUTHOR_ENVIRONMENT_INVALID", submit, repository, "alice", oversized)
    assert repository.claim() is None


def test_additive_migration_preserves_old_projects_without_environment(repository):
    record = submit(repository)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("ALTER TABLE projects DROP COLUMN author_environment_json")
    reopened = SQLiteJobRepository(repository.settings)
    assert reopened.get_project("alice", record.project_id).author_environment is None
    assert reopened.archive_path(record.project_id).read_bytes() == source_zip()


def test_decisions_default_and_compare_and_swap_are_durable(repository):
    record = submit(repository)
    report = complete(repository, record)
    assert repository.read_decisions("alice", record.project_id) == {
        "analysis_id": report.analysis_id,
        "revision": 0,
        "decisions": None,
        "updated_at": None,
    }
    decisions = {"analysis_id": report.analysis_id, "description": "Reviewed experiment"}
    saved = repository.save_decisions("alice", record.project_id, report.analysis_id, 0, decisions)
    assert saved["revision"] == 1 and saved["decisions"] == decisions
    assert isinstance(saved["updated_at"], float)
    assert (
        SQLiteJobRepository(repository.settings).read_decisions("alice", record.project_id) == saved
    )
    assert_error(
        "DECISIONS_REVISION_CONFLICT",
        repository.save_decisions,
        "alice",
        record.project_id,
        report.analysis_id,
        0,
        decisions,
    )
    assert repository.read_decisions("alice", record.project_id) == saved


def test_only_one_concurrent_decision_save_can_use_a_revision(repository):
    record = submit(repository)
    report = complete(repository, record)
    barrier = threading.Barrier(2)

    def save(index):
        other = SQLiteJobRepository(repository.settings)
        barrier.wait()
        try:
            return other.save_decisions(
                "alice",
                record.project_id,
                report.analysis_id,
                0,
                {"analysis_id": report.analysis_id, "description": str(index)},
            )
        except ServiceError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, range(2)))
    assert sum(isinstance(value, dict) for value in outcomes) == 1
    assert "DECISIONS_REVISION_CONFLICT" in outcomes
    assert repository.read_decisions("alice", record.project_id)["revision"] == 1


def test_decisions_require_current_ready_analysis_and_owner(repository):
    record = submit(repository)
    assert_error("PROJECT_NOT_READY", repository.read_decisions, "alice", record.project_id)
    report = complete(repository, record)
    decisions = {"analysis_id": report.analysis_id}
    assert_error("RESOURCE_NOT_FOUND", repository.read_decisions, "bob", record.project_id)
    assert_error(
        "RESOURCE_NOT_FOUND",
        repository.save_decisions,
        "bob",
        record.project_id,
        report.analysis_id,
        0,
        decisions,
    )
    assert_error(
        "ANALYSIS_STALE",
        repository.save_decisions,
        "alice",
        record.project_id,
        "old-analysis",
        0,
        {"analysis_id": "old-analysis"},
    )
    assert_error(
        "ANALYSIS_STALE",
        repository.save_decisions,
        "alice",
        record.project_id,
        report.analysis_id,
        0,
        {"analysis_id": "different"},
    )


def test_decision_bounds_and_active_operation_lease(repository):
    record = submit(repository)
    report = complete(repository, record)
    decisions = {"analysis_id": report.analysis_id}
    with repository.operation("alice", record.project_id):
        assert (
            repository.save_decisions("alice", record.project_id, report.analysis_id, 0, decisions)[
                "revision"
            ]
            == 1
        )
    assert_error(
        "DECISIONS_INVALID",
        repository.save_decisions,
        "alice",
        record.project_id,
        report.analysis_id,
        1,
        {**decisions, "description": "x" * (256 * 1024)},
    )
    assert_error(
        "DECISIONS_INVALID",
        repository.save_decisions,
        "alice",
        record.project_id,
        report.analysis_id,
        True,
        decisions,
    )


def test_reanalysis_preserves_zip_quota_and_lifetime_and_refreshes_decisions(repository):
    record = submit(repository)
    report = complete(repository, record)
    repository.save_decisions(
        "alice", record.project_id, report.analysis_id, 0, {"analysis_id": report.analysis_id}
    )
    with sqlite3.connect(repository.database_path) as connection:
        before = connection.execute("SELECT reserved_bytes FROM projects").fetchone()[0]
    new = repository.reanalyze("alice", record.project_id, AnalysisOptions(project_root="."))
    assert new.project_id == record.project_id and new.job_id != record.job_id
    assert new.created_at == record.created_at and new.expires_at == record.expires_at
    assert (
        new.status == "queued"
        and repository.archive_path(new.project_id).read_bytes() == source_zip()
    )
    assert_error("RESOURCE_NOT_FOUND", repository.get_job, "alice", record.job_id)
    assert_error("PROJECT_NOT_READY", repository.read_report, record)
    new_report = complete(repository, new)
    revised = repository.read_decisions("alice", new.project_id)
    assert revised["revision"] == 2
    assert revised["analysis_id"] == new_report.analysis_id
    assert revised["decisions"]["analysis_id"] == new_report.analysis_id
    assert revised["decisions"]["python_version"] is None
    assert revised["decisions"]["excluded_files"] == []
    assert_error(
        "ANALYSIS_STALE",
        repository.save_decisions,
        "alice",
        new.project_id,
        report.analysis_id,
        1,
        {"analysis_id": report.analysis_id},
    )
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT reserved_bytes FROM projects").fetchone()[0] == before


def test_reanalysis_is_rejected_while_worker_or_operation_is_active(repository):
    record = submit(repository)
    repository.claim()
    assert_error(
        "PROJECT_BUSY", repository.reanalyze, "alice", record.project_id, AnalysisOptions()
    )
    repository.fail(record.job_id, "TEST_FAILED")
    new = repository.reanalyze("alice", record.project_id, AnalysisOptions())
    complete(repository, new)
    with repository.operation("alice", record.project_id):
        assert_error(
            "PROJECT_BUSY", repository.reanalyze, "alice", record.project_id, AnalysisOptions()
        )


def test_reanalysis_checks_owner_queue_and_active_limits(repository):
    limited = SQLiteJobRepository(replace(repository.settings, max_queued=1, max_owner_active=1))
    record = submit(limited)
    # Replacing a queued job must not count the same project twice.
    queued = limited.reanalyze("alice", record.project_id, AnalysisOptions())
    complete(limited, queued)
    other = submit(limited, "bob")
    assert_error("QUEUE_FULL", limited.reanalyze, "alice", record.project_id, AnalysisOptions())
    limited.delete("bob", other.project_id)
    other = submit(limited, "alice")
    assert_error("QUEUE_FULL", limited.reanalyze, "alice", record.project_id, AnalysisOptions())
    limited.delete("alice", other.project_id)
    assert_error(
        "RESOURCE_NOT_FOUND", limited.reanalyze, "bob", record.project_id, AnalysisOptions()
    )


def test_reanalysis_obeys_owner_active_limit_without_a_full_global_queue(repository):
    limited = SQLiteJobRepository(replace(repository.settings, max_owner_active=1))
    record = submit(limited)
    complete(limited, record)
    submit(limited)
    assert_error(
        "OWNER_JOB_LIMIT", limited.reanalyze, "alice", record.project_id, AnalysisOptions()
    )


def test_delete_releases_private_environment_metadata_after_physical_cleanup(repository):
    record = submit(repository, author_environment=environment())
    complete(repository, record)
    repository.delete("alice", record.project_id)
    with sqlite3.connect(repository.database_path) as connection:
        stored = connection.execute(
            "SELECT author_environment_json, reserved_bytes FROM projects WHERE project_id = ?",
            (record.project_id,),
        ).fetchone()
    assert stored == (None, 0)


def test_manual_delete_removes_decisions_but_project_age_does_not(repository, monkeypatch):
    record = submit(repository)
    report = complete(repository, record)
    repository.save_decisions(
        "alice", record.project_id, report.analysis_id, 0, {"analysis_id": report.analysis_id}
    )
    repository.delete("alice", record.project_id)
    assert_error("RESOURCE_NOT_FOUND", repository.read_decisions, "alice", record.project_id)
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_documents").fetchone()[0] == 0
    another = submit(repository)
    report = complete(repository, another)
    repository.save_decisions(
        "alice", another.project_id, report.analysis_id, 0, {"analysis_id": report.analysis_id}
    )
    monkeypatch.setattr("nishtyak_service.storage.time.time", lambda: another.created_at + 10**8)
    assert repository.read_decisions("alice", another.project_id)["revision"] == 1
    repository.sweep()
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_documents").fetchone()[0] == 1
