from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from nishtyak_core import ArchiveReader, ReproducibilityService
from nishtyak_core.jobs import AnalysisJobResult
from nishtyak_core.locking import DependencyLock, resolution_input_id
from nishtyak_core.models import AnalysisOptions, DeclarationDecision, UserDecisions
from nishtyak_service.config import Settings
from nishtyak_service.errors import ServiceError
from nishtyak_service.service import LogicService


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    logic = LogicService(Settings(data_dir=tmp_path, automatic_resolution=True))
    calls = []

    def resolve(resolver, plan, target):
        text = "numpy==2.2.0 --hash=sha256:" + "a" * 64 + "\n"
        identifier = resolution_input_id(plan, target)
        return DependencyLock(
            hashlib.sha256((identifier + "\0" + text).encode()).hexdigest(),
            plan.analysis_id,
            identifier,
            target,
            text,
            ("numpy==2.2.0",),
        )

    def review(record, report, *, plan, aspects=(), pass_id):
        calls.append(("pre_human", pass_id, aspects))
        return {"attempted": True, "status": "unavailable", "reason": "no_grounded_answer"}

    def finalize(record, report, *, plan, aspects=(), pass_id):
        calls.append(("post_human", pass_id, aspects))
        return {"attempted": True, "status": "ready", "reason": None}

    monkeypatch.setattr("nishtyak_service.automatic_resolution.VersionResolver.resolve", resolve)
    monkeypatch.setattr(logic.automatic_review, "needs_description", lambda *args: False)
    monkeypatch.setattr(logic.automatic_review, "prepare_pass", review)
    monkeypatch.setattr(logic.automatic_review, "finalize", finalize)
    return logic, calls


def submit(logic, files=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path, content in (
            files or {"experiment.py": "import unknown_first\nimport unknown_second\nprint(1)\n"}
        ).items():
            archive.writestr(path, content)
    content = stream.getvalue()
    options = AnalysisOptions(python_version_auto=True)
    record = logic.repository.submit("alice", content, options)
    logic.repository.claim()
    report = ReproducibilityService().analyze(ArchiveReader().from_bytes(content), options)
    logic.repository.finish(record.job_id, AnalysisJobResult("succeeded", report))
    return logic.repository.get_project("alice", record.project_id), report


def state(logic, record):
    return logic.plan("alice", record.project_id)["automation"]


def answer(logic, record, report, decisions=None, revision=0, notes=""):
    plan = logic._prepare(report, decisions or UserDecisions(report.analysis_id), record)
    submission = logic.automation.submission_state(
        record, plan, state(logic, record)["questionnaire_id"], notes
    )
    result = logic.repository.save_decisions(
        "alice",
        record.project_id,
        report.analysis_id,
        revision,
        plan.to_dict()["decisions"],
        automation_state=submission,
    )
    logic.automatic_resolution.queue_if_changed(record, plan)
    return result


def test_project_wide_attempts_collect_all_remaining_questions(workflow):
    logic, calls = workflow
    record, _ = submit(
        logic,
        {
            "experiment.py": "import numpy\nimport unknown_first\n"
            "if __name__ == '__main__':\n print(1)\n",
            "preview.py": "import unknown_second\nif __name__ == '__main__':\n print(2)\n",
            "requirements.txt": "numpy<2\nnumpy==2.2.0\n",
        },
    )
    logic.review_pending()
    current = state(logic, record)
    assert current["phase"] == "needs_input"
    assert current["stage"] == "pre_human" and current["human_round"] == 0
    assert current["attempt"] == 2 and len(calls) == 2
    assert all(set(call[2]) == {"packages", "environment", "entrypoint"} for call in calls)
    assert {item["module"] for item in current["questions"] if item.get("module")} == {
        "unknown_first",
        "unknown_second",
    }
    assert {item["kind"] for item in current["questions"]} == {
        "package_mapping",
        "version_constraint",
        "entrypoint",
    }
    assert current["question"] == current["questions"][0]


def test_regular_edits_do_not_submit_the_questionnaire(workflow):
    logic, calls = workflow
    record, report = submit(logic)
    logic.review_pending()
    original = state(logic, record)
    logic.save_decisions(
        "alice",
        record.project_id,
        UserDecisions(report.analysis_id, packages={"unknown_first": "numpy"}),
        0,
    )
    logic.review_pending()
    current = state(logic, record)
    assert current["phase"] == "needs_input" and current["human_round"] == 0
    assert current["questionnaire_id"] == original["questionnaire_id"]
    assert len(calls) == 2


def test_one_submission_runs_one_final_review_then_finishes(workflow):
    logic, calls = workflow
    record, report = submit(logic)
    logic.review_pending()
    result = answer(
        logic,
        record,
        report,
        UserDecisions(
            report.analysis_id, packages={"unknown_first": "numpy", "unknown_second": "numpy"}
        ),
        notes="Both modules are private aliases of numpy.",
    )
    assert result["revision"] == 1
    assert state(logic, record)["phase"] == "finalizing"
    logic.review_pending()
    current = state(logic, record)
    assert current["phase"] == "ready" and current["stage"] == "complete"
    assert current["human_round"] == 1 and current["final_attempt"] == 1
    assert current["questions"] == [] and "question" not in current
    assert "clarification_notes" not in current
    assert logic.repository.read_automation(record)["clarification_notes"].startswith("Both")
    assert [call[0] for call in calls] == ["pre_human", "pre_human", "post_human"]
    assert zipfile.is_zipfile(io.BytesIO(logic.bundle("alice", record.project_id)))
    for _ in range(3):
        logic.review_pending()
        logic.preparation("alice", record.project_id)
    assert len(calls) == 3


def test_skipped_answers_produce_draft_without_a_second_questionnaire(workflow):
    logic, calls = workflow
    record, report = submit(logic)
    logic.review_pending()
    answer(logic, record, report, notes="Не знаю названий библиотек.")
    logic.review_pending()
    current = state(logic, record)
    assert current["phase"] == "draft" and current["stage"] == "complete"
    assert current["questions"] == [] and "question" not in current
    assert {item["module"] for item in current["unresolved"]} == {
        "unknown_first",
        "unknown_second",
    }
    assert current["final_attempt"] == 1
    archive = logic.bundle("alice", record.project_id)
    assert zipfile.is_zipfile(io.BytesIO(archive))
    assert len(calls) == 3


def test_source_safety_cannot_be_skipped_by_submitting_empty_answers(workflow):
    logic, calls = workflow
    record, report = submit(
        logic,
        {"experiment.py": "API_KEY = 'sk-" + "x" * 48 + "'\nprint('ok')\n"},
    )
    logic.review_pending()
    assert state(logic, record)["phase"] == "needs_input"
    assert state(logic, record)["attempt"] == 0
    assert calls == []
    answer(logic, record, report)
    logic.review_pending()
    assert state(logic, record)["phase"] == "draft"
    with pytest.raises(ServiceError, match="Resolve the blocking findings") as error:
        logic.bundle("alice", record.project_id)
    assert error.value.code == "PLAN_BLOCKED"


def test_questionnaire_and_decisions_are_atomic_on_revision_conflict(workflow):
    logic, _ = workflow
    record, report = submit(logic)
    logic.review_pending()
    original = state(logic, record)
    with pytest.raises(ServiceError) as error:
        answer(logic, record, report, revision=3)
    assert error.value.code == "DECISIONS_REVISION_CONFLICT"
    assert state(logic, record) == original
    assert logic.get_decisions("alice", record.project_id)["revision"] == 0


def test_questionnaire_id_is_stable_and_stale_submission_is_rejected(workflow):
    logic, calls = workflow
    record, report = submit(logic)
    logic.review_pending()
    original = state(logic, record)
    reopened = LogicService(logic.settings)
    assert state(reopened, record) == original
    reopened.review_pending()
    assert len(calls) == 2
    plan = logic._prepare(report, UserDecisions(report.analysis_id), record)
    with pytest.raises(ServiceError) as error:
        logic.automation.submission_state(record, plan, "wrong-questionnaire")
    assert error.value.code == "CLARIFICATIONS_STALE"
    answer(logic, record, report)
    with pytest.raises(ServiceError) as error:
        logic.automation.submission_state(record, plan, original["questionnaire_id"])
    assert error.value.code == "CLARIFICATIONS_STALE"


def test_manual_edits_after_completion_only_revalidate(workflow):
    logic, calls = workflow
    record, report = submit(logic)
    logic.review_pending()
    answer(logic, record, report)
    logic.review_pending()
    logic.save_decisions(
        "alice",
        record.project_id,
        UserDecisions(report.analysis_id, packages={"unknown_first": "numpy"}),
        1,
    )
    logic.review_pending()
    current = state(logic, record)
    assert current["phase"] == "draft" and current["questions"] == []
    assert current["human_round"] == 1 and len(calls) == 3


def test_no_provider_does_not_invent_two_attempts(workflow, monkeypatch):
    logic, calls = workflow
    record, _ = submit(logic)
    monkeypatch.setattr(
        logic.automatic_review,
        "prepare_pass",
        lambda *args, **kwargs: {"attempted": False, "reason": "advisor_unavailable"},
    )
    logic.review_pending()
    current = state(logic, record)
    assert current["phase"] == "needs_input" and current["attempt"] == 0
    assert calls == []


def test_compatible_duplicate_constraints_are_not_asked_with_a_real_conflict(workflow):
    logic, _ = workflow
    record, _ = submit(
        logic,
        {
            "experiment.py": "import numpy\nimport pandas\nprint(1)\n",
            "requirements.txt": "numpy>=1\nnumpy<4\npandas<2\npandas==3.0.0\n",
        },
    )
    logic.review_pending()
    questions = state(logic, record)["questions"]
    assert len(questions) == 1
    assert questions[0]["kind"] == "version_constraint"
    assert "pandas" in questions[0]["message"]


def test_large_unknown_import_set_is_grouped_without_losing_the_count(workflow):
    logic, calls = workflow
    names = ["unknown_" + str(index) + "_" + "x" * 300 for index in range(140)]
    names.append("unknown_" + "y" * 4000)
    record, report = submit(
        logic, {"experiment.py": "\n".join("import " + name for name in names) + "\nprint(1)\n"}
    )
    logic.review_pending()
    current = state(logic, record)
    assert current["phase"] == "needs_input" and len(current["questions"]) <= 12
    summary = next(item for item in current["questions"] if item.get("details_in_report"))
    assert summary["unresolved_count"] + len(current["questions"]) - 1 == len(names)
    assert summary["unresolved_aspects"] == {"dependencies": summary["unresolved_count"]}
    answer(logic, record, report, notes="🧪" * 6000)
    logic.review_pending()
    assert state(logic, record)["phase"] == "draft"
    assert len(logic.repository.read_automation(record)["clarification_notes"]) == 6000
    assert len(calls) == 3


@pytest.mark.parametrize("notes", [None, 12, "x" * 6001])
def test_python_submission_rejects_invalid_notes(workflow, notes):
    logic, _ = workflow
    record, report = submit(logic)
    logic.review_pending()
    with pytest.raises(ServiceError) as error:
        answer(logic, record, report, notes=notes)
    assert error.value.status == 422 and error.value.code == "CLARIFICATIONS_INVALID"
    assert state(logic, record)["human_round"] == 0


def test_legacy_single_question_expands_into_one_stable_batch(workflow):
    logic, _ = workflow
    record, report = submit(logic)
    logic.review_pending()
    current = state(logic, record)
    saved = logic.repository.read_automation(record)
    logic.repository.save_automation(
        record,
        {
            "phase": "needs_input",
            "question": current["questions"][0],
            "attempt": 2,
            "max_attempts": 2,
            "input_id": saved["input_id"],
        },
    )
    job = logic.get_job("alice", record.job_id)["automation"]
    preparation = state(logic, record)
    assert job["questionnaire_id"] == preparation["questionnaire_id"]
    assert len(preparation["questions"]) == 2
    answer(logic, record, report)
    logic.review_pending()
    assert state(logic, record)["phase"] == "draft"
    assert state(logic, record)["human_round"] == 1


def test_reserved_pass_is_reconciled_when_its_saved_facts_already_solve_project(
    workflow, monkeypatch
):
    logic, calls = workflow
    record, _ = submit(logic, {"experiment.py": "import unknown_first\nprint(1)\n"})
    mappings = {}
    monkeypatch.setattr(logic.automatic_review, "package_mappings", lambda *args: mappings)
    monkeypatch.setattr(logic.repository, "pending_automation", lambda: [record])

    def interrupted(record, report, *, plan, aspects, pass_id):
        calls.append(("pre_human", pass_id, aspects))
        if len(calls) == 1:
            mappings["unknown_first"] = "numpy"
            raise RuntimeError("crash after the review result was saved")
        return {"attempted": True, "status": "ready"}

    monkeypatch.setattr(logic.automatic_review, "prepare_pass", interrupted)
    with pytest.raises(RuntimeError, match="crash"):
        logic.review_pending()
    logic.review_pending()
    current = state(logic, record)
    assert current["phase"] == "ready" and current["attempt"] == 1
    assert len(calls) == 2 and calls[0][1] == calls[1][1]


def test_replaced_constraints_are_not_reasked_alongside_another_conflict(workflow):
    logic, _ = workflow
    record, report = submit(
        logic,
        {
            "experiment.py": "import numpy\nimport pandas\nprint(1)\n",
            "requirements.txt": "numpy<2\nnumpy==2.2.0\npandas<2\npandas==3.0.0\n",
        },
    )
    declaration = next(item for item in report.declarations if item.requirement == "numpy<2")
    logic.save_decisions(
        "alice",
        record.project_id,
        UserDecisions(
            report.analysis_id,
            declaration_actions={declaration.id: DeclarationDecision("replace", "numpy>=2")},
        ),
        0,
    )
    logic.review_pending()
    questions = state(logic, record)["questions"]
    assert len(questions) == 1 and "pandas" in questions[0]["message"]


def test_missing_local_module_is_reported_as_draft_after_the_only_human_round(workflow):
    logic, _ = workflow
    record, report = submit(
        logic, {"experiment.py": "from .helpers import compute\nprint(compute())\n"}
    )
    logic.review_pending()
    current = state(logic, record)
    assert any(item.get("code") == "LOCAL_MODULE_MISSING" for item in current["questions"])
    answer(logic, record, report)
    logic.review_pending()
    current = state(logic, record)
    assert current["phase"] == "draft" and current["questions"] == []
    assert any(item.get("code") == "LOCAL_MODULE_MISSING" for item in current["unresolved"])
