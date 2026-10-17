from dataclasses import replace

from nishtyak_core import ReproducibilityService, SnapshotBuilder
from nishtyak_core.locking import (
    DependencyLock,
    LockExporter,
    ResolutionTarget,
    resolution_input_id,
)
from nishtyak_core.models import AnalysisOptions, RunSpec, UserDecisions
from nishtyak_core.planning import PackagePlanner
from nishtyak_core.rendering import DocumentRenderer


def package():
    snapshot = SnapshotBuilder().from_mapping({"main.py": "print(42)\n"})
    report = ReproducibilityService().analyze(snapshot, AnalysisOptions(python_version="3.12"))
    return PackagePlanner().prepare(
        report,
        UserDecisions(
            report.analysis_id,
            python_version="3.12",
            run_spec=RunSpec("main.py"),
            description="Пример научного вычисления.",
            data_confirmed=True,
        ),
    )


def test_russian_readme_selects_the_required_interpreter():
    text = DocumentRenderer().render(package()).get("README.md").decode()
    assert text.startswith("# Как повторить эксперимент")
    assert "python3.12 -m venv .venv" in text
    assert "py -3.12 -m venv .venv" in text
    assert "\npython -m venv " not in text
    assert "## Проверка коллегой" in text
    assert "DATA_DEPENDENCIES_UNVERIFIED" not in text


def test_locked_readme_places_target_before_one_installation():
    plan = package()
    target = ResolutionTarget("3.12", "linux-x86_64")
    identifier = resolution_input_id(plan, target)
    lock = DependencyLock("a" * 64, plan.analysis_id, identifier, target, "", ())
    generated = LockExporter().attach(DocumentRenderer().render(plan), plan, lock)
    text = generated.get("README.md").decode()
    assert text.index("linux-x86_64") < text.index("## Установка")
    assert text.count("--require-hashes -r requirements.lock.txt") == 1
    assert "Activate.ps1" not in text
    assert "requirements.lock.txt\ndependency-lock.json" in text


def test_windows_readme_has_only_windows_environment_commands():
    plan = replace(package(), run_spec=RunSpec("main.py", args=("a'b",)))
    target = ResolutionTarget("3.12", "windows-x86_64")
    lock = DependencyLock(
        "b" * 64, plan.analysis_id, resolution_input_id(plan, target), target, "", ()
    )
    text = (
        LockExporter().attach(DocumentRenderer().render(plan), plan, lock).get("README.md").decode()
    )
    assert "py -3.12 -m venv .venv" in text
    assert ".\\.venv\\Scripts\\Activate.ps1" in text
    assert ".venv/bin/activate" not in text
    assert "Set-Location -LiteralPath 'project'" in text
    assert "python 'main.py' 'a''b'" in text
