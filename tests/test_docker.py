from __future__ import annotations

import json
from dataclasses import replace

import pytest

from nishtyak_core.docker import DockerfileGenerator
from nishtyak_core.models import AnalysisOptions, AnalysisReport, PackagePlan, RunSpec, SourceFile
from nishtyak_core.service import ReproducibilityService


@pytest.fixture
def plan() -> PackagePlan:
    report = AnalysisReport("analysis", "fingerprint", AnalysisOptions(), ())
    return PackagePlan(
        report=report,
        requirements=("numpy==2.2.0",),
        run_spec=RunSpec("main.py", args=("--label", "a b"), working_directory="experiments"),
        python_version="3.11",
        files=(),
        excluded_files=(),
        findings=(),
        state="ready_to_verify",
    )


def test_optional_docker_template_preserves_cwd_and_argument_boundaries(plan):
    generated = DockerfileGenerator().generate(plan)
    assert generated.path == "Dockerfile"
    content = generated.content.decode()
    assert "FROM python:3.11-slim" in content
    assert "COPY project/ ./project/" in content
    assert "WORKDIR /workspace/project/experiments" in content
    command = next(line[4:] for line in content.splitlines() if line.startswith("CMD "))
    assert json.loads(command) == ["python", "main.py", "--label", "a b"]
    assert "has not been built or run" in content
    assert generated == DockerfileGenerator().generate(plan)


def test_module_command_uses_exec_form(plan):
    plan = replace(plan, run_spec=RunSpec("lab", kind="module"))
    content = DockerfileGenerator().generate(plan).content.decode()
    assert 'CMD ["python", "-m", "lab"]' in content


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "draft"},
        {"state": "blocked"},
        {"python_version": None},
        {"run_spec": None},
        {"python_version": "3"},
        {"requirements": ("numpy @ https://example.com/numpy.whl",)},
        {"run_spec": RunSpec("main.py", working_directory="../outside")},
        {"run_spec": RunSpec("main.py", working_directory="space folder")},
    ],
)
def test_incomplete_or_unsupported_docker_profiles_are_rejected(plan, changes):
    with pytest.raises(ValueError):
        DockerfileGenerator().generate(replace(plan, **changes))


def test_ready_package_includes_dockerfile_automatically_with_explicit_opt_out(plan):
    service = ReproducibilityService()
    assert service.render(plan).get("Dockerfile").startswith(b"# Unverified template")
    assert "Dockerfile" not in {file.path for file in service.render(plan, False).files}


@pytest.mark.parametrize("platform", ["windows-x86_64", "macos-arm64", "macos-x86_64"])
def test_non_linux_target_skips_automatic_docker_but_rejects_forced_generation(plan, platform):
    report = replace(plan.report, options=AnalysisOptions(target_platform=platform))
    plan = replace(plan, report=report)
    assert "не Linux" in DockerfileGenerator().skip_reason(plan)
    assert "Dockerfile" not in {file.path for file in ReproducibilityService().render(plan).files}
    with pytest.raises(ValueError, match="не Linux"):
        ReproducibilityService().render(plan, True)


def test_automatic_resolution_target_also_prevents_a_linux_image_for_macos(plan):
    plan = replace(plan, automatic_resolution={"target": {"platform": "macos-arm64"}})
    assert "не Linux" in DockerfileGenerator().skip_reason(plan)


@pytest.mark.parametrize(
    ("target", "docker_platform"),
    [("linux-x86_64", "linux/amd64"), ("linux-aarch64", "linux/arm64")],
)
def test_linux_template_preserves_the_selected_architecture(plan, target, docker_platform):
    report = replace(plan.report, options=AnalysisOptions(target_platform=target))
    generated = DockerfileGenerator().generate(replace(plan, report=report))
    assert f"FROM --platform={docker_platform} python:3.11-slim" in generated.content.decode()


@pytest.mark.parametrize("filename", ["environment.yml", "environment.yaml"])
def test_conda_environment_is_preserved_without_a_misleading_pip_container(plan, filename):
    report = replace(plan.report, files=(SourceFile(filename, b"dependencies: []").info,))
    plan = replace(plan, report=report, files=report.files)
    assert "Conda" in DockerfileGenerator().skip_reason(plan)
    assert "Dockerfile" not in {file.path for file in ReproducibilityService().render(plan).files}


def test_an_excluded_conda_manifest_does_not_disable_the_remaining_pip_project(plan):
    report = replace(plan.report, files=(SourceFile("environment.yml", b"dependencies: []").info,))
    plan = replace(plan, report=report, excluded_files=("environment.yml",))
    assert DockerfileGenerator().skip_reason(plan) is None
    assert ReproducibilityService().render(plan).get("Dockerfile")


def test_render_respects_the_actual_lock_target_without_changing_the_analysis(plan):
    service = ReproducibilityService()
    generated = service.render(plan, target_platform="windows-x86_64")
    assert "Dockerfile" not in {file.path for file in generated.files}
    assert plan.report.options.target_platform is None
    with pytest.raises(ValueError, match="не Linux"):
        service.render(plan, True, target_platform="windows-x86_64")
    dockerfile = service.render(plan, target_platform="linux-aarch64").get("Dockerfile")
    assert b"FROM --platform=linux/arm64 python:" in dockerfile


def test_draft_still_exports_without_an_unusable_dockerfile(plan):
    plan = replace(plan, state="draft")
    generated = ReproducibilityService().render(plan)
    assert generated.get("README.md")
    assert "Dockerfile" not in {file.path for file in generated.files}
