from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import asdict
from pathlib import Path

from nishtyak_core import ArchiveReader, ReproducibilityService
from nishtyak_core.advisors import AdvisoryService
from nishtyak_core.advisors.models import AdvisoryResult
from nishtyak_core.archives import ArchiveError
from nishtyak_core.decisions import DecisionsCodec
from nishtyak_core.environment import AuthorEnvironmentSnapshot
from nishtyak_core.exporting import BundleWriter
from nishtyak_core.locking import LockExporter, ResolutionError, ResolutionTarget, VersionResolver
from nishtyak_core.models import AnalysisOptions, AnalysisReport, PackagePlan, UserDecisions
from nishtyak_core.redaction import TextRedactor

from .automatic_review import AutomaticProjectReview
from .config import Settings
from .errors import ServiceError
from .storage import JobRecord, SQLiteJobRepository


class LogicService:
    def __init__(
        self,
        settings: Settings,
        repository: SQLiteJobRepository | None = None,
        advisor: AdvisoryService | None = None,
    ):
        self.settings = settings
        self.repository = repository or SQLiteJobRepository(settings)
        self.core = ReproducibilityService()
        self.advisor = advisor
        self.automatic_review = AutomaticProjectReview(self.repository)

    def capabilities(self) -> dict:
        return {"automatic_review": self.automatic_review.capabilities()}

    def review_pending(self) -> None:
        self.automatic_review.process_pending()

    def submit(
        self,
        owner: str,
        archive: bytes,
        options: AnalysisOptions,
        author_environment: AuthorEnvironmentSnapshot | None = None,
    ) -> dict:
        self.repository.consume_rate(owner, "upload", 5)
        record = self.repository.submit(owner, archive, options, author_environment)
        return self._identity(record)

    def reanalyze(
        self,
        owner: str,
        project_id: str,
        options: AnalysisOptions,
        author_environment: AuthorEnvironmentSnapshot | None = None,
    ) -> dict:
        self.repository.consume_rate(owner, "upload", 5)
        return self._identity(
            self.repository.reanalyze(owner, project_id, options, author_environment)
        )

    def get_decisions(self, owner: str, project_id: str) -> dict:
        self.repository.consume_rate(owner, "read", 120)
        return self.repository.read_decisions(owner, project_id)

    def save_decisions(
        self, owner: str, project_id: str, decisions: UserDecisions, expected_revision: int
    ) -> dict:
        self.repository.consume_rate(owner, "plan", 30)
        with self.repository.operation(owner, project_id) as record:
            report = self.repository.read_report(record)
            plan = self._prepare(report, decisions)
            # Persist the same sanitized decisions used by the generated plan.
            result = self.repository.save_decisions(
                owner, project_id, report.analysis_id, expected_revision, asdict(plan.decisions)
            )
        self.repository.get_project(owner, project_id)
        return result

    def get_job(self, owner: str, job_id: str) -> dict:
        self.repository.consume_rate(owner, "read", 120)
        record = self.repository.get_job(owner, job_id)
        result = self._identity(record)
        result["created_at"] = record.created_at
        result["expires_at"] = record.expires_at
        if record.status == "succeeded":
            report = self.repository.read_report(record)
            result["analysis"] = report.to_dict()
            result["automatic_review"] = self.automatic_review.status(record, report)
        elif record.error_code:
            result["error"] = {
                "code": record.error_code,
                "message": "The analysis did not complete successfully",
            }
        self.repository.get_job(owner, job_id)
        return result

    def get_project(self, owner: str, project_id: str) -> dict:
        self.repository.consume_rate(owner, "read", 120)
        record = self.repository.get_project(owner, project_id)
        self._require_succeeded(record)
        report = self.repository.read_report(record)
        self.repository.get_project(owner, project_id)
        return {"project_id": project_id, "analysis": report.to_dict()}

    def delete(self, owner: str, project_id: str) -> None:
        self.repository.consume_rate(owner, "delete", 30)
        self.repository.delete(owner, project_id)

    def plan(self, owner: str, project_id: str, decisions: UserDecisions | None = None) -> dict:
        self.repository.consume_rate(owner, "plan", 30)
        with self.repository.operation(owner, project_id) as record:
            report = self.repository.read_report(record)
            self.automatic_review.ensure(record, report)
            plan = self._prepare(
                report, self._decisions(owner, project_id, report, decisions), record
            )
            result = plan.to_dict()
        self.repository.get_project(owner, project_id)
        return result

    def bundle(
        self,
        owner: str,
        project_id: str,
        decisions: UserDecisions | None = None,
        include_dockerfile: bool = False,
        lock_id: str | None = None,
    ) -> bytes:
        self.repository.consume_rate(owner, "bundle", 10)
        with self.repository.operation(owner, project_id) as record:
            report = self.repository.read_report(record)
            self.automatic_review.ensure(record, report)
            plan = self._prepare(
                report, self._decisions(owner, project_id, report, decisions), record
            )
            if plan.state == "blocked":
                raise ServiceError(409, "PLAN_BLOCKED", "Resolve the blocking findings first")
            try:
                snapshot = ArchiveReader().from_path(self.repository.archive_path(project_id))
                if snapshot.fingerprint != report.fingerprint:
                    raise ServiceError(409, "SNAPSHOT_CHANGED", "The stored project has changed")
                generated = self.core.render(plan, include_dockerfile=include_dockerfile)
                if lock_id is not None:
                    lock = self.repository.read_lock(owner, project_id)
                    if lock.id != lock_id:
                        raise ServiceError(409, "LOCK_STALE", "Dependency lock was replaced")
                    try:
                        generated = LockExporter().attach(generated, plan, lock)
                    except ResolutionError as error:
                        raise ServiceError(409, error.code, str(error)) from None
                source_bytes = sum(file.size for file in plan.files)
                generated_bytes = sum(len(file.content) for file in generated.files)
                if source_bytes + generated_bytes > self.settings.max_bundle_bytes - 1024**2:
                    raise ServiceError(413, "BUNDLE_LIMIT", "Generated package exceeds its limit")
                with tempfile.TemporaryDirectory(
                    prefix="bundle-", dir=self.repository.work_directory(project_id)
                ) as directory:
                    destination = Path(directory) / "project.zip"
                    artifact = BundleWriter().write(snapshot, plan, generated, destination)
                    if artifact.size > self.settings.max_bundle_bytes:
                        raise ServiceError(413, "BUNDLE_LIMIT", "Generated ZIP exceeds its limit")
                    content = destination.read_bytes()
            except ArchiveError:
                raise ServiceError(
                    409, "ARCHIVE_CHANGED", "Stored ZIP is no longer valid"
                ) from None
            except (ValueError, TypeError):
                raise ServiceError(
                    422, "EXPORT_INVALID", "Unsupported export configuration"
                ) from None
            except OSError:
                raise ServiceError(
                    503, "STORAGE_UNAVAILABLE", "Project storage is unavailable"
                ) from None
        self.repository.get_project(owner, project_id)
        return content

    def preparation(self, owner: str, project_id: str) -> dict:
        from nishtyak_core.launches import LaunchDetector

        self.repository.consume_rate(owner, "plan", 30)
        with self.repository.operation(owner, project_id) as record:
            report = self.repository.read_report(record)
            self.automatic_review.ensure(record, report)
            plan = self._prepare(report, self._decisions(owner, project_id, report, None), record)
            result = {
                "project_id": project_id,
                "plan": plan.to_dict(),
                "launch": LaunchDetector().detect(report, plan.files).to_dict(),
                "automatic_review": self.automatic_review.status(record, report),
            }
        self.repository.get_project(owner, project_id)
        return TextRedactor().redact_value(result)

    def graph(self, owner: str, project_id: str) -> dict:
        from nishtyak_core.import_graph import ImportGraph

        self.repository.consume_rate(owner, "read", 120)
        with self.repository.operation(owner, project_id) as record:
            report = self.repository.read_report(record)
            snapshot = ArchiveReader().from_path(self.repository.archive_path(project_id))
            if snapshot.fingerprint != report.fingerprint:
                raise ServiceError(409, "SNAPSHOT_CHANGED", "The stored project has changed")
            roots = report.layout.source_roots if report.layout else report.options.source_roots
            result = TextRedactor().redact_value(
                ImportGraph(snapshot, roots, report.imports).to_dict()
            )
        self.repository.get_project(owner, project_id)
        return result

    def project_advice(self, owner: str, project_id: str, feature: str, description: str) -> dict:
        from nishtyak_core.advisors import ProjectAdvisoryResult, SafeProjectContext

        from .project_advice_jobs import ProjectAdviceJobRunner

        self.repository.consume_rate(owner, "advice", 5)
        with self.repository.operation(owner, project_id) as record:
            report = self.repository.read_report(record)
            if not (os.environ.get("DEEPSEEK_API_KEY") and os.environ.get("DEEPSEEK_MODEL")):
                result = ProjectAdvisoryResult(
                    report.analysis_id, feature, "unavailable", reason="advisor_unavailable"
                ).to_dict()
            else:
                self.repository.consume_rate(owner, "advice-daily", 10, window=86400)
                self.repository.consume_rate("_service", "advice-global-daily", 100, window=86400)
                snapshot = ArchiveReader().from_path(self.repository.archive_path(project_id))
                if snapshot.fingerprint != report.fingerprint:
                    raise ServiceError(409, "SNAPSHOT_CHANGED", "The stored project has changed")
                try:
                    context = SafeProjectContext.from_project(report, snapshot)
                except ValueError:
                    raise ServiceError(
                        422,
                        "ADVICE_CONTEXT_LIMIT",
                        "Project context cannot be safely represented within its limit",
                    ) from None
                result = ProjectAdviceJobRunner().run(context, feature, description)
        self.repository.get_project(owner, project_id)
        return result

    def resolve(
        self,
        owner: str,
        project_id: str,
        target: ResolutionTarget,
        decisions: UserDecisions | None = None,
    ) -> dict:
        self.repository.consume_rate(owner, "resolve", 2)
        self.repository.consume_rate("_service", "resolve-global", 20)
        with self.repository.operation(owner, project_id) as record:
            report = self.repository.read_report(record)
            plan = self._prepare(report, self._decisions(owner, project_id, report, decisions))
            try:
                lock = VersionResolver().resolve(plan, target)
            except ResolutionError as error:
                status = 503 if error.code in {"RESOLVER_UNAVAILABLE", "INDEX_UNAVAILABLE"} else 422
                raise ServiceError(status, error.code, str(error)) from None
            self.repository.save_lock(owner, project_id, lock)
        self.repository.get_project(owner, project_id)
        return lock.to_dict()

    def get_lock(self, owner: str, project_id: str) -> dict:
        self.repository.consume_rate(owner, "read", 120)
        result = self.repository.read_lock(owner, project_id).to_dict()
        self.repository.get_project(owner, project_id)
        return result

    def _decisions(
        self, owner: str, project_id: str, report: AnalysisReport, decisions: UserDecisions | None
    ) -> UserDecisions:
        if decisions is not None:
            return decisions
        value = self.repository.read_decisions(owner, project_id)["decisions"]
        return DecisionsCodec().decode(value) if value else UserDecisions(report.analysis_id)

    def advice(self, owner: str, project_id: str, feature: str, description: str = "") -> dict:
        self.repository.consume_rate(owner, "advice", 5)
        with self.repository.operation(owner, project_id) as record:
            report = self.repository.read_report(record)
            configured = bool(
                os.environ.get("DEEPSEEK_API_KEY") and os.environ.get("DEEPSEEK_MODEL")
            )
            if self.advisor is not None or configured:
                self.repository.consume_rate(owner, "advice-daily", 10, window=86400)
                self.repository.consume_rate("_service", "advice-global-daily", 100, window=86400)
                if self.advisor is not None:
                    result = self.advisor.advise(
                        report, feature=feature, description=description
                    ).to_dict()
                else:
                    from .advice_jobs import AdviceJobRunner

                    result = AdviceJobRunner().run(report, feature, description)
            else:
                result = AdvisoryResult(report.analysis_id, feature, "unavailable").to_dict()
        self.repository.get_project(owner, project_id)
        return result

    def ready(self) -> bool:
        try:
            return self.repository.dispatcher_ready()
        except (OSError, ValueError, sqlite3.Error):
            return False

    def _prepare(
        self, report: AnalysisReport, decisions: UserDecisions, record: JobRecord | None = None
    ) -> PackagePlan:
        if decisions.analysis_id != report.analysis_id:
            raise ServiceError(409, "STALE_ANALYSIS", "Decisions belong to another analysis")
        try:
            candidate = (
                self.automatic_review.candidate_id(record, report) if record is not None else None
            )
            plan = (
                self.core.prepare(report, decisions, automatic_candidate_id=candidate)
                if candidate is not None
                else self.core.prepare(report, decisions)
            )
            return self.automatic_review.enrich(plan, record) if record is not None else plan
        except ValueError as error:
            if hasattr(error, "code") and hasattr(error, "field"):
                raise ServiceError(422, error.code, str(error), error.field) from None
            raise ServiceError(422, "DECISIONS_INVALID", "Invalid project decisions") from None
        except TypeError:
            raise ServiceError(422, "DECISIONS_INVALID", "Invalid project decisions") from None

    @staticmethod
    def _identity(record: JobRecord) -> dict:
        return {
            "project_id": record.project_id,
            "job_id": record.job_id,
            "status": record.status,
        }

    @staticmethod
    def _require_succeeded(record: JobRecord) -> None:
        if record.status in {"queued", "running"}:
            raise ServiceError(409, "ANALYSIS_NOT_READY", "Analysis is still in progress")
        if record.status != "succeeded":
            raise ServiceError(409, "ANALYSIS_UNAVAILABLE", "Analysis did not succeed")
