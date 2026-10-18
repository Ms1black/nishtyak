import io
import threading
import time
import zipfile

import pytest

from nishtyak_core.jobs import AnalysisJobResult
from nishtyak_core.models import AnalysisOptions
from nishtyak_service.config import Settings
from nishtyak_service.dispatcher import Dispatcher, DispatcherAlreadyRunning
from nishtyak_service.execution import SubprocessExecutor
from nishtyak_service.storage import SQLiteJobRepository


def source_zip():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("main.py", "import os\n")
    return stream.getvalue()


def wait_for(condition, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    assert condition(), "Condition was not reached before timeout"


def repository_at(tmp_path):
    return SQLiteJobRepository(Settings(data_dir=tmp_path / "data", runner="subprocess"))


class HeldExecutor:
    def __init__(self):
        self.release = threading.Event()
        self.entered = []
        self.cancelled = []
        self.allow_cleanup = True
        self._lock = threading.Lock()

    def ready(self):
        return True

    def cleanup(self, job_id):
        return self.allow_cleanup

    def analyze(self, record, archive_path, cancel_event):
        with self._lock:
            self.entered.append(record.job_id)
        while not self.release.wait(0.01):
            if cancel_event.is_set():
                self.cancelled.append(record.job_id)
                return AnalysisJobResult("failed", code="JOB_CANCELLED")
        return AnalysisJobResult("failed", code="ARCHIVE_INVALID")


def test_dispatcher_runs_a_real_worker_and_persists_typed_report(tmp_path):
    repository = repository_at(tmp_path)
    record = repository.submit("owner", source_zip(), AnalysisOptions())
    dispatcher = Dispatcher(repository, SubprocessExecutor(), poll_interval=0.01)
    dispatcher.start()
    try:
        wait_for(lambda: repository.get_job("owner", record.job_id).status == "succeeded")
        assert (
            repository.read_report(repository.get_project("owner", record.project_id)).files[0].path
            == "main.py"
        )
        assert dispatcher.ready()
    finally:
        dispatcher.stop()
    assert not dispatcher.ready()
    assert not repository.dispatcher_ready()


def test_dispatcher_admits_at_most_two_jobs_and_then_continues(tmp_path):
    repository = repository_at(tmp_path)
    records = [
        repository.submit(f"owner-{index}", source_zip(), AnalysisOptions()) for index in range(3)
    ]
    executor = HeldExecutor()
    dispatcher = Dispatcher(repository, executor, poll_interval=0.01)
    dispatcher.start()
    try:
        wait_for(lambda: len(executor.entered) == 2)
        assert len(repository.running_jobs()) == 2
        assert repository.get_job("owner-2", records[2].job_id).status == "queued"
        executor.release.set()
        wait_for(lambda: len(executor.entered) == 3)
        wait_for(lambda: not repository.running_jobs())
    finally:
        dispatcher.stop()


def test_another_dispatcher_cannot_use_the_same_data_directory(tmp_path):
    repository = repository_at(tmp_path)
    first = Dispatcher(repository, HeldExecutor(), poll_interval=0.01)
    second = Dispatcher(repository, HeldExecutor(), poll_interval=0.01)
    first.start()
    try:
        with pytest.raises(DispatcherAlreadyRunning):
            second.start()
    finally:
        first.stop()
    second.start()
    second.stop()


def test_startup_cleans_up_old_running_jobs_without_rerunning_them(tmp_path):
    repository = repository_at(tmp_path)
    record = repository.submit("owner", source_zip(), AnalysisOptions())
    assert repository.claim().job_id == record.job_id
    executor = HeldExecutor()
    dispatcher = Dispatcher(repository, executor, poll_interval=0.01)
    dispatcher.start()
    try:
        wait_for(lambda: repository.get_job("owner", record.job_id).status == "failed")
        assert repository.get_job("owner", record.job_id).error_code == "WORKER_RESTARTED"
        assert not executor.entered
    finally:
        dispatcher.stop()


def test_failed_orphan_cleanup_blocks_readiness_and_new_jobs(tmp_path):
    repository = repository_at(tmp_path)
    old = repository.submit("old", source_zip(), AnalysisOptions())
    assert repository.claim().job_id == old.job_id
    new = repository.submit("new", source_zip(), AnalysisOptions())
    executor = HeldExecutor()
    executor.allow_cleanup = False
    dispatcher = Dispatcher(repository, executor, poll_interval=0.01)
    dispatcher.start()
    try:
        wait_for(lambda: dispatcher._healthy)
        assert not dispatcher.ready()
        assert not repository.dispatcher_ready()
        assert repository.get_job("old", old.job_id).status == "running"
        assert repository.get_job("new", new.job_id).status == "queued"
        assert not executor.entered
        executor.allow_cleanup = True
        wait_for(lambda: executor.entered == [new.job_id])
        assert repository.get_job("old", old.job_id).error_code == "WORKER_RESTARTED"
    finally:
        dispatcher.stop()


def test_completed_worker_keeps_its_slot_until_cleanup_is_confirmed(tmp_path):
    repository = repository_at(tmp_path)
    record = repository.submit("owner", source_zip(), AnalysisOptions())
    executor = HeldExecutor()
    executor.allow_cleanup = False
    executor.release.set()
    dispatcher = Dispatcher(repository, executor, poll_interval=0.01)
    dispatcher.start()
    try:
        wait_for(lambda: record.job_id in dispatcher._active)
        wait_for(lambda: dispatcher._active[record.job_id].finished.is_set())
        assert repository.get_job("owner", record.job_id).status == "running"
        assert not dispatcher.ready()
        wait_for(lambda: not repository.dispatcher_ready())
        executor.allow_cleanup = True
        wait_for(lambda: repository.get_job("owner", record.job_id).status == "failed")
    finally:
        dispatcher.stop()


def test_delete_cancels_a_running_worker_and_prevents_late_publication(tmp_path):
    repository = repository_at(tmp_path)
    record = repository.submit("owner", source_zip(), AnalysisOptions())
    executor = HeldExecutor()
    dispatcher = Dispatcher(repository, executor, poll_interval=0.01)
    dispatcher.start()
    try:
        wait_for(lambda: executor.entered == [record.job_id])
        repository.delete("owner", record.project_id)
        wait_for(lambda: executor.cancelled == [record.job_id])
        wait_for(lambda: not repository.running_jobs())
        assert not repository.report_path(record.project_id).exists()
    finally:
        dispatcher.stop()


def test_stop_cancels_jobs_and_releases_the_host_lock(tmp_path):
    repository = repository_at(tmp_path)
    record = repository.submit("owner", source_zip(), AnalysisOptions())
    executor = HeldExecutor()
    dispatcher = Dispatcher(repository, executor, poll_interval=0.01)
    dispatcher.start()
    wait_for(lambda: executor.entered == [record.job_id])
    dispatcher.stop(timeout=3)
    assert not dispatcher._thread.is_alive()
    assert executor.cancelled == [record.job_id]
    assert repository.get_job("owner", record.job_id).error_code == "JOB_CANCELLED"
    assert not repository.running_jobs()
    replacement = Dispatcher(repository, HeldExecutor(), poll_interval=0.01)
    replacement.start()
    replacement.stop(timeout=3)


def test_executor_exception_returns_a_static_failure_and_keeps_dispatcher_alive(tmp_path):
    class BrokenExecutor(HeldExecutor):
        def analyze(self, record, archive_path, cancel_event):
            raise RuntimeError("a private host path must not escape")

    repository = repository_at(tmp_path)
    record = repository.submit("owner", source_zip(), AnalysisOptions())
    dispatcher = Dispatcher(repository, BrokenExecutor(), poll_interval=0.01)
    dispatcher.start()
    try:
        wait_for(lambda: repository.get_job("owner", record.job_id).status == "failed")
        assert repository.get_job("owner", record.job_id).error_code == "WORKER_FAILED"
        assert dispatcher.ready()
    finally:
        dispatcher.stop()


def test_fatal_storage_error_clears_health_and_releases_lock(tmp_path, monkeypatch):
    repository = repository_at(tmp_path)
    dispatcher = Dispatcher(repository, HeldExecutor(), poll_interval=0.01)

    def broken_sweep():
        repository.mark_heartbeat(True)
        raise RuntimeError("storage is unavailable")

    monkeypatch.setattr(repository, "sweep", broken_sweep)
    with pytest.raises(RuntimeError, match="storage is unavailable"):
        dispatcher.run_forever()
    assert not dispatcher.ready()
    assert not repository.dispatcher_ready()
    assert dispatcher._lock_fd is None


def test_dispatcher_reconciles_owned_containers_before_admission_and_periodically(tmp_path):
    class ReconciledExecutor(HeldExecutor):
        def __init__(self):
            super().__init__()
            self.reconciled = []
            self.allow_reconcile = False
            self.bound = None

        def bind_namespace(self, data_dir):
            self.bound = data_dir

        def reconcile(self, active_job_ids):
            self.reconciled.append(active_job_ids)
            return self.allow_reconcile

    repository = repository_at(tmp_path)
    record = repository.submit("owner", source_zip(), AnalysisOptions())
    executor = ReconciledExecutor()
    dispatcher = Dispatcher(repository, executor, poll_interval=0.01)
    dispatcher.start()
    try:
        wait_for(lambda: executor.reconciled)
        assert executor.bound == repository.settings.data_dir
        assert not dispatcher.ready()
        assert repository.get_job("owner", record.job_id).status == "queued"
        assert not executor.entered
        executor.allow_reconcile = True
        wait_for(lambda: executor.entered == [record.job_id])
        wait_for(lambda: {record.job_id} in executor.reconciled)
        executor.allow_reconcile = False
        wait_for(lambda: not repository.dispatcher_ready())
        assert not dispatcher.ready()
    finally:
        dispatcher.stop()


def test_namespace_initialization_failure_releases_dispatcher_lock(tmp_path):
    class BrokenNamespace(HeldExecutor):
        def bind_namespace(self, data_dir):
            raise ValueError("Invalid namespace state")

    repository = repository_at(tmp_path)
    dispatcher = Dispatcher(repository, BrokenNamespace())
    with pytest.raises(ValueError, match="Invalid namespace state"):
        dispatcher.start()
    assert dispatcher._lock_fd is None
    replacement = Dispatcher(repository, HeldExecutor(), poll_interval=0.01)
    replacement.start()
    replacement.stop()
