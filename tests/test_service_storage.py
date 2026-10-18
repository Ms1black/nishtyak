from __future__ import annotations

import os
import sqlite3
import stat
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from nishtyak_core.jobs import AnalysisJobResult
from nishtyak_core.models import AnalysisOptions
from nishtyak_core.service import ReproducibilityService
from nishtyak_core.snapshot import SnapshotBuilder
from nishtyak_service.config import Settings
from nishtyak_service.errors import ServiceError
from nishtyak_service.service import LogicService
from nishtyak_service.storage import SQLiteJobRepository


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def repository(settings):
    return SQLiteJobRepository(settings)


def submit(repository, owner="alice"):
    return repository.submit(owner, b"source zip", AnalysisOptions())


def complete(repository, record):
    claimed = repository.claim()
    assert claimed.job_id == record.job_id
    snapshot = SnapshotBuilder().from_mapping({"main.py": b"print('ok')\n"})
    report = ReproducibilityService().analyze(snapshot, record.options)
    assert repository.finish(record.job_id, AnalysisJobResult("succeeded", report))
    return report


def assert_error(code, function, *args):
    with pytest.raises(ServiceError) as caught:
        function(*args)
    assert caught.value.code == code
    return caught.value


def reservation(settings, archive=b"source zip"):
    return len(archive) + 2 * settings.max_report_bytes + settings.max_bundle_bytes + 1024 * 1024


def test_records_and_private_files_survive_repository_restart(settings):
    repository = SQLiteJobRepository(settings)
    record = submit(repository)
    assert len(record.project_id) == len(record.job_id) == 32
    assert record.project_id != record.job_id
    assert record.status == "queued"
    assert record.expires_at is None
    assert repository.archive_path(record.project_id).read_bytes() == b"source zip"
    assert stat.S_IMODE(settings.data_dir.stat().st_mode) == 0o770
    assert stat.S_IMODE(repository.archive_path(record.project_id).stat().st_mode) == 0o660
    restarted = SQLiteJobRepository(settings)
    assert restarted.get_project("alice", record.project_id) == record
    assert restarted.get_job("alice", record.job_id) == record
    with sqlite3.connect(restarted.database_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_owner_and_invalid_identifiers_do_not_reveal_resources(repository):
    record = submit(repository)
    for value in (record.project_id, "../other", "", "f" * 31):
        error = assert_error("RESOURCE_NOT_FOUND", repository.get_project, "bob", value)
        assert error.status == 404
    assert_error("RESOURCE_NOT_FOUND", repository.get_job, "bob", record.job_id)
    assert_error("RESOURCE_NOT_FOUND", repository.delete, "bob", record.project_id)
    assert repository.archive_path(record.project_id).exists()


def test_claim_is_atomic_across_repository_instances(settings):
    repository = SQLiteJobRepository(settings)
    records = [submit(repository, f"owner-{index}") for index in range(4)]
    barrier = threading.Barrier(4)

    def claim(_):
        other = SQLiteJobRepository(settings)
        barrier.wait()
        return other.claim()

    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = [record for record in pool.map(claim, range(4)) if record]
    assert len(claimed) == settings.max_running
    assert len({record.job_id for record in claimed}) == len(claimed)
    assert {record.job_id for record in claimed} <= {record.job_id for record in records}
    assert len(repository.running_jobs()) == settings.max_running


def test_successful_report_is_persisted_and_decoded_after_restart(settings):
    repository = SQLiteJobRepository(settings)
    record = submit(repository)
    report = complete(repository, record)
    restarted = SQLiteJobRepository(settings)
    assert restarted.read_report(record) == report
    assert restarted.get_job("alice", record.job_id).status == "succeeded"
    assert not restarted.finish(record.job_id, AnalysisJobResult("failed", code="LATE"))


def test_queued_deletion_removes_files_immediately(repository):
    record = submit(repository)
    root = repository.archive_path(record.project_id).parent
    repository.delete("alice", record.project_id)
    assert not root.exists()
    assert repository.is_revoked(record.job_id)
    assert repository.claim() is None
    assert_error("RESOURCE_NOT_FOUND", repository.get_project, "alice", record.project_id)


def test_delete_during_analysis_keeps_files_until_late_finish(repository):
    record = submit(repository)
    repository.claim()
    path = repository.archive_path(record.project_id)
    repository.delete("alice", record.project_id)
    assert path.exists()
    assert repository.running_jobs()[0].deleted
    assert_error("RESOURCE_NOT_FOUND", repository.get_job, "alice", record.job_id)
    assert not repository.finish(record.job_id, AnalysisJobResult("failed", code="LATE"))
    assert not path.parent.exists()
    assert not repository.running_jobs()


def test_running_tombstone_can_be_recovered_after_dispatcher_restart(settings):
    repository = SQLiteJobRepository(settings)
    record = submit(repository)
    repository.claim()
    repository.delete("alice", record.project_id)
    restarted = SQLiteJobRepository(settings)
    assert restarted.running_jobs()[0].job_id == record.job_id
    restarted.fail(record.job_id, "DISPATCHER_RESTARTED")
    assert not restarted.running_jobs()
    assert not restarted.archive_path(record.project_id).parent.exists()


def test_time_does_not_revoke_access_or_remove_projects(repository, monkeypatch):
    record = submit(repository)
    monkeypatch.setattr("nishtyak_service.storage.time.time", lambda: record.created_at + 10**8)
    assert repository.get_project("alice", record.project_id).expires_at is None
    assert not repository.is_revoked(record.job_id)
    repository.sweep()
    assert repository.archive_path(record.project_id).exists()
    assert repository.claim().job_id == record.job_id


def test_deleted_running_project_still_consumes_storage_until_cleanup(settings):
    settings = replace(settings, max_owner_storage_bytes=reservation(settings))
    repository = SQLiteJobRepository(settings)
    record = submit(repository)
    repository.claim()
    repository.delete("alice", record.project_id)
    assert_error("OWNER_STORAGE_LIMIT", submit, repository)
    repository.fail(record.job_id, "CANCELLED")
    assert submit(repository).status == "queued"


def test_cleanup_failure_retains_quota_and_sweep_retries(settings, monkeypatch):
    settings = replace(settings, max_storage_bytes=reservation(settings))
    repository = SQLiteJobRepository(settings)
    record = submit(repository)
    remover = repository._remove_project
    monkeypatch.setattr(repository, "_remove_project", lambda _: False)
    repository.delete("alice", record.project_id)
    assert_error("STORAGE_LIMIT", submit, repository, "bob")
    monkeypatch.setattr(repository, "_remove_project", remover)
    repository.sweep()
    assert submit(repository, "bob").status == "queued"


def test_queue_and_owner_active_limits(settings):
    repository = SQLiteJobRepository(replace(settings, max_owner_active=1, max_queued=2))
    first = submit(repository)
    assert_error("OWNER_JOB_LIMIT", submit, repository)
    submit(repository, "bob")
    assert_error("QUEUE_FULL", submit, repository, "charlie")
    repository.claim()
    repository.fail(first.job_id, "EXPECTED")
    assert submit(repository).status == "queued"


def test_operation_lease_blocks_same_project_and_shares_worker_slots(settings):
    settings = replace(settings, max_running=1)
    repository = SQLiteJobRepository(settings)
    record = submit(repository)
    complete(repository, record)
    pending = submit(repository, "bob")
    other = SQLiteJobRepository(settings)
    with repository.operation("alice", record.project_id) as leased:
        assert leased.status == "succeeded"
        assert other.claim() is None
        with pytest.raises(ServiceError) as caught:
            with other.operation("alice", record.project_id):
                pass
        assert caught.value.code == "PROJECT_BUSY"
    assert other.claim().job_id == pending.job_id


def test_delete_during_operation_defers_cleanup_until_lease_released(repository):
    record = submit(repository)
    complete(repository, record)
    path = repository.archive_path(record.project_id)
    with repository.operation("alice", record.project_id):
        repository.delete("alice", record.project_id)
        repository.sweep()
        assert path.exists()
        assert_error("RESOURCE_NOT_FOUND", repository.get_project, "alice", record.project_id)
    assert not path.parent.exists()


def test_expired_operation_lease_is_recovered_after_crash(repository):
    record = submit(repository)
    complete(repository, record)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "INSERT INTO leases VALUES (?, ?, ?)",
            (uuid.uuid4().hex, record.project_id, time.time() - 1),
        )
    repository.delete("alice", record.project_id)
    assert not repository.archive_path(record.project_id).parent.exists()


@pytest.mark.parametrize("payload", [b"broken JSON", b"{}", b"[]"])
def test_corrupt_stored_report_has_safe_error(repository, payload):
    record = submit(repository)
    complete(repository, record)
    repository.report_path(record.project_id).write_bytes(payload)
    error = assert_error("REPORT_UNAVAILABLE", repository.read_report, record)
    assert error.status == 500


def test_report_symlink_is_never_followed(repository, tmp_path):
    record = submit(repository)
    complete(repository, record)
    outside = tmp_path / "outside"
    outside.write_text("private content")
    path = repository.report_path(record.project_id)
    path.unlink()
    path.symlink_to(outside)
    assert_error("REPORT_UNAVAILABLE", repository.read_report, record)
    repository.delete("alice", record.project_id)
    assert outside.read_text() == "private content"


def test_rate_limit_is_atomic_and_persistent(settings):
    repository = SQLiteJobRepository(settings)

    def consume(_):
        try:
            SQLiteJobRepository(settings).consume_rate("alice", "upload", 3, 3600)
            return True
        except ServiceError as error:
            assert error.status == 429
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(consume, range(8))) == 3
    assert_error("RATE_LIMIT", repository.consume_rate, "alice", "upload", 3, 3600)
    repository.consume_rate("bob", "upload", 3, 3600)
    repository.consume_rate("alice", "advice", 3, 3600)


def test_readiness_requires_fresh_dispatcher_heartbeat(settings, monkeypatch):
    repository = SQLiteJobRepository(settings)
    assert not repository.dispatcher_ready()
    repository.mark_heartbeat()
    restarted = SQLiteJobRepository(settings)
    assert restarted.dispatcher_ready()
    future = time.time() + 20
    monkeypatch.setattr("nishtyak_service.storage.time.time", lambda: future)
    assert not restarted.dispatcher_ready()


def test_dispatcher_can_invalidate_readiness_immediately(repository):
    repository.mark_heartbeat()
    assert repository.dispatcher_ready()
    repository.mark_heartbeat(ready=False)
    assert not repository.dispatcher_ready()


def test_sweep_removes_orphan_from_interrupted_submission(repository):
    orphan = repository._projects_dir / uuid.uuid4().hex
    orphan.mkdir()
    (orphan / "source.zip").write_bytes(b"interrupted")
    repository.sweep()
    assert not orphan.exists()


def test_symbolic_storage_root_is_rejected(settings, tmp_path):
    destination = tmp_path / "destination"
    destination.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        SQLiteJobRepository(replace(settings, data_dir=alias))


def test_invalid_or_oversized_submission_leaves_no_files(settings):
    repository = SQLiteJobRepository(replace(settings, max_archive_bytes=4))
    assert_error("ARCHIVE_INVALID", repository.submit, "alice", b"", AnalysisOptions())
    assert_error("ARCHIVE_LIMIT", repository.submit, "alice", b"large", AnalysisOptions())
    assert_error(
        "OPTIONS_INVALID",
        repository.submit,
        "alice",
        b"zip",
        AnalysisOptions(max_files=1001),
    )
    assert not list(repository._projects_dir.iterdir())


def test_operations_on_incomplete_project_are_rejected(repository):
    record = submit(repository)
    assert_error("PROJECT_NOT_READY", repository.read_report, record)
    with pytest.raises(ServiceError) as caught:
        with repository.operation("alice", record.project_id):
            pass
    assert caught.value.code == "PROJECT_NOT_READY"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Requires POSIX FIFOs")
def test_report_fifo_is_rejected_without_blocking(repository):
    record = submit(repository)
    complete(repository, record)
    path = repository.report_path(record.project_id)
    path.unlink()
    os.mkfifo(path)
    assert_error("REPORT_UNAVAILABLE", repository.read_report, record)


def test_long_running_job_is_not_revoked_by_project_age(repository, monkeypatch):
    record = submit(repository)
    repository.claim()
    monkeypatch.setattr("nishtyak_service.storage.time.time", lambda: record.created_at + 10**8)
    repository.sweep()
    assert repository.archive_path(record.project_id).exists()
    snapshot = SnapshotBuilder().from_mapping({"main.py": b"print('ok')\n"})
    report = ReproducibilityService().analyze(snapshot, record.options)
    assert repository.finish(record.job_id, AnalysisJobResult("succeeded", report))
    assert repository.archive_path(record.project_id).exists()


def test_cleaned_tombstone_expires_without_allowing_late_result(repository, monkeypatch):
    record = submit(repository)
    repository.delete("alice", record.project_id)
    later = record.created_at + 10**8
    monkeypatch.setattr("nishtyak_service.storage.time.time", lambda: later)
    repository.sweep()
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    assert not repository.finish(record.job_id, AnalysisJobResult("failed", code="LATE"))
    assert repository.is_revoked(record.job_id)


def test_owner_identity_uses_same_restrictions_as_http(repository):
    for owner in ("", "../alice", "alice\n", "юзер", "a" * 129):
        assert_error("OWNER_INVALID", submit, repository, owner)


def test_live_operation_renews_lease_and_prevents_early_cleanup(settings):
    settings = replace(settings, max_running=1, operation_lease_seconds=1)
    repository = SQLiteJobRepository(settings)
    other = SQLiteJobRepository(settings)
    record = submit(repository)
    complete(repository, record)
    with repository.operation("alice", record.project_id):
        time.sleep(1.2)
        other.sweep()
        with pytest.raises(ServiceError) as caught:
            with other.operation("alice", record.project_id):
                pass
        assert caught.value.code == "PROJECT_BUSY"
        other.delete("alice", record.project_id)
        time.sleep(0.5)
        other.sweep()
        assert repository.archive_path(record.project_id).exists()
    assert not repository.archive_path(record.project_id).exists()


def test_lost_operation_lease_cannot_return_success(repository):
    record = submit(repository)
    complete(repository, record)
    with pytest.raises(ServiceError) as caught:
        with repository.operation("alice", record.project_id):
            with sqlite3.connect(repository.database_path) as connection:
                connection.execute("DELETE FROM leases WHERE project_id = ?", (record.project_id,))
    assert caught.value.status == 503
    assert caught.value.code == "OPERATION_LEASE_LOST"


def test_heartbeat_start_failure_does_not_leak_processing_slot(repository, monkeypatch):
    record = submit(repository)
    complete(repository, record)

    def unavailable(self):
        raise RuntimeError("Thread limit")

    monkeypatch.setattr(threading.Thread, "start", unavailable)
    with pytest.raises(ServiceError) as caught:
        with repository.operation("alice", record.project_id):
            pass
    assert caught.value.code == "OPERATION_UNAVAILABLE"
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0


@pytest.mark.parametrize("method", ["plan", "preparation"])
def test_facade_rechecks_access_after_read_only_preparation(repository, monkeypatch, method):
    record = submit(repository)
    complete(repository, record)
    logic = LogicService(repository.settings, repository)
    original = logic._prepare

    def delete_after_preparing(*args, **kwargs):
        plan = original(*args, **kwargs)
        repository.delete("alice", record.project_id)
        return plan

    monkeypatch.setattr(logic, "_prepare", delete_after_preparing)
    assert_error(
        "RESOURCE_NOT_FOUND",
        getattr(logic, method),
        "alice",
        record.project_id,
    )


def test_existing_live_projects_migrate_to_no_expiry(repository):
    record = submit(repository)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE projects SET expires_at=1 WHERE project_id=?", (record.project_id,)
        )
    reopened = SQLiteJobRepository(repository.settings)
    assert reopened.get_project("alice", record.project_id).expires_at is None
    reopened.sweep()
    assert reopened.archive_path(record.project_id).exists()
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT expires_at FROM projects").fetchone()[0] == 0
