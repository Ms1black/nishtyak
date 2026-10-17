from __future__ import annotations

import io
import json
import zipfile

import pytest

from nishtyak_core import ArchiveReader, ReproducibilityService
from nishtyak_core.advisors import ProjectAdvisoryResult
from nishtyak_core.advisors.package_verification import PackageVerificationError
from nishtyak_core.advisors.project_agent import ProjectAdvisorySuggestion
from nishtyak_core.jobs import AnalysisJobResult
from nishtyak_core.models import AnalysisOptions, UserDecisions
from nishtyak_service.config import Settings
from nishtyak_service.service import LogicService


@pytest.fixture
def focused(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    logic = LogicService(
        Settings(
            data_dir=tmp_path, runner="subprocess", automatic_review=True, automatic_resolution=True
        )
    )

    def create(files):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for path, source in files.items():
                archive.writestr(path, source)
        content = stream.getvalue()
        record = logic.repository.submit("alice", content, AnalysisOptions())
        logic.repository.claim()
        report = ReproducibilityService().analyze(ArchiveReader().from_bytes(content))
        logic.repository.finish(record.job_id, AnalysisJobResult("succeeded", report))
        record = logic.repository.get_project("alice", record.project_id)
        plan = logic.core.prepare(report, UserDecisions(report.analysis_id))
        return logic, record, report, plan

    return create


def response(context, suggestions=(), status="ok", reason=None):
    result = ProjectAdvisoryResult(
        context.analysis_id, "repair", status, tuple(suggestions), rounds=1, reason=reason
    )
    return json.loads(json.dumps(result.to_dict()))


def test_two_failed_attempts_are_persisted_and_never_retried_on_reads(focused, monkeypatch):
    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})
    calls = []

    def unanswered(runner, context, feature):
        calls.append((context, runner.wall_time_seconds))
        return response(context)

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", unanswered)
    logic.automatic_review.ensure(record, report, plan=plan, aspects=("packages",))
    logic.automatic_review.ensure(record, report, plan=plan, aspects=("packages",))
    state = logic.automatic_review.aspect_status(record, report)["package:unknown_lab"]
    assert state["status"] == "exhausted" and state["attempts"] == 2
    assert len(calls) == 2
    assert calls[0][0].facts["summary"]["assistant_focus"]["modules"] == ["unknown_lab"]
    assert all(0 < budget <= 15 for _, budget in calls)
    assert logic.get_decisions("alice", record.project_id)["decisions"] is None


@pytest.mark.parametrize("review_mode", ["legacy", "project"])
def test_validated_wheel_mapping_is_cached_without_becoming_an_author_decision(
    focused, monkeypatch, review_mode
):
    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})
    calls = []

    def mapped(runner, context, feature):
        calls.append(feature)
        observation = next(
            item for item in context.facts["imports"] if item["module"] == "unknown_lab"
        )
        source = next(file for file in context.files if file["path"] == observation["path"])
        return response(
            context,
            [
                ProjectAdvisorySuggestion(
                    "package_hypothesis",
                    "Импорт предоставляет опубликованный пакет.",
                    (observation["id"], source["id"]),
                    module="unknown_lab",
                    package="research-provider",
                )
            ],
        )

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", mapped)
    monkeypatch.setattr(
        "nishtyak_service.automatic_review.PublishedPackageVerifier.verify",
        lambda *args, **kwargs: {
            "package": "research-provider",
            "method": "published_wheel_contents",
            "execution_verified": False,
        },
    )
    if review_mode == "legacy":
        logic.automatic_review.ensure(record, report, plan=plan, aspects=("packages",))
    else:
        logic.automatic_review.prepare_pass(
            record, report, plan=plan, aspects=("packages",), pass_id="mapping"
        )
    assert logic.automatic_review.package_mappings(record, report) == {
        "unknown_lab": "research-provider"
    }
    assert (
        logic.automatic_review.aspect_status(record, report)["package:unknown_lab"]["attempts"] == 1
    )
    logic.automatic_review.skip(record, report)
    assert logic.automatic_review.package_mappings(record, report) == {
        "unknown_lab": "research-provider"
    }
    assert logic.get_decisions("alice", record.project_id)["decisions"] is None
    assert calls == ["repair"]


def test_unverified_package_does_not_change_effective_mapping(focused, monkeypatch):
    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})

    def mapped(runner, context, feature):
        item = context.facts["imports"][0]
        return response(
            context,
            [
                ProjectAdvisorySuggestion(
                    "package_hypothesis",
                    "Гипотеза пакета.",
                    (item["id"], context.files[0]["id"]),
                    module="unknown_lab",
                    package="research-provider",
                )
            ],
        )

    def rejected(*args, **kwargs):
        raise PackageVerificationError("package_does_not_provide_module")

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", mapped)
    monkeypatch.setattr(
        "nishtyak_service.automatic_review.PublishedPackageVerifier.verify", rejected
    )
    logic.automatic_review.ensure(record, report, plan=plan, aspects=("packages",))
    assert logic.automatic_review.package_mappings(record, report) == {}
    state = logic.automatic_review.aspect_status(record, report)["package:unknown_lab"]
    assert state["attempts"] == 2 and state["status"] == "exhausted"
    assert state["reason"] == "package_does_not_provide_module"


def test_missing_provider_does_not_fake_two_attempts(focused, monkeypatch):
    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    logic.automatic_review.ensure(record, report, plan=plan, aspects=("packages",))
    state = logic.automatic_review.aspect_status(record, report)["package:unknown_lab"]
    assert state["status"] == "unavailable" and state["attempts"] == 0


def test_provider_outage_fails_fast_after_one_real_attempt(focused, monkeypatch):
    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})
    calls = []

    def unavailable(runner, context, feature):
        calls.append(feature)
        return response(context, status="unavailable", reason="http_503")

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", unavailable)
    logic.automatic_review.ensure(record, report, plan=plan, aspects=("packages",))
    assert calls == ["repair"]
    assert (
        logic.automatic_review.aspect_status(record, report)["package:unknown_lab"]["attempts"] == 1
    )


def test_description_failure_uses_facts_without_a_human_question(focused, monkeypatch):
    logic, record, report, plan = focused({"experiment.py": "print('value')\n"})
    monkeypatch.setattr(
        "nishtyak_service.automatic_review.ProjectAdviceJobRunner.run",
        lambda runner, context, feature: response(context),
    )
    assert logic.automatic_review.needs_description(record, report, plan)
    logic.automatic_review.ensure(record, report, plan=plan, aspects=("description",))
    enriched = logic.automatic_review.enrich(plan, record)
    assert enriched.description and enriched.description_origin == "inferred"
    assert "experiment.py" in enriched.description
    assert enriched.decisions.description == ""
    assert (
        logic.automatic_review.aspect_status(record, report)["description"]["status"] == "exhausted"
    )


def test_existing_useful_readme_skips_description_model(focused):
    logic, record, report, plan = focused(
        {
            "experiment.py": "print('value')\n",
            "README.md": (
                "# Experiment\n\nThis project computes the arithmetic mean "
                "of the supplied numeric observations.\n"
            ),
        }
    )
    assert logic.automatic_review.needs_description(record, report, plan) is False
    enriched = logic.automatic_review.enrich(plan, record)
    assert "arithmetic mean" in enriched.description


def test_environment_diagnostics_do_not_silently_relax_constraints(focused, monkeypatch):
    logic, record, report, plan = focused(
        {"experiment.py": "import numpy\n", "requirements.txt": "numpy<2\nnumpy>=2\n"}
    )
    calls = []

    def diagnostic(runner, context, feature):
        calls.append(feature)
        return response(
            context,
            [
                ProjectAdvisorySuggestion(
                    "explanation",
                    "Ограничения numpy противоречат друг другу.",
                    (context.facts["declarations"][0]["id"],),
                )
            ],
        )

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", diagnostic)
    logic.automatic_review.ensure(record, report, plan=plan, aspects=("environment",))
    state = logic.automatic_review.aspect_status(record, report)["environment"]
    assert state["attempts"] == 2 and state["status"] == "exhausted"
    assert plan.requirements == ("numpy<2", "numpy>=2")
    assert calls == ["repair", "repair"]


def test_shared_budget_is_not_reset_by_another_aspect_or_a_later_pipeline_pass(
    focused, monkeypatch
):
    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})
    clock = [100.0]
    calls = []
    monkeypatch.setattr("nishtyak_service.automatic_review.time.monotonic", lambda: clock[0])

    def slow(runner, context, feature):
        calls.append(feature)
        clock[0] += runner.wall_time_seconds
        return response(context)

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", slow)
    logic.automatic_review.ensure(record, report, plan=plan, aspects=("packages",))
    assert len(calls) == 2
    clock[0] += 60
    logic.automatic_review.ensure(record, report, plan=plan, aspects=("description",))
    assert len(calls) == 2
    state = logic.automatic_review.aspect_status(record, report)["description"]
    assert state["attempts"] == 0 and state["reason"] == "review_deadline"
    logic.automatic_review.begin_cycle(record, report, "semantic-answer-2")
    logic.automatic_review.ensure(record, report, plan=plan, aspects=("description",))
    assert len(calls) == 4
    assert (
        logic.automatic_review.aspect_status(record, report)["package:unknown_lab"]["attempts"] == 2
    )
    logic.automatic_review.begin_cycle(record, report, "semantic-answer-2")
    logic.automatic_review.ensure(record, report, plan=plan, aspects=("packages", "description"))
    assert len(calls) == 4


def test_interrupted_last_attempt_cannot_be_replayed_after_restart(focused, monkeypatch):
    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})
    logic.repository.save_review(
        record,
        report.analysis_id,
        {
            "status": "running",
            "analysis_id": report.analysis_id,
            "policy": logic.automatic_review.policy,
            "targeted": True,
            "budget_used_ms": 9000,
            "budget_reserved_ms": 5000,
            "aspects": {
                "package:unknown_lab": {"status": "pending", "attempts": 2, "max_attempts": 2}
            },
            "paragraphs": [],
            "entrypoint": None,
            "packages": {},
        },
    )
    monkeypatch.setattr(
        "nishtyak_service.automatic_review.ProjectAdviceJobRunner.run",
        lambda *args: pytest.fail("replayed completed attempt budget"),
    )
    logic.automatic_review.ensure(record, report, plan=plan, aspects=("packages",))
    state = logic.automatic_review.aspect_status(record, report)["package:unknown_lab"]
    assert state["status"] == "exhausted" and state["attempts"] == 2


def test_targeted_review_starts_after_uv_regardless_of_analysis_age(focused, monkeypatch):
    from dataclasses import replace

    logic, record, report, plan = focused({"experiment.py": "print('value')\n"})
    old_record = replace(record, review_ready_at=1)
    calls = []

    def answered(runner, context, feature):
        calls.append(feature)
        return response(
            context,
            [
                ProjectAdvisorySuggestion(
                    "readme_description",
                    "Скрипт выводит значение в стандартный поток вывода.",
                    (context.files[0]["id"],),
                )
            ],
        )

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", answered)
    logic.automatic_review.ensure(old_record, report, plan=plan, aspects=("description",))
    assert calls == ["repair"]
    assert (
        logic.automatic_review.aspect_status(record, report)["description"]["status"] == "resolved"
    )


def test_project_pass_batches_modules_and_never_runs_more_than_two_pre_human(focused, monkeypatch):
    logic, record, report, plan = focused(
        {"experiment.py": "import unknown_lab\nimport other_lab\n"}
    )
    calls = []

    def unanswered(runner, context, feature):
        calls.append(context.facts["summary"]["assistant_focus"])
        return response(context)

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", unanswered)
    first = logic.automatic_review.prepare_pass(
        record, report, plan=plan, aspects=("packages", "description"), pass_id="pre-1"
    )
    assert first["attempted"] and first["retryable"]
    assert calls[0]["modules"] == ["other_lab", "unknown_lab"]
    assert set(calls[0]["aspects"]) == {"packages", "description"}
    second = logic.automatic_review.prepare_pass(
        record, report, plan=plan, aspects=("packages", "description"), pass_id="pre-2"
    )
    assert second["attempted"] and not second["retryable"]
    third = logic.automatic_review.prepare_pass(
        record, report, plan=plan, aspects=("environment",), pass_id="pre-3"
    )
    assert not third["attempted"]
    assert third["project_passes"] == {"pre_human": 2, "post_human": 0}
    assert len(calls) == 2


def test_project_pass_is_idempotent_across_new_review_instance(focused, monkeypatch):
    from nishtyak_service.automatic_review import AutomaticProjectReview

    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})
    calls = []

    def unanswered(runner, context, feature):
        calls.append(feature)
        return response(context)

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", unanswered)
    first = logic.automatic_review.prepare_pass(
        record, report, plan=plan, aspects=("packages",), pass_id="stable-id"
    )
    reloaded = AutomaticProjectReview(logic.repository)
    repeated = reloaded.prepare_pass(
        record, report, plan=plan, aspects=("packages",), pass_id="stable-id"
    )
    assert repeated == first
    assert calls == ["repair"]


def test_project_pass_reservation_prevents_replay_after_crash(focused, monkeypatch):
    import copy

    from nishtyak_service.automatic_review import AutomaticProjectReview

    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})
    reserved = []

    def interrupted(runner, context, feature):
        reserved.append(copy.deepcopy(logic.automatic_review._cached(record, report.analysis_id)))
        raise SystemExit("simulated process interruption")

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", interrupted)
    with pytest.raises(SystemExit):
        logic.automatic_review.prepare_pass(
            record, report, plan=plan, aspects=("packages",), pass_id="interrupted"
        )
    # A hard process kill would leave the pre-provider reservation, not the finally result.
    logic.repository.save_review(record, report.analysis_id, reserved[0])
    monkeypatch.setattr(
        "nishtyak_service.automatic_review.ProjectAdviceJobRunner.run",
        lambda *args: pytest.fail("reserved provider pass was replayed"),
    )
    outcome = AutomaticProjectReview(logic.repository).prepare_pass(
        record, report, plan=plan, aspects=("packages",), pass_id="interrupted"
    )
    assert outcome["attempted"] and outcome["reason"] == "review_interrupted"
    assert outcome["project_passes"]["pre_human"] == 1


def test_final_review_has_fresh_budget_once_after_two_exhausted_project_passes(
    focused, monkeypatch
):
    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})
    clock = [100.0]
    calls = []
    monkeypatch.setattr("nishtyak_service.automatic_review.time.monotonic", lambda: clock[0])

    def slow(runner, context, feature):
        calls.append((runner.wall_time_seconds, context.facts["summary"]["review_stage"]))
        clock[0] += runner.wall_time_seconds
        return response(context)

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", slow)
    for number in range(2):
        logic.automatic_review.prepare_pass(
            record, report, plan=plan, aspects=("packages",), pass_id=f"pre-{number}"
        )
    assert sum(budget for budget, _ in calls) == 15
    final = logic.automatic_review.finalize(
        record, report, plan=plan, aspects=("packages",), pass_id="final"
    )
    assert final["attempted"]
    assert final["project_passes"] == {"pre_human": 2, "post_human": 1}
    assert calls[-1] == (15, "post_human")
    repeated = logic.automatic_review.finalize(
        record, report, plan=plan, aspects=("packages",), pass_id="another-final"
    )
    assert repeated["attempted"] is False
    assert len(calls) == 3


def test_final_review_runs_consistency_when_author_answers_solved_all_aspects(focused, monkeypatch):
    from dataclasses import replace

    from nishtyak_core.models import RunSpec

    logic, record, report, plan = focused({"experiment.py": "print('value')\n"})
    decisions = UserDecisions(
        report.analysis_id,
        description="Авторское описание эксперимента.",
        run_spec=RunSpec("experiment.py"),
        python_version="3.12",
    )
    plan = replace(
        plan,
        decisions=decisions,
        run_spec=decisions.run_spec,
        python_version="3.12",
        description=decisions.description,
        description_origin="author",
        automatic_resolution={"status": "ready"},
    )
    logic.repository.save_automation(
        record,
        {"phase": "validating", "clarification_notes": "api_key=sk-hidden-key\nДанные измерены."},
    )
    contexts = []

    def consistent(runner, context, feature):
        contexts.append(context)
        return response(context)

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", consistent)
    outcome = logic.automatic_review.finalize(record, report, plan=plan, pass_id="author-final")
    assert outcome["attempted"]
    summary = contexts[0].facts["summary"]
    assert summary["assistant_focus"]["aspects"] == ["environment"]
    assert summary["saved_author_decisions"]["description"] == decisions.description
    assert summary["effective_run"]["target"] == "experiment.py"
    assert summary["effective_python"] == "3.12"
    assert "sk-hidden-key" not in json.dumps(summary)
    assert "Данные измерены." in summary["author_clarification_notes"]
    assert logic.automatic_review.enrich(plan, record).description == decisions.description
    assert (
        logic.automatic_review.aspect_status(record, report)["environment"]["status"] == "resolved"
    )


@pytest.mark.parametrize("stage", ["pre_human", "post_human"])
def test_project_review_unavailable_or_quota_does_not_consume_a_provider_pass(
    focused, monkeypatch, stage
):
    from nishtyak_service.errors import ServiceError

    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})

    def quota(*args):
        raise ServiceError(429, "RATE_LIMIT", "Test quota")

    monkeypatch.setattr(logic.repository, "consume_rate", quota)
    monkeypatch.setattr(
        "nishtyak_service.automatic_review.ProjectAdviceJobRunner.run",
        lambda *args: pytest.fail("provider called after exhausted quota"),
    )
    outcome = logic.automatic_review.assist_once(
        record, report, plan, ("packages",), stage=stage, pass_id="quota"
    )
    assert outcome["attempted"] is False and outcome["retryable"] is False
    assert outcome["reason"] == "review_quota"
    assert outcome["project_passes"] == {"pre_human": 0, "post_human": 0}
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    outcome = logic.automatic_review.assist_once(
        record, report, plan, ("packages",), stage=stage, pass_id="no-key"
    )
    assert outcome["attempted"] is False and outcome["reason"] == "advisor_unavailable"


def test_project_pass_provider_outage_is_not_retryable(focused, monkeypatch):
    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})
    monkeypatch.setattr(
        "nishtyak_service.automatic_review.ProjectAdviceJobRunner.run",
        lambda runner, context, feature: response(context, status="unavailable", reason="http_503"),
    )
    outcome = logic.automatic_review.prepare_pass(
        record, report, plan=plan, aspects=("packages",), pass_id="outage"
    )
    assert outcome["attempted"] is True and outcome["retryable"] is False
    assert outcome["project_passes"]["pre_human"] == 1


def test_explicit_same_source_reanalysis_keeps_facts_but_refreshes_final_author_review(
    focused, monkeypatch
):
    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})
    contexts = []

    def unanswered(runner, context, feature):
        contexts.append(context)
        return response(context)

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", unanswered)
    for number in range(2):
        logic.automatic_review.prepare_pass(
            record, report, plan=plan, aspects=("packages",), pass_id=f"pre-{number}"
        )
    logic.automatic_review.finalize(record, report, plan=plan, pass_id="final")
    cached = logic.automatic_review._cached(record, report.analysis_id)
    assert cached["project_passes"] == {"pre_human": 2, "post_human": 1}
    cached["packages"] = {
        "unknown_lab": {
            "package": "verified-provider",
            "verification": {"method": "published_wheel_contents"},
        }
    }
    cached["paragraphs"] = [
        {"text": "Сохранённое описание.", "source_paths": ["experiment.py"], "evidence_ids": []}
    ]
    logic.repository.save_review(record, report.analysis_id, cached)
    new_record = logic.repository.reanalyze("alice", record.project_id, AnalysisOptions())
    logic.repository.claim()
    logic.repository.finish(new_record.job_id, AnalysisJobResult("succeeded", report))
    new_record = logic.repository.get_project("alice", record.project_id)
    assert new_record.job_id != record.job_id
    logic.repository.save_automation(
        new_record, {"phase": "finalizing", "clarification_notes": "Новые ответы автора."}
    )
    final = logic.automatic_review.finalize(new_record, report, plan=plan, pass_id="final")
    assert final["attempted"] and final["project_passes"] == {"pre_human": 0, "post_human": 1}
    assert len(contexts) == 4
    assert contexts[-1].facts["summary"]["author_clarification_notes"] == "Новые ответы автора."
    refreshed = logic.automatic_review._cached(new_record, report.analysis_id)
    assert refreshed["project_job_id"] == new_record.job_id
    assert refreshed["paragraphs"] == cached["paragraphs"]
    assert logic.automatic_review.package_mappings(new_record, report) == {
        "unknown_lab": "verified-provider"
    }
    logic.automatic_review.finalize(new_record, report, plan=plan, pass_id="final")
    assert len(contexts) == 4


@pytest.mark.parametrize("reanalyze", [False, True])
def test_legacy_pass_counters_belong_to_the_database_job(focused, monkeypatch, reanalyze):
    logic, record, report, plan = focused({"experiment.py": "import unknown_lab\n"})
    logic.repository.save_review(
        record,
        report.analysis_id,
        {
            "status": "unavailable",
            "analysis_id": report.analysis_id,
            "policy": logic.automatic_review.policy,
            "budget_used_ms": 9000,
            "budget_reserved_ms": 5000,
            # Persisted metadata cannot override the table's authoritative job identity.
            "_source_job_id": "forged-job",
            "aspects": {
                "package:unknown_lab": {"status": "exhausted", "attempts": 2, "max_attempts": 2}
            },
        },
    )
    if reanalyze:
        new_record = logic.repository.reanalyze("alice", record.project_id, AnalysisOptions())
        logic.repository.claim()
        logic.repository.finish(new_record.job_id, AnalysisJobResult("succeeded", report))
        record = logic.repository.get_project("alice", record.project_id)
    contexts = []

    def unanswered(runner, context, feature):
        contexts.append(context)
        return response(context)

    monkeypatch.setattr("nishtyak_service.automatic_review.ProjectAdviceJobRunner.run", unanswered)
    outcome = logic.automatic_review.prepare_pass(
        record, report, plan=plan, aspects=("packages",), pass_id="legacy-upgrade"
    )
    assert outcome["attempted"] is reanalyze
    assert len(contexts) == int(reanalyze)
    assert outcome["project_passes"]["pre_human"] == (1 if reanalyze else 2)
    cached = logic.automatic_review._cached(record, report.analysis_id)
    assert cached["_source_job_id"] == record.job_id
    assert cached["project_job_id"] == record.job_id
