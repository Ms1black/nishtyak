from __future__ import annotations

import json
import os
import re
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from nishtyak_core.jobs import AnalysisJobResult, AnalysisJobRunner, JobPolicy
from nishtyak_core.models import AnalysisOptions
from nishtyak_core.report_codec import bounded_json


class AnalysisJob(Protocol):
    job_id: str
    options: AnalysisOptions


class JobExecutor(Protocol):
    def analyze(
        self, record: AnalysisJob, archive_path: Path, cancel_event: threading.Event
    ) -> AnalysisJobResult: ...

    def cleanup(self, job_id: str) -> bool: ...

    def ready(self) -> bool: ...


class SubprocessExecutor(AnalysisJobRunner):
    """Development executor; this is not a filesystem or network sandbox."""

    def __init__(self, policy: JobPolicy | None = None):
        super().__init__(policy)
        self._processes: dict[str, subprocess.Popen] = {}
        self._process_lock = threading.Lock()

    def ready(self) -> bool:
        return os.name == "posix"

    def analyze(
        self, record: AnalysisJob, archive_path: Path, cancel_event: threading.Event
    ) -> AnalysisJobResult:
        if cancel_event.is_set():
            return AnalysisJobResult("failed", code="JOB_CANCELLED")
        try:
            options = self._codec.decode_options(asdict(record.options))
            archive = Path(archive_path)
            if archive.is_symlink() or not archive.is_file():
                return AnalysisJobResult("failed", code="ARCHIVE_UNAVAILABLE")
            archive = archive.resolve(strict=True)
            with tempfile.TemporaryDirectory(prefix="nishtyak-job-") as directory:
                root = Path(directory)
                options_path, result_path = root / "options.json", root / "result.json"
                options_path.write_bytes(bounded_json(asdict(options), 64 * 1024))
                process = subprocess.Popen(
                    self._command(archive, options_path, result_path),
                    cwd=root,
                    env=self._environment(root),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
                with self._process_lock:
                    self._processes[record.job_id] = process
                try:
                    result = self._wait_for_process(process, cancel_event)
                    if result is not None:
                        return result
                    return self._read_result(result_path)
                finally:
                    if self.cleanup(record.job_id):
                        with self._process_lock:
                            self._processes.pop(record.job_id, None)
        except (OSError, ValueError, TypeError):
            return AnalysisJobResult("failed", code="WORKER_UNAVAILABLE")

    def _wait_for_process(
        self, process: subprocess.Popen, cancel_event: threading.Event
    ) -> AnalysisJobResult | None:
        deadline = time.monotonic() + self.policy.wall_time_seconds
        while True:
            if cancel_event.is_set():
                self._stop_process(process)
                return AnalysisJobResult("failed", code="JOB_CANCELLED")
            if time.monotonic() >= deadline:
                self._stop_process(process)
                return AnalysisJobResult("timed_out", code="WALL_TIME_LIMIT")
            returncode = process.poll()
            if returncode is not None:
                return self._terminated(returncode) if returncode else None
            cancel_event.wait(min(0.05, max(0, deadline - time.monotonic())))

    def cleanup(self, job_id: str) -> bool:
        with self._process_lock:
            process = self._processes.get(job_id)
        if process is None:
            return True
        return self._stop_process(process)

    @staticmethod
    def _stop_process(process: subprocess.Popen) -> bool:
        if process.poll() is not None:
            return True
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=2)
            return True
        except ProcessLookupError:
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False


class DockerExecutor:
    """Host-side launcher. The API container must never receive the Docker socket."""

    def __init__(self, policy: JobPolicy | None = None, image: str = "nishtyak-analyzer:0.1.0"):
        if not image or image.startswith("-") or any(char.isspace() for char in image):
            raise ValueError("A configured analyzer image is required")
        self.policy = policy or JobPolicy()
        self.image = image
        self._reader = AnalysisJobRunner(self.policy)
        self._namespace = uuid.uuid4().hex

    def bind_namespace(self, data_dir: Path) -> None:
        """Load the persistent identity while the dispatcher holds its global flock."""
        root = Path(data_dir)
        path = root / "executor.namespace"
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Invalid executor namespace file") from None
                raw_namespace = stream.read(129)
                namespace = raw_namespace.decode("ascii").strip()
                if len(raw_namespace) > 128 or re.fullmatch(r"[0-9a-f]{32}", namespace) is None:
                    raise ValueError("Invalid executor namespace") from None
        else:
            namespace = uuid.uuid4().hex
            with os.fdopen(descriptor, "wb") as stream:
                stream.write((namespace + "\n").encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
            directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        self._namespace = namespace

    def container_name(self, job_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
            raise ValueError("Invalid job identifier")
        return f"nishtyak-job-{self._namespace}-{job_id}"

    def ready(self) -> bool:
        try:
            info = subprocess.run(
                ["docker", "info", "--format", "{{.OSType}}"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if info.returncode or info.stdout.strip() != b"linux":
                return False
            image = subprocess.run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", self.image],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            return image.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def analyze(
        self, record: AnalysisJob, archive_path: Path, cancel_event: threading.Event
    ) -> AnalysisJobResult:
        if cancel_event.is_set():
            return AnalysisJobResult("failed", code="JOB_CANCELLED")
        try:
            archive = Path(archive_path)
            if archive.is_symlink() or not archive.is_file():
                return AnalysisJobResult("failed", code="ARCHIVE_UNAVAILABLE")
            if archive.stat().st_size > 10 * 1024 * 1024:
                return AnalysisJobResult("failed", code="ARCHIVE_LIMIT")
            options = self._reader._codec.decode_options(asdict(record.options))
            deadline = time.monotonic() + self.policy.wall_time_seconds
            outcome = self._create(record.job_id, options, cancel_event, deadline)
            if outcome is not None:
                self.cleanup(record.job_id)
                return outcome
            if cancel_event.is_set():
                self.cleanup(record.job_id)
                return AnalysisJobResult("failed", code="JOB_CANCELLED")
            with tempfile.TemporaryDirectory(prefix="nishtyak-docker-job-") as directory:
                result_path = Path(directory) / "result.json"
                with archive.open("rb") as source, result_path.open("xb") as output:
                    process = subprocess.Popen(
                        self._start_command(record.job_id),
                        stdin=source,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        close_fds=True,
                    )
                    overflow, read_failed = threading.Event(), threading.Event()
                    reader = threading.Thread(
                        target=self._copy_result,
                        args=(process.stdout, output, overflow, read_failed),
                        name="nishtyak-docker-result",
                        daemon=True,
                    )
                    reader.start()
                    try:
                        outcome = self._wait(process, cancel_event, overflow, deadline)
                    finally:
                        SubprocessExecutor._stop_process(process)
                        reader.join(timeout=2)
                        if not reader.is_alive() and process.stdout is not None:
                            process.stdout.close()
                    if not self.cleanup(record.job_id):
                        return AnalysisJobResult("failed", code="WORKER_CLEANUP_FAILED")
                    if outcome is not None:
                        return outcome
                    if overflow.is_set():
                        return AnalysisJobResult("resource_limited", code="RESULT_LIMIT")
                    if reader.is_alive() or read_failed.is_set():
                        return AnalysisJobResult("failed", code="WORKER_PROTOCOL_INVALID")
                    output.flush()
                    return self._reader._read_result(result_path)
        except (OSError, ValueError, TypeError):
            return AnalysisJobResult("failed", code="WORKER_UNAVAILABLE")

    def _copy_result(self, source, destination, overflow, failed) -> None:
        total = 0
        try:
            while chunk := source.read(64 * 1024):
                total += len(chunk)
                if total > self.policy.max_result_bytes:
                    overflow.set()
                    return
                destination.write(chunk)
        except (OSError, ValueError):
            failed.set()

    def _create(self, job_id, options, cancel_event, deadline) -> AnalysisJobResult | None:
        # A late daemon response can leave an inert created container, but can never
        # start one: starting requires a separate acknowledged create operation.
        process = subprocess.Popen(
            self._command(job_id, options),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        try:
            return self._wait(process, cancel_event, threading.Event(), deadline)
        finally:
            SubprocessExecutor._stop_process(process)

    def _start_command(self, job_id: str) -> list[str]:
        return ["docker", "start", "--attach", "--interactive", self.container_name(job_id)]

    def _wait(self, process, cancel_event, overflow, deadline=None) -> AnalysisJobResult | None:
        deadline = deadline or (time.monotonic() + self.policy.wall_time_seconds)
        while True:
            if cancel_event.is_set():
                return AnalysisJobResult("failed", code="JOB_CANCELLED")
            if overflow.is_set():
                return AnalysisJobResult("resource_limited", code="RESULT_LIMIT")
            if time.monotonic() >= deadline:
                return AnalysisJobResult("timed_out", code="WALL_TIME_LIMIT")
            returncode = process.poll()
            if returncode is not None:
                if returncode in {137, 152, 153}:
                    return AnalysisJobResult("resource_limited", code="PROCESS_RESOURCE_LIMIT")
                if returncode:
                    return AnalysisJobResult("failed", code="WORKER_TERMINATED")
                return None
            cancel_event.wait(min(0.05, max(0, deadline - time.monotonic())))

    def cleanup(self, job_id: str) -> bool:
        try:
            deadline = time.monotonic() + 5
            container = self._inspect_owned(self.container_name(job_id), deadline)
            if container is None:
                return True
            if container["labels"]["nishtyak.job_id"] != job_id:
                return False
            return self._remove(container["id"], deadline)
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            return False

    def reconcile(self, active_job_ids: set[str]) -> bool:
        """Remove only this persistent service instance's abandoned containers."""
        deadline = time.monotonic() + 5
        try:
            listing = self._control(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--no-trunc",
                    "--filter",
                    "label=nishtyak.managed=true",
                    "--filter",
                    f"label=nishtyak.instance_id={self._namespace}",
                    "--format",
                    "{{.ID}}",
                ],
                deadline,
            )
            if listing.returncode or len(listing.stdout) > 64 * 1024:
                return False
            identifiers = listing.stdout.decode("ascii").splitlines()
            for identifier in identifiers[:32]:
                if re.fullmatch(r"[0-9a-f]{64}", identifier) is None:
                    return False
                container = self._inspect_owned(identifier, deadline)
                if container is None:
                    continue
                if container["labels"]["nishtyak.job_id"] not in active_job_ids:
                    if not self._remove(container["id"], deadline):
                        return False
            return len(identifiers) <= 32
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            return False

    def _inspect_owned(self, identifier: str, deadline: float) -> dict | None:
        result = self._control(
            [
                "docker",
                "inspect",
                "--format",
                '{"id":{{json .Id}},"labels":{{json .Config.Labels}}}',
                identifier,
            ],
            deadline,
        )
        if self._absent(result):
            return None
        if result.returncode or len(result.stdout) > 16 * 1024:
            raise ValueError("Cannot inspect container ownership")
        container = json.loads(result.stdout)
        if (
            not isinstance(container, dict)
            or set(container) != {"id", "labels"}
            or not isinstance(container["id"], str)
            or re.fullmatch(r"[0-9a-f]{64}", container["id"]) is None
            or not isinstance(container["labels"], dict)
            or container["labels"].get("nishtyak.managed") != "true"
            or container["labels"].get("nishtyak.instance_id") != self._namespace
            or not isinstance(container["labels"].get("nishtyak.job_id"), str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", container["labels"]["nishtyak.job_id"]) is None
        ):
            raise ValueError("Container is not owned by this service instance")
        return container

    def _remove(self, identifier: str, deadline: float) -> bool:
        result = self._control(["docker", "rm", "--force", identifier], deadline)
        return result.returncode == 0 or self._absent(result)

    @staticmethod
    def _absent(result: subprocess.CompletedProcess) -> bool:
        return result.returncode == 1 and (
            b"no such container:" in result.stderr.lower()
            or b"no such object:" in result.stderr.lower()
        )

    @staticmethod
    def _control(command: list[str], deadline: float) -> subprocess.CompletedProcess:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, 0)
        return subprocess.run(command, capture_output=True, timeout=min(remaining, 5), check=False)

    def _command(self, job_id: str, options: AnalysisOptions) -> list[str]:
        options_json = bounded_json(asdict(options), 64 * 1024).decode("utf-8")
        return [
            "docker",
            "create",
            "--interactive",
            "--init",
            "--pull=never",
            "--name",
            self.container_name(job_id),
            "--label",
            "nishtyak.managed=true",
            "--label",
            f"nishtyak.instance_id={self._namespace}",
            "--label",
            f"nishtyak.job_id={job_id}",
            "--network",
            "none",
            "--read-only",
            "--user",
            "65532:65532",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--cpus",
            "1",
            "--memory",
            str(self.policy.memory_bytes),
            "--memory-swap",
            str(self.policy.memory_bytes),
            "--pids-limit",
            "32",
            "--log-driver",
            "none",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=32m,uid=65532,gid=65532,mode=700",
            self.image,
            "--archive",
            "-",
            "--output",
            "-",
            "--options-json",
            options_json,
            "--memory-bytes",
            str(self.policy.memory_bytes),
            "--cpu-seconds",
            str(self.policy.cpu_time_seconds),
            "--max-report-bytes",
            str(self.policy.max_result_bytes),
            "--max-open-files",
            str(self.policy.max_open_files),
        ]
