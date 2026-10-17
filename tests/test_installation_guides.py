import io
import zipfile
from dataclasses import replace

import pytest

from nishtyak_core import ReproducibilityService, SnapshotBuilder
from nishtyak_core.exporting import BundleWriter
from nishtyak_core.locking import (
    DependencyLock,
    LockExporter,
    ResolutionTarget,
    resolution_input_id,
)
from nishtyak_core.models import AnalysisOptions, RunSpec, UserDecisions


def prepare(files, *, root=".", excluded=(), extras=()):
    prefix = "" if root == "." else root + "/"
    snapshot = SnapshotBuilder().from_mapping(
        {prefix + name: content for name, content in {"main.py": "print(42)\n", **files}.items()}
    )
    service = ReproducibilityService()
    report = service.analyze(
        snapshot,
        AnalysisOptions(python_version="3.11", target_platform="linux-x86_64", project_root=root),
    )
    plan = service.prepare(
        report,
        UserDecisions(
            report.analysis_id,
            python_version="3.11",
            run_spec=RunSpec("main.py", working_directory=root),
            excluded_files=tuple(prefix + name for name in excluded),
            selected_extras=extras,
        ),
    )
    return snapshot, service, plan


def test_nested_pyproject_and_extras_keep_their_real_archive_path_after_lock_export(tmp_path):
    snapshot, service, plan = prepare(
        {
            "pyproject.toml": '[project]\nname="experiment"\nversion="0.1"\n'
            'requires-python=">=3.11"\n[project.optional-dependencies]\nplot=[]\n',
        },
        root="research",
        extras=("plot",),
    )
    target = ResolutionTarget("3.11", "linux-x86_64")
    lock = DependencyLock(
        "a" * 64, plan.analysis_id, resolution_input_id(plan, target), target, "", ()
    )
    generated = LockExporter().attach(
        service.render(plan, snapshot=snapshot), plan, lock, snapshot=snapshot
    )
    artifact = BundleWriter().write(snapshot, plan, generated, tmp_path / "package.zip")
    with zipfile.ZipFile(io.BytesIO((tmp_path / "package.zip").read_bytes())) as archive:
        text = archive.read("README.md").decode()
        assert artifact.state == "ready_to_verify"
        assert "project/research/pyproject.toml" in text
        assert "python -m pip install --no-deps './project/research[plot]'" in text
        assert "--require-hashes -r requirements.lock.txt" in text
        assert "docker build --platform linux/amd64 -t experiment ." in text
        assert "docker run --rm --platform linux/amd64 experiment" in text
        tree = text.split("## Состав пакета", 1)[1]
        for name in (
            "Dockerfile",
            "requirements.lock.txt",
            "dependency-lock.json",
        ):
            assert name in tree
            assert name in archive.namelist()


def test_tool_only_pyproject_is_not_advertised_as_an_installable_package():
    snapshot, service, plan = prepare({"pyproject.toml": "[tool.ruff]\nline-length=100\n"})
    text = service.render(plan, snapshot=snapshot).get("README.md").decode()
    assert "Проект из pyproject.toml" in text
    assert "настройки инструментов" in text
    assert "pip install --no-deps" not in text


def test_conda_gets_its_own_environment_instructions_without_silent_pip_conversion():
    snapshot, service, plan = prepare(
        {
            "environment.yml": "name: author\nchannels: [conda-forge]\n"
            "dependencies: [python=3.11, numpy=1.26]\nprefix: /authors/computer/env\n"
        },
        root="science",
    )
    generated = service.render(plan, snapshot=snapshot)
    text = generated.get("README.md").decode()
    assert "conda env create --prefix ./.conda-env --file project/science/environment.yml" in text
    assert "conda activate ./.conda-env" in text
    assert "не заменяет системные пакеты Conda" in text
    assert "python3.11 -m venv" not in text
    assert "/authors/computer" not in text
    assert "Dockerfile" not in {file.path for file in generated.files}
    assert "нужен отдельный профиль Conda" in text


def test_invalid_conda_is_retained_but_never_given_a_working_create_command():
    snapshot, service, plan = prepare({"environment.yml": "dependencies: [broken\n"})
    text = service.render(plan, snapshot=snapshot).get("README.md").decode()
    assert "структура или зависимости требуют исправления" in text
    assert "ограниченный pip-вариант" in text
    assert "conda env create" not in text


def test_excluded_manifests_do_not_generate_installation_recipes():
    snapshot, service, plan = prepare(
        {"pyproject.toml": '[project]\nname="sample"\nversion="0.1"\n'},
        excluded=("pyproject.toml",),
    )
    text = service.render(plan, snapshot=snapshot).get("README.md").decode()
    assert "Проект из pyproject.toml" not in text
    assert "pip install --no-deps" not in text


def test_author_dockerfile_remains_an_explicit_unverified_alternative():
    snapshot, service, plan = prepare(
        {
            "Dockerfile": "FROM python:3.11-slim\n",
            "environment.yaml": "dependencies: [python=3.11]\n",
        },
        root="research package",
    )
    text = service.render(plan, snapshot=snapshot).get("README.md").decode()
    assert "## Dockerfile автора" in text
    assert "docker build -f 'project/research package/Dockerfile'" in text
    assert "-t experiment-author 'project/research package'" in text
    assert (
        "conda env create --prefix ./.conda-env --file 'project/research package/environment.yaml'"
        in text
    )


def test_documentation_rejects_a_snapshot_from_another_project():
    snapshot, service, plan = prepare({})
    with pytest.raises(ValueError, match="different package plan"):
        service.render(plan, snapshot=replace(snapshot, fingerprint="another-project"))


def test_explicit_docker_optout_does_not_leave_phantom_build_commands():
    snapshot, service, plan = prepare({})
    generated = service.render(plan, snapshot=snapshot, include_dockerfile=False)
    text = generated.get("README.md").decode()
    assert "docker build" not in text
    assert "Генерация Dockerfile отключена" in text
    assert "Dockerfile" not in {file.path for file in generated.files}


def test_conda_pyproject_and_author_dockerfile_each_receive_their_actual_instructions():
    snapshot, service, plan = prepare(
        {
            "environment.yml": "dependencies: [python=3.11, pip]\n",
            "pyproject.toml": '[project]\nname="experiment"\nversion="0.1"\n',
            "Dockerfile": "FROM python:3.11-slim\n",
        }
    )
    text = service.render(plan, snapshot=snapshot).get("README.md").decode()
    assert "### Conda" in text
    assert "### Проект из pyproject.toml" in text
    assert "## Dockerfile автора" in text
    assert "conda env create --prefix ./.conda-env --file project/environment.yml" in text
    assert "python -m pip install --no-deps ./project" in text
    assert "docker build -f project/Dockerfile -t experiment-author project" in text
    assert "python3.11 -m venv" not in text


def test_explicit_export_target_is_used_for_the_readme_and_docker_eligibility():
    snapshot, service, plan = prepare({})
    generated = service.render(plan, snapshot=snapshot, target_platform="windows-x86_64")
    text = generated.get("README.md").decode()
    assert "Целевая ОС и архитектура: `windows-x86_64`" in text
    assert "Activate.ps1" in text
    assert "выбранная среда не Linux" in text
    assert "Dockerfile" not in {file.path for file in generated.files}
