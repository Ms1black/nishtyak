from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from nishtyak_core.environment import AuthorEnvironmentSnapshot
from nishtyak_core.jobs import AnalysisJobResult
from nishtyak_core.locking import DependencyLock
from nishtyak_core.models import AnalysisOptions, AnalysisReport
from nishtyak_core.report_codec import (
    AnalysisReportCodec,
    InvalidWorkerReport,
    bounded_json,
)

from .errors import ServiceError

if TYPE_CHECKING:
    from .config import Settings


@dataclass(frozen=True)
class JobRecord:
    project_id: str
    job_id: str
    owner_id: str
    status: str
    created_at: float
    expires_at: float
    options: AnalysisOptions
    error_code: str | None = None
    deleted: bool = False
    author_environment: AuthorEnvironmentSnapshot | None = None
    automatic_review_eligible: bool = False
    review_ready_at: float | None = None


class SQLiteJobRepository:
    """Durable admission, ownership and lifecycle for one analysis per project.

    The SQLite write transaction also protects filesystem changes. Reservations stay
    charged until files are physically removed, including after a deletion or crash.
    """

    lease_seconds = 180
    _identifier = re.compile(r"[0-9a-f]{32}\Z")
    _terminal = {"succeeded", "failed", "timed_out", "resource_limited"}

    def __init__(self, settings: Settings):
        self.settings = settings
        self.data_dir = Path(settings.data_dir).absolute()
        self._ensure_private_directory(self.data_dir)
        self._projects_dir = self.data_dir / "projects"
        self._ensure_private_directory(self._projects_dir)
        self.database_path = self.data_dir / "jobs.sqlite3"
        if self.database_path.is_symlink():
            raise ValueError("The database must not be a symbolic link")
        self._codec = AnalysisReportCodec()
        self._initialize()

    def submit(
        self,
        owner_id: str,
        archive: bytes,
        options: AnalysisOptions,
        author_environment: AuthorEnvironmentSnapshot | None = None,
    ) -> JobRecord:
        self._validate_owner(owner_id)
        if not isinstance(archive, bytes) or not archive:
            raise ServiceError(400, "ARCHIVE_INVALID", "A nonempty ZIP is required")
        if len(archive) > self.settings.max_archive_bytes:
            raise ServiceError(413, "ARCHIVE_LIMIT", "Archive exceeds the byte limit")
        try:
            options = self._codec.decode_options(asdict(options))
            options_json = bounded_json(asdict(options), 64 * 1024).decode("ascii")
        except (TypeError, ValueError):
            raise ServiceError(422, "OPTIONS_INVALID", "Invalid analysis options") from None
        environment_json = self._environment_json(author_environment)
        project_id, job_id = uuid.uuid4().hex, uuid.uuid4().hex
        reservation = (
            len(archive)
            + 2 * self.settings.max_report_bytes
            + self.settings.max_bundle_bytes
            + 1024 * 1024
        )
        root = self._project_directory(project_id)
        try:
            with self._transaction() as connection:
                now = time.time()
                self._revoke_expired(connection, now)
                self._cleanup(connection, now)
                self._check_admission(connection, owner_id, reservation, now)
                self._ensure_private_directory(root)
                self._ensure_private_directory(root / "work")
                self._write_file(root / "source.zip", archive)
                connection.execute(
                    """INSERT INTO projects
                    (project_id, job_id, owner_id, status, created_at, expires_at,
                     options_json, reserved_bytes, deleted, worker_active, author_environment_json,
                     automatic_review_eligible)
                    VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, 0, 0, ?, ?)""",
                    (
                        project_id,
                        job_id,
                        owner_id,
                        now,
                        now + self.settings.ttl_seconds,
                        options_json,
                        reservation,
                        environment_json,
                        self._review_eligible(),
                    ),
                )
                if self.settings.automatic_resolution:
                    self._queue_resolution(connection, project_id, job_id)
                row = connection.execute(
                    "SELECT * FROM projects WHERE project_id = ?", (project_id,)
                ).fetchone()
        except BaseException:
            self._remove_project(project_id)
            raise
        return self._record(row)

    def reanalyze(
        self,
        owner_id: str,
        project_id: str,
        options: AnalysisOptions,
        author_environment: AuthorEnvironmentSnapshot | None = None,
    ) -> JobRecord:
        self._validate_owner(owner_id)
        self._validate_identifier(project_id)
        try:
            options = self._codec.decode_options(asdict(options))
            options_json = bounded_json(asdict(options), 64 * 1024).decode("ascii")
        except (TypeError, ValueError):
            raise ServiceError(422, "OPTIONS_INVALID", "Invalid analysis options") from None
        environment_json = self._environment_json(author_environment)
        with self._transaction() as connection:
            now = time.time()
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ? AND owner_id = ?",
                (project_id, owner_id),
            ).fetchone()
            self._accessible(row)
            connection.execute("DELETE FROM leases WHERE expires_at <= ?", (now,))
            if (
                row["worker_active"]
                or row["status"] == "running"
                or connection.execute(
                    "SELECT 1 FROM leases WHERE project_id = ?", (project_id,)
                ).fetchone()
            ):
                raise ServiceError(409, "PROJECT_BUSY", "A project operation is active")
            self._revoke_expired(connection, now)
            self._cleanup(connection, now)
            self._check_admission(connection, owner_id, 0, now, project_id)
            job_id = uuid.uuid4().hex
            connection.execute(
                """UPDATE projects SET job_id = ?, status = 'queued', options_json = ?,
                author_environment_json = ?, automatic_review_eligible = ?, review_ready_at = NULL,
                error_code = NULL WHERE project_id = ?""",
                (
                    job_id,
                    options_json,
                    environment_json
                    if author_environment is not None
                    else row["author_environment_json"],
                    self._review_eligible(),
                    project_id,
                ),
            )
            previous = connection.execute(
                "SELECT decisions_json FROM decision_documents WHERE project_id = ?", (project_id,)
            ).fetchone()
            prose = {}
            if previous is not None and previous["decisions_json"]:
                old_decisions = json.loads(previous["decisions_json"])
                prose = {
                    key: old_decisions[key]
                    for key in ("description", "input_description", "expected_output")
                    if old_decisions.get(key)
                }
            connection.execute(
                """INSERT INTO decision_documents
                (project_id, analysis_id, revision, decisions_json, updated_at)
                VALUES (?, '', 1, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET revision = revision + 1,
                analysis_id = '', decisions_json = excluded.decisions_json,
                updated_at = excluded.updated_at""",
                (project_id, json.dumps(prose) if prose else None, now),
            )
            (self._project_directory(project_id) / "dependency-lock.json").unlink(missing_ok=True)
            if self.settings.automatic_resolution:
                self._queue_resolution(connection, project_id, job_id)
            else:
                connection.execute(
                    "DELETE FROM automatic_resolutions WHERE project_id = ?", (project_id,)
                )
            return self._record(
                connection.execute(
                    "SELECT * FROM projects WHERE project_id = ?", (project_id,)
                ).fetchone()
            )

    def read_decisions(self, owner_id: str, project_id: str) -> dict:
        self._validate_owner(owner_id)
        self._validate_identifier(project_id)
        with self._transaction() as connection:
            report = self._decision_report(connection, owner_id, project_id)
            document = connection.execute(
                "SELECT * FROM decision_documents WHERE project_id = ?", (project_id,)
            ).fetchone()
            return self._decision_document(report.analysis_id, document)

    def pending_reviews(self) -> list[JobRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT p.* FROM projects p LEFT JOIN automatic_reviews r
                ON r.project_id = p.project_id WHERE p.status = 'succeeded'
                AND p.deleted = 0 AND p.expires_at > ? AND p.automatic_review_eligible = 1
                AND (r.job_id IS NULL OR r.job_id != p.job_id)
                ORDER BY p.created_at LIMIT 8""",
                (time.time(),),
            ).fetchall()
        return [self._record(row) for row in rows]

    def pending_resolutions(self) -> list[JobRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT p.* FROM projects p JOIN automatic_resolutions r
                ON r.project_id = p.project_id AND r.job_id = p.job_id
                WHERE p.status = 'succeeded' AND p.deleted = 0 AND p.expires_at > ?
                AND (r.status = 'pending' OR (r.status = 'running' AND r.updated_at < ?))
                ORDER BY p.created_at LIMIT 8""",
                (time.time(), time.time() - 30),
            ).fetchall()
        return [self._record(row) for row in rows]

    def pending_automation(self) -> list[JobRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT p.* FROM projects p JOIN automation_flows f
                ON f.project_id = p.project_id AND f.job_id = p.job_id
                WHERE p.status = 'succeeded' AND p.deleted = 0 AND p.expires_at > ?
                AND (f.phase = 'static' OR (f.phase IN ('assisting', 'validating')
                AND f.updated_at < ?)) ORDER BY p.created_at LIMIT 8""",
                (time.time(), time.time() - 60),
            ).fetchall()
        return [self._record(row) for row in rows]

    def read_automation(self, record: JobRecord) -> dict | None:
        self.get_project(record.owner_id, record.project_id)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT result_json FROM automation_flows
                WHERE project_id = ? AND job_id = ?""",
                (record.project_id, record.job_id),
            ).fetchone()
        return json.loads(row["result_json"]) if row is not None else None

    def save_automation(self, record: JobRecord, result: dict) -> None:
        payload = bounded_json(result, 64 * 1024).decode("ascii")
        with self._transaction() as connection:
            current = self._accessible(
                connection.execute(
                    "SELECT * FROM projects WHERE project_id = ? AND owner_id = ?",
                    (record.project_id, record.owner_id),
                ).fetchone()
            )
            if current.job_id != record.job_id or current.status != "succeeded":
                raise ServiceError(409, "ANALYSIS_STALE", "Preparation belongs to old analysis")
            connection.execute(
                """INSERT INTO automation_flows
                (project_id, job_id, phase, result_json, updated_at) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET job_id = excluded.job_id,
                phase = excluded.phase, result_json = excluded.result_json,
                updated_at = excluded.updated_at""",
                (record.project_id, record.job_id, result["phase"], payload, time.time()),
            )

    def read_resolution(self, record: JobRecord) -> dict | None:
        self.get_project(record.owner_id, record.project_id)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT result_json FROM automatic_resolutions
                WHERE project_id = ? AND job_id = ?""",
                (record.project_id, record.job_id),
            ).fetchone()
        return json.loads(row["result_json"]) if row is not None else None

    def save_resolution(self, record: JobRecord, result: dict) -> None:
        payload = bounded_json(result, 256 * 1024).decode("ascii")
        with self._transaction() as connection:
            current = self._accessible(
                connection.execute(
                    "SELECT * FROM projects WHERE project_id = ? AND owner_id = ?",
                    (record.project_id, record.owner_id),
                ).fetchone()
            )
            if current.job_id != record.job_id or current.status != "succeeded":
                raise ServiceError(409, "ANALYSIS_STALE", "Resolution belongs to old analysis")
            connection.execute(
                """INSERT INTO automatic_resolutions
                (project_id, job_id, status, result_json, updated_at) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET job_id = excluded.job_id,
                status = excluded.status, result_json = excluded.result_json,
                updated_at = excluded.updated_at""",
                (record.project_id, record.job_id, result["status"], payload, time.time()),
            )

    @staticmethod
    def _queue_resolution(connection, project_id: str, job_id: str) -> None:
        connection.execute(
            """INSERT INTO automatic_resolutions
            (project_id, job_id, status, result_json, updated_at) VALUES (?, ?, 'pending', ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET job_id = excluded.job_id,
            status = excluded.status, result_json = excluded.result_json,
            updated_at = excluded.updated_at""",
            (project_id, job_id, json.dumps({"status": "pending"}), time.time()),
        )
        connection.execute(
            """INSERT INTO automation_flows
            (project_id, job_id, phase, result_json, updated_at) VALUES (?, ?, 'static', ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET job_id = excluded.job_id,
            phase = excluded.phase, result_json = excluded.result_json,
            updated_at = excluded.updated_at""",
            (project_id, job_id, json.dumps({"phase": "static", "max_attempts": 2}), time.time()),
        )

    def read_review(
        self, record: JobRecord, analysis_id: str, *, reuse_policy: str | None = None
    ) -> dict | None:
        self.get_project(record.owner_id, record.project_id)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT result_json, job_id FROM automatic_reviews
                WHERE project_id = ? AND analysis_id = ?""",
                (record.project_id, analysis_id),
            ).fetchone()
        if row is None:
            return None
        result = json.loads(row["result_json"])
        if (
            reuse_policy is not None
            and row["job_id"] != record.job_id
            and result.get("policy") != reuse_policy
        ):
            return None
        return result

    def save_review(self, record: JobRecord, analysis_id: str, result: dict) -> None:
        payload = bounded_json(result, 256 * 1024).decode("ascii")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ? AND owner_id = ?",
                (record.project_id, record.owner_id),
            ).fetchone()
            current = self._accessible(row)
            if current.job_id != record.job_id or current.status != "succeeded":
                raise ServiceError(
                    409, "ANALYSIS_STALE", "Automatic review belongs to old analysis"
                )
            connection.execute(
                """INSERT INTO automatic_reviews
                (project_id, analysis_id, job_id, result_json) VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET analysis_id = excluded.analysis_id,
                job_id = excluded.job_id, result_json = excluded.result_json""",
                (record.project_id, analysis_id, record.job_id, payload),
            )

    def save_lock(self, owner_id: str, project_id: str, lock: DependencyLock) -> None:
        payload = bounded_json(lock.to_dict(), 512 * 1024)
        with self._transaction() as connection:
            report = self._decision_report(connection, owner_id, project_id)
            if lock.analysis_id != report.analysis_id:
                raise ServiceError(409, "LOCK_STALE", "Lock belongs to another analysis")
            self._write_file(
                self._project_directory(project_id) / "dependency-lock.json", payload, replace=True
            )

    def read_lock(self, owner_id: str, project_id: str) -> DependencyLock:
        with self._transaction() as connection:
            report = self._decision_report(connection, owner_id, project_id)
            path = self._project_directory(project_id) / "dependency-lock.json"
            try:
                descriptor = os.open(
                    path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                )
                with os.fdopen(descriptor, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError("Invalid lock file")
                    raw = stream.read(512 * 1024 + 1)
                if len(raw) > 512 * 1024:
                    raise ValueError("Lock exceeds its limit")
                lock = DependencyLock.from_dict(json.loads(raw))
            except FileNotFoundError:
                raise ServiceError(404, "LOCK_NOT_FOUND", "Resolve dependencies first") from None
            except (OSError, ValueError, TypeError):
                raise ServiceError(
                    409, "LOCK_INVALID", "Stored dependency lock is invalid"
                ) from None
            if lock.analysis_id != report.analysis_id:
                raise ServiceError(409, "LOCK_STALE", "Lock belongs to another analysis")
            return lock

    def save_decisions(
        self,
        owner_id: str,
        project_id: str,
        analysis_id: str,
        expected_revision: int,
        decisions: dict,
    ) -> dict:
        self._validate_owner(owner_id)
        self._validate_identifier(project_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ServiceError(422, "DECISIONS_INVALID", "Invalid decision revision")
        if not isinstance(decisions, dict):
            raise ServiceError(422, "DECISIONS_INVALID", "Decisions must be an object")
        try:
            payload = bounded_json(decisions, 256 * 1024).decode("ascii")
        except (ValueError, TypeError, RecursionError):
            raise ServiceError(
                422, "DECISIONS_INVALID", "Decision document exceeds its limits"
            ) from None
        with self._transaction() as connection:
            report = self._decision_report(connection, owner_id, project_id)
            if analysis_id != report.analysis_id or decisions.get("analysis_id") != analysis_id:
                raise ServiceError(409, "ANALYSIS_STALE", "Decisions belong to another analysis")
            document = connection.execute(
                "SELECT * FROM decision_documents WHERE project_id = ?", (project_id,)
            ).fetchone()
            revision = document["revision"] if document is not None else 0
            if expected_revision != revision:
                raise ServiceError(
                    409, "DECISIONS_REVISION_CONFLICT", "Decision revision has changed"
                )
            connection.execute(
                """INSERT INTO decision_documents
                (project_id, analysis_id, revision, decisions_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET analysis_id = excluded.analysis_id,
                revision = excluded.revision, decisions_json = excluded.decisions_json,
                updated_at = excluded.updated_at""",
                (project_id, analysis_id, revision + 1, payload, time.time()),
            )
            return self._decision_document(
                analysis_id,
                connection.execute(
                    "SELECT * FROM decision_documents WHERE project_id = ?", (project_id,)
                ).fetchone(),
            )

    def _decision_report(self, connection, owner_id: str, project_id: str) -> AnalysisReport:
        row = connection.execute(
            "SELECT * FROM projects WHERE project_id = ? AND owner_id = ?", (project_id, owner_id)
        ).fetchone()
        record = self._accessible(row)
        if record.status != "succeeded":
            raise ServiceError(409, "PROJECT_NOT_READY", "Analysis has not succeeded")
        return self._read_report_file(record)

    @staticmethod
    def _decision_document(analysis_id: str, document) -> dict:
        return {
            "analysis_id": analysis_id,
            "revision": document["revision"] if document is not None else 0,
            "decisions": json.loads(document["decisions_json"])
            if document is not None
            and document["analysis_id"] == analysis_id
            and document["decisions_json"] is not None
            else None,
            "updated_at": document["updated_at"] if document is not None else None,
        }

    @staticmethod
    def _environment_json(environment: AuthorEnvironmentSnapshot | None) -> str | None:
        if environment is None:
            return None
        try:
            raw = bounded_json(asdict(environment), 256 * 1024)
            parsed = AuthorEnvironmentSnapshot.from_dict(json.loads(raw))
            return bounded_json(asdict(parsed), 256 * 1024).decode("ascii")
        except (TypeError, ValueError, RecursionError):
            raise ServiceError(
                422, "AUTHOR_ENVIRONMENT_INVALID", "Invalid author environment"
            ) from None

    def get_project(self, owner_id: str, project_id: str) -> JobRecord:
        self._validate_owner(owner_id)
        self._validate_identifier(project_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ? AND owner_id = ?",
                (project_id, owner_id),
            ).fetchone()
        return self._accessible(row)

    def get_job(self, owner_id: str, job_id: str) -> JobRecord:
        self._validate_owner(owner_id)
        self._validate_identifier(job_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE job_id = ? AND owner_id = ?",
                (job_id, owner_id),
            ).fetchone()
        return self._accessible(row)

    def claim(self) -> JobRecord | None:
        with self._transaction() as connection:
            now = time.time()
            self._revoke_expired(connection, now)
            self._cleanup(connection, now)
            if self._active_count(connection, now) >= self.settings.max_running:
                return None
            row = connection.execute(
                """SELECT * FROM projects WHERE status = 'queued' AND deleted = 0
                AND expires_at > ? ORDER BY created_at, job_id LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """UPDATE projects SET status = 'running', worker_active = 1
                WHERE job_id = ?""",
                (row["job_id"],),
            )
            return self._record(
                connection.execute(
                    "SELECT * FROM projects WHERE job_id = ?", (row["job_id"],)
                ).fetchone()
            )

    def finish(self, job_id: str, result: AnalysisJobResult) -> bool:
        self._validate_identifier(job_id)
        payload = None
        status, code = result.status, result.code
        if status not in self._terminal or (
            status == "succeeded" and (result.report is None or code is not None)
        ):
            status, code = "failed", "WORKER_PROTOCOL_INVALID"
        if status == "succeeded":
            try:
                value = self._codec.encode(result.report)
                self._codec.decode(value)
                payload = bounded_json(value, self.settings.max_report_bytes)
            except (TypeError, ValueError, RecursionError):
                status, code = "failed", "WORKER_PROTOCOL_INVALID"
        with self._transaction() as connection:
            now = time.time()
            row = connection.execute(
                "SELECT * FROM projects WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None or row["status"] != "running":
                return False
            if row["deleted"] or row["expires_at"] <= now:
                connection.execute(
                    """UPDATE projects SET worker_active = 0, deleted = 1,
                    status = 'failed', error_code = 'JOB_REVOKED' WHERE job_id = ?""",
                    (job_id,),
                )
                self._cleanup(connection, now)
                return False
            if payload is not None:
                try:
                    self._write_file(self.report_path(row["project_id"]), payload, replace=True)
                    document = connection.execute(
                        "SELECT * FROM decision_documents WHERE project_id = ?",
                        (row["project_id"],),
                    ).fetchone()
                    if (
                        document is not None
                        and document["analysis_id"] == ""
                        and document["decisions_json"]
                    ):
                        prose = json.loads(document["decisions_json"])
                        prose["analysis_id"] = result.report.analysis_id
                        connection.execute(
                            """UPDATE decision_documents SET analysis_id = ?, decisions_json = ?
                            WHERE project_id = ?""",
                            (result.report.analysis_id, json.dumps(prose), row["project_id"]),
                        )
                except (OSError, ServiceError):
                    status, code = "failed", "REPORT_STORAGE_FAILED"
            connection.execute(
                """UPDATE projects SET status = ?, error_code = ?, worker_active = 0,
                review_ready_at = ?
                WHERE job_id = ?""",
                (status, code, now if status == "succeeded" else None, job_id),
            )
            return True

    def fail(self, job_id: str, code: str, status: str = "failed") -> None:
        self._validate_identifier(job_id)
        if status not in self._terminal - {"succeeded"}:
            raise ValueError("Invalid failure status")
        with self._transaction() as connection:
            now = time.time()
            connection.execute(
                """UPDATE projects SET status = ?, error_code = ?, worker_active = 0
                WHERE job_id = ? AND status IN ('queued', 'running')""",
                (status, code, job_id),
            )
            self._revoke_expired(connection, now)
            self._cleanup(connection, now)

    def running_jobs(self) -> list[JobRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projects WHERE worker_active = 1 ORDER BY created_at"
            ).fetchall()
        return [self._record(row) for row in rows]

    def is_revoked(self, job_id: str) -> bool:
        self._validate_identifier(job_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT deleted, expires_at FROM projects WHERE job_id = ?", (job_id,)
            ).fetchone()
        return row is None or bool(row["deleted"]) or row["expires_at"] <= time.time()

    def delete(self, owner_id: str, project_id: str) -> None:
        self._validate_owner(owner_id)
        self._validate_identifier(project_id)
        with self._transaction() as connection:
            now = time.time()
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ? AND owner_id = ?",
                (project_id, owner_id),
            ).fetchone()
            self._accessible(row)
            connection.execute(
                "UPDATE projects SET deleted = 1 WHERE project_id = ?", (project_id,)
            )
            self._cleanup(connection, now)

    def sweep(self) -> None:
        with self._transaction() as connection:
            now = time.time()
            self._revoke_expired(connection, now)
            self._cleanup(connection, now)
            connection.execute(
                """DELETE FROM projects WHERE deleted = 1 AND reserved_bytes = 0
                AND worker_active = 0 AND expires_at < ? AND NOT EXISTS
                (SELECT 1 FROM leases WHERE leases.project_id = projects.project_id)""",
                (now - self.settings.ttl_seconds,),
            )
            known = {row[0] for row in connection.execute("SELECT project_id FROM projects")}
            for entry in self._projects_dir.iterdir():
                if self._identifier.fullmatch(entry.name) and entry.name not in known:
                    self._remove_project(entry.name)
            connection.execute("DELETE FROM rate_limits WHERE expires_at <= ?", (now,))

    def archive_path(self, project_id: str) -> Path:
        return self._project_directory(project_id) / "source.zip"

    def report_path(self, project_id: str) -> Path:
        return self._project_directory(project_id) / "report.json"

    def work_directory(self, project_id: str) -> Path:
        path = self._project_directory(project_id) / "work"
        if path.is_symlink():
            raise ServiceError(500, "STORAGE_UNAVAILABLE", "Invalid project storage")
        return path

    def read_report(self, record: JobRecord) -> AnalysisReport:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ? AND owner_id = ?",
                (record.project_id, record.owner_id),
            ).fetchone()
            current = self._accessible(row)
            if current.status != "succeeded":
                raise ServiceError(409, "PROJECT_NOT_READY", "Analysis has not succeeded")
            return self._read_report_file(current)

    def _read_report_file(self, record: JobRecord) -> AnalysisReport:
        path = self.report_path(record.project_id)
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            )
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise OSError("Invalid report file")
                payload = stream.read(self.settings.max_report_bytes + 1)
            if len(payload) > self.settings.max_report_bytes:
                raise InvalidWorkerReport("Report exceeds its byte limit")
            return self._codec.decode(json.loads(payload))
        except (OSError, ValueError, TypeError, RecursionError):
            raise ServiceError(
                500, "REPORT_UNAVAILABLE", "Stored analysis report is unavailable"
            ) from None

    @contextmanager
    def operation(
        self, owner_id: str, project_id: str, *, lease_seconds: int | None = None
    ) -> Iterator[JobRecord]:
        self._validate_owner(owner_id)
        self._validate_identifier(project_id)
        token = uuid.uuid4().hex
        if lease_seconds is not None and (type(lease_seconds) is not int or lease_seconds <= 0):
            raise ValueError("Operation lease must be a positive integer")
        lease_seconds = min(
            self.lease_seconds,
            self.settings.operation_lease_seconds,
            lease_seconds or self.lease_seconds,
        )
        with self._transaction() as connection:
            now = time.time()
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ? AND owner_id = ?",
                (project_id, owner_id),
            ).fetchone()
            record = self._accessible(row)
            if record.status != "succeeded":
                raise ServiceError(409, "PROJECT_NOT_READY", "Analysis has not succeeded")
            connection.execute("DELETE FROM leases WHERE expires_at <= ?", (now,))
            if connection.execute(
                "SELECT 1 FROM leases WHERE project_id = ?", (project_id,)
            ).fetchone():
                raise ServiceError(409, "PROJECT_BUSY", "A project operation is active")
            if self._active_count(connection, now) >= self.settings.max_running:
                raise ServiceError(503, "WORKER_BUSY", "All processing slots are occupied")
            connection.execute(
                "INSERT INTO leases (token, project_id, expires_at) VALUES (?, ?, ?)",
                (
                    token,
                    project_id,
                    now + lease_seconds,
                ),
            )
        stop, lost = threading.Event(), threading.Event()
        heartbeat = threading.Thread(
            target=self._maintain_lease,
            args=(token, lease_seconds, stop, lost),
            name=f"nishtyak-lease-{token}",
            daemon=True,
        )
        try:
            heartbeat.start()
        except RuntimeError:
            with self._transaction() as connection:
                connection.execute("DELETE FROM leases WHERE token = ?", (token,))
                self._cleanup(connection, time.time())
            raise ServiceError(
                503, "OPERATION_UNAVAILABLE", "The project operation could not start"
            ) from None
        failed = True
        try:
            yield record
            failed = False
        finally:
            stop.set()
            heartbeat.join(timeout=6)
            if heartbeat.is_alive():
                lost.set()
            with self._transaction() as connection:
                lease = connection.execute(
                    "SELECT expires_at FROM leases WHERE token = ?", (token,)
                ).fetchone()
                if lease is None or lease[0] <= time.time():
                    lost.set()
                connection.execute("DELETE FROM leases WHERE token = ?", (token,))
                self._revoke_expired(connection, time.time())
                self._cleanup(connection, time.time())
            if not failed and lost.is_set():
                raise ServiceError(
                    503, "OPERATION_LEASE_LOST", "The project operation lease was lost"
                )

    def _maintain_lease(
        self,
        token: str,
        lease_seconds: int,
        stop: threading.Event,
        lost: threading.Event,
    ) -> None:
        # A live operation renews its lease; an API process crash leaves at most 180s
        # before recovery. Revocation must not remove files beneath a live operation.
        while not stop.wait(min(5, lease_seconds / 3)):
            try:
                with self._transaction() as connection:
                    now = time.time()
                    updated = connection.execute(
                        """UPDATE leases SET expires_at = ?
                        WHERE token = ? AND expires_at > ?""",
                        (now + lease_seconds, token, now),
                    ).rowcount
                if not updated:
                    lost.set()
                    return
            except (sqlite3.Error, OSError):
                # A transient database lock can recover before the existing deadline.
                continue
            except (ValueError, ServiceError):
                lost.set()
                return

    def mark_heartbeat(self, ready: bool = True) -> None:
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO metadata (key, value) VALUES ('dispatcher_heartbeat', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (time.time() if ready else 0,),
            )

    def dispatcher_ready(self, max_age: float = 15) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'dispatcher_heartbeat'"
            ).fetchone()
        return row is not None and float(row[0]) > 0 and 0 <= time.time() - float(row[0]) <= max_age

    def consume_rate(self, owner: str, action: str, limit: int, window: int = 60) -> None:
        self._validate_owner(owner)
        if not action or len(action) > 128 or limit < 1 or window < 1:
            raise ValueError("Invalid rate limit")
        now = time.time()
        bucket = int(now // window) * window
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT used FROM rate_limits
                WHERE owner_id = ? AND action = ? AND window_seconds = ?
                AND bucket = ?""",
                (owner, action, window, bucket),
            ).fetchone()
            if row is not None and row[0] >= limit:
                raise ServiceError(429, "RATE_LIMIT", "Request rate limit exceeded")
            connection.execute(
                """INSERT INTO rate_limits
                (owner_id, action, window_seconds, bucket, used, expires_at)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(owner_id, action, window_seconds, bucket)
                DO UPDATE SET used = used + 1""",
                (owner, action, window, bucket, bucket + window),
            )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    options_json TEXT NOT NULL,
                    reserved_bytes INTEGER NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    worker_active INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS project_owner ON projects(owner_id);
                CREATE INDEX IF NOT EXISTS project_queue ON projects(status, created_at);
                CREATE TABLE IF NOT EXISTS leases (
                    token TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL UNIQUE REFERENCES projects(project_id),
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY, value REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_documents (
                    project_id TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
                    analysis_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    decisions_json TEXT,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rate_limits (
                    owner_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    window_seconds INTEGER NOT NULL,
                    bucket INTEGER NOT NULL,
                    used INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY(owner_id, action, window_seconds, bucket)
                );
                CREATE TABLE IF NOT EXISTS automatic_reviews (
                    project_id TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
                    analysis_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS automatic_resolutions (
                    project_id TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS automation_flows (
                    project_id TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            try:
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(projects)")}
                if "author_environment_json" not in columns:
                    connection.execute(
                        "ALTER TABLE projects ADD COLUMN author_environment_json TEXT"
                    )
                if "automatic_review_eligible" not in columns:
                    connection.execute(
                        "ALTER TABLE projects ADD COLUMN automatic_review_eligible "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
                if "review_ready_at" not in columns:
                    connection.execute("ALTER TABLE projects ADD COLUMN review_ready_at REAL")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        self._secure_database_files()

    def _review_eligible(self) -> bool:
        return self.settings.automatic_review and bool(
            os.environ.get("DEEPSEEK_API_KEY") and os.environ.get("DEEPSEEK_MODEL")
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self.data_dir.is_symlink() or self._projects_dir.is_symlink():
            raise ServiceError(500, "STORAGE_UNAVAILABLE", "Invalid database storage")
        self._secure_database_files()
        connection = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            self._secure_database_files()
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _check_admission(
        self,
        connection: sqlite3.Connection,
        owner: str,
        reservation: int,
        now: float,
        exclude_project_id: str | None = None,
    ) -> None:
        queued = connection.execute(
            """SELECT COUNT(*) FROM projects WHERE status = 'queued' AND deleted = 0
            AND (? IS NULL OR project_id != ?)""",
            (exclude_project_id, exclude_project_id),
        ).fetchone()[0]
        if queued >= self.settings.max_queued:
            raise ServiceError(503, "QUEUE_FULL", "The analysis queue is full")
        active = connection.execute(
            """SELECT COUNT(*) FROM projects WHERE owner_id = ? AND deleted = 0
            AND expires_at > ? AND status IN ('queued', 'running')
            AND (? IS NULL OR project_id != ?)""",
            (owner, now, exclude_project_id, exclude_project_id),
        ).fetchone()[0]
        if active >= self.settings.max_owner_active:
            raise ServiceError(429, "OWNER_JOB_LIMIT", "Too many active owner jobs")
        total, owner_total = connection.execute(
            """SELECT COALESCE(SUM(reserved_bytes), 0),
            COALESCE(SUM(CASE WHEN owner_id = ? THEN reserved_bytes ELSE 0 END), 0)
            FROM projects""",
            (owner,),
        ).fetchone()
        if total + reservation > self.settings.max_storage_bytes:
            raise ServiceError(507, "STORAGE_LIMIT", "Service storage quota exceeded")
        if owner_total + reservation > self.settings.max_owner_storage_bytes:
            raise ServiceError(429, "OWNER_STORAGE_LIMIT", "Owner storage quota exceeded")

    @staticmethod
    def _active_count(connection: sqlite3.Connection, now: float) -> int:
        workers = connection.execute(
            "SELECT COUNT(*) FROM projects WHERE worker_active = 1"
        ).fetchone()[0]
        leases = connection.execute(
            "SELECT COUNT(*) FROM leases WHERE expires_at > ?", (now,)
        ).fetchone()[0]
        return workers + leases

    @staticmethod
    def _revoke_expired(connection: sqlite3.Connection, now: float) -> None:
        connection.execute("UPDATE projects SET deleted = 1 WHERE expires_at <= ?", (now,))

    def _cleanup(self, connection: sqlite3.Connection, now: float) -> None:
        connection.execute("DELETE FROM leases WHERE expires_at <= ?", (now,))
        connection.execute(
            """DELETE FROM decision_documents WHERE project_id IN
            (SELECT project_id FROM projects WHERE deleted = 1)"""
        )
        rows = connection.execute(
            """SELECT project_id FROM projects WHERE deleted = 1 AND worker_active = 0
            AND reserved_bytes > 0 AND NOT EXISTS
            (SELECT 1 FROM leases WHERE leases.project_id = projects.project_id)"""
        ).fetchall()
        for row in rows:
            project_id = row[0]
            if self._remove_project(project_id):
                connection.execute(
                    """UPDATE projects SET reserved_bytes = 0, author_environment_json = NULL,
                    status = CASE WHEN status = 'queued' THEN 'failed' ELSE status END
                    WHERE project_id = ?""",
                    (project_id,),
                )

    def _project_directory(self, project_id: str) -> Path:
        self._validate_identifier(project_id)
        root = self._projects_dir / project_id
        if self.data_dir.is_symlink() or self._projects_dir.is_symlink() or root.is_symlink():
            raise ServiceError(500, "STORAGE_UNAVAILABLE", "Invalid project storage")
        return root

    def _remove_project(self, project_id: str) -> bool:
        try:
            root = self._project_directory(project_id)
            shutil.rmtree(root)
            self._sync_directory(self._projects_dir)
            return True
        except FileNotFoundError:
            return True
        except (OSError, ServiceError):
            return False

    @staticmethod
    def _write_file(path: Path, payload: bytes, replace: bool = False) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        target = temporary if replace else path
        try:
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o660,
            )
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o660)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                os.replace(temporary, path)
            SQLiteJobRepository._sync_directory(path.parent)
        finally:
            if replace:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _ensure_private_directory(path: Path) -> None:
        for component in (path, *path.parents):
            if component.is_symlink():
                raise ValueError("Storage directories must not contain symbolic links")
        exists = path.exists()
        path.mkdir(parents=True, exist_ok=True, mode=0o770)
        if not path.is_dir():
            raise ValueError("Storage root must be a directory")
        path.chmod(0o770)
        if not exists:
            SQLiteJobRepository._sync_directory(path.parent)

    def _secure_database_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.database_path}{suffix}")
            try:
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise ValueError("Database files must be regular files")
                path.chmod(0o660)
            except FileNotFoundError:
                pass

    @staticmethod
    def _sync_directory(path: Path) -> None:
        if os.name != "posix":
            return
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _validate_identifier(cls, value: str) -> None:
        if not isinstance(value, str) or not cls._identifier.fullmatch(value):
            raise ServiceError(404, "RESOURCE_NOT_FOUND", "Resource not found")

    @staticmethod
    def _validate_owner(owner: str) -> None:
        if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", owner):
            raise ServiceError(400, "OWNER_INVALID", "Invalid owner identity")

    def _accessible(self, row: sqlite3.Row | None) -> JobRecord:
        if row is None or row["deleted"] or row["expires_at"] <= time.time():
            raise ServiceError(404, "RESOURCE_NOT_FOUND", "Resource not found")
        return self._record(row)

    def _record(self, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            project_id=row["project_id"],
            job_id=row["job_id"],
            owner_id=row["owner_id"],
            status=row["status"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            options=self._codec.decode_options(json.loads(row["options_json"])),
            error_code=row["error_code"],
            deleted=bool(row["deleted"]),
            automatic_review_eligible=bool(row["automatic_review_eligible"]),
            review_ready_at=row["review_ready_at"],
            author_environment=AuthorEnvironmentSnapshot.from_dict(
                json.loads(row["author_environment_json"])
            )
            if row["author_environment_json"] is not None
            else None,
        )
