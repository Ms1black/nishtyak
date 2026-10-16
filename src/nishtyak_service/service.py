from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from nishtyak_core import ArchiveReader, ReproducibilityService
from nishtyak_core.advisors import AdvisoryService
from nishtyak_core.advisors.models import AdvisoryResult
from nishtyak_core.archives import ArchiveError
from nishtyak_core.exporting import BundleWriter
from nishtyak_core.models import AnalysisOptions, AnalysisReport, PackagePlan, UserDecisions

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

    def submit(self, owner: str, archive: bytes, options: AnalysisOptions) -> dict:
        self.repository.consume_rate(owner, "upload", 5)
        record = self.repository.submit(owner, archive, options)
        return self._identity(record)

    def get_job(self, owner: str, job_id: str) -> dict:
        self.repository.consume_rate(owner, "read", 120)
        record = self.repository.get_job(owner, job_id)
        result = self._identity(record)
        result["created_at"] = record.created_at
        result["expires_at"] = record.expires_at
        if record.status == "succeeded":
            result["analysis"] = self.repository.read_report(record).to_dict()
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

    def plan(self, owner: str, project_id: str, decisions: UserDecisions) -> dict:
        self.repository.consume_rate(owner, "plan", 30)
        with self.repository.operation(owner, project_id) as record:
            report = self.repository.read_report(record)
            plan = self._prepare(report, decisions)
            result = plan.to_dict()
        self.repository.get_project(owner, project_id)
        return result

    def bundle(
        self,
        owner: str,
        project_id: str,
        decisions: UserDecisions,
        include_dockerfile: bool = False,
    ) -> bytes:
        self.repository.consume_rate(owner, "bundle", 10)
        with self.repository.operation(owner, project_id) as record:
            report = self.repository.read_report(record)
            plan = self._prepare(report, decisions)
            if plan.state == "blocked":
                raise ServiceError(409, "PLAN_BLOCKED", "Resolve the blocking findings first")
            try:
                snapshot = ArchiveReader().from_path(self.repository.archive_path(project_id))
                if snapshot.fingerprint != report.fingerprint:
                    raise ServiceError(409, "SNAPSHOT_CHANGED", "The stored project has changed")
                generated = self.core.render(plan, include_dockerfile=include_dockerfile)
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

    def _prepare(self, report: AnalysisReport, decisions: UserDecisions) -> PackagePlan:
        if decisions.analysis_id != report.analysis_id:
            raise ServiceError(409, "STALE_ANALYSIS", "Decisions belong to another analysis")
        try:
            return self.core.prepare(report, decisions)
        except (ValueError, TypeError):
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
