from __future__ import annotations

import hashlib
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from nishtyak_core import ArchiveReader, ReproducibilityService
from nishtyak_core.environment import AuthorEnvironmentSnapshot
from nishtyak_core.jobs import AnalysisJobResult
from nishtyak_core.locking import (
    DependencyLock,
    ResolutionError,
    ResolutionTarget,
    resolution_input_id,
)
from nishtyak_core.models import AnalysisOptions, UserDecisions
from nishtyak_core.python_targets import select_python_target
from nishtyak_service.config import Settings
from nishtyak_service.dto import AnalysisOptionsDTO
from nishtyak_service.errors import ServiceError
from nishtyak_service.service import LogicService


def create_project(logic, files=None, options=None, environment=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path, source in (
            files or {"experiment.py": "import numpy\nprint(numpy.mean([1, 2]))\n"}
        ).items():
            archive.writestr(path, source)
    content = stream.getvalue()
    options = options or AnalysisOptions(python_version_auto=True)
    record = logic.repository.submit("alice", content, options, environment)
    logic.repository.claim()
    report = ReproducibilityService().analyze(
        ArchiveReader().from_bytes(content), options, environment
    )
    logic.repository.finish(record.job_id, AnalysisJobResult("succeeded", report))
    return logic.repository.get_project("alice", record.project_id), report


@pytest.fixture
def automatic(tmp_path, monkeypatch):
    logic = LogicService(
        Settings(data_dir=tmp_path, runner="subprocess", automatic_resolution=True)
    )
    calls = []

    def resolve(resolver, plan, target):
        calls.append((plan, target, resolver.timeout))
        version = plan.decisions.versions.get("numpy", "2.2.0").removeprefix("==")
        packages = (f"numpy=={version}",) if plan.requirements else ()
        text = "".join(package + " --hash=sha256:" + "a" * 64 + "\n" for package in packages)
        identifier = resolution_input_id(plan, target)
        return DependencyLock(
            hashlib.sha256((identifier + "\0" + text).encode()).hexdigest(),
            plan.analysis_id,
            identifier,
            target,
            text,
            packages,
        )

    monkeypatch.setattr("nishtyak_service.automatic_resolution.VersionResolver.resolve", resolve)
    return logic, calls


def test_resolves_without_questions_and_automatically_exports_hashed_set(automatic):
    logic, calls = automatic
    record, report = create_project(logic)
    assert logic.get_job("alice", record.job_id)["automatic_resolution"]["status"] == "pending"
    plan = logic.plan("alice", record.project_id)
    assert calls == []  # Reads never initiate network operations.
    assert plan["python_version"] == "3.11"
    assert plan["python_version_origin"] == "inferred"
    assert plan["decisions"]["python_version"] is None
    assert plan["decisions"]["data_confirmed"] is False
    logic.review_pending()
    plan = logic.preparation("alice", record.project_id)["plan"]
    assert plan["automatic_resolution"]["status"] == "ready"
    assert plan["automatic_resolution"]["packages"] == ["numpy==2.2.0"]
    assert calls[0][2] == 20
    assert calls[0][1].platform == "linux-x86_64"
    with zipfile.ZipFile(io.BytesIO(logic.bundle("alice", record.project_id))) as archive:
        assert "requirements.lock.txt" in archive.namelist()
        assert "numpy==2.2.0" in archive.read("requirements.lock.txt").decode()
        readme = archive.read("README.md").decode()
        assert "Automatically selected target Python: `3.11`" in readme
        assert "Observed author-environment Python" not in readme
        assert "--require-hashes -r requirements.lock.txt" in readme
        metadata = json.loads(archive.read("analysis-report.json"))
        assert metadata["decisions"]["python_version"] is None
    reopened = LogicService(logic.settings)
    reopened.review_pending()
    assert len(calls) == 1
    assert reopened.get_decisions("alice", record.project_id)["decisions"] is None


def test_missing_input_and_unknown_historical_versions_do_not_prevent_resolution(automatic):
    logic, calls = automatic
    record, _ = create_project(logic, {"experiment.py": "import numpy\nopen('measurements.csv')\n"})
    logic.review_pending()
    plan = logic.plan("alice", record.project_id)
    assert "INPUT_PATH_UNVERIFIED" in {finding["code"] for finding in plan["findings"]}
    assert plan["automatic_resolution"]["status"] == "ready"
    assert len(calls) == 1


def test_unresolved_import_never_produces_a_misleading_partial_lock(automatic):
    logic, calls = automatic
    record, report = create_project(
        logic, {"experiment.py": "import unknown_research_tool\nimport numpy\n"}
    )
    logic.review_pending()
    plan = logic.plan("alice", record.project_id)
    assert plan["automatic_resolution"]["status"] == "needs_input"
    assert plan["automatic_resolution"]["reason"] == "DEPENDENCY_UNRESOLVED"
    assert calls == []
    assert logic.bundle("alice", record.project_id).startswith(b"PK")


def test_index_failure_is_cached_and_export_still_works(automatic, monkeypatch):
    logic, calls = automatic
    record, _ = create_project(logic)

    def unavailable(*args, **kwargs):
        calls.append("failed")
        raise ResolutionError("INDEX_UNAVAILABLE", "private network diagnostics")

    monkeypatch.setattr(
        "nishtyak_service.automatic_resolution.VersionResolver.resolve", unavailable
    )
    logic.review_pending()
    logic.review_pending()
    plan = logic.plan("alice", record.project_id)
    assert plan["automatic_resolution"]["status"] == "unavailable"
    assert plan["automatic_resolution"]["reason"] == "INDEX_UNAVAILABLE"
    assert calls == ["failed"]
    with zipfile.ZipFile(io.BytesIO(logic.bundle("alice", record.project_id))) as archive:
        assert "requirements.lock.txt" not in archive.namelist()


def test_stale_queue_snapshot_cannot_resolve_the_same_input_twice(automatic, monkeypatch):
    logic, calls = automatic
    record, _ = create_project(logic)
    monkeypatch.setattr(logic.repository, "pending_resolutions", lambda: [record])
    logic.review_pending()
    logic.review_pending()
    assert len(calls) == 1


def test_description_only_edit_does_not_retry_unchanged_unknown_import(automatic):
    logic, calls = automatic
    record, report = create_project(logic, {"experiment.py": "import unknown_research_tool\n"})
    logic.review_pending()
    logic.save_decisions(
        "alice",
        record.project_id,
        UserDecisions(report.analysis_id, description="A description"),
        0,
    )
    assert logic.get_job("alice", record.job_id)["automatic_resolution"]["status"] == "needs_input"
    logic.review_pending()
    assert calls == []


def test_unsaved_preview_never_claims_background_work_is_queued(automatic):
    logic, calls = automatic
    record, report = create_project(logic)
    logic.review_pending()
    preview = logic.plan(
        "alice", record.project_id, UserDecisions(report.analysis_id, versions={"numpy": "1.26.4"})
    )
    assert preview["automatic_resolution"]["status"] == "needs_input"
    assert preview["automatic_resolution"]["reason"] == "DECISIONS_NOT_SAVED"
    assert logic.repository.pending_resolutions() == []
    logic.review_pending()
    assert len(calls) == 1


def test_explicit_partial_lock_does_not_claim_a_complete_environment(automatic):
    logic, calls = automatic
    record, _ = create_project(
        logic, {"experiment.py": "import numpy\nimport unknown_research_tool\n"}
    )
    logic.review_pending()
    logic.resolve("alice", record.project_id, ResolutionTarget("3.11", "linux-x86_64"))
    metadata = logic.plan("alice", record.project_id)["automatic_resolution"]
    assert metadata["status"] == "needs_input"
    assert metadata["reason"] == "DEPENDENCY_UNRESOLVED"
    with zipfile.ZipFile(io.BytesIO(logic.bundle("alice", record.project_id))) as archive:
        assert "requirements.lock.txt" not in archive.namelist()
    assert len(calls) == 1


def test_ambiguous_project_roots_do_not_resolve_an_arbitrary_subset(automatic):
    logic, calls = automatic
    record, _ = create_project(
        logic,
        {
            "first/pyproject.toml": '[project]\nname="first"\ndependencies=["numpy<2"]\n',
            "first/main.py": "import numpy\n",
            "second/pyproject.toml": '[project]\nname="second"\ndependencies=["numpy>=2"]\n',
            "second/main.py": "import numpy\n",
        },
    )
    logic.review_pending()
    metadata = logic.plan("alice", record.project_id)["automatic_resolution"]
    assert metadata["status"] == "needs_input"
    assert metadata["reason"] == "PROJECT_ROOT_AMBIGUOUS"
    assert calls == []


def test_excluding_secret_file_requeues_even_when_requirements_do_not_change(automatic):
    logic, calls = automatic
    record, report = create_project(
        logic,
        {
            "experiment.py": "import numpy\nprint(numpy.mean([1, 2]))\n",
            "credentials.txt": "-----BEGIN RSA PRIVATE KEY-----\nprivate\n",
        },
    )
    logic.review_pending()
    assert logic.plan("alice", record.project_id)["automatic_resolution"]["status"] == "needs_input"
    assert calls == []
    logic.save_decisions(
        "alice",
        record.project_id,
        UserDecisions(report.analysis_id, excluded_files=("credentials.txt",)),
        0,
    )
    logic.review_pending()
    assert len(calls) == 1
    assert logic.plan("alice", record.project_id)["automatic_resolution"]["status"] == "ready"


def test_readonly_preparation_stays_available_during_resolution(automatic, monkeypatch):
    logic, calls = automatic
    record, _ = create_project(logic)
    entered, release = Event(), Event()
    original = logic.automatic_resolution._resolve

    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(logic.automatic_resolution, "_resolve", delayed)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(logic.review_pending)
        try:
            assert entered.wait(5)
            assert logic.preparation("alice", record.project_id)["plan"]["python_version"] == "3.11"
            assert logic.plan("alice", record.project_id)["run_spec"]["target"] == "experiment.py"
        finally:
            release.set()
        pending.result(timeout=5)
    assert len(calls) == 1


def test_explicit_manual_target_and_lock_metadata_stay_consistent(automatic):
    logic, calls = automatic
    record, report = create_project(logic)
    logic.review_pending()
    lock = logic.resolve("alice", record.project_id, ResolutionTarget("3.11", "windows-x86_64"))
    plan = logic.plan("alice", record.project_id)
    assert plan["automatic_resolution"]["lock_id"] == lock["id"]
    assert plan["automatic_resolution"]["target"]["platform"] == "windows-x86_64"
    assert plan["automatic_resolution"]["platform_source"] == "author"
    logic.save_decisions(
        "alice",
        record.project_id,
        UserDecisions(report.analysis_id, versions={"numpy": "1.26.4"}),
        0,
    )
    logic.review_pending()
    assert calls[-1][1].platform == "windows-x86_64"


@pytest.mark.parametrize(
    "manifest",
    [
        '[project]\nname="example"\ndynamic=["dependencies"]\n',
        '[project]\nname="example"\ndependencies=["customlib @ https://example.test/custom.whl"]\n',
    ],
)
def test_unsupported_metadata_does_not_claim_an_empty_complete_environment(automatic, manifest):
    logic, calls = automatic
    record, _ = create_project(
        logic, {"experiment.py": "print('ok')\n", "pyproject.toml": manifest}
    )
    logic.review_pending()
    assert logic.plan("alice", record.project_id)["automatic_resolution"]["status"] == "unavailable"
    assert calls == []
    assert logic.bundle("alice", record.project_id).startswith(b"PK")


def test_runtime_import_selects_extra_without_persisting_author_checkbox(automatic):
    logic, calls = automatic
    record, _ = create_project(
        logic,
        {
            "experiment.py": "import numpy\nprint(numpy.mean([1, 2]))\n",
            "pyproject.toml": (
                '[project]\nname="example"\n[project.optional-dependencies]\nscience=["numpy>=1"]\n'
            ),
            "tests/test_experiment.py": "import unknown_test_only_tool\n",
            "types.py": (
                "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n import unknown_typing_tool\n"
            ),
        },
    )
    plan = logic.plan("alice", record.project_id)
    assert plan["requirements"] == ("numpy>=1",)
    assert plan["decisions"]["selected_extras"] == ()
    assert "DEPENDENCY_UNRESOLVED" not in {finding["code"] for finding in plan["findings"]}
    assert "EXTRAS_AUTOMATIC" in {finding["code"] for finding in plan["findings"]}
    logic.review_pending()
    assert len(calls) == 1
    assert logic.plan("alice", record.project_id)["automatic_resolution"]["status"] == "ready"


def test_changed_effective_constraints_requeue_but_description_edits_reuse_lock(automatic):
    logic, calls = automatic
    record, report = create_project(logic)
    logic.review_pending()
    authored = UserDecisions(report.analysis_id, description="An experiment")
    logic.save_decisions("alice", record.project_id, authored, 0)
    logic.review_pending()
    assert len(calls) == 1
    changed = replace(authored, versions={"numpy": "1.26.4"})
    logic.save_decisions("alice", record.project_id, changed, 1)
    assert logic.plan("alice", record.project_id)["automatic_resolution"]["status"] == "pending"
    with pytest.raises(ServiceError) as error:
        logic.bundle("alice", record.project_id)
    assert error.value.code == "PREPARATION_PENDING"
    logic.review_pending()
    assert len(calls) == 2
    assert logic.plan("alice", record.project_id)["automatic_resolution"]["packages"] == [
        "numpy==1.26.4"
    ]
    with zipfile.ZipFile(io.BytesIO(logic.bundle("alice", record.project_id))) as archive:
        assert "numpy==1.26.4" in archive.read("requirements.lock.txt").decode()
        assert "numpy==2.2.0" not in archive.read("requirements.lock.txt").decode()


def test_metadata_selects_python_before_stdlib_classification(automatic):
    logic, calls = automatic
    record, report = create_project(
        logic,
        {
            "experiment.py": "import tomllib\nprint('ok')\n",
            "pyproject.toml": '[project]\nname="example"\nrequires-python=">=3.12,<3.13"\n',
        },
    )
    assert report.options.python_version == "3.12"
    assert report.dependencies[0].category == "stdlib"
    logic.review_pending()
    plan = logic.plan("alice", record.project_id)
    assert plan["python_version"] == "3.12"
    assert plan["automatic_resolution"]["python_source"] == "declared"
    assert calls[0][1].python_version == "3.12"


def test_explicit_python_profile_is_preserved(automatic):
    logic, _ = automatic
    _, report = create_project(
        logic,
        {
            "experiment.py": "import tomllib\n",
            "pyproject.toml": '[project]\nname="example"\nrequires-python=">=3.12"\n',
        },
        AnalysisOptions(python_version="3.10"),
    )
    assert report.options.python_version == "3.10"
    assert report.dependencies[0].category == "unresolved"


def test_author_environment_python_and_supported_platform_take_precedence(automatic):
    logic, calls = automatic
    environment = AuthorEnvironmentSnapshot.from_dict(
        {"python_version": "3.10.14", "platform": "macos-arm64", "distributions": []}
    )
    record, report = create_project(
        logic, {"experiment.py": "print('ok')\n"}, environment=environment
    )
    assert report.options.python_version == "3.10"
    logic.review_pending()
    plan = logic.plan("alice", record.project_id)
    assert plan["python_version"] == "3.10.14"
    assert plan["python_version_origin"] == "author_environment"
    assert calls[0][1].platform == "macos-arm64"


@pytest.mark.parametrize(
    "constraint, expected",
    [
        (None, "3.11"),
        (">=3.12,<3.13", "3.12"),
        (">=3.11.8,<3.12", "3.11.8"),
        ("==3.10.*", "3.10"),
        (">=3.15", None),
    ],
)
def test_python_target_selection(constraint, expected):
    assert select_python_target(constraint) == expected


def test_http_options_are_automatic_unless_the_caller_selects_a_profile():
    assert AnalysisOptionsDTO().to_core().python_version_auto is True
    assert AnalysisOptionsDTO(python_version="3.10").to_core().python_version_auto is False
    assert (
        AnalysisOptionsDTO(python_version="3.10", python_version_auto=True)
        .to_core()
        .python_version_auto
        is True
    )
