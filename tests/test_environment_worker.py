from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import zipfile
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from nishtyak_core.environment import AuthorEnvironmentSnapshot, InstalledDistribution
from nishtyak_core.jobs import AUTHOR_ENVIRONMENT_MAX_BYTES, AnalysisJobRunner
from nishtyak_core.models import AnalysisOptions
from nishtyak_service.execution import DockerExecutor, SubprocessExecutor


def environment():
    return AuthorEnvironmentSnapshot(
        "3.11.9", "Linux-x86_64", (InstalledDistribution("numpy", "1.26.4", ("numpy",)),)
    )


def source_zip(tmp_path):
    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("main.py", "import numpy\n")
    return path


def worker_command(*arguments):
    return [
        sys.executable,
        "-I",
        "-m",
        "nishtyak_core.worker",
        "--archive",
        "-",
        "--output",
        "-",
        *arguments,
    ]


def test_local_runner_uses_private_environment_file_and_removes_it(tmp_path):
    class ObservedRunner(AnalysisJobRunner):
        def _command(self, archive, options, result):
            self.private_directory = options.parent
            return super()._command(archive, options, result)

    runner = ObservedRunner()
    result = runner.run(source_zip(tmp_path), author_environment=environment())
    assert result.status == "succeeded"
    assert result.report.author_environment_id == environment().id
    assert result.report.author_python_version == "3.11.9"
    assert not runner.private_directory.exists()
    assert any(item.requirement == "numpy==1.26.4" for item in result.report.dependencies)


def test_service_subprocess_executor_passes_author_snapshot(tmp_path):
    record = SimpleNamespace(
        job_id="a" * 32, options=AnalysisOptions(), author_environment=environment()
    )
    result = SubprocessExecutor().analyze(record, source_zip(tmp_path), threading.Event())
    assert result.status == "succeeded"
    assert result.report.author_environment_id == environment().id


def test_worker_cli_accepts_bounded_inline_environment(tmp_path):
    process = subprocess.run(
        worker_command("--author-environment-json", json.dumps(asdict(environment()))),
        input=source_zip(tmp_path).read_bytes(),
        capture_output=True,
        timeout=5,
        check=True,
    )
    payload = json.loads(process.stdout)
    assert payload["status"] == "succeeded"
    assert payload["report"]["author_environment_id"] == environment().id
    assert process.stderr == b""


@pytest.mark.parametrize(
    "frame",
    [
        b"",
        b"xx",
        (0).to_bytes(4, "big"),
        (AUTHOR_ENVIRONMENT_MAX_BYTES + 1).to_bytes(4, "big"),
        (100).to_bytes(4, "big") + b"{}",
        (2).to_bytes(4, "big") + b"{}",
    ],
)
def test_worker_refuses_truncated_oversized_or_invalid_environment_frames(frame):
    process = subprocess.run(
        worker_command("--author-environment-stdin"),
        input=frame,
        capture_output=True,
        timeout=5,
        check=True,
    )
    assert json.loads(process.stdout)["code"] == "AUTHOR_ENVIRONMENT_INVALID"
    assert process.stderr == b""


def test_docker_transport_sends_large_environment_in_stdin_not_argv(tmp_path):
    class FramedWorkerProbe(DockerExecutor):
        def _create(self, job_id, options, cancel_event, deadline, author_environment_stdin=False):
            self.creation_command = self._command(job_id, options, author_environment_stdin)
            self.framed = author_environment_stdin
            return None

        def _start_command(self, job_id):
            assert self.framed
            return worker_command("--author-environment-stdin")

        def cleanup(self, job_id):
            return True

    large_environment = AuthorEnvironmentSnapshot(
        "3.11.9",
        "Linux",
        (
            *environment().distributions,
            *(
                InstalledDistribution(f"package-for-environment-{index:04d}", "1.0")
                for index in range(2300)
            ),
        ),
    )
    normalized = AuthorEnvironmentSnapshot.from_dict(asdict(large_environment))
    encoded = AnalysisJobRunner._encode_environment(normalized)
    assert 128 * 1024 < len(encoded) < AUTHOR_ENVIRONMENT_MAX_BYTES
    record = SimpleNamespace(
        job_id="a" * 32, options=AnalysisOptions(), author_environment=normalized
    )
    executor = FramedWorkerProbe()
    result = executor.analyze(record, source_zip(tmp_path), threading.Event())
    assert result.status == "succeeded", result
    assert result.report.author_environment_id == normalized.id
    assert "--author-environment-stdin" in executor.creation_command
    assert "package-for-environment" not in " ".join(executor.creation_command)


def test_archive_limit_applies_to_zip_bytes_after_environment_frame():
    metadata = AnalysisJobRunner._encode_environment(environment())
    frame = len(metadata).to_bytes(4, "big") + metadata + b"x" * (10 * 1024**2 + 1)
    process = subprocess.run(
        worker_command("--author-environment-stdin"),
        input=frame,
        capture_output=True,
        timeout=5,
        check=True,
    )
    assert json.loads(process.stdout)["code"] == "ARCHIVE_LIMIT"


def test_environment_header_does_not_reduce_the_existing_archive_budget():
    metadata = AnalysisJobRunner._encode_environment(environment())
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as stream:
        stream.writestr("main.py", "import numpy\n")
        stream.writestr("data.bin", b"x" * (10 * 1024**2 - 1024))
    content = archive.getvalue()
    assert len(content) <= 10 * 1024**2
    frame = len(metadata).to_bytes(4, "big") + metadata + content
    process = subprocess.run(
        worker_command("--author-environment-stdin"),
        input=frame,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert json.loads(process.stdout)["status"] == "succeeded"


def test_invalid_author_environment_cannot_add_environment_variables_or_commands(tmp_path):
    metadata = {
        **asdict(environment()),
        "DEEPSEEK_API_KEY": "private-key-value",
        "command": "execute-me",
    }
    process = subprocess.run(
        worker_command("--author-environment-json", json.dumps(metadata)),
        input=source_zip(tmp_path).read_bytes(),
        capture_output=True,
        timeout=5,
        check=True,
    )
    assert json.loads(process.stdout)["code"] == "AUTHOR_ENVIRONMENT_INVALID"
    assert b"private-key-value" not in process.stdout + process.stderr
