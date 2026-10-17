from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager

from nishtyak_client import LogicClientError

from .config import WebSettings


class ProjectStore:
    """Small private browser index; project ownership remains workspace scoped."""

    def __init__(self, settings: WebSettings):
        self.settings = settings
        self.root = settings.data_dir
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.root.chmod(0o700)
        self._mutation = threading.RLock()
        self._locks: dict[str, tuple[threading.RLock, int]] = {}
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, job_id TEXT NOT NULL,
                name TEXT NOT NULL, source_type TEXT NOT NULL, source_size INTEGER NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL NOT NULL,
                job TEXT NOT NULL, archive_tag TEXT, archive_size INTEGER NOT NULL DEFAULT 0
            )""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(projects)")}
            if "source_zip_size" not in columns:
                db.execute(
                    "ALTER TABLE projects ADD COLUMN source_zip_size INTEGER NOT NULL DEFAULT 0"
                )
                db.execute("UPDATE projects SET source_zip_size=source_size")
            db.execute("""CREATE TABLE IF NOT EXISTS verifications (
                project_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                analysis_id TEXT NOT NULL, archive_hash TEXT NOT NULL,
                document TEXT NOT NULL
            )""")
            db.execute("UPDATE projects SET expires_at=0")
        (self.root / "web.sqlite3").chmod(0o600)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.root / "web.sqlite3", timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def locked(self, project_id: str):
        with self._mutation:
            lock, holders = self._locks.get(project_id, (threading.RLock(), 0))
            self._locks[project_id] = (lock, holders + 1)
        try:
            with lock:
                yield
        finally:
            with self._mutation:
                lock, holders = self._locks[project_id]
                if holders == 1:
                    self._locks.pop(project_id)
                else:
                    self._locks[project_id] = (lock, holders - 1)

    def workspace_locked(self, scope: str):
        return self.locked("workspace:" + scope)

    def cleanup(self) -> None:
        """Projects are retained until their owner deletes them."""

    def list(self, scope: str) -> list[dict]:
        self.cleanup()
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM projects WHERE scope=? ORDER BY created_at DESC",
                    (scope,),
                )
            ]

    def get(self, scope: str, project_id: str) -> dict:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM projects WHERE id=? AND scope=?",
                (project_id, scope),
            ).fetchone()
        if row is None:
            raise LogicClientError(404, "RESOURCE_NOT_FOUND", "Project was not found")
        return dict(row)

    def scope_for(self, project_id: str) -> str:
        with self.connection() as db:
            row = db.execute(
                "SELECT scope FROM projects WHERE id=?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise LogicClientError(404, "RESOURCE_NOT_FOUND", "Project was not found")
        return row[0]

    def create(
        self,
        scope: str,
        submission: dict,
        name: str,
        source_type: str,
        source_size: int,
        archive: bytes,
    ) -> dict:
        project_id = submission["project_id"]
        now = time.time()
        with self._mutation, self.connection() as db:
            used = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(source_zip_size+archive_size),0) "
                "FROM projects WHERE scope=?",
                (scope,),
            ).fetchone()
            total = db.execute(
                "SELECT COALESCE(SUM(source_zip_size+archive_size),0) FROM projects"
            ).fetchone()[0]
            if (
                used[0] >= self.settings.max_workspace_projects
                or used[1] + len(archive) > self.settings.max_workspace_bytes
                or total + len(archive) > self.settings.max_storage_bytes
            ):
                raise LogicClientError(
                    429, "STORAGE_LIMIT", "Gateway workspace storage limit reached"
                )
            directory = self.root / project_id
            directory.mkdir(mode=0o700)
            self._write(directory / "source.zip", archive)
            try:
                db.execute(
                    "INSERT INTO projects(id,scope,job_id,name,source_type,source_size,"
                    "created_at,updated_at,expires_at,job,source_zip_size) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        project_id,
                        scope,
                        submission["job_id"],
                        name,
                        source_type,
                        source_size,
                        now,
                        now,
                        0,
                        json.dumps(submission),
                        len(archive),
                    ),
                )
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise
        return self.get(scope, project_id)

    def update_job(self, scope: str, project_id: str, job: dict) -> dict:
        with self.connection() as db:
            db.execute(
                "UPDATE projects SET job_id=?,job=?,expires_at=0 WHERE id=? AND scope=?",
                (
                    job["job_id"],
                    json.dumps(job),
                    project_id,
                    scope,
                ),
            )
        return self.get(scope, project_id)

    @contextmanager
    def source_change(self, scope: str, project_id: str, content: bytes):
        with self._mutation:
            record = self.get(scope, project_id)
            with self.connection() as db:
                own = db.execute(
                    "SELECT COALESCE(SUM(source_zip_size+archive_size),0) "
                    "FROM projects WHERE scope=?",
                    (scope,),
                ).fetchone()[0]
                total = db.execute(
                    "SELECT COALESCE(SUM(source_zip_size+archive_size),0) FROM projects"
                ).fetchone()[0]
            delta = len(content) - record["source_zip_size"]
            if (
                own + delta > self.settings.max_workspace_bytes
                or total + delta > self.settings.max_storage_bytes
            ):
                raise LogicClientError(429, "STORAGE_LIMIT", "Gateway source storage limit reached")
            yield

    def replace_source(self, scope, project_id, content, job):
        with self.connection() as db:
            self._write(self.root / project_id / "source.zip", content)
            db.execute(
                """UPDATE projects SET job_id=?,job=?,source_type='zip',source_size=?,
                source_zip_size=?,archive_tag=NULL,archive_size=0,updated_at=?
                WHERE id=? AND scope=?""",
                (
                    job["job_id"],
                    json.dumps(job),
                    len(content),
                    len(content),
                    time.time(),
                    project_id,
                    scope,
                ),
            )
        (self.root / project_id / "bundle.zip").unlink(missing_ok=True)
        return self.get(scope, project_id)

    def invalidate(self, scope: str, project_id: str) -> None:
        self.get(scope, project_id)
        with self.connection() as db:
            db.execute(
                "UPDATE projects SET archive_tag=NULL,archive_size=0,updated_at=? "
                "WHERE id=? AND scope=?",
                (time.time(), project_id, scope),
            )
        (self.root / project_id / "bundle.zip").unlink(missing_ok=True)

    def save_archive(self, scope: str, project_id: str, tag: str, content: bytes) -> None:
        with self._mutation, self.connection() as db:
            record = self.get(scope, project_id)
            own = db.execute(
                "SELECT COALESCE(SUM(source_zip_size+archive_size),0) FROM projects WHERE scope=?",
                (scope,),
            ).fetchone()[0]
            total = db.execute(
                "SELECT COALESCE(SUM(source_zip_size+archive_size),0) FROM projects"
            ).fetchone()[0]
            delta = len(content) - record["archive_size"]
            if (
                own + delta > self.settings.max_workspace_bytes
                or total + delta > self.settings.max_storage_bytes
            ):
                raise LogicClientError(
                    429, "STORAGE_LIMIT", "Gateway archive storage limit reached"
                )
            self._write(self.root / project_id / "bundle.zip", content)
            db.execute(
                "UPDATE projects SET archive_tag=?,archive_size=?,updated_at=? "
                "WHERE id=? AND scope=?",
                (tag, len(content), time.time(), project_id, scope),
            )

    def delete(self, scope: str, project_id: str) -> None:
        self.get(scope, project_id)
        with self._mutation, self.connection() as db:
            try:
                shutil.rmtree(self.root / project_id)
            except FileNotFoundError:
                pass
            except OSError:
                raise LogicClientError(
                    503, "STORAGE_UNAVAILABLE", "Project cleanup must be retried"
                ) from None
            db.execute("DELETE FROM projects WHERE id=? AND scope=?", (project_id, scope))
            db.execute("DELETE FROM verifications WHERE project_id=?", (project_id,))

    def verification(self, scope: str, project_id: str) -> dict | None:
        self.get(scope, project_id)
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM verifications WHERE project_id=?", (project_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def save_verification(
        self, scope, project_id, expected_revision, analysis_id, archive_hash, document
    ) -> None:
        self.get(scope, project_id)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM verifications WHERE project_id=?", (project_id,)
            ).fetchone()
            if (row[0] if row is not None else 0) != expected_revision:
                raise LogicClientError(
                    409, "VERIFICATION_REVISION_CONFLICT", "Verification was changed"
                )
            db.execute(
                """INSERT INTO verifications VALUES(?,?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision,
                analysis_id=excluded.analysis_id, archive_hash=excluded.archive_hash,
                document=excluded.document""",
                (
                    project_id,
                    expected_revision + 1,
                    analysis_id,
                    archive_hash,
                    json.dumps(document),
                ),
            )

    @staticmethod
    def _write(path, content: bytes) -> None:
        temporary = path.with_suffix(".tmp")
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        temporary.replace(path)
