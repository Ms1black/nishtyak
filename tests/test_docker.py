from __future__ import annotations

import json
from dataclasses import replace

import pytest

from nishtyak_core.docker import DockerfileGenerator
from nishtyak_core.models import AnalysisOptions, AnalysisReport, PackagePlan, RunSpec


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
