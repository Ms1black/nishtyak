import json
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from nishtyak_core.jobs import AnalysisJobResult, JobPolicy
from nishtyak_core.models import AnalysisOptions
from nishtyak_core.report_codec import AnalysisReportCodec
from nishtyak_service.execution import DockerExecutor, SubprocessExecutor


def archive_at(tmp_path):
    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("src/main.py", "import os\nimport numpy\n")
    return path


def record():
    return SimpleNamespace(job_id="a" * 32, options=AnalysisOptions(source_roots=("src",)))


def test_subprocess_service_executor_preserves_nondefault_options(tmp_path):
    executor = SubprocessExecutor()
    result = executor.analyze(record(), archive_at(tmp_path), threading.Event())
    assert result.status == "succeeded"
    assert result.report.options.source_roots == ("src",)
    assert executor.cleanup(record().job_id)
    assert not executor._processes


def test_subprocess_executor_cancels_a_real_process_before_wall_timeout(tmp_path):
    class SlowExecutor(SubprocessExecutor):
        def _command(self, archive, options, result):
            return [sys.executable, "-I", "-c", "import time; time.sleep(60)"]

    executor = SlowExecutor()
    cancelled = threading.Event()
    results = []
    worker = threading.Thread(
        target=lambda: results.append(executor.analyze(record(), archive_at(tmp_path), cancelled))
    )
    worker.start()
    deadline = time.monotonic() + 2
    while not executor._processes and time.monotonic() < deadline:
        time.sleep(0.01)
    cancelled.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert results[0].code == "JOB_CANCELLED"
    assert not executor._processes


def test_subprocess_executor_has_a_wall_time_limit(tmp_path):
    class SlowExecutor(SubprocessExecutor):
        def _command(self, archive, options, result):
            return [sys.executable, "-I", "-c", "import time; time.sleep(60)"]

    result = SlowExecutor(JobPolicy(wall_time_seconds=0.05)).analyze(
        record(), archive_at(tmp_path), threading.Event()
    )
    assert result.status == "timed_out"
    assert result.code == "WALL_TIME_LIMIT"


def test_worker_cli_receives_options_without_a_host_mount(tmp_path):
    path = archive_at(tmp_path)
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "nishtyak_core.worker",
            "--archive",
            "-",
            "--output",
            "-",
            "--options-json",
            json.dumps(asdict(record().options)),
        ],
        input=path.read_bytes(),
        capture_output=True,
        check=True,
    )
    result = json.loads(process.stdout)
    assert result["status"] == "succeeded"
    assert AnalysisReportCodec().decode(result["report"]).options.source_roots == ("src",)


def test_worker_cli_rejects_invalid_inline_options(tmp_path):
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "nishtyak_core.worker",
            "--archive",
            "-",
            "--output",
            "-",
            "--options-json",
            '{"max_files": true}',
        ],
        input=archive_at(tmp_path).read_bytes(),
        capture_output=True,
        check=True,
    )
    assert json.loads(process.stdout)["code"] == "OPTIONS_INVALID"


def test_docker_command_has_fixed_isolation_and_no_mounts_or_secrets(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "never-forward-this-key")
    command = DockerExecutor()._command(record().job_id, record().options)
    assert command[:2] == ["docker", "create"]
    for flag, value in {
        "--network": "none",
        "--user": "65532:65532",
        "--cap-drop": "ALL",
        "--security-opt": "no-new-privileges",
        "--memory": str(512 * 1024**2),
        "--memory-swap": str(512 * 1024**2),
        "--pids-limit": "32",
        "--log-driver": "none",
    }.items():
        assert command[command.index(flag) + 1] == value
    assert "--read-only" in command
    assert "--mount" not in command and "--volume" not in command and "-v" not in command
    assert "--env" not in command and "-e" not in command
    assert "never-forward-this-key" not in " ".join(command)
    assert json.loads(command[command.index("--options-json") + 1])["source_roots"] == ["src"]


@pytest.mark.parametrize("job_id", ["--privileged", "../other", "x;id", "x/y", ""])
def test_docker_job_names_are_validated(job_id):
    # Leading dashes are safe only after the fixed container prefix.
    if job_id == "--privileged":
        assert DockerExecutor().container_name(job_id).endswith("---privileged")
    else:
        with pytest.raises(ValueError):
            DockerExecutor().container_name(job_id)


def test_docker_readiness_requires_linux_daemon_and_analyzer_image(monkeypatch):
    replies = iter(
        [subprocess.CompletedProcess([], 0, b"linux\n"), subprocess.CompletedProcess([], 0)]
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: next(replies))
    assert DockerExecutor().ready()
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b"windows")
    )
    assert not DockerExecutor().ready()


@pytest.mark.parametrize(
    ("returncode", "stderr", "cleaned"),
    [
        (1, b"Error response from daemon: No such container: nishtyak-job-x", True),
        (1, b"error: no such object: nishtyak-job-x", True),
        (1, b"error: no such container: nishtyak-job-x", True),
        (1, b"Cannot connect to the Docker daemon", False),
        (1, b"permission denied", False),
    ],
)
def test_docker_cleanup_fails_closed_on_unconfirmed_removal(
    monkeypatch, returncode, stderr, cleaned
):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], returncode, b"", stderr),
    )
    assert DockerExecutor().cleanup(record().job_id) is cleaned


class LocalDockerProbe(DockerExecutor):
    """Exercise bounded CLI I/O without requiring or pretending to run Docker."""

    def __init__(self, code, policy=None):
        super().__init__(policy)
        self.code = code
        self.cleaned = []

    def _create(self, job_id, options, cancel_event, deadline):
        return None

    def _start_command(self, job_id):
        return [sys.executable, "-I", "-c", self.code]

    def cleanup(self, job_id):
        self.cleaned.append(job_id)
        return True


def test_docker_transport_decodes_worker_failure_envelopes(tmp_path):
    executor = LocalDockerProbe(
        'print(\'{"status":"failed","report":null,"code":"ARCHIVE_INVALID"}\')'
    )
    result = executor.analyze(record(), archive_at(tmp_path), threading.Event())
    assert result.code == "ARCHIVE_INVALID"
    assert executor.cleaned == [record().job_id]


def test_docker_transport_bounds_stdout_and_cleans_up(tmp_path):
    executor = LocalDockerProbe(
        "import os; os.write(1, b'x'*1000000)", JobPolicy(max_result_bytes=128)
    )
    result = executor.analyze(record(), archive_at(tmp_path), threading.Event())
    assert result.status == "resource_limited"
    assert result.code == "RESULT_LIMIT"
    assert executor.cleaned == [record().job_id]


def test_docker_transport_cancels_client_and_explicitly_removes_container(tmp_path):
    executor = LocalDockerProbe("import time; time.sleep(60)")
    cancel = threading.Event()
    timer = threading.Timer(0.05, cancel.set)
    timer.start()
    try:
        result = executor.analyze(record(), archive_at(tmp_path), cancel)
    finally:
        timer.cancel()
    assert result.code == "JOB_CANCELLED"
    assert executor.cleaned == [record().job_id]


def test_docker_transport_reports_cleanup_failure(tmp_path):
    class FailedCleanup(LocalDockerProbe):
        def cleanup(self, job_id):
            return False

    executor = FailedCleanup(
        'print(\'{"status":"failed","report":null,"code":"ARCHIVE_INVALID"}\')'
    )
    assert (
        executor.analyze(record(), archive_at(tmp_path), threading.Event()).code
        == "WORKER_CLEANUP_FAILED"
    )


def test_executor_namespace_is_persistent_and_distinct_for_each_service(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    executor = DockerExecutor()
    executor.bind_namespace(first)
    original = executor.container_name(record().job_id)
    restarted = DockerExecutor()
    restarted.bind_namespace(first)
    assert restarted.container_name(record().job_id) == original
    other = DockerExecutor()
    other.bind_namespace(second)
    assert other.container_name(record().job_id) != original


def test_executor_namespace_rejects_corrupt_or_symlink_state(tmp_path):
    path = tmp_path / "executor.namespace"
    path.write_text("partial namespace")
    with pytest.raises(ValueError):
        DockerExecutor().bind_namespace(tmp_path)
    path.unlink()
    target = tmp_path / "other-service-namespace"
    target.write_text("a" * 32)
    path.symlink_to(target)
    with pytest.raises(OSError):
        DockerExecutor().bind_namespace(tmp_path)


def owned_container(executor, identifier="b" * 64, job_id=None, namespace=None):
    return {
        "id": identifier,
        "labels": {
            "nishtyak.managed": "true",
            "nishtyak.instance_id": namespace or executor._namespace,
            "nishtyak.job_id": job_id or record().job_id,
        },
    }


def test_cleanup_inspects_owned_labels_and_removes_by_id_not_name(monkeypatch):
    executor = DockerExecutor()
    container = owned_container(executor)
    calls = []

    def control(command, deadline):
        calls.append(command)
        output = json.dumps(container).encode() if command[1] == "inspect" else b""
        return subprocess.CompletedProcess(command, 0, output, b"")

    monkeypatch.setattr(executor, "_control", control)
    assert executor.cleanup(record().job_id)
    assert calls[-1] == ["docker", "rm", "--force", container["id"]]


def test_cleanup_never_removes_another_service_container(monkeypatch):
    executor = DockerExecutor()
    container = owned_container(executor, namespace="c" * 32)
    calls = []

    def control(command, deadline):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(container).encode(), b"")

    monkeypatch.setattr(executor, "_control", control)
    assert not executor.cleanup(record().job_id)
    assert all(command[1] != "rm" for command in calls)


def test_reconciliation_cleans_terminal_ghosts_and_preserves_active_jobs(monkeypatch):
    executor = DockerExecutor()
    active, ghost = "a" * 64, "b" * 64
    containers = {
        active: owned_container(executor, active),
        ghost: owned_container(executor, ghost, job_id="c" * 32),
    }
    removed = []

    def control(command, deadline):
        if command[1] == "ps":
            assert f"label=nishtyak.instance_id={executor._namespace}" in command
            assert "label=nishtyak.managed=true" in command
            output = ("\n".join(containers) + "\n").encode()
        elif command[1] == "inspect":
            output = json.dumps(containers[command[-1]]).encode()
        else:
            removed.append(command[-1])
            output = b""
        return subprocess.CompletedProcess(command, 0, output, b"")

    monkeypatch.setattr(executor, "_control", control)
    assert executor.reconcile({record().job_id})
    assert removed == [ghost]


def test_reconciliation_fails_closed_and_does_not_remove_foreign_instances(monkeypatch):
    executor = DockerExecutor()
    foreign = owned_container(executor, namespace="d" * 32)
    removed = []

    def control(command, deadline):
        if command[1] == "ps":
            output = (foreign["id"] + "\n").encode()
        elif command[1] == "inspect":
            output = json.dumps(foreign).encode()
        else:
            removed.append(command[-1])
            output = b""
        return subprocess.CompletedProcess(command, 0, output, b"")

    monkeypatch.setattr(executor, "_control", control)
    assert not executor.reconcile(set())
    assert not removed


def test_late_create_ack_can_never_start_a_cancelled_container(tmp_path):
    class CancelledCreate(LocalDockerProbe):
        def _create(self, job_id, options, cancel_event, deadline):
            self.creation_command = self._command(job_id, options)
            cancel_event.set()
            return None

        def _start_command(self, job_id):
            raise AssertionError("Cancelled created containers must never be started")

    executor = CancelledCreate("")
    result = executor.analyze(record(), archive_at(tmp_path), threading.Event())
    assert result.code == "JOB_CANCELLED"
    assert executor.creation_command[:2] == ["docker", "create"]
    assert executor.cleaned == [record().job_id]


def test_create_timeout_never_attempts_to_start_a_container(tmp_path):
    class SlowCreate(LocalDockerProbe):
        def _create(self, job_id, options, cancel_event, deadline):
            return DockerExecutor._create(self, job_id, options, cancel_event, deadline)

        def _command(self, job_id, options):
            return [sys.executable, "-I", "-c", "import time; time.sleep(60)"]

        def _start_command(self, job_id):
            raise AssertionError("A create timeout must not start a container")

    executor = SlowCreate("", JobPolicy(wall_time_seconds=0.05))
    result = executor.analyze(record(), archive_at(tmp_path), threading.Event())
    assert result.code == "WALL_TIME_LIMIT"
    assert executor.cleaned == [record().job_id]


def test_reconciliation_removes_a_late_created_container_after_job_already_finished(tmp_path):
    class LateDaemonCreate(DockerExecutor):
        def __init__(self):
            super().__init__()
            self.container = None

        def _create(self, job_id, options, cancel_event, deadline):
            assert self._command(job_id, options)[:2] == ["docker", "create"]
            return AnalysisJobResult("timed_out", code="WALL_TIME_LIMIT")

        def _start_command(self, job_id):
            raise AssertionError("An unacknowledged create must never be started")

        def _control(self, command, deadline):
            if command[1] == "ps":
                value = (self.container["id"] + "\n").encode() if self.container else b""
            elif command[1] == "inspect":
                if self.container is None:
                    return subprocess.CompletedProcess(command, 1, b"", b"No such object: job")
                value = json.dumps(self.container).encode()
            else:
                self.container = None
                value = b""
            return subprocess.CompletedProcess(command, 0, value, b"")

    executor = LateDaemonCreate()
    result = executor.analyze(record(), archive_at(tmp_path), threading.Event())
    assert result.status == "timed_out"
    # Simulate the daemon finishing create after the client died and cleanup saw
    # NoSuchContainer. The job is now terminal and absent from dispatcher.active.
    executor.container = owned_container(executor)
    assert executor.reconcile(set())
    assert executor.container is None
