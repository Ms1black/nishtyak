from __future__ import annotations

import os
import threading
import time
from dataclasses import replace

from nishtyak_core import ArchiveReader
from nishtyak_core.advisors import (
    DeepSeekClient,
    DeepSeekConfig,
    ProjectAdvisoryService,
    SafeProjectContext,
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
            "features": ["description", "entrypoint"],
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

    def ensure(self, record: JobRecord, report: AnalysisReport) -> None:
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

    def candidate_id(self, record: JobRecord, report: AnalysisReport) -> str | None:
        if not record.automatic_review_eligible:
            return None
        cached = self._cached(record, report.analysis_id)
        if cached is None or cached.get("status") != "ready":
            return None
        entrypoint = cached.get("entrypoint")
        return entrypoint.get("candidate_id") if isinstance(entrypoint, dict) else None

    def enrich(self, plan: PackagePlan, record: JobRecord) -> PackagePlan:
        if not record.automatic_review_eligible:
            return plan
        cached = self._cached(record, plan.analysis_id)
        if cached is None:
            return plan
        retained = {file.path for file in plan.files}
        source_files = {file.path for file in plan.report.files if file.path.endswith(".py")}
        paragraphs = [
            item
            for item in cached.get("paragraphs", [])
            if item["source_paths"] and set(item["source_paths"]).issubset(retained)
        ]
        if not source_files.issubset(retained):
            # Citations support prose but cannot prove all semantic dependencies.
            paragraphs = []
        authored = bool(plan.description.strip())
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
        return replace(
            plan,
            description="\n\n".join(item["text"] for item in paragraphs)
            if applied
            else plan.description,
            description_origin="automatic" if applied else "author" if authored else "unknown",
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
