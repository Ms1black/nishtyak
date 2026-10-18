from __future__ import annotations

from dataclasses import replace

import pytest
from packaging.requirements import Requirement

from nishtyak_core.manifests import ManifestReader
from nishtyak_core.models import (
    AnalysisOptions,
    AnalysisReport,
    DeclarationDecision,
    DependencyCandidate,
    DependencyDeclaration,
    FileInfo,
    Finding,
    ProjectLayout,
    ProjectSnapshot,
    RunSpec,
    SourceFile,
    UserDecisions,
)
from nishtyak_core.planning import DecisionError, PackagePlanner


def manifest(files: dict[str, str], root: str = "."):
    snapshot = ProjectSnapshot(
        tuple(SourceFile(path, value.encode()) for path, value in files.items()), "snapshot"
    )
    return ManifestReader(snapshot, project_root=root).read()


def report(*declarations: DependencyDeclaration, **kwargs) -> AnalysisReport:
    return AnalysisReport(
        "analysis",
        "snapshot",
        AnalysisOptions(),
        (FileInfo("main.py", 1, "hash"),),
        declarations=declarations,
        declared_requirements=tuple(
            item.requirement for item in declarations if item.group is None
        ),
        **kwargs,
    )


def prepare(value: AnalysisReport, **kwargs):
    return PackagePlanner().prepare(
        value,
        UserDecisions("analysis", run_spec=RunSpec("main.py"), data_confirmed=True, **kwargs),
    )


def test_selected_root_preserves_declaration_evidence_and_contains_includes():
    result = manifest(
        {
            "repo/requirements.txt": "-r deps/runtime.txt\n-r ../outside.txt\n",
            "repo/deps/runtime.txt": "numpy>=2\n",
            "outside.txt": "malicious-package\n",
        },
        "repo",
    )
    assert result.requirements == ("numpy>=2",)
    (declaration,) = result.declarations
    assert (declaration.path, declaration.line, declaration.group) == (
        "repo/deps/runtime.txt",
        1,
        None,
    )
    assert not result.complete
    assert "MANIFEST_INCLUDE_UNSUPPORTED" in {item.code for item in result.findings}


def test_optional_declarations_are_retained_separately_and_ids_stable():
    source = {
        "pyproject.toml": """[project]
dependencies = ["numpy>=2"]
[project.optional-dependencies]
Plot_Tools = ["matplotlib>=3; python_version >= '3.10'"]
empty = []
[project.scripts]
experiment = "lab.cli:main"
"""
    }
    result = manifest(source)
    assert result.requirements == ("numpy>=2",)
    assert result.optional_groups == ("empty", "plot-tools")
    assert result.entrypoints == ("lab.cli:main",)
    assert result.declarations == manifest(source).declarations
    assert result.declarations[1].group == "plot-tools"
    assert result.complete


def test_normalized_extra_collision_is_not_silently_merged():
    result = manifest(
        {
            "pyproject.toml": """[project.optional-dependencies]
test_tools = ["pytest"]
test-tools = ["ruff"]
"""
        }
    )
    assert not result.complete
    assert any(item.code == "EXTRA_NAME_COLLISION" for item in result.findings)


def test_selection_and_declaration_exclusion_control_effective_requirements():
    base = DependencyDeclaration("numpy>=2", "pyproject.toml")
    extra = DependencyDeclaration("matplotlib>=3", "pyproject.toml", group="plot")
    value = report(base, extra, optional_groups=("plot",))
    assert prepare(value).requirements == ("numpy>=2",)
    assert prepare(value, selected_extras=("plot",)).requirements == ("matplotlib>=3", "numpy>=2")
    assert prepare(
        value,
        selected_extras=("plot",),
        declaration_actions={base.id: DeclarationDecision("exclude")},
    ).requirements == ("matplotlib>=3",)


def test_replacement_retains_marker_and_extras_but_accepts_explicit_replacements():
    declaration = DependencyDeclaration(
        'requests[socks]>=2; sys_platform == "linux"', "requirements.txt", 1
    )
    value = report(declaration)
    plan = prepare(
        value,
        declaration_actions={declaration.id: DeclarationDecision("replace", "requests==2.32")},
    )
    requirement = Requirement(plan.requirements[0])
    assert requirement.extras == {"socks"}
    assert str(requirement.marker) == 'sys_platform == "linux"'
    assert str(requirement.specifier) == "==2.32"


@pytest.mark.parametrize(
    "action",
    [
        DeclarationDecision("replace"),
        DeclarationDecision("replace", "x @ https://example.com/x.whl"),
        DeclarationDecision("replace", "x\ny"),
        DeclarationDecision("unknown"),
        DeclarationDecision("keep", "numpy"),
        DeclarationDecision("exclude", "numpy"),
    ],
)
def test_invalid_action_has_field_specific_error(action):
    declaration = DependencyDeclaration("numpy", "requirements.txt", 1)
    with pytest.raises(DecisionError) as caught:
        prepare(report(declaration), declaration_actions={declaration.id: action})
    assert caught.value.field.startswith("declaration_actions." + declaration.id)


def test_unknown_declaration_and_optional_group_are_rejected():
    with pytest.raises(DecisionError, match="Unknown dependency declaration"):
        prepare(report(), declaration_actions={"old-id": DeclarationDecision("exclude")})
    with pytest.raises(DecisionError) as caught:
        prepare(report(), selected_extras=("missing",))
    assert caught.value.code == "EXTRA_UNKNOWN"


def test_excluded_declaration_is_not_resurrected_by_declared_only_candidate():
    declaration = DependencyDeclaration("numpy==2", "requirements.txt", 1)
    value = report(
        declaration,
        dependencies=(DependencyCandidate("numpy", "external", "numpy", "numpy==2", "declared"),),
    )
    plan = prepare(value, declaration_actions={declaration.id: DeclarationDecision("exclude")})
    assert plan.requirements == ()
    assert plan.state == "ready_to_verify"


def test_import_after_declaration_removal_requires_explicit_author_choice():
    declaration = DependencyDeclaration("numpy==2", "requirements.txt", 1)
    value = report(
        declaration,
        dependencies=(
            DependencyCandidate("numpy", "external", "numpy", "numpy==2", "declared", ("import",)),
        ),
    )
    actions = {declaration.id: DeclarationDecision("exclude")}
    plan = prepare(value, declaration_actions=actions)
    assert plan.requirements == ()
    assert plan.state == "draft"
    assert any(item.code == "DECLARATION_IMPORT_UNRESOLVED" for item in plan.findings)
    restored = prepare(value, declaration_actions=actions, packages={"numpy": "numpy==2.1"})
    assert restored.requirements == ("numpy==2.1",)
    assert restored.state == "ready_to_verify"
    excluded = prepare(value, declaration_actions=actions, excluded_modules=("numpy",))
    assert excluded.requirements == ()
    assert excluded.state == "ready_to_verify"


def test_selected_optional_declaration_can_resolve_unknown_distribution_import():
    extra = DependencyDeclaration("custom_tools==1", "pyproject.toml", group="tools")
    value = report(
        extra,
        optional_groups=("tools",),
        dependencies=(DependencyCandidate("custom_tools", "unresolved", evidence_ids=("import",)),),
    )
    assert prepare(value, selected_extras=("tools",)).requirements == ("custom_tools==1",)
    assert prepare(value, selected_extras=("tools",)).state == "ready_to_verify"
    assert prepare(value).state == "draft"


def test_source_conflict_no_longer_forces_draft_after_effective_resolution():
    first = DependencyDeclaration("numpy<2", "requirements.txt", 1)
    second = DependencyDeclaration("numpy==2.1", "requirements.txt", 2)
    value = report(first, second, findings=(Finding("DEPENDENCY_CONFLICT", "Conflict"),))
    assert prepare(value).state == "draft"
    resolved = prepare(value, declaration_actions={first.id: DeclarationDecision("exclude")})
    assert resolved.state == "ready_to_verify"
    assert not any(item.code == "DEPENDENCY_CONFLICT" for item in resolved.findings)
    assert value.findings[0].code == "DEPENDENCY_CONFLICT"


def test_matching_explicit_author_version_resolves_environment_conflict():
    value = report(
        DependencyDeclaration("numpy>=1", "requirements.txt", 1),
        dependencies=(DependencyCandidate("numpy", "external", "numpy", evidence_ids=("i",)),),
        findings=(Finding("AUTHOR_ENVIRONMENT_CONFLICT", "Conflict", evidence_ids=("i",)),),
    )
    assert prepare(value).state == "draft"
    assert prepare(value, versions={"numpy": "2.1"}).state == "ready_to_verify"


def test_observed_version_pins_only_unversioned_declarations_and_keeps_marker():
    value = report(
        DependencyDeclaration('numpy; sys_platform == "linux"', "requirements.txt", 1),
        dependencies=(
            DependencyCandidate(
                "numpy", "external", "numpy", "numpy==2.1", "author_environment", ("i",)
            ),
        ),
    )
    assert prepare(value).requirements == ('numpy==2.1; sys_platform == "linux"',)
    constrained = replace(
        value, declarations=(DependencyDeclaration("numpy>=2", "requirements.txt", 1),)
    )
    assert prepare(constrained).requirements == ("numpy>=2",)


def test_module_launch_requires_an_existing_working_directory():
    with_directory = replace(report(), layout=ProjectLayout())
    plan = PackagePlanner().prepare(
        with_directory,
        UserDecisions(
            "analysis",
            run_spec=RunSpec("main", kind="module", working_directory="missing"),
            data_confirmed=True,
        ),
    )
    assert plan.state == "blocked"
    assert any(item.code == "RUN_WORKING_DIRECTORY_MISSING" for item in plan.findings)


@pytest.mark.parametrize(
    "changes,code,field",
    [
        ({"packages": {"unknown-private-input": "numpy"}}, "PACKAGE_MODULE_UNKNOWN", "packages"),
        (
            {"packages": {"numpy": "bad\nrequirement"}},
            "PACKAGE_REQUIREMENT_INVALID",
            "packages.numpy",
        ),
        (
            {"packages": {"numpy": "numpy @ https://example.com/a.whl"}},
            "PACKAGE_DIRECT_REFERENCE_UNSUPPORTED",
            "packages.numpy",
        ),
        ({"versions": {"unknown-private-input": "1"}}, "VERSION_PACKAGE_UNKNOWN", "versions"),
        ({"versions": {"numpy": "whatever"}}, "VERSION_SPECIFIER_INVALID", "versions.numpy"),
        ({"versions": {"numpy": "1\n"}}, "VERSION_SPECIFIER_INVALID", "versions.numpy"),
        (
            {"excluded_modules": ("unknown-private-input",)},
            "EXCLUDED_MODULE_UNKNOWN",
            "excluded_modules",
        ),
        ({"python_version": "1!3.11"}, "PYTHON_VERSION_INVALID", "python_version"),
        ({"python_version": "3.11.post1"}, "PYTHON_VERSION_INVALID", "python_version"),
        (
            {"excluded_files": ("../unknown-private-input",)},
            "EXCLUDED_FILE_PATH_INVALID",
            "excluded_files",
        ),
        (
            {"run_spec": RunSpec("../unknown-private-input")},
            "RUN_TARGET_INVALID",
            "run_spec.target",
        ),
        (
            {"run_spec": RunSpec("main.py", working_directory="../unknown-private-input")},
            "RUN_DIRECTORY_INVALID",
            "run_spec.working_directory",
        ),
    ],
)
def test_invalid_decisions_expose_fixed_errors_and_field_paths(changes, code, field):
    value = report(dependencies=(DependencyCandidate("numpy", "external", "numpy"),))
    decisions = UserDecisions("analysis", run_spec=RunSpec("main.py"), data_confirmed=True)
    with pytest.raises(DecisionError) as caught:
        PackagePlanner().prepare(value, replace(decisions, **changes))
    assert caught.value.code == code
    assert caught.value.field == field
    assert "unknown-private-input" not in str(caught.value)


def test_extra_features_do_not_allow_two_versions_of_one_distribution():
    value = report(
        DependencyDeclaration("requests[socks]==2.31", "requirements.txt", 1),
        DependencyDeclaration("requests[security]==2.32", "requirements.txt", 2),
    )
    plan = prepare(value)
    assert plan.state == "draft"
    assert any(item.code == "DEPENDENCY_CONFLICT" for item in plan.findings)


def test_declaration_only_environment_conflict_uses_candidate_evidence():
    candidate = DependencyCandidate("numpy", "external", "numpy", "numpy>=1", "declared")
    value = report(
        DependencyDeclaration("numpy>=1", "requirements.txt", 1),
        dependencies=(candidate,),
        findings=(
            Finding("AUTHOR_ENVIRONMENT_CONFLICT", "Conflict", evidence_ids=(candidate.id,)),
        ),
    )
    assert prepare(value).state == "draft"
    assert prepare(value, versions={"numpy": "2.1"}).state == "ready_to_verify"


def test_local_import_cannot_be_replaced_with_external_distribution():
    value = report(dependencies=(DependencyCandidate("numpy", "local"),))
    with pytest.raises(DecisionError) as caught:
        prepare(value, packages={"numpy": "malicious-package"})
    assert caught.value.code == "PACKAGE_MODULE_NOT_EXTERNAL"
    assert caught.value.field == "packages.numpy"


def test_layout_module_and_callable_targets_resolve_in_retained_source_roots():
    value = replace(
        report(),
        files=(FileInfo("repo/src/lab/cli.py", 1, "hash"),),
        layout=ProjectLayout("repo", ("repo/src",)),
    )
    for run in (RunSpec("lab.cli", kind="module"), RunSpec("lab.cli:main", kind="callable")):
        decisions = UserDecisions("analysis", run_spec=run, data_confirmed=True)
        assert PackagePlanner().prepare(value, decisions).state == "ready_to_verify"
        assert (
            PackagePlanner()
            .prepare(value, replace(decisions, excluded_files=("repo/src/lab/cli.py",)))
            .state
            == "blocked"
        )
    for target in ("lab.cli:main()", "lab.cli:main;bad", "lab.cli:", "lab.cli.main"):
        with pytest.raises(ValueError):
            PackagePlanner().prepare(
                value, UserDecisions("analysis", run_spec=RunSpec(target, kind="callable"))
            )
