from __future__ import annotations

import json
import math
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from .environment import AuthorEnvironmentSnapshot
from .models import AnalysisOptions, AnalysisReport
from .report_codec import AnalysisReportCodec, InvalidWorkerReport, bounded_json

AUTHOR_ENVIRONMENT_MAX_BYTES = 256 * 1024


@dataclass(frozen=True)
class JobPolicy:
    wall_time_seconds: float = 30.0
    cpu_time_seconds: int = 15
    memory_bytes: int = 512 * 1024 * 1024
    max_result_bytes: int = 8 * 1024 * 1024
    max_open_files: int = 64
    max_concurrent_jobs: int = 1

    def __post_init__(self) -> None:
        if (
            isinstance(self.wall_time_seconds, bool)
            or not isinstance(self.wall_time_seconds, (int, float))
            or not math.isfinite(self.wall_time_seconds)
            or self.wall_time_seconds <= 0
        ):
            raise ValueError("Wall timeout must be finite and positive")
        for name, value in vars(self).items():
            if name != "wall_time_seconds" and (type(value) is not int or value <= 0):
                raise ValueError("Job limits must be positive integers")
        if self.max_concurrent_jobs > 2:
            raise ValueError("This runner supports one or two concurrent jobs")
        if self.max_result_bytes < 128:
            raise ValueError("Result limit must fit the worker error envelope")


@dataclass(frozen=True)
class AnalysisJobResult:
    status: str
    report: AnalysisReport | None = None
    code: str | None = None


class AnalysisJobRunner:
    """Fresh bounded process per job, not a filesystem or network sandbox.

    Create one shared runner per service instance. Cross-process admission and container
    isolation are responsibilities of the server deployment.
    """

    def __init__(self, policy: JobPolicy | None = None):
        self.policy = policy or JobPolicy()
        self._slots = threading.BoundedSemaphore(self.policy.max_concurrent_jobs)
        self._codec = AnalysisReportCodec()

    def run(
        self,
        archive_path: str | Path,
        options: AnalysisOptions | None = None,
        author_environment: AuthorEnvironmentSnapshot | None = None,
    ) -> AnalysisJobResult:
        if not self._slots.acquire(blocking=False):
            return AnalysisJobResult("failed", code="WORKER_BUSY")
        try:
            if author_environment is not None:
                return self._run(archive_path, options or AnalysisOptions(), author_environment)
            return self._run(archive_path, options or AnalysisOptions())
        finally:
            self._slots.release()

    def _run(
        self,
        archive_path: str | Path,
        options: AnalysisOptions,
        author_environment: AuthorEnvironmentSnapshot | None = None,
    ) -> AnalysisJobResult:
        try:
            options = self._codec.decode_options(asdict(options))
        except (TypeError, ValueError):
            return AnalysisJobResult("failed", code="OPTIONS_INVALID")
        try:
            environment = self._encode_environment(author_environment)
        except (TypeError, ValueError):
            return AnalysisJobResult("failed", code="AUTHOR_ENVIRONMENT_INVALID")
        try:
            archive = Path(archive_path)
            if archive.is_symlink() or not archive.is_file():
                return AnalysisJobResult("failed", code="ARCHIVE_UNAVAILABLE")
            archive = archive.resolve(strict=True)
            with tempfile.TemporaryDirectory(prefix="nishtyak-job-") as directory:
                root = Path(directory)
                options_path, result_path = root / "options.json", root / "result.json"
                options_path.write_bytes(bounded_json(asdict(options), 64 * 1024))
                command = self._command(archive, options_path, result_path)
                if environment is not None:
                    environment_path = root / "author-environment.json"
                    environment_path.write_bytes(environment)
                    command.extend(["--author-environment", str(environment_path)])
                with subprocess.Popen(
                    command,
                    cwd=root,
                    env=self._environment(root),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                ) as process:
                    try:
                        process.wait(timeout=self.policy.wall_time_seconds)
                    except subprocess.TimeoutExpired:
                        self._kill(process)
                        return AnalysisJobResult("timed_out", code="WALL_TIME_LIMIT")
                    if process.returncode != 0:
                        return self._terminated(process.returncode)
                return self._read_result(result_path)
        except (OSError, ValueError):
            return AnalysisJobResult("failed", code="WORKER_UNAVAILABLE")

    def _command(self, archive: Path, options: Path, result: Path) -> list[str]:
        return [
            sys.executable,
            "-I",
            "-m",
            "nishtyak_core.worker",
            "--archive",
            str(archive),
            "--options",
            str(options),
            "--output",
            str(result),
            "--memory-bytes",
            str(self.policy.memory_bytes),
            "--cpu-seconds",
            str(self.policy.cpu_time_seconds),
            "--max-report-bytes",
            str(self.policy.max_result_bytes),
            "--max-open-files",
            str(self.policy.max_open_files),
        ]

    @staticmethod
    def _encode_environment(environment: AuthorEnvironmentSnapshot | None) -> bytes | None:
        if environment is None:
            return None
        content = bounded_json(asdict(environment), AUTHOR_ENVIRONMENT_MAX_BYTES)
        parsed = AuthorEnvironmentSnapshot.from_dict(json.loads(content))
        return bounded_json(asdict(parsed), AUTHOR_ENVIRONMENT_MAX_BYTES)

    @staticmethod
    def _environment(root: Path) -> dict[str, str]:
        # An allowlist deliberately excludes all application secrets, proxy settings and
        # import/loader overrides. -I independently ignores Python environment settings.
        return {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": str(root),
            "TMP": str(root),
            "TEMP": str(root),
        }

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        finally:
            process.wait()

    @staticmethod
    def _terminated(returncode: int) -> AnalysisJobResult:
        limits = {
            signal.SIGKILL,
            getattr(signal, "SIGXCPU", -100),
            getattr(signal, "SIGXFSZ", -101),
        }
        if -returncode in limits:
            return AnalysisJobResult("resource_limited", code="PROCESS_RESOURCE_LIMIT")
        return AnalysisJobResult("failed", code="WORKER_TERMINATED")

    def _read_result(self, path: Path) -> AnalysisJobResult:
        try:
            if not stat.S_ISREG(path.lstat().st_mode):
                raise InvalidWorkerReport("Invalid result path")
            with path.open("rb") as stream:
                content = stream.read(self.policy.max_result_bytes + 1)
            if len(content) > self.policy.max_result_bytes:
                return AnalysisJobResult("resource_limited", code="RESULT_LIMIT")
            value = json.loads(content)
            if not isinstance(value, dict) or set(value) != {"status", "report", "code"}:
                raise InvalidWorkerReport("Invalid worker envelope")
            if value["status"] == "succeeded" and value["code"] is None:
                return AnalysisJobResult("succeeded", self._codec.decode(value["report"]))
            if (
                value["status"] not in {"failed", "resource_limited"}
                or value["report"] is not None
                or value["code"]
                not in {
                    "ARCHIVE_INVALID",
                    "ARCHIVE_LIMIT",
                    "ARCHIVE_PATH_INVALID",
                    "ARCHIVE_PATH_CONFLICT",
                    "ARCHIVE_ENTRY_INVALID",
                    "ARCHIVE_ENCRYPTED",
                    "ARCHIVE_COMPRESSION",
                    "ARCHIVE_UNAVAILABLE",
                    "OPTIONS_INVALID",
                    "AUTHOR_ENVIRONMENT_INVALID",
                    "ANALYSIS_INVALID",
                    "ANALYSIS_FAILED",
                    "MEMORY_LIMIT",
                    "RESULT_LIMIT",
                    "LIMITS_UNAVAILABLE",
                    "REQUEST_INVALID",
                }
            ):
                raise InvalidWorkerReport("Invalid worker failure")
            return AnalysisJobResult(value["status"], code=value["code"])
        except (OSError, ValueError, TypeError, RecursionError):
            return AnalysisJobResult("failed", code="WORKER_PROTOCOL_INVALID")
