import io
import zipfile
from dataclasses import replace

import pytest

from nishtyak_core import ReproducibilityService, SnapshotBuilder
from nishtyak_core.exporting import BundleWriter
from nishtyak_core.installation import EnvironmentInstructions
from nishtyak_core.locking import (
    LockExporter,
    ResolutionTarget,
    VersionResolver,
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
    lock = VersionResolver().resolve(plan, target)
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
            "pyproject.toml",
            "environment.yml",
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


@pytest.mark.parametrize("receipt", [{}, {"status": "checked"}, {"status": "unavailable"}])
def test_audited_archive_retains_pyproject_without_installing_unchecked_build_packages(
    tmp_path, receipt
):
    manifest = (
        '[build-system]\nrequires=["bad-provider==1.0"]\n'
        'build-backend="bad_provider.build"\n'
        '[project]\nname="experiment"\nversion="0.1"\n'
    )
    snapshot, service, plan = prepare({"pyproject.toml": manifest}, root="research")
    plan = replace(plan, automatic_resolution={"package_safety": receipt})
    target = ResolutionTarget("3.11", "linux-x86_64")
    lock = VersionResolver().resolve(plan, target)
    plan = replace(
        plan, automatic_resolution={"package_safety": receipt, "safety_lock_id": lock.id}
    )
    generated = LockExporter().attach(
        service.render(plan, snapshot=snapshot), plan, lock, snapshot=snapshot
    )
    BundleWriter().write(snapshot, plan, generated, tmp_path / "package.zip")
    with zipfile.ZipFile(tmp_path / "package.zip") as archive:
        text = archive.read("README.md").decode()
        assert archive.read("project/research/pyproject.toml").decode() == manifest
        assert "python -m pip install --no-deps" not in text
        assert "pip install ./project" not in text
        assert "build-system.requires" in text
        assert "изолированную среду сборки" in text
        assert "--only-binary=:all: --require-hashes -r requirements.lock.txt" in text
        dockerfile = archive.read("Dockerfile").decode()
        assert "--require-hashes -r requirements.lock.txt" in dockerfile
        assert "pip install ./project" not in dockerfile


@pytest.mark.parametrize("filename", ["environment.yml", "environment.yaml"])
@pytest.mark.parametrize("platform", ["linux-x86_64", "windows-x86_64"])
def test_audited_conda_archive_keeps_unchecked_yaml_but_only_recommends_generated_pip_lock(
    tmp_path, filename, platform
):
    manifest = (
        "name: author\nchannels: [conda-forge]\ndependencies:\n"
        "  - python=3.11\n  - pip\n  - pip:\n    - bad-provider==1.0\n"
    )
    snapshot, service, plan = prepare({filename: manifest}, root="research package")
    plan = replace(plan, automatic_resolution={"package_safety": {"status": "checked"}})
    target = ResolutionTarget("3.11", platform)
    lock = VersionResolver().resolve(plan, target)
    generated = LockExporter().attach(
        service.render(plan, snapshot=snapshot, target_platform=platform),
        plan,
        lock,
        snapshot=snapshot,
    )
    BundleWriter().write(snapshot, plan, generated, tmp_path / "package.zip")
    with zipfile.ZipFile(tmp_path / "package.zip") as archive:
        text = archive.read("README.md").decode()
        assert archive.read("project/research package/" + filename).decode() == manifest
        assert "conda env create --prefix ./.conda-env --file environment.yml" in text
        assert "--file 'project/research package/" not in text
        assert "каналы Conda" in text
        assert "отдельная проверка исходного YAML" in text
        assert "--only-binary=:all: --require-hashes -r requirements.lock.txt" in text


def test_audited_author_dockerfile_is_retained_without_build_or_run_recommendations(tmp_path):
    dockerfile = "FROM python:3.11-slim\nRUN pip install bad-provider==1.0\n"
    snapshot, service, plan = prepare(
        {"Dockerfile": dockerfile, "environment.yml": "dependencies: [python=3.11]\n"}
    )
    plan = replace(plan, automatic_resolution={"package_safety": {"status": "checked"}})
    generated = service.render(plan, snapshot=snapshot)
    BundleWriter().write(snapshot, plan, generated, tmp_path / "package.zip")
    with zipfile.ZipFile(tmp_path / "package.zip") as archive:
        text = archive.read("README.md").decode()
        assert archive.read("project/Dockerfile").decode() == dockerfile
        assert "## Dockerfile автора" in text
        assert "базовый образ, команды RUN" in text
        assert "docker build" not in text
        assert "docker run" not in text
        assert "conda env create" not in text
        assert "python -m pip install -r requirements.txt" not in text
        assert "транзитивных зависимостей" in text


def test_absent_service_receipt_keeps_cli_manifest_installation_behavior():
    snapshot, service, plan = prepare(
        {
            "pyproject.toml": '[project]\nname="experiment"\nversion="0.1"\n',
            "environment.yml": "dependencies: [python=3.11]\n",
        }
    )
    plan = replace(plan, automatic_resolution={"package_safety": None})
    text = service.render(plan, snapshot=snapshot).get("README.md").decode()
    assert "python -m pip install --no-deps ./project" in text
    assert "conda env create" in text


@pytest.mark.parametrize("platform", [None, "linux-x86_64", "windows-x86_64"])
def test_audited_unlocked_requirements_do_not_offer_unchecked_transitive_installation(platform):
    snapshot, _, plan = prepare({})
    plan = replace(plan, automatic_resolution={"package_safety": {"status": "checked"}})
    instructions = EnvironmentInstructions(plan, "3.11", platform, snapshot=snapshot)
    text = "\n".join(instructions.installation())
    assert "-m venv .venv" in text
    assert "pip install" not in text
    assert "транзитивных зависимостей" in text
    assert "проверенный lock-файл" in text
    assert "Выберите один способ" not in text


def test_generated_locked_profiles_describe_only_their_retained_secure_installation():
    snapshot, _, plan = prepare(
        {"environment.yml": "dependencies: [python=3.11, unchecked-native-library]\n"}
    )
    plan = replace(plan, automatic_resolution={"package_safety": {"status": "checked"}})
    text = "\n".join(
        EnvironmentInstructions(
            plan,
            "3.11",
            "linux-x86_64",
            locked=True,
            generated_paths=("pyproject.toml", "environment.yml", "requirements.lock.txt"),
            snapshot=snapshot,
        ).installation()
    )
    assert "Корневой `pyproject.toml` — новый профиль зависимостей" in text
    assert "служебные имя и версия" in text
    assert "pip install ." not in text
    assert "--only-binary=:all: --require-hashes -r requirements.lock.txt" in text
    assert "conda env create --prefix ./.conda-env --file environment.yml" in text
    assert "доступной для записи" in text
    assert "не охватывает Python, pip, каналы и системные пакеты Conda" in text
    assert "не является полным Conda-lock" in text
    assert "Исходный YAML автора в `project/` сохранён отдельно" in text
    assert "--file project/environment.yml" not in text
    assert "dependency-lock.json" not in text


@pytest.mark.parametrize(
    ("locked", "python_version", "platform"),
    [(False, "3.11", "linux-x86_64"), (True, "unknown", "linux-x86_64"), (True, "3.11", None)],
)
def test_generated_conda_draft_has_no_create_or_install_command(locked, python_version, platform):
    _, _, plan = prepare({})
    plan = replace(plan, automatic_resolution={"package_safety": {"status": "checked"}})
    instructions = EnvironmentInstructions(
        plan,
        python_version,
        platform,
        locked=locked,
        generated_paths=("pyproject.toml", "environment.yml"),
    )
    text = "\n".join(instructions._generated_profiles())
    assert "черновик без установки пакетов проекта" in text
    assert "conda env create" not in text
    assert "conda activate" not in text
    assert "pip install" not in text


def test_absent_generated_profiles_do_not_add_phantom_conda_or_metadata_guides():
    _, _, plan = prepare({})
    text = "\n".join(EnvironmentInstructions(plan, "3.11", "linux-x86_64").installation())
    assert "Другие форматы среды" not in text
    assert "Корневой `pyproject.toml`" not in text
    assert "Корневой `environment.yml`" not in text
