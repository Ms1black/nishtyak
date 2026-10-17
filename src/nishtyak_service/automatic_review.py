from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import asdict, replace
from pathlib import PurePosixPath

from nishtyak_core import ArchiveReader
from nishtyak_core.advisors import (
    DeepSeekClient,
    DeepSeekConfig,
    ProjectAdvisoryService,
    SafeProjectContext,
)
from nishtyak_core.advisors.client import InvalidAdvisorResponse
from nishtyak_core.advisors.package_verification import (
    PackageVerificationError,
    PublishedPackageVerifier,
)
from nishtyak_core.models import AnalysisReport, PackagePlan
from nishtyak_core.redaction import TextRedactor

from .errors import ServiceError
from .project_advice_jobs import ProjectAdviceJobRunner
from .storage import JobRecord, SQLiteJobRepository


class AutomaticProjectReview:
    """Cache descriptions and candidate choices without changing author decisions.

    The API process owns network access. The offline analysis dispatcher never
    receives provider credentials. Call ensure only inside a project operation.
    """

    budget_seconds = 15
    policy = "source_description_and_entrypoint"

    def __init__(self, repository: SQLiteJobRepository):
        self.repository = repository
        self._running: set[str] = set()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.repository.settings.automatic_review and bool(
            os.environ.get("DEEPSEEK_API_KEY") and os.environ.get("DEEPSEEK_MODEL")
        )

    def capabilities(self) -> dict:
        return {
            "enabled": self.enabled,
            "provider": "deepseek",
            "budget_seconds": self.budget_seconds,
            "features": ["description", "entrypoint", "packages", "environment"]
            if self.repository.settings.automatic_resolution
            else ["description", "entrypoint"],
        }

    def status(self, record: JobRecord, report: AnalysisReport) -> dict:
        if not record.automatic_review_eligible:
            return {"status": "disabled", "provider": "deepseek", "applied": False}
        with self._lock:
            running = record.project_id in self._running
        if not running:
            cached = self._cached(record, report.analysis_id)
            if cached is not None:
                return self.summary(cached)
            if self._remaining(record) <= 0:
                return {
                    "status": "skipped",
                    "provider": "deepseek",
                    "applied": False,
                    "reason": "review_deadline",
                }
        return {
            "status": "running" if running else "pending" if self.enabled else "disabled",
            "provider": "deepseek",
            "applied": False,
        }

    def process_pending(self) -> None:
        if not self.enabled:
            return
        for record in self.repository.pending_reviews():
            claimed = False
            try:
                with self.repository.operation(
                    record.owner_id, record.project_id, lease_seconds=20
                ) as current:
                    with self._lock:
                        self._running.add(record.project_id)
                    claimed = True
                    self.ensure(current, self.repository.read_report(current))
            except ServiceError:
                # Another operation, a deletion or an expired project takes precedence.
                continue
            finally:
                if claimed:
                    with self._lock:
                        self._running.discard(record.project_id)
            # One bounded operation per tick keeps shutdown and other work responsive.
            return

    def ensure(
        self,
        record: JobRecord,
        report: AnalysisReport,
        *,
        plan: PackagePlan | None = None,
        aspects: tuple[str, ...] | None = None,
    ) -> None:
        if plan is not None and aspects is not None:
            self._ensure_aspects(record, report, plan, aspects)
            return
        self._ensure_legacy(record, report)

    def _ensure_legacy(self, record: JobRecord, report: AnalysisReport) -> None:
        if not self.enabled or not record.automatic_review_eligible:
            return
        cached = self._cached(record, report.analysis_id)
        if cached is not None:
            # Identical source/options may have been analyzed again. Reuse its review.
            self.repository.save_review(record, report.analysis_id, cached)
            return
        started = time.monotonic()
        result = {"status": "unavailable", "paragraphs": [], "reason": "review_unavailable"}
        try:
            if self._remaining(record) <= 0:
                result.update(status="skipped", reason="review_deadline")
                return
            self.repository.consume_rate(record.owner_id, "advice-daily", 10, 86400)
            self.repository.consume_rate("_service", "advice-global-daily", 100, 86400)
            snapshot = ArchiveReader().from_path(self.repository.archive_path(record.project_id))
            context = SafeProjectContext.from_project(report, snapshot)
            remaining = min(
                self._remaining(record), self.budget_seconds - (time.monotonic() - started)
            )
            if remaining <= 0:
                return
            response = ProjectAdviceJobRunner(wall_time_seconds=remaining).run(context, "review")
            ProjectAdvisoryService(DeepSeekClient(DeepSeekConfig.from_env())).validate_result(
                response, context, "review"
            )
            paragraphs = self._paragraphs(response, context)
            entrypoint = self._entrypoint(response, context)
            result = {
                "status": "ready" if paragraphs or entrypoint else "unavailable",
                "paragraphs": paragraphs,
                "entrypoint": entrypoint,
                "reason": None
                if paragraphs or entrypoint
                else response.get("reason") or "no_grounded_review",
                "usage": response.get("usage"),
                "rounds": response.get("rounds", 0),
                "tool_calls": response.get("tool_calls", 0),
            }
        except ServiceError as error:
            result["status"] = "skipped"
            result["reason"] = "review_quota" if error.status == 429 else "review_unavailable"
        except Exception:
            # Provider, context and validation failures cannot invalidate static analysis.
            pass
        finally:
            result.update(
                {
                    "analysis_id": report.analysis_id,
                    "provider": "deepseek",
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "policy": self.policy,
                }
            )
            self.repository.save_review(record, report.analysis_id, result)

    def skip(
        self, record: JobRecord, report: AnalysisReport, reason: str = "static_sufficient"
    ) -> None:
        if self._cached(record, report.analysis_id) is None:
            self.repository.save_review(
                record,
                report.analysis_id,
                {
                    "status": "skipped",
                    "provider": "deepseek",
                    "analysis_id": report.analysis_id,
                    "policy": self.policy,
                    "targeted": True,
                    "paragraphs": [],
                    "entrypoint": None,
                    "packages": {},
                    "aspects": {},
                    "budget_used_ms": 0,
                    "elapsed_ms": 0,
                    "reason": reason,
                },
            )

    def begin_cycle(self, record: JobRecord, report: AnalysisReport, cycle_id: str) -> None:
        """A semantic human answer grants a fresh budget, preserving previous facts and limits."""
        if not isinstance(cycle_id, str) or not 1 <= len(cycle_id) <= 128:
            raise ValueError("Invalid assistance cycle")
        cached = self._cached(record, report.analysis_id)
        if cached is None:
            self.skip(record, report)
            cached = self._cached(record, report.analysis_id)
        if cached.get("assistance_cycle") == cycle_id:
            return
        cached.update(assistance_cycle=cycle_id, budget_used_ms=0)
        cached.pop("budget_reserved_ms", None)
        for state in cached.get("aspects", {}).values():
            if state.get("reason") == "review_deadline" and state.get("attempts", 0) < 2:
                state.update(status="pending", reason=None)
        self.repository.save_review(record, report.analysis_id, cached)

    def aspect_status(self, record: JobRecord, report: AnalysisReport) -> dict:
        cached = self._cached(record, report.analysis_id) or {}
        return cached.get("aspects", {})

    def package_mappings(self, record: JobRecord, report: AnalysisReport) -> dict[str, str]:
        cached = self._cached(record, report.analysis_id) or {}
        return {
            module: value["package"]
            for module, value in cached.get("packages", {}).items()
            if value.get("verification", {}).get("method") == "published_wheel_contents"
            or (
                value.get("origin") == "provider_discovery"
                and value.get("verification", {}).get("method")
                in {"published_core_metadata", "published_wheel_directory"}
            )
        }

    def needs_description(
        self, record: JobRecord, report: AnalysisReport, plan: PackagePlan
    ) -> bool:
        if plan.decisions is not None and plan.decisions.description.strip():
            return False
        cached = self._cached(record, report.analysis_id) or {}
        if cached.get("paragraphs"):
            return False
        try:
            return not self._readme_prose(self._context(record, report, plan))
        except (ValueError, OSError):
            return True

    def prepare_pass(
        self,
        record: JobRecord,
        report: AnalysisReport,
        *,
        plan: PackagePlan,
        aspects: tuple[str, ...],
        pass_id: str,
    ) -> dict:
        return self.assist_once(record, report, plan, aspects, stage="pre_human", pass_id=pass_id)

    def finalize(
        self,
        record: JobRecord,
        report: AnalysisReport,
        *,
        plan: PackagePlan,
        aspects: tuple[str, ...] = (),
        pass_id: str,
    ) -> dict:
        return self.assist_once(record, report, plan, aspects, stage="post_human", pass_id=pass_id)

    def assist_once(
        self,
        record: JobRecord,
        report: AnalysisReport,
        plan: PackagePlan,
        aspects: tuple[str, ...],
        *,
        stage: str,
        pass_id: str,
    ) -> dict:
        """One idempotent project-wide review; caller holds the project operation lease."""
        if stage not in {"pre_human", "post_human"}:
            raise ValueError("Invalid project review stage")
        if not isinstance(pass_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", pass_id) is None:
            raise ValueError("Invalid project review identity")
        if set(aspects) - {"entrypoint", "packages", "description", "environment"}:
            raise ValueError("Unsupported automatic advisory aspect")
        result = self._project_review(record, report)
        previous = result["project_pass_results"].get(pass_id)
        if previous is not None:
            return self._pass_outcome(result, previous)
        outcome = {
            "stage": stage,
            "attempted": False,
            "status": "skipped",
            "reason": None,
            "retryable": False,
        }
        maximum = 2 if stage == "pre_human" else 1
        started = time.monotonic()
        budget_before = result["project_budgets_ms"][stage]
        result["budget_used_ms"] = budget_before
        requested = self._requested_aspects(plan, aspects, module_limit=200)
        if stage == "post_human":
            # This final consistency check is useful even when the human fixed every gap.
            requested = list(dict.fromkeys((*requested, "environment")))
            if not (plan.decisions and plan.decisions.description.strip()):
                requested = list(dict.fromkeys((*requested, "description")))
        for key in requested:
            state = result["aspects"].setdefault(
                key, {"status": "pending", "attempts": 0, "max_attempts": 2}
            )
            if stage == "post_human":
                state["max_attempts"] = 3
            if state["status"] != "resolved":
                state.update(status="pending", reason=None)
        try:
            if result["project_passes"][stage] >= maximum:
                outcome["reason"] = "project_attempt_budget_exhausted"
                return self._pass_outcome(result, outcome)
            if not self.enabled or not record.automatic_review_eligible:
                outcome["reason"] = "advisor_unavailable"
                return self._pass_outcome(result, outcome)
            remaining = self.budget_seconds - budget_before / 1000
            if remaining <= 0.2:
                outcome["reason"] = "review_deadline"
                return self._pass_outcome(result, outcome)
            context = self._pass_context(record, report, plan, requested, result, stage)
            if not requested:
                outcome["reason"] = "static_sufficient"
                return self._pass_outcome(result, outcome)
            remaining -= time.monotonic() - started
            if remaining <= 0.2:
                outcome["reason"] = "review_deadline"
                return self._pass_outcome(result, outcome)
            self.repository.consume_rate(record.owner_id, "advice-daily", 10, 86400)
            self.repository.consume_rate("_service", "advice-global-daily", 100, 86400)
            passes_left = maximum - result["project_passes"][stage]
            attempt_budget = remaining / passes_left
            result["project_passes"][stage] += 1
            for key in requested:
                result["aspects"][key]["attempts"] += 1
            outcome.update(attempted=True, status="unavailable", reason="review_interrupted")
            # Reserve before invoking the provider. A restarted coordinator cannot replay it.
            result["project_pass_results"][pass_id] = dict(outcome)
            result["project_budgets_ms"][stage] = min(
                15000, budget_before + round(attempt_budget * 1000)
            )
            self.repository.save_review(record, report.analysis_id, result)
            response = ProjectAdviceJobRunner(wall_time_seconds=attempt_budget).run(
                context, "repair"
            )
            ProjectAdvisoryService(DeepSeekClient(DeepSeekConfig.from_env())).validate_result(
                response, context, "repair"
            )
            self._apply_answers(result, response, context, requested, started)
            result["rounds"] = result.get("rounds", 0) + response.get("rounds", 0)
            result["tool_calls"] = result.get("tool_calls", 0) + response.get("tool_calls", 0)
            unresolved = [
                key for key in requested if result["aspects"][key]["status"] != "resolved"
            ]
            outcome.update(
                status="ready" if response["status"] == "ok" else "unavailable",
                reason=response.get("reason") or ("aspects_unresolved" if unresolved else None),
                retryable=response["status"] != "unavailable"
                and bool(unresolved)
                and result["project_passes"][stage] < maximum,
            )
        except ServiceError as error:
            outcome.update(
                status="unavailable",
                reason="review_quota" if error.status == 429 else "review_unavailable",
            )
        except Exception:
            outcome.update(
                status="unavailable",
                reason="invalid_or_unavailable_answer",
                retryable=outcome["attempted"] and result["project_passes"][stage] < maximum,
            )
        finally:
            for key in requested:
                state = result["aspects"][key]
                if state["status"] != "resolved":
                    state.update(
                        status="exhausted"
                        if result["project_passes"][stage] >= maximum
                        else "unavailable"
                        if outcome["status"] == "unavailable" or not outcome["attempted"]
                        else "pending",
                        reason=state.get("reason") or outcome["reason"] or "no_grounded_answer",
                    )
            result["project_budgets_ms"][stage] = min(
                15000, budget_before + round((time.monotonic() - started) * 1000)
            )
            result["budget_used_ms"] = result["project_budgets_ms"][stage]
            result["elapsed_ms"] = sum(result["project_budgets_ms"].values())
            result["status"] = (
                "ready"
                if result["paragraphs"] or result["entrypoint"] or result["packages"]
                else outcome["status"]
            )
            result["reason"] = outcome["reason"]
            result["project_pass_results"][pass_id] = dict(outcome)
            self.repository.save_review(record, report.analysis_id, result)
        return self._pass_outcome(result, outcome)

    def _project_review(self, record: JobRecord, report: AnalysisReport) -> dict:
        cached = self._cached(record, report.analysis_id) or {}
        result = {
            "status": "unavailable",
            "provider": "deepseek",
            "analysis_id": report.analysis_id,
            "policy": self.policy,
            "targeted": True,
            "paragraphs": [],
            "entrypoint": None,
            "packages": {},
            "aspects": {},
            **cached,
        }
        source_job = cached.get("_source_job_id", cached.get("project_job_id"))
        if source_job != record.job_id:
            # Identical source may reuse grounded facts after explicit reanalysis, but
            # its new questionnaire and final author answers own a fresh review budget.
            result.update(
                project_job_id=record.job_id,
                project_passes={"pre_human": 0, "post_human": 0},
                project_budgets_ms={"pre_human": 0, "post_human": 0},
                project_pass_results={},
                budget_used_ms=0,
                elapsed_ms=0,
                rounds=0,
                tool_calls=0,
                usage=None,
            )
            result.pop("budget_reserved_ms", None)
            for state in result["aspects"].values():
                state.update(attempts=0, max_attempts=2)
                if state.get("status") != "resolved":
                    state.update(status="pending", reason=None)
        result["project_job_id"] = record.job_id
        legacy_attempts = max(
            (state.get("attempts", 0) for state in cached.get("aspects", {}).values()), default=0
        )
        result.setdefault("project_passes", {"pre_human": min(2, legacy_attempts), "post_human": 0})
        result.setdefault(
            "project_budgets_ms",
            {
                "pre_human": min(
                    15000,
                    cached.get("budget_used_ms", 0) + cached.get("budget_reserved_ms", 0),
                ),
                "post_human": 0,
            },
        )
        result.setdefault("project_pass_results", {})
        return result

    @staticmethod
    def _pass_outcome(result: dict, outcome: dict) -> dict:
        return {**outcome, "project_passes": dict(result["project_passes"])}

    def _pass_context(self, record, report, plan, requested, result, stage):
        from nishtyak_core.advisors.project_context import ProjectTextFilter

        context = self._context(record, report, plan)
        groups = tuple(
            dict.fromkeys("packages" if key.startswith("package:") else key for key in requested)
        )
        modules = tuple(key.split(":", 1)[1] for key in requested if key.startswith("package:"))
        feedback = {
            key: result["aspects"][key].get("reason") or "not_resolved" for key in requested
        }
        document = context.focused(groups, modules, feedback).to_dict()
        summary = document["facts"]["summary"]
        summary["review_stage"] = stage
        summary["effective_requirements"] = list(plan.requirements[:100])
        summary["effective_python"] = plan.python_version
        summary["effective_run"] = asdict(plan.run_spec) if plan.run_spec else None
        summary["effective_environment_status"] = (plan.automatic_resolution or {}).get("status")
        if stage == "post_human":
            decisions = asdict(plan.decisions) if plan.decisions else {}
            # Validated choices are evidence, never instructions or authority to execute.
            summary["saved_author_decisions"] = self._bounded_decisions(decisions)
            flow = self.repository.read_automation(record) or {}
            notes = flow.get("internal_clarification_notes", flow.get("clarification_notes", ""))
            if isinstance(notes, str):
                summary["author_clarification_notes"] = notes[:6000]
        document = ProjectTextFilter().redact_value(document)
        SafeProjectContext._fit_document(document)
        return SafeProjectContext.from_dict(document)

    @staticmethod
    def _bounded_decisions(decisions: dict) -> dict:
        values = {}
        for key, value in decisions.items():
            if isinstance(value, str):
                values[key] = value[:1000]
            elif isinstance(value, dict):
                values[key] = {
                    str(name)[:200]: {
                        str(field)[:100]: str(item)[:300] if item is not None else None
                        for field, item in content.items()
                    }
                    if isinstance(content, dict)
                    else str(content)[:300]
                    for name, content in list(value.items())[:30]
                }
            elif isinstance(value, (list, tuple)):
                values[key] = [str(item)[:300] for item in value[:30]]
            elif isinstance(value, bool) or value is None:
                values[key] = value
        return values

    def _ensure_aspects(
        self, record: JobRecord, report: AnalysisReport, plan: PackagePlan, aspects: tuple[str, ...]
    ) -> None:
        if set(aspects) - {"entrypoint", "packages", "description", "environment"}:
            raise ValueError("Unsupported automatic advisory aspect")
        cached = self._cached(record, report.analysis_id) or {}
        result = {
            "status": "unavailable",
            "provider": "deepseek",
            "analysis_id": report.analysis_id,
            "policy": self.policy,
            "targeted": True,
            "paragraphs": [],
            "entrypoint": None,
            "packages": {},
            "aspects": {},
            "budget_used_ms": 0,
            "elapsed_ms": 0,
            **cached,
        }
        if result.pop("budget_reserved_ms", 0):
            result["budget_used_ms"] = min(
                15000, result["budget_used_ms"] + cached["budget_reserved_ms"]
            )
        requested = self._requested_aspects(plan, aspects)
        for key in requested:
            result["aspects"].setdefault(
                key, {"status": "pending", "attempts": 0, "max_attempts": 2}
            )
            state = result["aspects"][key]
            if state["status"] == "pending" and state["attempts"] >= 2:
                state.update(status="exhausted", reason="attempt_budget_exhausted")
        started = time.monotonic()
        try:
            if not self.enabled or not record.automatic_review_eligible:
                for key in requested:
                    if result["aspects"][key]["status"] != "resolved":
                        result["aspects"][key].update(
                            status="unavailable", reason="advisor_unavailable"
                        )
                result["reason"] = "advisor_unavailable"
                return
            context = self._context(record, report, plan)
            if "entrypoint" in requested and not context.launches:
                result["aspects"]["entrypoint"].update(
                    status="unavailable", reason="no_launch_candidate"
                )
            while True:
                pending = [
                    key for key in requested if result["aspects"][key]["status"] == "pending"
                ]
                remaining = (
                    self.budget_seconds
                    - result["budget_used_ms"] / 1000
                    - (time.monotonic() - started)
                )
                if not pending or remaining <= 0.2:
                    for key in pending:
                        result["aspects"][key].update(
                            status="unavailable", reason="review_deadline"
                        )
                    break
                groups = tuple(
                    dict.fromkeys(
                        "packages" if key.startswith("package:") else key for key in pending
                    )
                )
                modules = tuple(
                    key.split(":", 1)[1] for key in pending if key.startswith("package:")
                )
                feedback = {
                    key: result["aspects"][key].get("reason", "not_resolved") for key in pending
                }
                focused = context.focused(groups, modules, feedback)
                attempts_left = max(
                    1, 2 - max(result["aspects"][key]["attempts"] for key in pending)
                )
                attempt_budget = min(remaining, max(0.2, remaining / attempts_left))
                response = None
                failure = "no_grounded_answer"
                try:
                    self.repository.consume_rate(record.owner_id, "advice-daily", 10, 86400)
                    self.repository.consume_rate("_service", "advice-global-daily", 100, 86400)
                    for key in pending:
                        result["aspects"][key]["attempts"] += 1
                    self.repository.save_review(
                        record,
                        report.analysis_id,
                        {
                            **result,
                            "status": "running",
                            "budget_used_ms": result["budget_used_ms"]
                            + round((time.monotonic() - started) * 1000),
                            "budget_reserved_ms": round(attempt_budget * 1000),
                        },
                    )
                    response = ProjectAdviceJobRunner(wall_time_seconds=attempt_budget).run(
                        focused, "repair"
                    )
                    ProjectAdvisoryService(
                        DeepSeekClient(DeepSeekConfig.from_env())
                    ).validate_result(response, focused, "repair")
                    failure = response.get("reason") or failure
                    if response["status"] == "unavailable":
                        for key in pending:
                            result["aspects"][key].update(status="unavailable", reason=failure)
                    self._apply_answers(result, response, focused, pending, started)
                    result["rounds"] = result.get("rounds", 0) + response.get("rounds", 0)
                    result["tool_calls"] = result.get("tool_calls", 0) + response.get(
                        "tool_calls", 0
                    )
                except ServiceError as error:
                    failure = "review_quota" if error.status == 429 else "review_unavailable"
                    for key in pending:
                        result["aspects"][key].update(status="unavailable", reason=failure)
                except Exception:
                    failure = "invalid_or_unavailable_answer"
                for key in pending:
                    state = result["aspects"][key]
                    if state["status"] == "resolved":
                        continue
                    state["reason"] = state.get("reason") or failure
                    if state["attempts"] >= 2:
                        state["status"] = "exhausted"
                result["elapsed_ms"] = result["budget_used_ms"] + round(
                    (time.monotonic() - started) * 1000
                )
                self.repository.save_review(record, report.analysis_id, result)
            result["status"] = (
                "ready"
                if result["paragraphs"] or result["entrypoint"] or result["packages"]
                else "unavailable"
            )
            result["reason"] = None if result["status"] == "ready" else "aspects_unresolved"
        finally:
            result["budget_used_ms"] = min(
                15000, result["budget_used_ms"] + round((time.monotonic() - started) * 1000)
            )
            result["elapsed_ms"] = result["budget_used_ms"]
            self.repository.save_review(record, report.analysis_id, result)

    @staticmethod
    def _requested_aspects(
        plan: PackagePlan, aspects: tuple[str, ...], *, module_limit: int = 20
    ) -> list[str]:
        requested = []
        if "entrypoint" in aspects and plan.run_spec is None:
            requested.append("entrypoint")
        if "environment" in aspects:
            requested.append("environment")
        if "description" in aspects and not (plan.decisions and plan.decisions.description.strip()):
            requested.append("description")
        if "packages" in aspects:
            evidence = {
                value
                for finding in plan.findings
                if finding.code in {"DEPENDENCY_UNRESOLVED", "DECLARATION_IMPORT_UNRESOLVED"}
                for value in finding.evidence_ids
            }
            modules = {
                candidate.module
                for candidate in plan.report.dependencies
                if candidate.category in {"external", "unresolved"}
                and candidate.module.isidentifier()
                and set(candidate.evidence_ids).intersection(evidence)
            }
            requested.extend("package:" + module for module in sorted(modules)[:module_limit])
        return requested

    def _apply_answers(self, result, response, context, pending, started) -> None:
        if response["status"] != "ok":
            return
        if "environment" in pending:
            explanations = [
                item["text"] for item in response["suggestions"] if item["kind"] == "explanation"
            ]
            if explanations:
                result["aspects"]["environment"]["explanation"] = "\n".join(explanations)[:2000]
            final_review = context.facts["summary"].get("review_stage") == "post_human"
            already_resolved = (
                context.facts["summary"].get("effective_environment_status") == "ready"
            )
            result["aspects"]["environment"].update(
                status="resolved" if final_review and already_resolved else "pending",
                reason=None
                if final_review and already_resolved
                else "environment_unresolved"
                if final_review
                else "authored_constraints_require_confirmation",
            )
        if "description" in pending:
            paragraphs = self._paragraphs(response, context)
            if paragraphs:
                result["paragraphs"] = paragraphs
                result["aspects"]["description"].update(status="resolved", reason=None)
        if "entrypoint" in pending:
            entrypoint = self._entrypoint(response, context)
            if entrypoint:
                result["entrypoint"] = entrypoint
                result["aspects"]["entrypoint"].update(status="resolved", reason=None)
        for key in pending:
            if not key.startswith("package:"):
                continue
            module = key.split(":", 1)[1]
            suggestions = [
                item
                for item in response["suggestions"]
                if item["kind"] == "package_hypothesis" and item.get("module") == module
            ]
            names = {item.get("package") for item in suggestions}
            if len(names) != 1:
                continue
            remaining = (
                self.budget_seconds - result["budget_used_ms"] / 1000 - (time.monotonic() - started)
            )
            if remaining <= 0.1:
                result["aspects"][key]["reason"] = "review_deadline"
                continue
            try:
                verification = PublishedPackageVerifier().verify(
                    module, next(iter(names)), timeout=min(4, remaining)
                )
            except PackageVerificationError as error:
                result["aspects"][key]["reason"] = str(error)
                continue
            result["packages"][module] = {
                "package": verification["package"],
                "verification": verification,
                "evidence_ids": suggestions[0]["evidence_ids"],
            }
            result["aspects"][key].update(status="resolved", reason=None)

    def _context(self, record, report, plan):
        from nishtyak_core.launches import LaunchDetector

        snapshot = ArchiveReader().from_path(self.repository.archive_path(record.project_id))
        context = SafeProjectContext.from_project(report, snapshot)
        retained = {file.path for file in plan.files}
        document = context.to_dict()
        document["files"] = [file for file in document["files"] if file["path"] in retained]
        candidates = {item.id for item in LaunchDetector().detect(report, plan.files).candidates}
        document["launches"] = [item for item in document["launches"] if item["id"] in candidates]
        for key in ("imports", "findings", "declarations", "code_map", "local_imports"):
            document["facts"][key] = [
                item
                for item in document["facts"].get(key, [])
                if item.get("path") is None or item["path"] in retained
            ]
        document["facts"]["local_imports"] = [
            item
            for item in document["facts"].get("local_imports", [])
            if set(item.get("targets", [])).issubset(retained)
        ]
        return SafeProjectContext.from_dict(document)

    @staticmethod
    def _readme_prose(context):
        for file in context.files:
            if PurePosixPath(file["path"]).stem.lower() != "readme" or not file.get("text"):
                continue
            paragraphs = []
            for paragraph in re.split(r"\n\s*\n", file["text"]):
                text = " ".join(
                    line.strip()
                    for line in paragraph.splitlines()
                    if not line.lstrip().startswith(("#", "```", "~~~", "|", "-", "*"))
                )
                if len(text) < 40 or re.search(
                    r"\b(?:todo|placeholder|coming soon)\b|заполните|описание проекта здесь",
                    text,
                    re.I,
                ):
                    continue
                try:
                    ProjectAdvisoryService._validate_readme_prose(text)
                except (ValueError, InvalidAdvisorResponse):
                    continue
                paragraphs.append(text[:1000])
                if len(paragraphs) == 2:
                    return "\n\n".join(paragraphs)
            if paragraphs:
                return "\n\n".join(paragraphs)
        return ""

    def _static_description(self, plan, record):
        try:
            context = self._context(record, plan.report, plan)
            readme = self._readme_prose(context)
            if re.search(r"[А-Яа-яЁё]", readme):
                return readme
            reads = {
                value
                for file in context.facts.get("code_map", [])
                for call in file.get("calls", [])
                if call["name"].split(".")[-1]
                in {"read_csv", "read_json", "read_excel", "loadtxt", "genfromtxt", "open"}
                for value in call.get("string_arguments", [])[:1]
                if len(value) <= 120 and "[" not in value
            }
        except (ValueError, OSError):
            reads = set()
        sources = [file.path for file in plan.files if file.path.endswith(".py")]
        text = f"Python-проект содержит {len(sources)} модулей."
        if plan.run_spec is not None:
            text += f" Начальная точка запуска — `{plan.run_spec.target}`."
        if reads:
            paths = ", ".join(f"`{path}`" for path in sorted(reads)[:5])
            text += " В коде обнаружены обращения к файлам: " + paths + "."
        return TextRedactor().redact(text)

    def candidate_id(self, record: JobRecord, report: AnalysisReport) -> str | None:
        if not record.automatic_review_eligible:
            return None
        cached = self._cached(record, report.analysis_id)
        if cached is None or cached.get("status") != "ready":
            return None
        entrypoint = cached.get("entrypoint")
        return entrypoint.get("candidate_id") if isinstance(entrypoint, dict) else None

    def enrich(self, plan: PackagePlan, record: JobRecord) -> PackagePlan:
        if self.repository.settings.automatic_resolution and not plan.description.strip():
            plan = replace(
                plan,
                description=self._static_description(plan, record),
                description_origin="inferred",
            )
        cached = self._cached(record, plan.analysis_id)
        if cached is None:
            return plan
        discovery = cached.get("provider_discovery")
        discovery_summary = None
        if discovery:
            discovery_summary = {
                "source": "published_index",
                "status": discovery["status"],
                "elapsed_ms": discovery.get("elapsed_ms", 0),
                "packages": {
                    module: value["verification"]
                    for module, value in cached.get("packages", {}).items()
                    if module in plan.automatic_packages
                    and value.get("origin") == "provider_discovery"
                },
                "unresolved": discovery.get("unresolved", {}),
            }
        if not record.automatic_review_eligible:
            return (
                replace(
                    plan,
                    automatic_review={
                        "status": "disabled",
                        "provider": "deepseek",
                        "applied": False,
                        "provider_discovery": discovery_summary,
                    },
                )
                if discovery_summary
                else plan
            )
        retained = {file.path for file in plan.files}
        source_files = {file.path for file in plan.report.files if file.path.endswith(".py")}
        paragraphs = [
            item
            for item in cached.get("paragraphs", [])
            if item["source_paths"]
            and set(item["source_paths"]).issubset(retained)
            and re.search(r"[А-Яа-яЁё]", item.get("text", ""))
        ]
        if not source_files.issubset(retained):
            # Citations support prose but cannot prove all semantic dependencies.
            paragraphs = []
        authored = bool(plan.decisions and plan.decisions.description.strip())
        if (
            not authored
            and not paragraphs
            and any(
                not re.search(r"[А-Яа-яЁё]", item.get("text", ""))
                for item in cached.get("paragraphs", [])
            )
        ):
            plan = replace(
                plan,
                description=self._static_description(plan, record),
                description_origin="inferred",
            )
        applied = not authored and bool(paragraphs)
        entrypoint = cached.get("entrypoint") or {}
        entrypoint_applied = plan.run_spec_origin == "automatic"
        entrypoint_reason = (
            "selection_accepted"
            if entrypoint_applied
            else "author_override"
            if plan.run_spec_origin == "author"
            else "source_excluded"
            if entrypoint and not source_files.issubset(retained)
            else "candidate_unavailable"
            if entrypoint
            else "static_fallback"
        )
        metadata = {
            **self.summary(cached),
            "applied": applied,
            "policy": cached.get("policy", "source_descriptions_only"),
            "entrypoint": {
                "applied": entrypoint_applied,
                "candidate_id": entrypoint.get("candidate_id"),
                "reason": entrypoint_reason,
                "evidence_ids": entrypoint.get("evidence_ids", []),
                "explanation": entrypoint.get("text"),
            },
            "evidence_ids": sorted({key for item in paragraphs for key in item["evidence_ids"]})
            if applied
            else [],
            "source_paths": sorted({path for item in paragraphs for path in item["source_paths"]})
            if applied
            else [],
            "rounds": cached.get("rounds", 0),
            "tool_calls": cached.get("tool_calls", 0),
            "usage": cached.get("usage"),
        }
        if discovery_summary:
            metadata["provider_discovery"] = discovery_summary
        return replace(
            plan,
            description="\n\n".join(item["text"] for item in paragraphs)
            if applied
            else plan.description,
            description_origin="automatic"
            if applied
            else "author"
            if authored
            else plan.description_origin,
            automatic_review=metadata,
        )

    @staticmethod
    def summary(cached: dict) -> dict:
        return {
            "status": cached["status"],
            "provider": "deepseek",
            "applied": False,
            "reason": cached.get("reason"),
            "elapsed_ms": cached.get("elapsed_ms", 0),
        }

    def _remaining(self, record: JobRecord) -> float:
        return max(0, self.budget_seconds - (time.time() - (record.review_ready_at or 0)))

    def _cached(self, record: JobRecord, analysis_id: str) -> dict | None:
        return self.repository.read_review(record, analysis_id, reuse_policy=self.policy)

    @staticmethod
    def _paragraphs(response: dict, context: SafeProjectContext) -> list[dict]:
        if response["status"] != "ok":
            return []
        sources = {
            item["id"]: item["path"]
            for item in context.files
            if item.get("text") and item["path"].endswith(".py")
        }
        blocked_sources = {item["id"] for item in context.files if not item.get("text")}
        paragraphs, seen, size = [], set(), 0
        for item in response["suggestions"]:
            if item.get("kind") != "readme_description":
                continue
            if blocked_sources.intersection(item["evidence_ids"]):
                continue
            paths = sorted({sources[key] for key in item["evidence_ids"] if key in sources})
            text = TextRedactor().redact(item["text"]).strip()
            if not paths or not text or text in seen or size + len(text) > 8192:
                continue
            paragraphs.append(
                {"text": text, "evidence_ids": item["evidence_ids"], "source_paths": paths}
            )
            seen.add(text)
            size += len(text) + 2
        return paragraphs

    @staticmethod
    def _entrypoint(response: dict, context: SafeProjectContext) -> dict | None:
        if response["status"] != "ok":
            return None
        candidates = {candidate["id"] for candidate in context.launches}
        for item in response["suggestions"]:
            if item["kind"] == "entrypoint_hypothesis" and item["candidate_id"] in candidates:
                return {
                    "candidate_id": item["candidate_id"],
                    "text": TextRedactor().redact(item["text"]),
                    "evidence_ids": item["evidence_ids"],
                }
        return None
