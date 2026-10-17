from __future__ import annotations

import json
from dataclasses import replace

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

from nishtyak_core.analysis import ProjectAnalyzer
from nishtyak_core.models import (
    AnalysisOptions,
    AnalysisReport,
    DependencyCandidate,
    FileInfo,
    Finding,
    ImportObservation,
    RunSpec,
    UserDecisions,
)
from nishtyak_core.planning import PackagePlanner
from nishtyak_core.rendering import DocumentRenderer
from nishtyak_core.service import ReproducibilityService
from nishtyak_core.snapshot import SnapshotBuilder


@pytest.fixture
def report() -> AnalysisReport:
    return AnalysisReport(
        analysis_id="analysis-1",
        fingerprint="snapshot-1",
        options=AnalysisOptions(),
        files=(FileInfo("main.py", 1, "hash-main"), FileInfo("data.csv", 2, "hash-data")),
        dependencies=(
            DependencyCandidate("os", "stdlib"),
            DependencyCandidate("helpers", "local"),
            DependencyCandidate("numpy", "external", package="numpy"),
        ),
    )


@pytest.fixture
def decisions(report: AnalysisReport) -> UserDecisions:
    return UserDecisions(
        analysis_id=report.analysis_id,
        run_spec=RunSpec("main.py"),
        data_confirmed=True,
        python_version="3.11",
        description="A small numerical experiment.",
    )


def codes(plan) -> set[str]:
    return {finding.code for finding in plan.findings}


def test_known_dependency_is_ready_but_not_verified(report, decisions):
    plan = PackagePlanner().prepare(report, decisions)
    assert plan.requirements == ("numpy",)
    assert plan.state == "ready_to_verify"
    assert plan.decisions == decisions
    assert "VERSION_NOT_PINNED" in codes(plan)
    assert [file.path for file in plan.files] == ["data.csv", "main.py"]


def test_stale_decisions_are_rejected(report, decisions):
    with pytest.raises(ValueError, match="another analysis"):
        PackagePlanner().prepare(report, replace(decisions, analysis_id="outdated"))


def test_unresolved_dependency_keeps_draft_until_author_maps_it(report, decisions):
    report = replace(report, dependencies=(DependencyCandidate("special", "unresolved"),))
    draft = PackagePlanner().prepare(report, decisions)
    assert draft.requirements == ()
    assert draft.state == "draft"
    assert "DEPENDENCY_UNRESOLVED" in codes(draft)
    resolved = PackagePlanner().prepare(
        report, replace(decisions, packages={"special": "special-package>=2"})
    )
    assert resolved.requirements == ("special-package>=2",)
    assert resolved.state == "ready_to_verify"


def test_versions_are_selected_by_module_or_distribution_name(report, decisions):
    report = replace(
        report, dependencies=(DependencyCandidate("sklearn", "external", package="scikit-learn"),)
    )
    for versions in ({"sklearn": "1.5"}, {"scikit_learn": "==1.5"}):
        plan = PackagePlanner().prepare(report, replace(decisions, versions=versions))
        assert plan.requirements == ("scikit-learn==1.5",)
        assert "VERSION_NOT_PINNED" not in codes(plan)


def test_declared_requirements_survive_without_imports_and_preserve_markers(report, decisions):
    report = replace(
        report,
        declared_requirements=(
            'numpy==1.26; python_version < "3.12"',
            'numpy==2.1; python_version >= "3.12"',
            'rich[foo]>=13; sys_platform == "linux"',
        ),
    )
    plan = PackagePlanner().prepare(report, decisions)
    assert len(plan.requirements) == 3
    requirements = [Requirement(raw) for raw in plan.requirements]
    assert all(item.marker for item in requirements)
    assert {str(item.specifier) for item in requirements if item.name == "numpy"} == {
        "==1.26",
        "==2.1",
    }
    assert plan.state == "ready_to_verify"


def test_version_override_retains_declared_markers(report, decisions):
    report = replace(
        report,
        declared_requirements=(
            'numpy>=1; sys_platform == "linux"',
            'numpy>=2; sys_platform == "win32"',
        ),
    )
    plan = PackagePlanner().prepare(report, replace(decisions, versions={"numpy": "2.3"}))
    assert len(plan.requirements) == 2
    assert all(str(Requirement(raw).specifier) == "==2.3" for raw in plan.requirements)
    assert all(Requirement(raw).marker for raw in plan.requirements)


def test_module_named_version_override_updates_declared_distribution(report, decisions):
    report = replace(
        report,
        declared_requirements=('scikit-learn>=1; sys_platform == "linux"',),
        dependencies=(DependencyCandidate("sklearn", "external", package="scikit-learn"),),
    )
    plan = PackagePlanner().prepare(report, replace(decisions, versions={"sklearn": "1.5"}))
    assert plan.requirements == ('scikit-learn==1.5; sys_platform == "linux"',)


def test_declared_exact_version_conflict_is_draft(report, decisions):
    report = replace(report, declared_requirements=("numpy<2", "numpy==2.1"))
    plan = PackagePlanner().prepare(report, decisions)
    assert plan.state == "draft"
    assert "DEPENDENCY_CONFLICT" in codes(plan)


def test_repeated_equivalent_pins_need_only_one_check_per_constraint(
    report, decisions, monkeypatch
):
    count = 1200
    declarations = tuple(
        f"numpy>=0.{index},=={'1' if index % 2 else '1.0'}" for index in range(count)
    )
    report = replace(report, dependencies=(), declared_requirements=declarations)
    contains = SpecifierSet.contains
    calls = 0

    def checked_contains(self, version, **kwargs):
        nonlocal calls
        calls += 1
        return contains(self, version, **kwargs)

    monkeypatch.setattr(SpecifierSet, "contains", checked_contains)
    plan = PackagePlanner().prepare(report, decisions)
    assert len(plan.requirements) == count
    assert "DEPENDENCY_CONFLICT" not in codes(plan)
    assert calls == count


def test_distinct_pins_conflict_without_cross_product_of_constraints(report, decisions):
    report = replace(
        report,
        dependencies=(),
        declared_requirements=("numpy>=0.1,==1", "numpy>=0.2,==2"),
    )
    plan = PackagePlanner().prepare(report, decisions)
    assert len(plan.requirements) == 2
    assert "DEPENDENCY_CONFLICT" in codes(plan)
    assert plan.state == "draft"


@pytest.mark.parametrize(
    "packages,versions",
    [
        ({"numpy": "numpy\npandas"}, {}),
        ({"numpy": "numpy @ https://example.com/numpy.whl"}, {}),
        ({"os": "evil-package"}, {}),
        ({"unseen": "new-package"}, {}),
        ({}, {"numpy": "whatever"}),
        ({}, {"unseen": "1.0"}),
    ],
)
def test_invalid_dependency_decisions_fail_instead_of_guessing(
    report, decisions, packages, versions
):
    with pytest.raises(ValueError):
        PackagePlanner().prepare(report, replace(decisions, packages=packages, versions=versions))


@pytest.mark.parametrize(
    "run",
    [
        RunSpec("../main.py"),
        RunSpec("/main.py"),
        RunSpec("C:/main.py"),
        RunSpec("main.py", working_directory="../other"),
        RunSpec("main.py", args=("bad\nargument",)),
        RunSpec("package; evil", kind="module"),
        RunSpec("main.py", kind="command"),
    ],
)
def test_unsafe_launch_specs_are_rejected(report, decisions, run):
    with pytest.raises(ValueError):
        PackagePlanner().prepare(report, replace(decisions, run_spec=run))


def test_launch_target_must_remain_included(report, decisions):
    plan = PackagePlanner().prepare(report, replace(decisions, excluded_files=("main.py",)))
    assert plan.state == "blocked"
    assert "RUN_TARGET_MISSING" in codes(plan)
    with pytest.raises(ValueError, match="blocking"):
        DocumentRenderer().render(plan)


def test_baseline_exclusions_and_directory_exclusions(report, decisions):
    paths = [
        "main.py",
        ".git/config",
        ".venv/lib/dependency.py",
        ".env",
        ".env.production",
        "private.key",
        "id_ed25519",
        "data.csv",
        "data/more.csv",
        "__pycache__/main.pyc",
    ]
    report = replace(report, files=tuple(FileInfo(path, 1, path) for path in paths))
    plan = PackagePlanner().prepare(report, replace(decisions, excluded_files=("data/",)))
    assert [file.path for file in plan.files] == ["data.csv", "main.py"]
    assert set(plan.excluded_files) == set(paths) - {"main.py", "data.csv"}


def test_detected_secret_blocks_export_until_file_is_excluded(report, decisions):
    report = replace(
        report,
        findings=(
            Finding(
                "SECRET_DETECTED", "A credential was detected.", path="data.csv", severity="error"
            ),
        ),
    )
    blocked = PackagePlanner().prepare(report, decisions)
    assert blocked.state == "blocked"
    with pytest.raises(ValueError):
        DocumentRenderer().render(blocked)
    excluded = PackagePlanner().prepare(report, replace(decisions, excluded_files=("data.csv",)))
    assert excluded.state == "draft"
    assert "SECRET_FILE_EXCLUDED" in codes(excluded)
    assert [file.path for file in excluded.files] == ["main.py"]


def test_partial_analysis_is_draft_but_data_acknowledgement_is_not_required(report, decisions):
    partial = PackagePlanner().prepare(replace(report, status="partial"), decisions)
    assert partial.state == "draft"
    missing_data = PackagePlanner().prepare(report, replace(decisions, data_confirmed=False))
    assert missing_data.state == "ready_to_verify"
    assert "DATA_NOT_CONFIRMED" not in codes(missing_data)


def test_module_launch_resolves_from_working_directory(report, decisions):
    report = replace(report, files=(FileInfo("src/lab/__main__.py", 1, "hash"),))
    plan = PackagePlanner().prepare(
        report, replace(decisions, run_spec=RunSpec("lab", kind="module", working_directory="src"))
    )
    assert plan.state == "ready_to_verify"
    readme = DocumentRenderer().render(plan).get("README.md").decode()
    assert "cd project\ncd src\npython -m lab" in readme


def test_documents_are_deterministic_and_commands_quote_arguments(report, decisions):
    report = replace(report, files=(FileInfo("nested folder/main file.py", 1, "hash"),))
    decisions = replace(
        decisions,
        run_spec=RunSpec(
            "main file.py",
            args=("$(touch unwanted)", "--label", "a b"),
            working_directory="nested folder",
        ),
    )
    plan = PackagePlanner().prepare(report, decisions)
    renderer = DocumentRenderer()
    generated = renderer.render(plan)
    assert generated == renderer.render(plan)
    readme = generated.get("README.md").decode()
    assert "cd project\ncd 'nested folder'" in readme
    assert "python 'main file.py' '$(touch unwanted)' --label 'a b'" in readme
    assert "python -m pip install -r requirements.txt" in readme
    assert "Код на сервере не выполнялся" in readme
    assert json.loads(generated.get("analysis-report.json"))["state"] == "ready_to_verify"
    assert generated.get("requirements.txt").decode().endswith("numpy\n")


def test_readme_consolidates_limits_but_json_retains_all_evidence():
    snapshot = SnapshotBuilder().from_mapping(
        {
            "experiment.py": "import pandas\nimport numpy\npandas.read_csv('measurements.csv')\n",
        }
    )
    report = ReproducibilityService().analyze(snapshot)
    plan = PackagePlanner().prepare(report, UserDecisions(report.analysis_id))
    generated = DocumentRenderer().render(plan)
    readme = generated.get("README.md").decode()
    review = readme.split("## Оставшиеся ограничения", 1)[1].split("## Состав пакета", 1)[0]
    assert review.count("[VERSIONS_UNVERIFIED]") == 1
    assert "numpy, pandas" in review
    assert '"measurements.csv"' in readme
    assert "(experiment.py:3)" in readme
    assert "INPUT_PATH_UNVERIFIED" not in review
    assert "DATA_NOT_CONFIRMED" not in review
    assert "DATA_DEPENDENCIES_UNVERIFIED" not in review
    assert "README_MISSING" not in review
    document = json.loads(generated.get("analysis-report.json"))
    raw_codes = [item["code"] for item in document["findings"]]
    assert raw_codes.count("VERSION_NOT_PINNED") == 2
    assert {"DATA_DEPENDENCIES_UNVERIFIED", "README_MISSING"} <= set(raw_codes)
    assert "DATA_NOT_CONFIRMED" not in raw_codes


def test_generated_description_is_distinguished_from_author_confirmation(report, decisions):
    plan = PackagePlanner().prepare(report, decisions)
    assert plan.description_origin == "author"
    automatic = replace(
        plan,
        description="Computes measurement summaries.",
        description_origin="automatic",
        automatic_review={"status": "ready", "provider": "deepseek"},
    )
    generated = DocumentRenderer().render(automatic)
    assert "это не подтверждение автора" in generated.get("README.md").decode()
    document = json.loads(generated.get("analysis-report.json"))
    assert document["automatic_review"]["provider"] == "deepseek"
    assert document["decisions"]["description"] == decisions.description


def test_human_report_preserves_distinct_diagnostics_and_errors(report, decisions):
    plan = PackagePlanner().prepare(report, decisions)
    findings = (
        Finding("INPUT_PATH_UNVERIFIED", "Data missing", path="a.py", line=1),
        Finding("INPUT_PATH_UNVERIFIED", "Data missing", path="b.py", line=2),
        Finding("NEW_DIAGNOSTIC", "New important limitation"),
        Finding("README_MISSING", "Escalated diagnostic", severity="error"),
    )
    presented = DocumentRenderer._review_findings(replace(plan, findings=findings))
    assert presented == findings


def test_launch_credentials_are_rejected_without_echoing_values(report, decisions):
    token = "sk-" + "x" * 24
    for run in (
        RunSpec("main.py", args=("--token", "short-private-value")),
        RunSpec("main.py", args=("--password=private-value",)),
        RunSpec("main.py", args=("--api-key", "${API_KEY}")),
        RunSpec("main.py", args=("--option", token)),
        RunSpec(f"{token}.py"),
        RunSpec("main.py", working_directory=token),
    ):
        with pytest.raises(ValueError, match="credentials") as error:
            PackagePlanner().prepare(report, replace(decisions, run_spec=run))
        assert token not in str(error.value)
        assert "private-value" not in str(error.value)


def test_excluded_source_secrets_are_redacted_from_nested_report_and_readme(report, decisions):
    token = "sk-" + "a" * 24
    report = replace(
        report,
        imports=(ImportObservation(token, "data.csv", 1, kind="dynamic"),),
        dependencies=(DependencyCandidate(token, "unresolved", reason=f"Inspect {token}"),),
        findings=(Finding("SECRET_DETECTED", f"Credential {token}", path="data.csv"),),
    )
    plan = PackagePlanner().prepare(
        report,
        replace(
            decisions,
            excluded_files=("data.csv",),
            description=f"Do not share {token}",
            input_description="URL https://user:password@example.com/data",
        ),
    )
    generated = DocumentRenderer().render(plan)
    public_report = json.loads(generated.get("analysis-report.json"))
    assert public_report["report"]["imports"][0]["module"] == "[REDACTED]"
    assert "[REDACTED]" in public_report["report"]["dependencies"][0]["reason"]
    for file in generated.files:
        assert token.encode() not in file.content
        assert b"user:password" not in file.content


def test_excluding_a_local_dependency_blocks_retained_importer():
    snapshot = SnapshotBuilder().from_mapping(
        {
            "main.py": "import helpers\n",
            "helpers.py": "VALUE = 42\n",
        }
    )
    report = ProjectAnalyzer().analyze(snapshot)
    plan = PackagePlanner().prepare(
        report,
        UserDecisions(
            analysis_id=report.analysis_id,
            excluded_files=("helpers.py",),
            data_confirmed=True,
            run_spec=RunSpec("main.py"),
        ),
    )
    assert plan.state == "blocked"
    finding = next(item for item in plan.findings if item.code == "LOCAL_MODULE_EXCLUDED")
    assert finding.path == "main.py"
    assert finding.line == 1
    assert "helpers" in finding.message
    assert plan.requirements == ()


def test_excluding_importer_does_not_block_for_its_removed_local_dependency():
    snapshot = SnapshotBuilder().from_mapping(
        {
            "main.py": "print('standalone')\n",
            "other.py": "import helpers\n",
            "helpers.py": "VALUE = 42\n",
        }
    )
    report = ProjectAnalyzer().analyze(snapshot)
    plan = PackagePlanner().prepare(
        report,
        UserDecisions(
            analysis_id=report.analysis_id,
            excluded_files=("helpers.py", "other.py"),
            data_confirmed=True,
            run_spec=RunSpec("main.py"),
        ),
    )
    assert plan.state == "ready_to_verify"
    assert "LOCAL_MODULE_EXCLUDED" not in codes(plan)


@pytest.mark.parametrize("package_name", ["env", "venv"])
def test_ordinary_environment_named_python_packages_are_retained(package_name):
    snapshot = SnapshotBuilder().from_mapping(
        {
            "main.py": f"import {package_name}\n",
            f"{package_name}/__init__.py": "VALUE = 42\n",
        }
    )
    report = ProjectAnalyzer().analyze(snapshot)
    plan = PackagePlanner().prepare(
        report,
        UserDecisions(
            analysis_id=report.analysis_id, data_confirmed=True, run_spec=RunSpec("main.py")
        ),
    )
    assert plan.state == "ready_to_verify"
    assert f"{package_name}/__init__.py" in {file.path for file in plan.files}
    assert plan.requirements == ()


@pytest.mark.parametrize(
    "environment_files",
    [
        {"env/pyvenv.cfg": "home = /python", "env/random.py": ""},
        {"venv/bin/activate": "", "venv/bin/python": ""},
        {"env/Scripts/activate": "", "env/Lib/site-packages/vendor/__init__.py": ""},
    ],
)
def test_detected_virtual_environments_remain_excluded(environment_files):
    snapshot = SnapshotBuilder().from_mapping({"main.py": "print('ok')", **environment_files})
    report = ProjectAnalyzer().analyze(snapshot)
    plan = PackagePlanner().prepare(
        report,
        UserDecisions(
            analysis_id=report.analysis_id, data_confirmed=True, run_spec=RunSpec("main.py")
        ),
    )
    assert {file.path for file in plan.files} == {"main.py"}
    assert set(environment_files) <= set(plan.excluded_files)


@pytest.mark.parametrize(
    "import_statement",
    [
        "from .helpers import VALUE",
        "from . import helpers",
        "from experiment import helpers",
        "import experiment.helpers",
    ],
)
def test_excluded_relative_or_absolute_package_submodule_blocks(import_statement):
    snapshot = SnapshotBuilder().from_mapping(
        {
            "experiment/__init__.py": "",
            "experiment/main.py": import_statement,
            "experiment/helpers.py": "VALUE = 42",
        }
    )
    report = ProjectAnalyzer().analyze(snapshot)
    plan = PackagePlanner().prepare(
        report,
        UserDecisions(
            analysis_id=report.analysis_id,
            excluded_files=("experiment/helpers.py",),
            data_confirmed=True,
            run_spec=RunSpec("experiment.main", kind="module"),
        ),
    )
    assert plan.state == "blocked"
    finding = next(item for item in plan.findings if item.code == "LOCAL_MODULE_EXCLUDED")
    assert "experiment.helpers" in finding.message


def test_excluding_package_initializer_does_not_silently_change_to_namespace():
    snapshot = SnapshotBuilder().from_mapping(
        {
            "main.py": "import experiment.helpers",
            "experiment/__init__.py": "CONFIGURED = True",
            "experiment/helpers.py": "VALUE = 42",
        }
    )
    report = ProjectAnalyzer().analyze(snapshot)
    plan = PackagePlanner().prepare(
        report,
        UserDecisions(
            analysis_id=report.analysis_id,
            excluded_files=("experiment/__init__.py",),
            data_confirmed=True,
            run_spec=RunSpec("main.py"),
        ),
    )
    assert plan.state == "blocked"
    assert "LOCAL_MODULE_EXCLUDED" in codes(plan)


def test_revalidation_uses_configured_source_roots():
    snapshot = SnapshotBuilder().from_mapping(
        {
            "src/main.py": "import helpers",
            "src/helpers.py": "VALUE = 42",
        }
    )
    report = ProjectAnalyzer().analyze(snapshot, AnalysisOptions(source_roots=("src",)))
    plan = PackagePlanner().prepare(
        report,
        UserDecisions(
            analysis_id=report.analysis_id,
            excluded_files=("src/helpers.py",),
            data_confirmed=True,
            run_spec=RunSpec("main.py", working_directory="src"),
        ),
    )
    assert plan.state == "blocked"
    assert "LOCAL_MODULE_EXCLUDED" in codes(plan)


def test_automatic_candidate_is_validated_against_current_root_cwd_and_author_choices():
    from nishtyak_core.launches import LaunchDetector

    core = ReproducibilityService()
    snapshot = SnapshotBuilder().from_mapping(
        {
            "example/run.py": "print('example')\n",
            "research/run.py": "print('experiment')\n",
            "research/pyproject.toml": (
                '[project]\nname="experiment"\nversion="1.0"\n'
                '[project.optional-dependencies]\nplots=["matplotlib>=3"]\n'
            ),
        }
    )
    report = core.analyze(snapshot, AnalysisOptions(project_root="research"))
    candidate = LaunchDetector().detect(report).recommended
    assert candidate is not None
    decisions = UserDecisions(report.analysis_id, selected_extras=("plots",))
    automatic = core.prepare(report, decisions, automatic_candidate_id=candidate.id)
    assert automatic.run_spec == RunSpec("run.py", working_directory="research")
    assert automatic.run_spec_origin == "automatic"
    assert "matplotlib>=3" in automatic.requirements
    assert automatic.decisions == decisions
    assert automatic.decisions.run_spec is None
    unknown = core.prepare(report, decisions, automatic_candidate_id="another-analysis-candidate")
    assert unknown.run_spec_origin == "inferred" and unknown.run_spec == automatic.run_spec
    author = RunSpec("run.py", args=("--seed", "42"), working_directory="example")
    overridden = core.prepare(
        report, replace(decisions, run_spec=author), automatic_candidate_id=candidate.id
    )
    assert overridden.run_spec == author and overridden.run_spec_origin == "author"
    assert "matplotlib>=3" in overridden.requirements
