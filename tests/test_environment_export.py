import hashlib
import tomllib
import zipfile
from dataclasses import replace

import pytest
import yaml

from nishtyak_core import ReproducibilityService, SnapshotBuilder
from nishtyak_core.environment_export import EnvironmentExportGenerator
from nishtyak_core.exporting import BundleWriter
from nishtyak_core.locking import DependencyLock, ResolutionTarget, resolution_input_id
from nishtyak_core.models import (
    AnalysisOptions,
    AnalysisReport,
    GeneratedFiles,
    PackagePlan,
    UserDecisions,
)


def plan_for(requirements=(), *, python="3.11"):
    report = AnalysisReport("a" * 20, "b" * 64, AnalysisOptions(python_version="3.11"), ())
    return PackagePlan(report, tuple(requirements), None, python, (), (), (), "draft")


def lock_for(plan, packages, *, python="3.11", platform="linux-x86_64"):
    target = ResolutionTarget(python, platform)
    text = "".join(raw + " --hash=sha256:" + "c" * 64 + "\n" for raw in packages)
    identifier = resolution_input_id(plan, target)
    return DependencyLock(
        hashlib.sha256((identifier + "\0" + text).encode()).hexdigest(),
        plan.analysis_id,
        identifier,
        target,
        text,
        tuple(packages),
    )


def formats(plan, lock=None):
    files = {
        file.path: file.content.decode()
        for file in EnvironmentExportGenerator().generate(plan, lock=lock)
    }
    return tomllib.loads(files["pyproject.toml"]), yaml.safe_load(files["environment.yml"]), files


def test_locked_profile_pins_only_direct_dependencies_and_preserves_extras():
    plan = plan_for(["numpy>=1.26", "pandas[performance]>=2"])
    lock = lock_for(plan, ["numpy==2.2.0", "pandas==2.2.3", "python-dateutil==2.9.0.post0"])
    project, environment, files = formats(plan, lock)
    assert project["project"]["dependencies"] == ["numpy==2.2.0", "pandas[performance]==2.2.3"]
    assert project["project"]["requires-python"] == "==3.11.*"
    assert "build-system" not in project
    assert project["tool"]["nishtyak-reproducibility"] == {
        "generated": True,
        "author-environment-restored": False,
        "execution-verified": False,
        "dependency-mode": "locked",
        "package-state": "draft",
        "omitted-requirement-count": 0,
        "source-directory": "project",
        "target-python": "3.11",
        "target-platform": "linux-x86_64",
        "lock-file": "requirements.lock.txt",
    }
    assert environment["dependencies"] == [
        "python=3.11",
        "pip",
        {"pip": ["--only-binary=:all:", "--require-hashes", "-r requirements.lock.txt"]},
    ]
    assert "не покрыты проверкой PyPI/OSV" in files["environment.yml"]
    assert "Сервис не создавал среду" in files["environment.yml"]
    assert "pip install" not in "\n".join(files.values())
    assert project["tool"]["uv"]["package"] is False


def test_active_direct_markers_are_retained_and_inactive_packages_are_omitted():
    plan = plan_for(['numpy>=1; sys_platform == "linux"', 'colorama; sys_platform == "win32"'])
    lock = lock_for(plan, ["numpy==2.2.0"])
    project, _, _ = formats(plan, lock)
    assert project["project"]["dependencies"] == ['numpy==2.2.0; sys_platform == "linux"']


def test_explicit_patch_target_is_not_silently_broadened_in_conda():
    plan = plan_for(python="3.11.7")
    project, environment, _ = formats(plan, lock_for(plan, [], python="3.11.7"))
    assert project["project"]["requires-python"] == "==3.11.7"
    assert environment["dependencies"][0] == "python=3.11.7"


def test_unlocked_profile_keeps_declared_constraints_without_a_pip_installer_section():
    project, environment, files = formats(plan_for(["numpy>=1.26", "pandas<3"]))
    assert project["project"]["dependencies"] == ["numpy>=1.26", "pandas<3"]
    assert project["tool"]["nishtyak-reproducibility"]["dependency-mode"] == "draft"
    assert environment["dependencies"] == ["python=3.11", "pip"]
    assert "Черновик" in files["environment.yml"]
    assert "numpy>=1.26" in files["environment.yml"]
    assert "-r requirements" not in files["environment.yml"]
    assert "--require-hashes" not in files["environment.yml"]


def test_unknown_python_never_uses_the_default_analysis_profile_as_author_evidence():
    project, environment, files = formats(plan_for(["numpy"], python=None))
    assert "requires-python" not in project["project"]
    assert "target-python" not in project["tool"]["nishtyak-reproducibility"]
    assert environment["dependencies"] == []
    assert "Версия Python неизвестна" in files["environment.yml"]
    assert "3.11" not in files["pyproject.toml"] + files["environment.yml"]


def test_known_python_constraint_is_preserved_without_inventing_a_concrete_interpreter():
    plan = plan_for(python=None)
    plan = replace(plan, report=replace(plan.report, python_requires=">=3.10,<3.13"))
    project, environment, _ = formats(plan)
    assert project["project"]["requires-python"] == "<3.13,>=3.10"
    assert environment["dependencies"] == []


@pytest.mark.parametrize(
    "requirement",
    [
        "numpy @ https://example.org/numpy.whl",
        "private @ git+https://example.org/repo.git",
        "private @ file:///tmp/private.whl",
        "../local",
        "numpy\n--extra-index-url https://example.org/simple",
    ],
)
def test_unsafe_references_are_not_copied_into_generated_installation_formats(requirement):
    project, environment, files = formats(plan_for(["numpy", requirement]))
    assert project["project"]["dependencies"] == ["numpy"]
    assert project["tool"]["nishtyak-reproducibility"]["omitted-requirement-count"] == 1
    assert "Часть объявлений зависимостей не перенесена" in files["pyproject.toml"]
    assert "Часть объявлений зависимостей не перенесена" in files["environment.yml"]
    assert environment["dependencies"] == ["python=3.11", "pip"]
    assert requirement not in "\n".join(files.values())


def test_unsafe_reference_in_locked_plan_still_rejects_the_whole_lock_export():
    plan = plan_for(["numpy"])
    lock = lock_for(plan, ["numpy==2.2.0"])
    with pytest.raises(ValueError, match="URL"):
        formats(replace(plan, requirements=("numpy @ https://example.org/file.whl",)), lock)


def test_stale_or_incomplete_lock_cannot_label_the_profile_as_locked():
    plan = plan_for(["numpy>=2"])
    lock = lock_for(plan, ["numpy==2.2.0"])
    with pytest.raises(ValueError, match="current package plan"):
        formats(replace(plan, requirements=("numpy>=2.1",)), lock)
    with pytest.raises(ValueError, match="omitted"):
        formats(plan, lock_for(plan, []))
    with pytest.raises(ValueError, match="satisfy"):
        formats(plan, lock_for(plan, ["numpy==1.26.0"]))
    with pytest.raises(ValueError, match="checksum"):
        formats(plan, replace(lock, id="0" * 64))


def test_local_modules_and_stdlib_are_not_exported_as_installable_dependencies():
    snapshot = SnapshotBuilder().from_mapping(
        {
            "main.py": "import math\nfrom helper import value\nprint(math.sqrt(value))\n",
            "helper.py": "value = 4\n",
        }
    )
    service = ReproducibilityService()
    report = service.analyze(snapshot)
    plan = service.prepare(report, UserDecisions(report.analysis_id))
    project, _, _ = formats(plan)
    assert project["project"]["dependencies"] == []


def test_original_author_formats_remain_unchanged_under_project_in_exported_zip(tmp_path):
    originals = {
        "main.py": "print(42)\n",
        "pyproject.toml": '[project]\nname="author-project"\nversion="7.0"\n',
        "environment.yml": "name: author-environment\ndependencies: [python=3.11]\n",
    }
    snapshot = SnapshotBuilder().from_mapping(originals)
    service = ReproducibilityService()
    report = service.analyze(snapshot)
    plan = service.prepare(report, UserDecisions(report.analysis_id))
    generated = GeneratedFiles(EnvironmentExportGenerator().generate(plan))
    output = tmp_path / "package.zip"
    BundleWriter().write(snapshot, plan, generated, output)
    with zipfile.ZipFile(output) as archive:
        for name, original in originals.items():
            assert archive.read("project/" + name).decode() == original
        assert tomllib.loads(archive.read("pyproject.toml").decode())["project"]["name"] == (
            "reproducibility-profile"
        )
        assert yaml.safe_load(archive.read("environment.yml"))["name"] == "reproducibility-profile"


@pytest.mark.parametrize("value", ['quote " and slash \\', "Русский текст\n\t\x1f\x7f"])
def test_toml_string_quoting_roundtrips_unicode_quotes_and_controls(value):
    assert tomllib.loads("value = " + EnvironmentExportGenerator._string(value))["value"] == value
