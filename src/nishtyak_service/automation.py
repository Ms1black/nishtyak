from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from nishtyak_core.decisions import DecisionsCodec
from nishtyak_core.launches import LaunchDetector
from nishtyak_core.models import PackagePlan, UserDecisions, stable_id

from .automatic_resolution import AutomaticDependencyResolution
from .automatic_review import AutomaticProjectReview
from .errors import ServiceError
from .storage import JobRecord, SQLiteJobRepository


class PreparationAutomation:
    """Validate static facts first, then request bounded assistance for missing aspects."""

    maximum_attempts = 2
    active_phases = frozenset({"static", "assisting", "validating"})

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
        return self._public(state) if state is not None else None

    def enrich(self, plan: PackagePlan, record: JobRecord) -> PackagePlan:
        state = self.repository.read_automation(record)
        if state is None:
            return plan
        if state.get("input_id") not in {None, self._input_id(plan)}:
            state = {
                "phase": "needs_input",
                "max_attempts": self.maximum_attempts,
                "reason": "DECISIONS_NOT_SAVED",
            }
        return replace(plan, automation=self._public(state))

    def queue_if_changed(self, record: JobRecord, plan: PackagePlan) -> None:
        state = self.repository.read_automation(record)
        if state is None or state.get("input_id") == self._input_id(plan):
            return
        self.repository.save_automation(
            record,
            {
                "phase": "static",
                "input_id": self._input_id(plan),
                "max_attempts": self.maximum_attempts,
                "cycle": state.get("cycle", 0) + 1,
            },
        )

    def process_pending(self, prepare: Callable) -> None:
        for record in self.repository.pending_automation():
            try:
                with self.repository.operation(record.owner_id, record.project_id) as current:
                    state = self.repository.read_automation(current)
                    if state is None or state.get("phase") not in self.active_phases:
                        continue
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

    def _prepare(self, record, prepare, report, decisions, previous):
        plan = prepare(report, decisions, record)
        state = {
            "input_id": self._input_id(plan),
            "max_attempts": self.maximum_attempts,
            "cycle": previous.get("cycle", 0),
        }
        assisted = False
        for _ in range(3):
            self._save(record, state, "validating", aspect="environment")
            self.resolution.ensure(record, plan)
            plan = prepare(report, decisions, record)
            if plan.state == "blocked":
                break
            self.review.begin_cycle(record, report, f"{record.job_id}:{previous.get('cycle', 0)}")
            aspects = self._missing_aspects(record, plan)
            if not aspects:
                break
            retryable = self._retryable_aspects(record, plan, aspects)
            if not retryable:
                break
            self._save(
                record, state, "assisting", aspect=self._public_aspect(retryable[0]), attempt=1
            )
            self.review.ensure(record, report, plan=plan, aspects=tuple(retryable))
            assisted = True
            plan = prepare(report, decisions, record)
        self._save(record, state, "validating", aspect="environment")
        self.resolution.ensure(record, plan)
        if not assisted and not self._missing_aspects(record, plan):
            self.review.skip(
                record,
                report,
                reason="source_safety_required" if plan.state == "blocked" else "static_sufficient",
            )
        plan = prepare(report, decisions, record)
        question = self._question(record, plan)
        if question is not None:
            statuses = self.review.aspect_status(record, report)
            key = (
                "package:" + question["module"]
                if question.get("module")
                else "environment"
                if question["kind"] == "version_constraint" or question["aspect"] == "environment"
                else "entrypoint"
            )
            attempted = statuses.get(key, {})
            self._save(
                record,
                state,
                "needs_input",
                aspect=question["aspect"],
                attempt=attempted.get("attempts", 0),
                reason=attempted.get("reason"),
                question=question,
            )
            return
        resolution = plan.automatic_resolution or {}
        phase = (
            "ready" if resolution.get("status") == "ready" and plan.state != "blocked" else "draft"
        )
        if plan.run_spec is None:
            phase = "draft"
        self._save(record, state, phase, reason=resolution.get("reason"))

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

    def _retryable_aspects(self, record, plan, aspects):
        statuses = self.review.aspect_status(record, plan.report)
        result = []
        for aspect in aspects:
            keys = [aspect]
            if aspect == "packages":
                evidence = {
                    key
                    for finding in plan.findings
                    if finding.code in {"DEPENDENCY_UNRESOLVED", "DECLARATION_IMPORT_UNRESOLVED"}
                    for key in finding.evidence_ids
                }
                keys = [
                    "package:" + candidate.module
                    for candidate in plan.report.dependencies
                    if candidate.category in {"external", "unresolved"}
                    and not candidate.module.startswith(".")
                    and (not evidence or set(candidate.evidence_ids) & evidence)
                ]
            if any(
                statuses.get(key, {}).get("status") not in {"resolved", "exhausted", "unavailable"}
                for key in keys
            ):
                result.append(aspect)
        return result

    def _question(self, record: JobRecord, plan: PackagePlan) -> dict | None:
        codes = {finding.code for finding in plan.findings}
        if plan.state == "blocked":
            finding = next(
                (
                    finding
                    for finding in plan.findings
                    if finding.code == "SECRET_DETECTED" or finding.severity == "error"
                ),
                None,
            )
            if finding is not None:
                message = (
                    f"В файле {finding.path or 'проекта'} обнаружен секрет. "
                    "Исключите файл из пакета в дополнительных настройках "
                    "или загрузите исправленный проект."
                    if finding.code == "SECRET_DETECTED"
                    else "Для запуска не хватает локальных файлов проекта. "
                    "Восстановите исключённые модули или загрузите полный проект."
                )
                return self._question_document(
                    plan, "environment", "text", finding.id, message=message
                )
        if codes & {"DEPENDENCY_UNRESOLVED", "DECLARATION_IMPORT_UNRESOLVED"}:
            unresolved_evidence = {
                evidence
                for finding in plan.findings
                if finding.code in {"DEPENDENCY_UNRESOLVED", "DECLARATION_IMPORT_UNRESOLVED"}
                for evidence in finding.evidence_ids
            }
            candidate = next(
                (
                    candidate
                    for candidate in plan.report.dependencies
                    if candidate.category in {"external", "unresolved"}
                    and not candidate.module.startswith(".")
                    and candidate.module not in plan.automatic_packages
                    and (
                        not unresolved_evidence or set(candidate.evidence_ids) & unresolved_evidence
                    )
                ),
                None,
            )
            if candidate is not None:
                return self._question_document(
                    plan,
                    "dependencies",
                    "package_mapping",
                    candidate.module,
                    module=candidate.module,
                    message=(
                        f"Не удалось подтвердить пакет для импорта {candidate.module}. "
                        "Укажите название устанавливаемого пакета."
                    ),
                )
        if (
            "DEPENDENCY_CONFLICT" in codes
            or (plan.automatic_resolution or {}).get("reason") == "RESOLUTION_CONFLICT"
        ):
            grouped = {}
            for declaration in plan.report.declarations:
                requirement = Requirement(declaration.requirement)
                grouped.setdefault(canonicalize_name(requirement.name), []).append(declaration)
            matching = next(
                (declarations for declarations in grouped.values() if len(declarations) > 1), None
            )
            if matching:
                declaration = matching[0]
                return self._question_document(
                    plan,
                    "dependencies",
                    "version_constraint",
                    declaration.id,
                    declaration_id=declaration.id,
                    message=(
                        "Ограничения зависимостей противоречат друг другу. "
                        f"Укажите корректную замену для {declaration.requirement}."
                    ),
                )
            return self._question_document(
                plan,
                "environment",
                "text",
                "resolution_conflict",
                message=(
                    "Не удалось подобрать совместимый набор при исходных ограничениях. "
                    "Проверьте зависимости в дополнительных настройках."
                ),
            )
        launch = LaunchDetector().detect(plan.report, plan.files)
        if plan.run_spec is None and launch.candidates:
            return self._question_document(
                plan,
                "entrypoint",
                "entrypoint",
                "entrypoint",
                candidate_ids=[candidate.id for candidate in launch.candidates],
                message=(
                    "Не удалось однозначно определить запуск проекта. Выберите подходящую команду."
                ),
            )
        return None

    @staticmethod
    def _question_document(plan, aspect, kind, binding, **fields):
        return {
            "id": stable_id(plan.analysis_id, aspect, kind, binding),
            "aspect": aspect,
            "kind": kind,
            **fields,
        }

    def _save(self, record, state, phase, **fields):
        self.repository.save_automation(record, {**state, "phase": phase, **fields})

    @staticmethod
    def _public_aspect(aspect):
        return "dependencies" if aspect == "packages" else aspect

    @staticmethod
    def _input_id(plan: PackagePlan) -> str:
        decisions = asdict(plan.decisions) if plan.decisions else {}
        for key in ("description", "input_description", "expected_output", "data_confirmed"):
            decisions.pop(key, None)
        return stable_id(plan.analysis_id, decisions)

    @staticmethod
    def _public(state):
        return {key: value for key, value in state.items() if key not in {"input_id", "cycle"}}
