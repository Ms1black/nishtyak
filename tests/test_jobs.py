import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import asdict

import pytest

from nishtyak_core import AnalysisOptions, ReproducibilityService, SnapshotBuilder
from nishtyak_core.jobs import AnalysisJobRunner, JobPolicy
from nishtyak_core.report_codec import AnalysisReportCodec, InvalidWorkerReport


def archive_at(tmp_path, files=None):
    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in (files or {"main.py": "import os\nimport pandas\n"}).items():
            archive.writestr(name, content)
    return path


def test_worker_analyzes_a_real_archive_and_preserves_typed_evidence(tmp_path):
    result = AnalysisJobRunner().run(archive_at(tmp_path))
    assert result.status == "succeeded", result
    assert result.code is None
    assert result.report is not None
    assert isinstance(result.report.options.source_roots, tuple)
    assert isinstance(result.report.imports[0].names, tuple)
    assert {item.module for item in result.report.imports} == {"os", "pandas"}
    assert all(item.id for item in result.report.findings)
    assert any(item.category == "stdlib" for item in result.report.dependencies)


def test_corrupt_archive_returns_a_safe_code(tmp_path):
    path = tmp_path / "secret-server-path.zip"
    path.write_bytes(b"not zip")
    result = AnalysisJobRunner().run(path)
    assert result.status == "failed"
    assert result.code == "ARCHIVE_INVALID"
    assert result.report is None
    assert "secret-server-path" not in repr(result)


def test_uploaded_python_and_build_files_are_never_executed(tmp_path):
    marker = tmp_path / "executed"
    malicious = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    path = archive_at(
        tmp_path,
        {
            "main.py": malicious,
            "sitecustomize.py": malicious,
            "setup.py": malicious,
            "pyproject.toml": '[build-system]\nrequires=[]\nbuild-backend="setup"\n',
        },
    )
    assert AnalysisJobRunner().run(path).status == "succeeded"
    assert not marker.exists()


def test_oversized_analysis_options_are_rejected_before_spawn(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Must not start worker for invalid options")

    monkeypatch.setattr(subprocess, "Popen", unexpected)
    runner = AnalysisJobRunner()
    result = runner.run(archive_at(tmp_path), AnalysisOptions(max_files=1001))
    assert result.code == "OPTIONS_INVALID"
    assert runner.run(tmp_path / "missing").code == "ARCHIVE_UNAVAILABLE"


def test_worker_cli_streams_archive_and_bounded_json(tmp_path):
    source = archive_at(tmp_path)
    process = subprocess.run(
        [sys.executable, "-I", "-m", "nishtyak_core.worker", "--archive", "-", "--output", "-"],
        input=source.read_bytes(),
        capture_output=True,
        check=True,
    )
    payload = json.loads(process.stdout)
    assert payload["status"] == "succeeded"
    assert process.stderr == b""
    report = AnalysisReportCodec().decode(payload["report"])
    assert report.files[0].path == "main.py"


def test_worker_cli_refuses_excessive_stdin(tmp_path):
    process = subprocess.run(
        [sys.executable, "-I", "-m", "nishtyak_core.worker", "--archive", "-", "--output", "-"],
        input=b"x" * (10 * 1024 * 1024 + 1),
        capture_output=True,
        check=True,
    )
    assert json.loads(process.stdout)["code"] == "ARCHIVE_LIMIT"


def test_worker_cli_rejects_oversized_options_file(tmp_path):
    source = archive_at(tmp_path)
    options = tmp_path / "options.json"
    options.write_text(" " * (64 * 1024 + 1))
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "nishtyak_core.worker",
            "--archive",
            str(source),
            "--output",
            "-",
            "--options",
            str(options),
        ],
        capture_output=True,
        check=True,
    )
    assert json.loads(process.stdout)["code"] == "OPTIONS_INVALID"


def test_worker_reports_a_bounded_output_instead_of_a_truncated_report(tmp_path):
    result = AnalysisJobRunner(JobPolicy(max_result_bytes=128)).run(archive_at(tmp_path))
    assert result.status == "resource_limited"
    assert result.code == "RESULT_LIMIT"


def test_child_does_not_inherit_application_or_import_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "a-private-value")
    monkeypatch.setenv("OTHER_APPLICATION_SECRET", "another-private-value")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("HTTP_PROXY", "http://private-proxy")

    class EnvironmentProbe(AnalysisJobRunner):
        def _command(self, archive, options, result):
            code = (
                "import json,os,pathlib,sys; "
                "unexpected=set(os.environ)-{'LANG','LC_ALL','HOME','TMPDIR','TMP','TEMP',"
                "'__CF_USER_TEXT_ENCODING'}; "
                "pathlib.Path(sys.argv[1]).write_text(json.dumps({'status':'failed', "
                "'report':None, 'code':'ANALYSIS_INVALID' if unexpected else 'ARCHIVE_INVALID'}))"
            )
            return [sys.executable, "-I", "-c", code, str(result)]

    assert EnvironmentProbe().run(archive_at(tmp_path)).code == "ARCHIVE_INVALID"


def test_wall_timeout_kills_worker_and_releases_the_slot(tmp_path):
    class SlowWorker(AnalysisJobRunner):
        def _command(self, archive, options, result):
            return [sys.executable, "-I", "-c", "import time; time.sleep(60)"]

    runner = SlowWorker(JobPolicy(wall_time_seconds=0.05))
    path = archive_at(tmp_path)
    start = time.monotonic()
    for _ in range(2):
        result = runner.run(path)
        assert result.status == "timed_out"
        assert result.code == "WALL_TIME_LIMIT"
    assert time.monotonic() - start < 5


def test_runner_refuses_busy_work_without_queueing(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class HeldRunner(AnalysisJobRunner):
        def _run(self, archive, options):
            entered.set()
            release.wait(timeout=5)
            return super()._run(archive, options)

    runner = HeldRunner()
    path = archive_at(tmp_path)
    thread = threading.Thread(target=runner.run, args=(path,))
    thread.start()
    try:
        assert entered.wait(timeout=5)
        result = runner.run(path)
        assert result.status == "failed"
        assert result.code == "WORKER_BUSY"
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_kernel_resource_termination_is_reported_safely(tmp_path):
    class LimitedWorker(AnalysisJobRunner):
        def _command(self, archive, options, result):
            return [
                sys.executable,
                "-I",
                "-c",
                "import os,signal; os.kill(os.getpid(),signal.SIGXCPU)",
            ]

    result = LimitedWorker().run(archive_at(tmp_path))
    assert result.status == "resource_limited"
    assert result.code == "PROCESS_RESOURCE_LIMIT"


def test_report_codec_roundtrip_preserves_the_complete_internal_report():
    report = ReproducibilityService().analyze(
        SnapshotBuilder().from_mapping(
            {
                "main.py": "from .local import item\nimport pandas\n",
                "requirements.txt": "pandas>=2; python_version>='3.11'\n",
            }
        )
    )
    codec = AnalysisReportCodec()
    encoded = json.loads(json.dumps(codec.encode(report)))
    assert codec.decode(encoded) == report
    encoded["files"][0]["size"] = True
    with pytest.raises(InvalidWorkerReport):
        codec.decode(encoded)


def test_report_codec_rejects_unrecognized_model_fields():
    report = ReproducibilityService().analyze(SnapshotBuilder().from_mapping({"main.py": "pass"}))
    value = asdict(report)
    value["__type__"] = "arbitrary.module.Class"
    with pytest.raises(InvalidWorkerReport):
        AnalysisReportCodec().decode(value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFOs")
def test_parent_refuses_a_fifo_result_without_blocking(tmp_path):
    path = tmp_path / "result.json"
    os.mkfifo(path)
    outcomes = []
    thread = threading.Thread(
        target=lambda: outcomes.append(AnalysisJobRunner()._read_result(path)), daemon=True
    )
    thread.start()
    thread.join(timeout=1)
    blocked = thread.is_alive()
    if blocked:
        # Unblock a regressed reader so the test also cleans up its thread.
        descriptor = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(descriptor, b"{}")
        finally:
            os.close(descriptor)
        thread.join(timeout=1)
    assert not blocked, "The result reader tried to open a FIFO"
    assert outcomes[0].code == "WORKER_PROTOCOL_INVALID"


def test_parent_refuses_a_symlink_result(tmp_path):
    target = tmp_path / "outside.json"
    target.write_text('{"status":"failed","report":null,"code":"ARCHIVE_INVALID"}')
    path = tmp_path / "result.json"
    path.symlink_to(target)
    assert AnalysisJobRunner()._read_result(path).code == "WORKER_PROTOCOL_INVALID"


@pytest.mark.parametrize(
    "change",
    [
        {"wall_time_seconds": float("nan")},
        {"wall_time_seconds": 0},
        {"memory_bytes": True},
        {"cpu_time_seconds": -1},
        {"max_concurrent_jobs": 3},
        {"max_result_bytes": 1},
    ],
)
def test_invalid_job_policies_are_rejected(change):
    with pytest.raises(ValueError):
        JobPolicy(**change)
