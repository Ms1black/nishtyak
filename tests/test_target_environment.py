from __future__ import annotations

import io
import zipfile
from dataclasses import asdict

import pytest

from nishtyak_core import ArchiveReader
from nishtyak_core.environment import AuthorEnvironmentSnapshot
from nishtyak_core.jobs import AnalysisJobResult
from nishtyak_core.locking import ResolutionTarget
from nishtyak_core.models import AnalysisOptions, RunSpec, UserDecisions
from nishtyak_core.report_codec import AnalysisReportCodec, InvalidWorkerReport
from nishtyak_service.config import Settings
from nishtyak_service.errors import ServiceError
from nishtyak_service.service import LogicService
from nishtyak_web.mapping import analysis_to_web


@pytest.fixture
def logic(tmp_path):
    return LogicService(Settings(data_dir=tmp_path.resolve(), runner="subprocess"))


def source_zip(*, changed_data=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("experiment.py", "from helper import answer\nprint(answer())\n")
        archive.writestr("helper.py", "def answer():\n    return 42\n")
        archive.writestr("unused.py", "import tomllib\n")
        archive.writestr("data.csv", "value\n99\n" if changed_data else "value\n42\n")
    return stream.getvalue()


def finish(logic, record):
    assert logic.repository.claim().job_id == record.job_id
    report = logic.core.analyze(
        ArchiveReader().from_path(logic.repository.archive_path(record.project_id)),
        record.options,
        record.author_environment,
    )
    assert logic.repository.finish(record.job_id, AnalysisJobResult("succeeded", report))
    return logic.repository.get_project("alice", record.project_id), report


def project(logic, *, options=None, environment=None):
    record = logic.repository.submit(
        "alice", source_zip(), options or AnalysisOptions(), environment
    )
    return finish(logic, record)


def save_choices(logic, record, report):
    decisions = UserDecisions(
        analysis_id=report.analysis_id,
        python_version="3.11",
        run_spec=RunSpec("experiment.py", args=("--scale", "2")),
        excluded_files=("unused.py",),
        description="Авторское описание",
        input_description="Колонка value в data.csv",
        expected_output="Число в stdout",
        data_confirmed=True,
    )
    return logic.save_decisions("alice", record.project_id, decisions, 0)["decisions"]


def test_patch_target_uses_minor_stdlib_profile_and_retains_full_version(logic):
    record, report = project(logic)
    saved = save_choices(logic, record, report)
    target = ResolutionTarget("3.12.8", "windows-x86_64")

    queued = logic.apply_target("alice", record.project_id, target, 1)
    record = logic.repository.get_project("alice", record.project_id)
    assert record.job_id == queued["job_id"]
    assert record.options.python_version == "3.12"
    assert record.options.python_version_auto is False
    assert record.options.target_python_version == "3.12.8"
    record, updated = finish(logic, record)
    assert updated.analysis_id != report.analysis_id
    assert (
        next(item for item in updated.dependencies if item.module == "tomllib").category == "stdlib"
    )
    document = logic.get_decisions("alice", record.project_id)
    assert document["revision"] == 2
    assert document["decisions"] == {
        **saved,
        "analysis_id": updated.analysis_id,
        "python_version": "3.12.8",
    }
    assert logic.plan("alice", record.project_id)["python_version"] == "3.12.8"
    web = analysis_to_web(record.project_id, {"status": "succeeded", "analysis": updated.to_dict()})
    assert web["targetEnvironment"] == {
        "pythonVersion": "3.12.8",
        "platform": "windows-x86_64",
    }


def test_explicit_target_options_win_over_observed_author_environment(logic):
    environment = AuthorEnvironmentSnapshot.from_dict(
        {"python_version": "3.10.14", "platform": "macos-arm64", "distributions": []}
    )
    options = AnalysisOptions(
        python_version="3.12",
        target_python_version="3.12.8",
        target_platform="windows-x86_64",
    )
    record, report = project(logic, options=options, environment=environment)

    plan = logic.plan("alice", record.project_id)
    assert report.author_python_version == "3.10.14"
    assert report.author_platform == "macos-arm64"
    assert report.options.target_python_version == "3.12.8"
    assert plan["python_version"] == "3.12.8"
    assert "PYTHON_PROFILE_MISMATCH" not in {item["code"] for item in plan["findings"]}


def test_inconsistent_target_profile_is_rejected_before_reanalysis(logic):
    options = AnalysisOptions(
        python_version="3.11",
        target_python_version="3.12.8",
        target_platform="windows-x86_64",
    )
    with pytest.raises(InvalidWorkerReport):
        AnalysisReportCodec().decode_options(asdict(options))
    record, report = project(logic)
    before = logic.get_decisions("alice", record.project_id)

    with pytest.raises(ServiceError) as raised:
        logic.repository.reanalyze("alice", record.project_id, options, expected_revision=0)

    assert raised.value.status == 422 and raised.value.code == "OPTIONS_INVALID"
    assert logic.repository.get_project("alice", record.project_id) == record
    assert logic.get_decisions("alice", record.project_id) == before
    assert logic.repository.read_report(record).analysis_id == report.analysis_id


@pytest.mark.parametrize("first", ["source", "target"])
def test_queued_source_and_target_changes_retain_choices_but_reset_data_confirmation(logic, first):
    record, report = project(logic)
    saved = save_choices(logic, record, report)
    target = ResolutionTarget("3.12.8", "windows-x86_64")

    if first == "source":
        logic.replace_source("alice", record.project_id, source_zip(changed_data=True), 1)
        logic.apply_target("alice", record.project_id, target, 2)
    else:
        logic.apply_target("alice", record.project_id, target, 1)
        logic.replace_source("alice", record.project_id, source_zip(changed_data=True), 2)
    queued = logic.repository.get_project("alice", record.project_id)
    record, updated = finish(logic, queued)

    document = logic.get_decisions("alice", record.project_id)
    assert document["revision"] == 3
    assert document["decisions"] == {
        **saved,
        "analysis_id": updated.analysis_id,
        "python_version": "3.12.8",
        "data_confirmed": False,
    }
    assert logic.plan("alice", record.project_id)["python_version"] == "3.12.8"
    snapshot = ArchiveReader().from_path(logic.repository.archive_path(record.project_id))
    assert (
        next(item.content for item in snapshot.files if item.path == "data.csv") == b"value\n99\n"
    )


@pytest.mark.parametrize(
    "expected_revision, error_code",
    [(0, "DECISIONS_REVISION_CONFLICT"), (1, "CLARIFICATION_REQUIRED")],
)
def test_target_rejection_preserves_current_job_decisions_and_pending_questions(
    logic, expected_revision, error_code
):
    record, report = project(logic)
    save_choices(logic, record, report)
    state = {
        "phase": "needs_input",
        "stage": "pre_human",
        "human_round": 0,
        "questionnaire_id": "a" * 20,
        "questions": [{"kind": "python_version"}],
    }
    logic.repository.save_automation(record, state)
    lock_path = logic.repository.report_path(record.project_id).parent / "dependency-lock.json"
    lock_path.write_bytes(b"unchanged lock sentinel")
    before = logic.get_decisions("alice", record.project_id)
    source = logic.repository.archive_path(record.project_id).read_bytes()

    with pytest.raises(ServiceError) as raised:
        logic.apply_target(
            "alice",
            record.project_id,
            ResolutionTarget("3.12.8", "windows-x86_64"),
            expected_revision,
        )

    assert raised.value.status == 409 and raised.value.code == error_code
    assert logic.repository.get_project("alice", record.project_id) == record
    assert logic.get_decisions("alice", record.project_id) == before
    assert logic.repository.read_automation(record) == state
    assert logic.repository.archive_path(record.project_id).read_bytes() == source
    assert lock_path.read_bytes() == b"unchanged lock sentinel"
    assert logic.repository.read_report(record).analysis_id == report.analysis_id
