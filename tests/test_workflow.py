import json
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from nishtyak_core import ReproducibilityService, RunSpec, SnapshotBuilder, UserDecisions
from nishtyak_core.cli import CommandLine
from nishtyak_core.exporting import BundleWriter
from nishtyak_core.security import SecretScanner

FIXTURES = Path(__file__).parent / "fixtures"


def prepare_example(name):
    snapshot = SnapshotBuilder().from_directory(FIXTURES / name)
    service = ReproducibilityService()
    report = service.analyze(snapshot)
    decisions = UserDecisions(
        report.analysis_id,
        run_spec=RunSpec("main.py"),
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
        input_description="data.csv contains the values 2, 4, 6.",
        expected_output="mean=4.00",
        data_confirmed=True,
    )
    plan = service.prepare(report, decisions)
    return snapshot, service, plan


@pytest.mark.parametrize("name", ["local_project", "scientific_project"])
def test_full_package_can_be_built_for_own_examples(name, tmp_path):
    snapshot, service, plan = prepare_example(name)
    assert plan.state == "ready_to_verify"
    generated = service.render(plan)
    artifact = BundleWriter().write(snapshot, plan, generated, tmp_path / "bundle.zip")
    with zipfile.ZipFile(artifact.path) as archive:
        assert {"README.md", "requirements.txt", "analysis-report.json"} <= set(archive.namelist())
        assert "project/data.csv" in archive.namelist()
        payload = json.loads(archive.read("analysis-report.json"))
        assert payload["state"] == "ready_to_verify"
        assert "verified" not in payload
    second = BundleWriter().write(snapshot, plan, generated, tmp_path / "second.zip")
    assert artifact.sha256 == second.sha256


def test_packaged_local_example_preserves_relative_data_paths(tmp_path):
    snapshot, service, plan = prepare_example("local_project")
    artifact = BundleWriter().write(snapshot, plan, service.render(plan), tmp_path / "bundle.zip")
    with zipfile.ZipFile(artifact.path) as archive:
        archive.extractall(tmp_path / "unpacked")
    result = subprocess.run(
        [sys.executable, "main.py"],
        cwd=tmp_path / "unpacked" / "project",
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "mean=4.00"


def test_scientific_example_has_only_external_scientific_dependencies():
    _, _, plan = prepare_example("scientific_project")
    assert set(plan.requirements) == {"numpy", "pandas"}


def test_known_secret_blocks_export_and_never_appears_in_findings(tmp_path):
    token = "sk-" + "control-secret-value-123456789"
    snapshot = SnapshotBuilder().from_mapping({"main.py": f'API_KEY = "{token}"\nprint(1)'})
    service = ReproducibilityService()
    report = service.analyze(snapshot)
    assert token not in json.dumps(report.to_dict())
    assert "SECRET_DETECTED" in {finding.code for finding in report.findings}
    plan = service.prepare(report, UserDecisions(report.analysis_id, run_spec=RunSpec("main.py")))
    assert plan.state == "blocked"
    with pytest.raises(ValueError, match="blocking"):
        service.render(plan)


def test_export_rejects_a_changed_snapshot(tmp_path):
    snapshot, service, plan = prepare_example("local_project")
    altered = replace(snapshot, fingerprint="different")
    with pytest.raises(ValueError):
        BundleWriter().write(altered, plan, service.render(plan), tmp_path / "bundle.zip")


def test_cli_analyze_outputs_json(capsys):
    code = CommandLine().run(["analyze", str(FIXTURES / "local_project")])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1


def test_cli_can_add_an_unverified_docker_template(tmp_path, capsys):
    output = tmp_path / "docker-bundle.zip"
    code = CommandLine().run(
        [
            "build",
            str(FIXTURES / "local_project"),
            "--entry",
            "main.py",
            "--output",
            str(output),
            "--python",
            "3.11",
            "--data",
            "data.csv",
            "--docker",
        ]
    )
    assert code == 0
    with zipfile.ZipFile(output) as archive:
        assert b"FROM python:3.11-slim" in archive.read("Dockerfile")
        assert b"Unverified template" in archive.read("Dockerfile")


def test_cli_advice_is_optional_without_a_key(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    code = CommandLine().run(["advise", str(FIXTURES / "local_project")])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "unavailable"


def test_service_inspects_missing_documentation():
    report = ReproducibilityService().analyze(
        SnapshotBuilder().from_mapping({"main.py": "print(1)"})
    )
    codes = {finding.code for finding in report.findings}
    assert any("README" in code for code in codes)
    assert any("LICENSE" in code for code in codes)


def test_author_environment_can_be_applied_through_the_public_service():
    from nishtyak_core.environment import AuthorEnvironmentSnapshot, InstalledDistribution

    snapshot = SnapshotBuilder().from_mapping({"main.py": "import pandas"})
    service = ReproducibilityService()
    report = service.analyze(snapshot)
    author = AuthorEnvironmentSnapshot(
        "3.11.1", "test-platform", (InstalledDistribution("pandas", "2.2.0", ("pandas",)),)
    )
    enriched = service.analyze(snapshot, author_environment=author)
    assert enriched.analysis_id != report.analysis_id
    assert enriched.dependencies[0].requirement == "pandas==2.2.0"
    plan = service.prepare(
        enriched,
        UserDecisions(enriched.analysis_id, run_spec=RunSpec("main.py"), data_confirmed=True),
    )
    assert plan.requirements == ("pandas==2.2.0",)


def test_cli_inspects_conda_without_converting_its_packages(tmp_path, capsys):
    (tmp_path / "environment.yml").write_text(
        "name: experiment\nchannels: [conda-forge]\n"
        "dependencies:\n  - python=3.11\n  - numpy=1.26\n  - pip:\n    - pandas>=2\n"
    )
    assert CommandLine().run(["conda", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "numpy=1.26" in payload["conda_dependencies"]
    assert payload["pip_dependencies"] == ["pandas>=2"]


def test_conda_declaration_requires_review_before_a_pip_bundle_is_ready():
    service = ReproducibilityService()
    snapshot = SnapshotBuilder().from_mapping(
        {"main.py": "print(1)", "environment.yml": "dependencies: [python=3.11]"}
    )
    report = service.analyze(snapshot)
    plan = service.prepare(
        report,
        UserDecisions(
            report.analysis_id,
            run_spec=RunSpec("main.py"),
            python_version="3.11",
            data_confirmed=True,
        ),
    )
    assert plan.state == "draft"


def test_scanner_allows_environment_lookups_but_detects_literal_credentials():
    scanner = SecretScanner()
    lookup = 'api_key = os.environ.get("DEEPSEEK_API_KEY")'
    assert scanner.redact(lookup) == lookup
    value = "a-long-private-value-1234"
    assert value not in scanner.redact(f"DEEPSEEK_API_KEY={value}")
    assert value not in scanner.redact(f'deepseek_api_key = "{value}"')
