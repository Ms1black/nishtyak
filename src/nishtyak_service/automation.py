from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, replace

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from nishtyak_core.decisions import DecisionsCodec
from nishtyak_core.launches import LaunchDetector
from nishtyak_core.models import PackagePlan, UserDecisions, stable_id

from .automatic_resolution import AutomaticDependencyResolution
from .automatic_review import AutomaticProjectReview
from .errors import ServiceError
from .storage import JobRecord, SQLiteJobRepository


class PreparationAutomation:
    """Prepare twice, collect one questionnaire, then perform one final review."""

    maximum_attempts = 2
    active_phases = frozenset({"static", "assisting", "finalizing", "validating"})
    _internal_fields = frozenset({"input_id", "passes", "cycle", "clarification_notes"})

    def __init__(
        self,
        repository: SQLiteJobRepository,
        resolution: AutomaticDependencyResolution,
        review: AutomaticProjectReview,
    ):
        self.repository = repository
        self.resolution = resolution
        self.review = review

    def status(self, record: JobRecord) -> dict | None:
        state = self.repository.read_automation(record)
        return self._public(self._state(record, state)) if state is not None else None

    def enrich(self, plan: PackagePlan, record: JobRecord) -> PackagePlan:
        stored = self.repository.read_automation(record)
        if stored is None:
            return plan
        state = self._state(record, stored, plan)
        if state.get("input_id") not in {None, self._input_id(plan)}:
            state = {**state, "phase": "needs_input", "reason": "DECISIONS_NOT_SAVED"}
        return replace(plan, automation=self._public(state))

    def queue_if_changed(self, record: JobRecord, plan: PackagePlan) -> None:
        stored = self.repository.read_automation(record)
        if stored is None or stored.get("input_id") == self._input_id(plan):
            return
        state = self._state(record, stored, plan)
        state["input_id"] = self._input_id(plan)
        # Editing settings is not submission of the questionnaire and does not grant
        # another model budget. Completed workflows only repeat deterministic checks.
        if state["phase"] != "needs_input":
            state["phase"] = "static"
        self.repository.save_automation(record, state)

    def submission_state(
        self, record: JobRecord, plan: PackagePlan, questionnaire_id: str, notes: str = ""
    ) -> dict:
        """Return the state to persist atomically with the submitted decisions."""
        if not isinstance(notes, str) or len(notes) > 6000:
            raise ServiceError(
                422, "CLARIFICATIONS_INVALID", "Clarification notes exceed their limit"
            )
        stored = self.repository.read_automation(record)
        state = self._state(record, stored or {}, plan)
        if (
            state["phase"] != "needs_input"
            or state["human_round"] != 0
            or not questionnaire_id
            or questionnaire_id != state.get("questionnaire_id")
        ):
            raise ServiceError(
                409, "CLARIFICATIONS_STALE", "The preparation questionnaire is no longer current"
            )
        state.update(
            input_id=self._input_id(plan),
            phase="finalizing",
            stage="post_human",
            human_round=1,
            questions=[],
            reason=None,
            clarification_notes=notes,
        )
        state.pop("question", None)
        state.pop("aspect", None)
        return state

    def process_pending(self, prepare: Callable) -> None:
        for record in self.repository.pending_automation():
            try:
                with self.repository.operation(record.owner_id, record.project_id) as current:
                    stored = self.repository.read_automation(current)
                    if stored is None or stored.get("phase") not in self.active_phases:
                        continue
                    state = self._state(current, stored)
                    report = self.repository.read_report(current)
                    document = self.repository.read_decisions(current.owner_id, current.project_id)
                    decisions = (
                        DecisionsCodec().decode(document["decisions"])
                        if document["decisions"]
                        else UserDecisions(report.analysis_id)
                    )
                    self._prepare(current, prepare, report, decisions, state)
            except ServiceError:
                continue
            return

    def _prepare(self, record, prepare, report, decisions, state):
        plan = self._validate(record, prepare, report, decisions, state)
        if state["stage"] == "pre_human":
            plan = self._prepare_before_questions(record, prepare, report, decisions, plan, state)
            questions = self._questions(plan)
            if questions:
                state.update(
                    phase="needs_input",
                    questions=questions,
                    question=questions[0],
                    questionnaire_id=stable_id(record.job_id, [item["id"] for item in questions]),
                    aspect=questions[0]["aspect"],
                )
                self.repository.save_automation(record, state)
                return
        elif state["stage"] == "post_human":
            self._assist(record, report, plan, state, "post_human")
            plan = self._validate(record, prepare, report, decisions, state)
        self._finish(record, plan, state)

    def _prepare_before_questions(self, record, prepare, report, decisions, plan, state):
        if plan.state == "blocked":
            return plan
        while len(self._passes(state, "pre_human")) < self.maximum_attempts or any(
            item["status"] == "reserved" for item in self._passes(state, "pre_human")
        ):
            aspects = self._missing_aspects(record, plan)
            if not aspects:
                if any(item["status"] == "reserved" for item in self._passes(state, "pre_human")):
                    self._assist(record, report, plan, state, "pre_human", [])
                if not self._passes(state, "pre_human"):
                    self.review.skip(record, report, reason="static_sufficient")
                break
            outcome = self._assist(record, report, plan, state, "pre_human", aspects)
            plan = self._validate(record, prepare, report, decisions, state)
            if not outcome.get("attempted") or not outcome.get("retryable", True):
                break
            if plan.state == "blocked":
                break
        return plan

    def _assist(self, record, report, plan, state, stage, aspects=None):
        passes = self._passes(state, stage)
        maximum = self.maximum_attempts if stage == "pre_human" else 1
        pending = next((item for item in passes if item["status"] == "reserved"), None)
        if pending is None:
            if len(passes) >= maximum:
                return {"attempted": False, "reason": "attempt_budget_exhausted"}
            pending = {
                "id": stable_id(record.job_id, stage, len(passes) + 1),
                "stage": stage,
                "status": "reserved",
            }
            state["passes"].append(pending)
        state.update(phase="assisting" if stage == "pre_human" else "finalizing")
        self.repository.save_automation(record, state)
        review = self.review.prepare_pass if stage == "pre_human" else self.review.finalize
        outcome = review(
            record,
            report,
            plan=plan,
            aspects=tuple(aspects or self._missing_aspects(record, plan)),
            pass_id=pending["id"],
        )
        pending.update(status="complete", attempted=bool(outcome.get("attempted")))
        counts = outcome.get("project_passes", {})
        state["attempt"] = counts.get(
            "pre_human",
            sum(bool(item.get("attempted")) for item in self._passes(state, "pre_human")),
        )
        state["final_attempt"] = counts.get(
            "post_human",
            sum(bool(item.get("attempted")) for item in self._passes(state, "post_human")),
        )
        state["reason"] = outcome.get("reason")
        self.repository.save_automation(record, state)
        return outcome

    def _validate(self, record, prepare, report, decisions, state):
        state["phase"] = "validating"
        plan = prepare(report, decisions, record)
        state["input_id"] = self._input_id(plan)
        self.repository.save_automation(record, state)
        self.resolution.ensure(record, plan)
        return prepare(report, decisions, record)

    def _finish(self, record, plan, state):
        resolution = plan.automatic_resolution or {}
        unresolved = self._questions(plan)
        complete = (
            resolution.get("status") == "ready"
            and plan.state != "blocked"
            and plan.run_spec is not None
            and not unresolved
        )
        state.update(
            phase="ready" if complete else "draft",
            stage="complete",
            questions=[],
            reason=resolution.get("reason") or state.get("reason"),
            unresolved=unresolved,
        )
        state.pop("question", None)
        state.pop("aspect", None)
        self.repository.save_automation(record, state)

    def _missing_aspects(self, record: JobRecord, plan: PackagePlan) -> list[str]:
        if plan.state == "blocked":
            return []
        codes = {finding.code for finding in plan.findings}
        result = []
        if codes & {"DEPENDENCY_UNRESOLVED", "DECLARATION_IMPORT_UNRESOLVED"}:
            result.append("packages")
        if (
            "DEPENDENCY_CONFLICT" in codes
            or (plan.automatic_resolution or {}).get("reason") == "RESOLUTION_CONFLICT"
        ):
            result.append("environment")
        if plan.run_spec is None and LaunchDetector().detect(plan.report, plan.files).candidates:
            result.append("entrypoint")
        if self.review.needs_description(record, plan.report, plan):
            result.append("description")
        return result

    def _questions(self, plan: PackagePlan) -> list[dict]:
        result = self._source_questions(plan)
        result.extend(self._package_questions(plan))
        result.extend(self._constraint_questions(plan))
        launch = LaunchDetector().detect(plan.report, plan.files)
        if plan.run_spec is None and launch.candidates:
            result.append(
                self._question_document(
                    plan,
                    "entrypoint",
                    "entrypoint",
                    "entrypoint",
                    candidate_ids=[candidate.id for candidate in launch.candidates],
                    message="Выберите команду запуска проекта или оставьте её неопределённой.",
                )
            )
        unique = list({question["id"]: question for question in result}.values())
        return self._bounded_questions(plan, unique)

    def _bounded_questions(self, plan, questions):
        selected, overflow = [], []
        size = 0
        for question in questions:
            encoded = json.dumps(question, ensure_ascii=True, separators=(",", ":"))
            if len(selected) >= 11 or len(encoded) > 3000 or size + len(encoded) > 22 * 1024:
                overflow.append(question)
            else:
                selected.append(question)
                size += len(encoded)
        if overflow:
            counts = dict(Counter(item["aspect"] for item in overflow))
            selected.append(
                self._question_document(
                    plan,
                    "environment",
                    "text",
                    stable_id([item["id"] for item in overflow]),
                    message=f"Осталось дополнительных неопределённостей: {len(overflow)}. "
                    "Полный список сохранён в отчёте анализа. Добавьте известные сведения "
                    "в общее пояснение или оставьте эти пункты неопределёнными.",
                    unresolved_count=len(overflow),
                    unresolved_aspects=counts,
                    details_in_report=True,
                )
            )
        return selected

    def _source_questions(self, plan):
        questions = []
        for finding in plan.findings:
            if finding.code == "SECRET_DETECTED" and plan.state == "blocked":
                message = (
                    f"В файле {finding.path or 'проекта'} обнаружен секрет. "
                    "Исключите файл в дополнительных настройках или загрузите исправленный проект."
                )
            elif finding.code == "LOCAL_MODULE_MISSING" or (
                plan.state == "blocked" and finding.severity == "error"
            ):
                message = (
                    "Для запуска не хватает локальных файлов проекта. "
                    "Восстановите исключённые модули или загрузите полный проект."
                )
            else:
                continue
            questions.append(
                self._question_document(
                    plan,
                    "environment",
                    "text",
                    stable_id(finding.code, finding.path),
                    message=message,
                    code=finding.code,
                    file_path=finding.path,
                )
            )
        codes = {finding.code for finding in plan.findings}
        if "PROJECT_ROOT_AMBIGUOUS" in codes:
            questions.append(
                self._question_document(
                    plan,
                    "environment",
                    "text",
                    "project_root",
                    message="В архиве несколько независимых проектов. Выберите корень проекта "
                    "в дополнительных настройках или оставьте результат черновиком.",
                    code="PROJECT_ROOT_AMBIGUOUS",
                )
            )
        return questions

    def _package_questions(self, plan):
        unresolved = {
            evidence
            for finding in plan.findings
            if finding.code in {"DEPENDENCY_UNRESOLVED", "DECLARATION_IMPORT_UNRESOLVED"}
            for evidence in finding.evidence_ids
        }
        codes = {finding.code for finding in plan.findings}
        if not codes & {"DEPENDENCY_UNRESOLVED", "DECLARATION_IMPORT_UNRESOLVED"}:
            return []
        questions = []
        for candidate in plan.report.dependencies:
            if (
                candidate.category not in {"external", "unresolved"}
                or candidate.module.startswith(".")
                or candidate.module in plan.automatic_packages
                or (unresolved and not set(candidate.evidence_ids) & unresolved)
            ):
                continue
            questions.append(
                self._question_document(
                    plan,
                    "dependencies",
                    "package_mapping",
                    candidate.module,
                    module=candidate.module,
                    message=f"Не удалось подтвердить пакет для импорта {candidate.module}. "
                    "Укажите название устанавливаемого пакета, если знаете его.",
                )
            )
        return questions

    def _constraint_questions(self, plan):
        codes = {finding.code for finding in plan.findings}
        if (
            "DEPENDENCY_CONFLICT" not in codes
            and (plan.automatic_resolution or {}).get("reason") != "RESOLUTION_CONFLICT"
        ):
            return []
        grouped = {}
        for declaration in plan.report.declarations:
            action = (plan.decisions.declaration_actions if plan.decisions else {}).get(
                declaration.id
            )
            if action is not None and action.action == "exclude":
                continue
            try:
                requirement = Requirement(
                    action.requirement
                    if action is not None and action.action == "replace"
                    else declaration.requirement
                )
            except InvalidRequirement:
                continue
            if str(requirement) not in plan.requirements:
                continue
            key = (canonicalize_name(requirement.name), str(requirement.marker or ""))
            grouped.setdefault(key, []).append((declaration, requirement))
        questions = []
        for group in grouped.values():
            pins = {
                Version(spec.version)
                for _, requirement in group
                for spec in requirement.specifier
                if spec.operator == "==" and "*" not in spec.version
            }
            if not pins or not any(
                not requirement.specifier.contains(pin, prereleases=True)
                for _, requirement in group
                for pin in pins
            ):
                continue
            declaration = group[0][0]
            questions.append(
                self._question_document(
                    plan,
                    "dependencies",
                    "version_constraint",
                    declaration.id,
                    declaration_id=declaration.id,
                    message="Ограничения зависимостей противоречат друг другу. "
                    f"Укажите корректную замену для {declaration.requirement}, если знаете её.",
                )
            )
        return questions or [
            self._question_document(
                plan,
                "environment",
                "text",
                "resolution_conflict",
                message="Не удалось подобрать совместимый набор при исходных ограничениях. "
                "Уточните ограничения в дополнительных настройках, если можете.",
            )
        ]

    @staticmethod
    def _question_document(plan, aspect, kind, binding, **fields):
        return {
            "id": stable_id(plan.analysis_id, aspect, kind, binding),
            "aspect": aspect,
            "kind": kind,
            **fields,
        }

    @staticmethod
    def _passes(state, stage):
        return [item for item in state["passes"] if item["stage"] == stage]

    @staticmethod
    def _input_id(plan: PackagePlan) -> str:
        decisions = asdict(plan.decisions) if plan.decisions else {}
        for key in ("description", "input_description", "expected_output", "data_confirmed"):
            decisions.pop(key, None)
        return stable_id(plan.analysis_id, decisions)

    def _state(self, record, stored, plan=None):
        state = {
            "phase": "static",
            "stage": "pre_human",
            "human_round": 0,
            "attempt": 0,
            "final_attempt": 0,
            "max_attempts": self.maximum_attempts,
            "max_final_attempts": 1,
            "passes": [],
            **stored,
        }
        if state["phase"] in {"ready", "draft"}:
            state["stage"] = "complete"
        if "stage" not in stored and state["phase"] == "needs_input" and plan is not None:
            state["questions"] = self._questions(plan)
        if state.get("question") and not state.get("questions"):
            state["questions"] = [state["question"]]
        if state["phase"] == "needs_input" and state.get("questions"):
            state.setdefault(
                "questionnaire_id",
                stable_id(record.job_id, "clarifications"),
            )
        return state

    def _public(self, state):
        return {key: value for key, value in state.items() if key not in self._internal_fields}
