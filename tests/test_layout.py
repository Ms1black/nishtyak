from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from dataclasses import replace

import pytest

from nishtyak_core.analysis import ProjectAnalyzer
from nishtyak_core.docker import DockerfileGenerator
from nishtyak_core.exporting import BundleWriter
from nishtyak_core.inspection import ProjectInspector
from nishtyak_core.layout import LayoutDetector, ProjectLauncher
from nishtyak_core.models import AnalysisOptions, RunSpec, UserDecisions
from nishtyak_core.service import ReproducibilityService
from nishtyak_core.snapshot import SnapshotBuilder


def test_wrapper_root_resolves_manifests_local_imports_and_documents():
    snapshot = SnapshotBuilder().from_mapping(
        {
            "research-main/pyproject.toml": (
                '[project]\nrequires-python = ">=3.11"\ndependencies = ["numpy==2.2.0"]\n'
            ),
            "research-main/main.py": "import helpers\nimport numpy\n",
            "research-main/helpers.py": "VALUE = 1\n",
            "research-main/README.md": "Research description",
            "research-main/LICENSE": "MIT",
            "research-main/data.csv": "x\n1\n",
        }
    )
    report = ProjectAnalyzer().analyze(snapshot)
    assert report.layout.root == "research-main"
    assert report.layout.source_roots == ("research-main",)
    assert report.declared_requirements == ("numpy==2.2.0",)
    assert {item.module: item.category for item in report.dependencies} == {
        "helpers": "local",
        "numpy": "unresolved",
    }
    assert report.entrypoints == ("research-main/main.py",)
    findings = ProjectInspector().inspect(snapshot, report.layout.root)
    assert not {"README_MISSING", "LICENSE_MISSING"} & {item.code for item in findings}
    assert {item.path for item in report.files} == {item.path for item in snapshot.files}


def test_independent_project_roots_require_selection_and_explicit_root_scopes_analysis():
    snapshot = SnapshotBuilder().from_mapping(
        {
            "a/pyproject.toml": '[project]\ndependencies = ["numpy==2.2.0"]\n',
            "a/main.py": "import (\n",
            "b/pyproject.toml": "[project]\ndependencies = []\n",
            "b/lib/lab.py": "import json\n",
        }
    )
    report = ProjectAnalyzer().analyze(snapshot)
    assert report.layout.ambiguous
    assert report.layout.root_candidates == ("a", "b")
    assert report.status == "partial"
    assert "PROJECT_ROOT_AMBIGUOUS" in {item.code for item in report.findings}
    selected = ProjectAnalyzer().analyze(
        snapshot, AnalysisOptions(project_root="b", source_roots=("lib",))
    )
    assert selected.layout.root == "b"
    assert selected.layout.source_roots == ("b/lib",)
    assert not selected.layout.ambiguous
    assert selected.declared_requirements == ()
    assert selected.status == "complete"
    assert not any(item.code == "SOURCE_PARSE_ERROR" for item in selected.findings)
    assert [item.path for item in selected.imports] == ["b/lib/lab.py"]


def test_nested_example_manifest_does_not_override_declared_parent_project():
    snapshot = SnapshotBuilder().from_mapping(
        {"pyproject.toml": "", "examples/pyproject.toml": "", "main.py": ""}
    )
    layout = LayoutDetector().detect(snapshot, AnalysisOptions())
    assert layout.root == "."
    assert layout.root_candidates == (".",)
    assert not layout.ambiguous


def test_single_python_package_directory_is_not_mistaken_for_archive_wrapper():
    snapshot = SnapshotBuilder().from_mapping(
        {"lab/__init__.py": "", "lab/__main__.py": "from lab import helper\n", "lab/helper.py": ""}
    )
    report = ProjectAnalyzer().analyze(snapshot)
    assert report.layout.root == "."
    assert report.dependencies[0].category == "local"


@pytest.mark.parametrize("root", ["../outside", "/tmp", "a/../../b", "C:\\temp", "", "missing"])
def test_explicit_root_must_be_a_relative_existing_directory(root):
    snapshot = SnapshotBuilder().from_mapping({"main.py": ""})
    with pytest.raises(ValueError):
        LayoutDetector().detect(snapshot, AnalysisOptions(project_root=root))


@pytest.mark.parametrize("kind,target", [("module", "lab"), ("callable", "lab.cli:main")])
def test_exported_src_layout_runs_with_reviewed_cwd_arguments_and_import_roots(
    tmp_path, kind, target
):
    files = {
        "research-main/pyproject.toml": (
            '[project]\nrequires-python = ">=3.11"\ndependencies = []\n'
            '[project.scripts]\nresearch = "lab.cli:main"\n'
        ),
        "research-main/src/lab/__init__.py": "",
        "research-main/src/lab/cli.py": (
            "import sys\nfrom pathlib import Path\nimport helpers\n"
            "def main():\n"
            "    print(helpers.VALUE + ':' + Path('data.txt').read_text() + ':' + sys.argv[1])\n"
            "    return 0\n"
        ),
        "research-main/src/lab/__main__.py": "from .cli import main\nraise SystemExit(main())\n",
        "research-main/helpers.py": "VALUE = 'local'\n",
        "research-main/data.txt": "42",
    }
    snapshot = SnapshotBuilder().from_mapping(files)
    service = ReproducibilityService()
    report = service.analyze(snapshot)
    assert report.layout.source_roots == ("research-main", "research-main/src")
    assert "lab.cli:main" in report.entrypoints
    plan = service.prepare(
        report,
        UserDecisions(
            analysis_id=report.analysis_id,
            run_spec=RunSpec(target, kind=kind, args=("a b",), working_directory="research-main"),
            python_version="3.11",
            data_confirmed=True,
        ),
    )
    assert plan.state == "ready_to_verify"
    generated = service.render(plan, include_dockerfile=True)
    assert "python run_project.py" in generated.get("README.md").decode()
    dockerfile = generated.get("Dockerfile").decode()
    assert "COPY run_project.py ./run_project.py" in dockerfile
    assert 'CMD ["python", "/workspace/run_project.py"]' in dockerfile
    artifact = BundleWriter().write(snapshot, plan, generated, tmp_path / "bundle.zip")
    extracted = tmp_path / "colleague"
    with zipfile.ZipFile(artifact.path) as archive:
        archive.extractall(extracted)
    # The trusted fixture runs in an isolated Python process outside the source checkout.
    completed = subprocess.run(
        [sys.executable, "-I", str(extracted / "run_project.py")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.stdout == "local:42:a b\n"
    assert (extracted / "project/research-main/src/lab/cli.py").exists()


def test_simple_scripts_keep_direct_readme_and_docker_commands():
    service = ReproducibilityService()
    snapshot = SnapshotBuilder().from_mapping({"main.py": "print('ok')\n"})
    report = service.analyze(snapshot)
    plan = service.prepare(
        report,
        UserDecisions(
            analysis_id=report.analysis_id,
            run_spec=RunSpec("main.py"),
            python_version="3.11",
            data_confirmed=True,
        ),
    )
    assert not ProjectLauncher.required(plan)
    generated = service.render(plan)
    assert "cd project\npython main.py" in generated.get("README.md").decode()
    assert all(item.path != "run_project.py" for item in generated.files)
    command = next(
        line[4:]
        for line in DockerfileGenerator().generate(plan).content.decode().splitlines()
        if line.startswith("CMD ")
    )
    assert json.loads(command) == ["python", "main.py"]
    # Legacy reports without layout still use their original direct command behavior.
    assert not ProjectLauncher.required(replace(plan, report=replace(report, layout=None)))
