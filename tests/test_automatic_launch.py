from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from dataclasses import replace

import pytest

from nishtyak_core.cli import CommandLine
from nishtyak_core.exporting import BundleWriter
from nishtyak_core.launches import LaunchDetector
from nishtyak_core.models import AnalysisOptions, RunSpec, UserDecisions
from nishtyak_core.service import ReproducibilityService
from nishtyak_core.snapshot import SnapshotBuilder


def prepare(files, **choices):
    service = ReproducibilityService()
    snapshot = SnapshotBuilder().from_mapping(files)
    report = service.analyze(snapshot)
    plan = service.prepare(report, UserDecisions(report.analysis_id, **choices))
    return snapshot, service, plan


def test_lone_scientific_script_needs_neither_main_guard_nor_manual_entry(tmp_path):
    snapshot, service, plan = prepare(
        {
            "experiment.py": "from pathlib import Path\nprint(Path('data.txt').read_text())\n",
            "data.txt": "42",
        }
    )
    assert plan.run_spec == RunSpec("experiment.py")
    assert plan.run_spec_origin == "inferred"
    assert plan.decisions.run_spec is None
    assert plan.state == "ready_to_verify"  # Existing input does not need a checkbox.
    artifact = BundleWriter().write(snapshot, plan, service.render(plan), tmp_path / "bundle.zip")
    with zipfile.ZipFile(artifact.path) as archive:
        readme = archive.read("README.md").decode()
        assert "cd project\npython experiment.py" in readme
        assert "inferred" in readme and "has not been confirmed" in readme
        assert "Ask the author" not in readme
        assert "  experiment.py" in readme
        archive.extractall(tmp_path / "colleague")
    result = subprocess.run(
        [sys.executable, "experiment.py"],
        cwd=tmp_path / "colleague/project",
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert result.stdout == "42\n"


def test_unguarded_experiment_is_selected_over_its_imported_local_helpers():
    _, _, plan = prepare(
        {
            "experiment.py": "import helpers\nprint(helpers.VALUE)\n",
            "helpers.py": "VALUE = 42\n",
            "tests/test_run.py": "if __name__ == '__main__':\n    print('test')\n",
        },
        data_confirmed=True,
    )
    assert plan.run_spec == RunSpec("experiment.py")
    assert plan.state == "ready_to_verify"
    assert {dependency.module for dependency in plan.report.dependencies} == {"helpers"}


def test_only_main_guard_is_a_better_signal_than_another_files_name():
    _, _, plan = prepare(
        {
            "calculate.py": "if __name__ == '__main__':\n    print(42)\n",
            "main.py": "def helper():\n    return 1\n",
        }
    )
    assert plan.run_spec == RunSpec("calculate.py")


def test_explicit_choice_wins_and_is_not_replaced_by_the_automatic_recommendation():
    _, _, plan = prepare(
        {"one.py": "print(1)\n", "two.py": "print(2)\n"},
        run_spec=RunSpec("two.py", args=("reviewed",)),
        data_confirmed=True,
    )
    assert plan.run_spec == RunSpec("two.py", args=("reviewed",))
    assert plan.run_spec_origin == "author"
    assert "RUN_INFERRED" not in {finding.code for finding in plan.findings}


def test_multiple_independent_scripts_remain_a_downloadable_draft_with_commands(tmp_path):
    snapshot, service, plan = prepare({"one.py": "print(1)\n", "two.py": "print(2)\n"})
    assert plan.run_spec is None and plan.state == "draft"
    assessment = LaunchDetector().detect(plan.report)
    assert assessment.recommended is None
    assert {candidate.run_spec.target for candidate in assessment.candidates} == {
        "one.py",
        "two.py",
    }
    artifact = BundleWriter().write(snapshot, plan, service.render(plan), tmp_path / "bundle.zip")
    with zipfile.ZipFile(artifact.path) as archive:
        readme = archive.read("README.md").decode()
    assert "python one.py" in readme and "python two.py" in readme
    assert "dependency and source package remains available" in readme


@pytest.mark.parametrize(
    "files",
    [
        {
            "lab/__init__.py": "",
            "lab/math.py": "def mean(data):\n    return sum(data) / len(data)\n",
        },
        {"helpers.py": "def useful_function():\n    return 42\n"},
        {"broken.py": "import ("},
        {"tests/test_main.py": "print(42)\n", "setup.py": "print('build hook')\n"},
    ],
)
def test_libraries_parse_failures_and_support_code_do_not_get_a_false_automatic_launch(files):
    _, _, plan = prepare(files)
    assert plan.run_spec is None
    assert plan.state == "draft"


def test_selected_root_and_source_layout_produce_an_automatic_callable_that_runs(tmp_path):
    snapshot, service, plan = prepare(
        {
            "project-main/pyproject.toml": (
                '[project]\ndependencies=[]\n[project.scripts]\nrun="lab.cli:main"\n'
            ),
            "project-main/src/lab/__init__.py": "",
            "project-main/src/lab/cli.py": "def main():\n    print('automatic-src-launch')\n",
        },
        data_confirmed=True,
    )
    assert plan.run_spec == RunSpec(
        "lab.cli:main", kind="callable", working_directory="project-main"
    )
    assert plan.run_spec_origin == "inferred"
    artifact = BundleWriter().write(snapshot, plan, service.render(plan), tmp_path / "bundle.zip")
    with zipfile.ZipFile(artifact.path) as archive:
        archive.extractall(tmp_path / "colleague")
    result = subprocess.run(
        [sys.executable, "-I", str(tmp_path / "colleague/run_project.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert result.stdout == "automatic-src-launch\n"


def test_package_main_uses_module_launch_instead_of_breaking_relative_imports(tmp_path):
    snapshot, service, plan = prepare(
        {
            "lab/__init__.py": "",
            "lab/__main__.py": "from .helper import VALUE\nprint(VALUE)\n",
            "lab/helper.py": "VALUE = 42\n",
        }
    )
    assert plan.run_spec == RunSpec("lab", kind="module")
    artifact = BundleWriter().write(snapshot, plan, service.render(plan), tmp_path / "bundle.zip")
    with zipfile.ZipFile(artifact.path) as archive:
        archive.extractall(tmp_path / "colleague")
    result = subprocess.run(
        [sys.executable, "-m", "lab"],
        cwd=tmp_path / "colleague/project",
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert result.stdout == "42\n"


def test_package_main_guard_also_preserves_relative_imports():
    _, _, plan = prepare(
        {
            "lab/__init__.py": "",
            "lab/calculate.py": (
                "from .helper import VALUE\nif __name__ == '__main__':\n    print(VALUE)\n"
            ),
            "lab/helper.py": "VALUE = 42\n",
        }
    )
    assert plan.run_spec == RunSpec("lab.calculate", kind="module")


def test_excluded_candidate_is_not_inferred_and_remaining_script_is_selected():
    _, _, plan = prepare(
        {"one.py": "print(1)\n", "two.py": "print(2)\n"}, excluded_files=("two.py",)
    )
    assert plan.run_spec == RunSpec("one.py")


def test_declared_script_ambiguity_does_not_fall_through_to_a_weaker_main_guard():
    _, _, plan = prepare(
        {
            "pyproject.toml": (
                "[project]\ndependencies=[]\n[project.scripts]\n"
                'one="lab.cli:first"\ntwo="lab.cli:second"\n'
            ),
            "lab/__init__.py": "",
            "lab/cli.py": (
                "def first(): pass\ndef second(): pass\nif __name__ == '__main__':\n    first()\n"
            ),
        }
    )
    assert plan.run_spec is None
    assert len(LaunchDetector().detect(plan.report).candidates) == 3


def test_already_supplied_environment_python_and_data_choices_clear_obsolete_questions():
    _, service, plan = prepare({"experiment.py": "print(42)\n"})
    report = replace(plan.report, author_python_version="3.11.1")
    confirmed = service.prepare(report, UserDecisions(report.analysis_id, data_confirmed=True))
    assert confirmed.python_version == "3.11.1"
    codes = {finding.code for finding in confirmed.findings}
    assert "PYTHON_VERSION_UNKNOWN" not in codes
    assert "DATA_DEPENDENCIES_UNVERIFIED" not in codes
    assert (
        "Observed author-environment Python version"
        in service.render(confirmed).get("README.md").decode()
    )


def test_configured_source_root_search_order_is_retained():
    snapshot = SnapshotBuilder().from_mapping({"a/lab.py": "", "b/lab.py": ""})
    service = ReproducibilityService()
    report = service.analyze(snapshot, AnalysisOptions(source_roots=("b", "a")))
    assert report.layout.source_roots == ("b", "a")


def test_cli_build_without_entry_exports_automatic_command(tmp_path, capsys):
    source = tmp_path / "source"
    source.mkdir()
    (source / "experiment.py").write_text("print(42)\n")
    output = tmp_path / "bundle.zip"
    assert CommandLine().run(["build", str(source), "--output", str(output)]) == 0
    capsys.readouterr()
    with zipfile.ZipFile(output) as archive:
        report = json.loads(archive.read("analysis-report.json"))
    assert report["run_spec"]["target"] == "experiment.py"
    assert report["run_spec_origin"] == "inferred"
    assert report["decisions"]["run_spec"] is None


def test_static_launch_detection_does_not_execute_top_level_code(tmp_path):
    marker = tmp_path / "executed"
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
    _, _, plan = prepare({"experiment.py": source})
    assert plan.run_spec == RunSpec("experiment.py")
    assert not marker.exists()


def test_declared_python_constraint_does_not_require_a_manual_version_for_draft_export():
    _, service, plan = prepare(
        {
            "pyproject.toml": '[project]\nrequires-python=">=3.11"\ndependencies=[]\n',
            "experiment.py": "print(42)\n",
        }
    )
    assert plan.python_version is None
    assert plan.state == "ready_to_verify"
    assert service.render(plan).get("README.md")
    assert [finding.code for finding in plan.findings].count("PYTHON_VERSION_UNKNOWN") == 1


def test_cli_lock_export_uses_the_selected_target_and_keeps_direct_requirements(
    tmp_path, capsys, monkeypatch
):
    from nishtyak_core.locking import DependencyLock, ResolutionTarget, resolution_input_id

    source = tmp_path / "source"
    source.mkdir()
    (source / "experiment.py").write_text("import numpy\nprint(numpy.arange(3))\n")
    output = tmp_path / "bundle.zip"
    seen = []

    class FakeResolver:
        def resolve(self, plan, target):
            seen.append((plan.requirements, target))
            locked = "numpy==2.2.0 --hash=sha256:" + "a" * 64 + "\n"
            input_id = resolution_input_id(plan, target)
            identifier = hashlib.sha256((input_id + "\0" + locked).encode()).hexdigest()
            return DependencyLock(
                identifier,
                plan.analysis_id,
                input_id,
                target,
                locked,
                ("numpy==2.2.0",),
            )

    monkeypatch.setattr("nishtyak_core.locking.VersionResolver", FakeResolver)
    assert (
        CommandLine().run(
            [
                "build",
                str(source),
                "--output",
                str(output),
                "--python",
                "3.11",
                "--lock",
                "--target-platform",
                "linux-x86_64",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert seen == [(("numpy",), ResolutionTarget("3.11", "linux-x86_64"))]
    with zipfile.ZipFile(output) as archive:
        assert "numpy==2.2.0" in archive.read("requirements.lock.txt").decode()
        assert "\nnumpy\n" in archive.read("requirements.txt").decode()
        assert "requirements.lock.txt" in archive.read("README.md").decode()
        metadata = json.loads(archive.read("dependency-lock.json"))
        assert metadata["target"] == {"python_version": "3.11", "platform": "linux-x86_64"}


@pytest.mark.parametrize(
    "flags,message",
    [
        (["--lock", "--target-platform", "linux-x86_64"], "--python"),
        (["--lock", "--python", "3.11"], "--target-platform"),
        (["--target-platform", "linux-x86_64"], "requires --lock"),
    ],
)
def test_cli_lock_requires_an_explicit_compatible_target(tmp_path, capsys, flags, message):
    source = tmp_path / "source"
    source.mkdir()
    (source / "experiment.py").write_text("print(42)\n")
    output = tmp_path / "bundle.zip"
    assert CommandLine().run(["build", str(source), "--output", str(output), *flags]) == 2
    assert message in capsys.readouterr().err
    assert not output.exists()


def test_selected_direct_script_clears_only_its_proven_sibling_import_context():
    files = {
        "README.md": "Example project",
        "tools/experiment.py": "import helper\nprint(helper.VALUE)\n",
        "tools/helper.py": "VALUE = 42\n",
    }
    _, service, plan = prepare(files, data_confirmed=True)
    assert plan.run_spec == RunSpec("tools/experiment.py")
    assert "LOCAL_IMPORT_CONTEXT_REQUIRED" in {item.code for item in plan.report.findings}
    assert "LOCAL_IMPORT_CONTEXT_REQUIRED" not in {item.code for item in plan.findings}
    assert plan.state == "ready_to_verify"
    module_plan = service.prepare(
        plan.report,
        UserDecisions(
            plan.analysis_id,
            run_spec=RunSpec("tools.experiment", kind="module"),
            data_confirmed=True,
        ),
    )
    assert "LOCAL_IMPORT_CONTEXT_REQUIRED" in {item.code for item in module_plan.findings}
    assert module_plan.state == "draft"


def test_current_package_mapping_and_pin_replace_stale_raw_dependency_questions():
    _, service, initial = prepare({"experiment.py": "import custom\nprint(custom.VALUE)\n"})
    assert [item.code for item in initial.findings].count("DEPENDENCY_UNRESOLVED") == 1
    resolved = service.prepare(
        initial.report,
        UserDecisions(
            initial.analysis_id,
            packages={"custom": "custom-distribution"},
            versions={"custom": "1.2"},
            data_confirmed=True,
        ),
    )
    assert resolved.requirements == ("custom-distribution==1.2",)
    assert not {"DEPENDENCY_UNRESOLVED", "VERSION_UNKNOWN", "VERSION_NOT_PINNED"} & {
        item.code for item in resolved.findings
    }
    assert resolved.state == "ready_to_verify"


@pytest.mark.parametrize(
    "python_version,profile,category",
    [("3.10", "3.10", "unresolved"), ("3.10.14", "3.10", "unresolved"), ("3.11", "3.11", "stdlib")],
)
def test_cli_analysis_classifies_imports_for_selected_python(
    tmp_path, capsys, python_version, profile, category
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "experiment.py").write_text("import tomllib\nprint(tomllib)\n")
    assert CommandLine().run(["analyze", str(source), "--python", python_version]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["options"]["python_version"] == profile
    assert report["dependencies"][0]["module"] == "tomllib"
    assert report["dependencies"][0]["category"] == category


def test_cli_build_other_python_warns_for_module_missing_from_its_stdlib(tmp_path, capsys):
    source = tmp_path / "source"
    source.mkdir()
    (source / "experiment.py").write_text("import tomllib\nprint(tomllib)\n")
    output = tmp_path / "bundle.zip"
    assert (
        CommandLine().run(["build", str(source), "--python", "3.10", "--output", str(output)]) == 0
    )
    capsys.readouterr()
    with zipfile.ZipFile(output) as archive:
        plan = json.loads(archive.read("analysis-report.json"))
        assert "DEPENDENCY_UNRESOLVED" in archive.read("README.md").decode()
    assert plan["report"]["options"]["python_version"] == "3.10"
    assert plan["report"]["dependencies"][0]["category"] == "unresolved"
    assert plan["state"] == "draft"


def test_cli_uses_explicit_author_environment_python_for_analysis(tmp_path, capsys):
    source = tmp_path / "source"
    source.mkdir()
    (source / "experiment.py").write_text("import tomllib\nprint(tomllib)\n")
    environment = tmp_path / "author-environment.json"
    environment.write_text(
        json.dumps({"python_version": "3.10.14", "platform": "linux", "distributions": []})
    )
    assert (
        CommandLine().run(["analyze", str(source), "--author-environment", str(environment)]) == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["options"]["python_version"] == "3.10"
    assert report["dependencies"][0]["category"] == "unresolved"


def test_changing_python_after_analysis_keeps_draft_and_requests_reanalysis():
    service = ReproducibilityService()
    snapshot = SnapshotBuilder().from_mapping({"experiment.py": "import tomllib\nprint(tomllib)\n"})
    report = service.analyze(snapshot, AnalysisOptions(python_version="3.11"))
    decisions = UserDecisions(report.analysis_id, python_version="3.10", data_confirmed=True)
    plan = service.prepare(report, decisions)
    assert plan.state == "draft"
    assert plan.analysis_id == report.analysis_id and plan.decisions == decisions
    mismatch = next(item for item in plan.findings if item.code == "PYTHON_PROFILE_MISMATCH")
    assert "options.python_version=3.10" in mismatch.message
    assert service.render(plan).get("README.md")  # The explanatory draft remains downloadable.
    reanalyzed = service.analyze(snapshot, AnalysisOptions(python_version="3.10"))
    assert reanalyzed.analysis_id != report.analysis_id
    assert reanalyzed.dependencies[0].category == "unresolved"
    assert report.dependencies[0].category == "stdlib"
