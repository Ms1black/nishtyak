from __future__ import annotations

import hashlib
import io
import json
import signal
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from nishtyak_core import ReproducibilityService, SnapshotBuilder
from nishtyak_core.exporting import BundleWriter
from nishtyak_core.locking import (
    DependencyLock,
    LockExporter,
    ResolutionError,
    ResolutionTarget,
    VersionResolver,
    _validate_output,
    resolution_input_id,
)
from nishtyak_core.models import RunSpec, UserDecisions

HASH = "a" * 64
TARGET = ResolutionTarget("3.11", "linux-x86_64")


def plan_for(requirements=("requests>=2",), python="3.11", requires_python=">=3.11"):
    snapshot = SnapshotBuilder().from_mapping(
        {
            "experiment.py": "print('result')\n",
            "pyproject.toml": (
                '[project]\nname="example"\n'
                f"requires-python={json.dumps(requires_python)}\n"
                f"dependencies={json.dumps(list(requirements))}\n"
            ),
        }
    )
    service = ReproducibilityService()
    report = service.analyze(snapshot)
    plan = service.prepare(
        report,
        UserDecisions(
            report.analysis_id,
            run_spec=RunSpec("experiment.py"),
            python_version=python,
            data_confirmed=True,
        ),
    )
    return snapshot, plan


def make_lock(plan, target=TARGET, packages=("requests==2.32.0",)):
    text = "".join(f"{package} --hash=sha256:{HASH}\n" for package in packages)
    input_id = resolution_input_id(plan, target)
    identity = hashlib.sha256((input_id + "\0" + text).encode()).hexdigest()
    return DependencyLock(identity, plan.analysis_id, input_id, target, text, packages)


@pytest.mark.parametrize(
    "value",
    [
        "requests @ https://example.com/request.whl",
        "requests @ file:///etc/passwd",
        "requests @ git+https://example.com/repository.git",
        "-r /tmp/requirements.txt",
        "--index-url https://evil.invalid/simple",
        "./local-project",
        "-e .",
        "requests\n--extra-index-url https://evil.invalid",
        "requests\x00",
    ],
)
def test_resolver_rejects_non_named_inputs_before_spawning(monkeypatch, value):
    _, plan = plan_for()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("spawned"))
    with pytest.raises(ResolutionError) as error:
        VersionResolver().resolve(replace(plan, requirements=(value,)), TARGET)
    assert error.value.code == "REQUIREMENT_UNSAFE"


@pytest.mark.parametrize(
    "text",
    [
        f"requests>=2 --hash=sha256:{HASH}\n",
        f"requests==2.* --hash=sha256:{HASH}\n",
        f"requests===2 --hash=sha256:{HASH}\n",
        f"requests @ https://evil.invalid/pkg.whl --hash=sha256:{HASH}\n",
        "requests==2.32.0\n",
        "requests==2.32.0 --hash=md5:abc\n",
        f"requests==2.32.0 --hash=sha256:{HASH} --index-url https://evil.invalid\n",
        f"--extra-index-url https://evil.invalid\nrequests==2 --hash=sha256:{HASH}\n",
        f"requests==2 --hash=sha256:{HASH}\nRequests==3 --hash=sha256:{HASH}\n",
        f"python_dateutil==2 --hash=sha256:{HASH}\npython-dateutil==2 --hash=sha256:{HASH}\n",
    ],
)
def test_lock_parser_rejects_unpinned_urls_options_hash_injection_and_duplicate_names(text):
    with pytest.raises(ValueError):
        _validate_output(text)


def test_lock_parser_accepts_hash_continuations_and_preserves_pep508_marker():
    text = (
        'requests==2.32.0 ; sys_platform == "linux" \\\n'
        f" --hash=sha256:{HASH} \\\n"
        f" --hash=sha256:{'b' * 64}\n"
    )
    assert _validate_output(text) == ('requests==2.32.0; sys_platform == "linux"',)


@pytest.fixture
def fake_uv(monkeypatch, tmp_path):
    executable = tmp_path / "trusted-uv"
    executable.write_bytes(b"not executed")
    monkeypatch.setitem(
        __import__("sys").modules, "uv", SimpleNamespace(find_uv_bin=lambda: executable)
    )
    calls = []

    def install(output, *, returncode=0):
        def spawn(command, **kwargs):
            calls.append((command, kwargs))
            Path(command[6]).write_text(output)
            return SimpleNamespace(
                pid=123456,
                returncode=returncode,
                wait=lambda **kwargs: returncode,
                poll=lambda: returncode,
            )

        monkeypatch.setattr(subprocess, "Popen", spawn)
        return calls

    return install


def test_uv_receives_only_named_requirements_private_paths_and_clean_proxy_environment(
    monkeypatch, fake_uv
):
    _, plan = plan_for()
    fake_uv(f"requests==2.32.0 --hash=sha256:{HASH}\n")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-leak")
    monkeypatch.setenv("PIP_INDEX_URL", "https://secret:token@evil.invalid")
    monkeypatch.setenv("NO_PROXY", "*")
    calls = fake_uv(f"requests==2.32.0 --hash=sha256:{HASH}\n")
    result = VersionResolver().resolve(plan, TARGET)
    assert result.packages == ("requests==2.32.0",)
    command, kwargs = calls[-1]
    assert command[1:4] == ["-I", "-m", "nishtyak_core.resolver_worker"]
    assert kwargs["start_new_session"] is True
    environment = kwargs["env"]
    assert "DEEPSEEK_API_KEY" not in environment
    assert "PIP_INDEX_URL" not in environment
    assert environment["NO_PROXY"] == environment["no_proxy"] == ""
    assert environment["HTTPS_PROXY"].startswith("http://127.0.0.1:")
    assert len({environment[name] for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}) == 1
    assert "pyproject.toml" not in repr(command)


@pytest.mark.parametrize(
    "output,code",
    [
        ("", "RESOLUTION_INCOMPLETE"),
        (f"urllib3==2 --hash=sha256:{HASH}\n", "RESOLUTION_INCOMPLETE"),
        (f"requests==1 --hash=sha256:{HASH}\n", "RESOLUTION_CONSTRAINT_MISMATCH"),
        ("requests>=2\n", "RESOLUTION_INVALID"),
    ],
)
def test_uv_result_must_cover_direct_constraints(fake_uv, output, code):
    _, plan = plan_for()
    fake_uv(output)
    with pytest.raises(ResolutionError) as error:
        VersionResolver().resolve(plan, TARGET)
    assert error.value.code == code


@pytest.mark.parametrize(
    "platform,packages",
    [("linux-x86_64", ()), ("windows-x86_64", ("colorama==0.4.6",))],
)
def test_coverage_evaluates_markers_for_target_instead_of_host(fake_uv, platform, packages):
    _, plan = plan_for(('colorama; sys_platform == "win32"',))
    fake_uv("".join(f"{package} --hash=sha256:{HASH}\n" for package in packages))
    result = VersionResolver().resolve(plan, ResolutionTarget("3.11", platform))
    assert result.packages == packages


def test_timeout_kills_whole_resolver_process_group(monkeypatch, fake_uv):
    _, plan = plan_for()
    fake_uv("")
    process = SimpleNamespace(pid=123456, returncode=None)

    def wait(timeout):
        if process.returncode is None:
            raise subprocess.TimeoutExpired("resolver", timeout)
        return process.returncode

    process.wait = wait
    process.poll = lambda: process.returncode
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    killed = []

    def killpg(pid, sig):
        killed.append((pid, sig))
        process.returncode = -signal.SIGKILL

    monkeypatch.setattr("nishtyak_core.locking.os.killpg", killpg)
    with pytest.raises(ResolutionError) as error:
        VersionResolver(timeout=0.1).resolve(plan, TARGET)
    assert error.value.code == "RESOLUTION_TIMEOUT"
    assert killed == [(process.pid, signal.SIGKILL)]


@pytest.mark.parametrize(
    "selected,target,constraint,code",
    [
        ("3.11", "3.10", ">=3.8", "TARGET_PYTHON_MISMATCH"),
        ("3.11.9", "3.11.8", ">=3.8", "TARGET_PYTHON_MISMATCH"),
        (None, "3.10", ">=3.11", "TARGET_PYTHON_UNSUPPORTED"),
        (None, "3.11", ">=3.11.5", "TARGET_PATCH_REQUIRED"),
        (None, "3.11", "!=3.11.8", "TARGET_PATCH_REQUIRED"),
        (None, "3.11", "==3.11", "TARGET_PATCH_REQUIRED"),
    ],
)
def test_incompatible_or_ambiguous_python_target_is_rejected_without_network(
    monkeypatch, selected, target, constraint, code
):
    _, plan = plan_for((), requires_python=constraint)
    plan = replace(
        plan,
        python_version=selected,
        report=replace(plan.report, options=replace(plan.report.options, python_version=target)),
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("spawned"))
    with pytest.raises(ResolutionError) as error:
        VersionResolver().resolve(plan, ResolutionTarget(target, "linux-x86_64"))
    assert error.value.code == code


@pytest.mark.parametrize("constraint", [">=3.11.0", ">=3.8.5", "==3.11.*", "<3.12.4"])
def test_minor_target_is_allowed_when_python_constraint_is_constant_across_patches(constraint):
    _, plan = plan_for((), requires_python=constraint)
    assert VersionResolver().resolve(plan, TARGET).packages == ()


@pytest.mark.parametrize(
    "requirement,code",
    [
        ('requests; python_full_version >= "3.11.4"', "TARGET_PATCH_REQUIRED"),
        ('requests; platform_release == "6.1"', "TARGET_MARKER_UNSUPPORTED"),
    ],
)
def test_markers_requiring_unspecified_target_details_are_explained(requirement, code):
    _, plan = plan_for((requirement,))
    with pytest.raises(ResolutionError) as error:
        VersionResolver().resolve(plan, TARGET)
    assert error.value.code == code


def test_lock_roundtrip_detects_tampering_and_selected_python_changes():
    _, plan = plan_for()
    lock = make_lock(plan)
    assert DependencyLock.from_dict(json.loads(json.dumps(lock.to_dict()))) == lock
    tampered = {**lock.to_dict(), "requirements": lock.requirements.replace("2.32.0", "2.31.0")}
    with pytest.raises(ValueError):
        DependencyLock.from_dict(tampered)
    updated = replace(plan, python_version="3.11.9")
    assert resolution_input_id(updated, TARGET) != lock.input_id


def test_lock_export_stale_requirements_fail_and_descriptions_do_not_invalidate():
    _, plan = plan_for()
    lock = make_lock(plan)
    generated = ReproducibilityService().render(plan)
    with pytest.raises(ResolutionError) as error:
        LockExporter().attach(generated, replace(plan, requirements=("requests>=3",)), lock)
    assert error.value.code == "LOCK_STALE"
    assert LockExporter().attach(generated, replace(plan, description="Updated explanation"), lock)


@pytest.mark.parametrize(
    "platform,architecture", [("linux-x86_64", "amd64"), ("linux-aarch64", "arm64")]
)
def test_locked_zip_readme_and_docker_install_only_hashed_dependencies(
    tmp_path, platform, architecture
):
    snapshot, plan = plan_for()
    lock = make_lock(plan, ResolutionTarget("3.11", platform))
    generated = ReproducibilityService().render(plan, include_dockerfile=True)
    generated = LockExporter().attach(generated, plan, lock)
    destination = tmp_path / "reproduction.zip"
    BundleWriter().write(snapshot, plan, generated, destination)
    import zipfile

    with zipfile.ZipFile(io.BytesIO(destination.read_bytes())) as archive:
        readme = archive.read("README.md").decode()
        docker = archive.read("Dockerfile").decode()
        assert "python -m pip install -r requirements.txt" not in readme
        assert "--require-hashes -r requirements.lock.txt" in readme
        assert f"FROM --platform=linux/{architecture} python:3.11-slim" in docker
        assert "COPY requirements.lock.txt ./requirements.lock.txt" in docker
        assert "--require-hashes -r requirements.lock.txt" in docker
        assert "-r requirements.txt" not in docker
        assert "requests>=2" in archive.read("requirements.txt").decode()
        assert archive.read("requirements.lock.txt").decode() == lock.requirements
        assert json.loads(archive.read("dependency-lock.json"))["id"] == lock.id


def test_non_linux_lock_is_usable_without_docker_but_refuses_linux_container():
    _, plan = plan_for()
    lock = make_lock(plan, ResolutionTarget("3.11", "windows-x86_64"))
    exporter = LockExporter()
    assert exporter.attach(ReproducibilityService().render(plan), plan, lock)
    with pytest.raises(ResolutionError) as error:
        exporter.attach(ReproducibilityService().render(plan, include_dockerfile=True), plan, lock)
    assert error.value.code == "LOCK_DOCKER_UNSUPPORTED"


def test_target_change_requires_reanalysis_to_reclassify_stdlib(monkeypatch):
    _, plan = plan_for(())
    plan = replace(plan, python_version=None)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("spawned"))
    with pytest.raises(ResolutionError) as error:
        VersionResolver().resolve(plan, ResolutionTarget("3.12", "linux-x86_64"))
    assert error.value.code == "TARGET_PROFILE_MISMATCH"
