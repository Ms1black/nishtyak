from __future__ import annotations

import fcntl
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from nishtyak_core.jobs import AnalysisJobResult

from .execution import JobExecutor

if TYPE_CHECKING:
    from .storage import JobRecord, SQLiteJobRepository


class DispatcherAlreadyRunning(RuntimeError):
    pass


@dataclass
class _ActiveJob:
    record: JobRecord
    cancel: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    result: AnalysisJobResult | None = None
    cleanup_confirmed: bool = False
    retry_at: float = 0


class Dispatcher:
    """A single host dispatcher admits a bounded number of independent worker jobs."""

    def __init__(
        self, repository: SQLiteJobRepository, executor: JobExecutor, poll_interval: float = 0.2
    ):
        if poll_interval <= 0:
            raise ValueError("Polling interval must be positive")
        self.repository = repository
        self.executor = executor
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active: dict[str, _ActiveJob] = {}
        self._recovering: dict[str, JobRecord] = {}
        self._lock_fd: int | None = None
        self._healthy = False
        self._next_probe = 0.0
        self._next_sweep = 0.0
        self._shutdown_deadline = float("inf")
        self._published_ready: bool | None = None
        self._reconciled = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._acquire_lock()
        self._stop.clear()
        self._shutdown_deadline = float("inf")
        self._thread = threading.Thread(target=self._run, name="nishtyak-dispatcher", daemon=True)
        self._thread.start()

    def run_forever(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise DispatcherAlreadyRunning("Dispatcher is already running")
        self._acquire_lock()
        self._stop.clear()
        self._shutdown_deadline = float("inf")
        self._thread = threading.current_thread()
        self._run()

    def ready(self) -> bool:
        return (
            self._healthy
            and not self._stop.is_set()
            and not self._recovering
            and self._reconciled
            and all(
                not job.finished.is_set() or job.cleanup_confirmed
                for job in list(self._active.values())
            )
            and self._thread is not None
            and self._thread.is_alive()
        )

    def stop(self, timeout: float = 10.0) -> None:
        self._shutdown_deadline = time.monotonic() + max(0, timeout)
        self._stop.set()
        for job in list(self._active.values()):
            job.cancel.set()
        try:
            self._publish_health(False)
        except Exception:
            pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0, timeout))

    def _run(self) -> None:
        try:
            self.repository.mark_heartbeat(False)
            self._published_ready = False
            self._recovering = {job.job_id: job for job in self.repository.running_jobs()}
            self._recover()
            self._reconcile()
            while True:
                now = time.monotonic()
                if now >= self._next_probe and not self._stop.is_set():
                    self._healthy = self.executor.ready()
                    self._next_probe = now + 5
                self._reap()
                for job_id, job in list(self._active.items()):
                    if self._stop.is_set() or self.repository.is_revoked(job_id):
                        job.cancel.set()
                if self._stop.is_set():
                    if not self._active or now >= self._shutdown_deadline:
                        break
                else:
                    if now >= self._next_sweep:
                        self.repository.sweep()
                        self._recover()
                        self._reconcile()
                        self._next_sweep = now + 1
                    if self.ready():
                        self._publish_health(True)
                        self._admit()
                    else:
                        self._publish_health(False)
                self._stop.wait(self.poll_interval) if not self._stop.is_set() else time.sleep(0.02)
        finally:
            self._healthy = False
            for job in list(self._active.values()):
                job.cancel.set()
            try:
                self.repository.mark_heartbeat(False)
                self._published_ready = False
            except Exception:
                pass
            self._release_lock()

    def _publish_health(self, ready: bool) -> None:
        if ready or self._published_ready is not ready:
            self.repository.mark_heartbeat(ready)
            self._published_ready = ready

    def _recover(self) -> None:
        for job_id in list(self._recovering):
            if self.executor.cleanup(job_id):
                self.repository.fail(job_id, "WORKER_RESTARTED")
                del self._recovering[job_id]

    def _reconcile(self) -> None:
        reconcile = getattr(self.executor, "reconcile", None)
        self._reconciled = reconcile(set(self._active)) if reconcile is not None else True

    def _admit(self) -> None:
        limit = self.repository.settings.max_running
        while len(self._active) < limit and not self._stop.is_set():
            record = self.repository.claim()
            if record is None:
                break
            job = _ActiveJob(record)
            self._active[record.job_id] = job
            thread = threading.Thread(
                target=self._analyze,
                args=(job,),
                name=f"nishtyak-job-{record.job_id}",
                daemon=True,
            )
            thread.start()

    def _analyze(self, job: _ActiveJob) -> None:
        try:
            job.result = self.executor.analyze(
                job.record,
                self.repository.archive_path(job.record.project_id),
                job.cancel,
            )
        except Exception:
            job.result = AnalysisJobResult("failed", code="WORKER_FAILED")
        finally:
            try:
                job.cleanup_confirmed = self.executor.cleanup(job.record.job_id)
            except Exception:
                job.cleanup_confirmed = False
            job.retry_at = time.monotonic() + 1
            job.finished.set()

    def _reap(self) -> None:
        for job_id, job in list(self._active.items()):
            if not job.finished.is_set():
                continue
            if not job.cleanup_confirmed and time.monotonic() >= job.retry_at:
                try:
                    job.cleanup_confirmed = self.executor.cleanup(job_id)
                except Exception:
                    job.cleanup_confirmed = False
                job.retry_at = time.monotonic() + 1
            if job.cleanup_confirmed:
                self.repository.finish(job_id, job.result)
                del self._active[job_id]

    def _acquire_lock(self) -> None:
        if self._lock_fd is not None:
            raise DispatcherAlreadyRunning("Dispatcher lock is already held")
        root = Path(self.repository.settings.data_dir)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(
            root / "dispatcher.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            os.close(descriptor)
            raise DispatcherAlreadyRunning("Another dispatcher owns this data directory") from error
        self._lock_fd = descriptor
        try:
            bind_namespace = getattr(self.executor, "bind_namespace", None)
            if bind_namespace is not None:
                bind_namespace(root)
        except Exception:
            self._release_lock()
            raise

    def _release_lock(self) -> None:
        descriptor, self._lock_fd = self._lock_fd, None
        if descriptor is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
