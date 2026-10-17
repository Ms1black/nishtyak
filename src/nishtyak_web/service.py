from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable

from nishtyak_client import LogicClient, LogicClientError
from nishtyak_core.archives import ArchiveReader
from nishtyak_core.redaction import TextRedactor
from nishtyak_core.snapshot import SnapshotBuilder

from .auth import WorkspaceIdentity
from .config import WebSettings
from .mapping import (
    analysis_status,
    analysis_to_web,
    decisions_to_core,
    document_to_web,
    options_to_core,
    plan_to_web,
    timestamp,
)
from .storage import ProjectStore


class BrowserService:
    def __init__(
        self, settings: WebSettings, client_factory: Callable[[str], LogicClient] | None = None
    ):
        self.settings = settings
        self.store = ProjectStore(settings)
        self.client_factory = client_factory or (
            lambda scope: LogicClient(settings.logic_url, settings.service_token, scope, timeout=60)
        )

    def list(self, identity: WorkspaceIdentity, team_id: str) -> list[dict]:
        self._team(identity, team_id)
        result = []
        for record in self.store.list(identity.scope):
            try:
                if json.loads(record["job"])["status"] in {"queued", "running"}:
                    record = self._refresh(identity, record)
                result.append(self._project(record))
            except LogicClientError as error:
                if error.status != 404:
                    raise
                try:
                    self.store.delete(identity.scope, record["id"])
                except LogicClientError as delete_error:
                    if delete_error.status != 404:
                        raise
        return result

    def upload(
        self, identity: WorkspaceIdentity, team_id: str, filename: str, content: bytes
    ) -> dict:
        self._team(identity, team_id)
        if (
            not isinstance(filename, str)
            or len(filename) > 200
            or "/" in filename
            or "\\" in filename
            or any(ord(char) < 32 for char in filename)
        ):
            raise LogicClientError(422, "INVALID_FILENAME", "Expected a plain .py or .zip filename")
        if not 0 < len(content) <= 10 * 1024**2:
            raise LogicClientError(413, "UPLOAD_LIMIT", "Upload must contain at most 10 MiB")
        extension = filename.rsplit(".", 1)[-1].lower()
        if extension == "py":
            SnapshotBuilder.normalize_path(filename)
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(filename, content)
            source = stream.getvalue()
        elif extension == "zip":
            source = content
        else:
            raise LogicClientError(422, "UPLOAD_TYPE", "Only .py and .zip uploads are supported")
        ArchiveReader().from_bytes(source)
        self.store.cleanup()
        client = self.client_factory(identity.scope)
        submission = client.submit(source, options={"python_version_auto": True})
        try:
            record = self.store.create(
                identity.scope,
                submission,
                TextRedactor().redact(filename.rsplit(".", 1)[0]),
                extension,
                len(content),
                source,
            )
        except Exception:
            try:
                client.delete(submission["project_id"])
            except LogicClientError:
                pass
            raise
        return self._project(record)

    def analysis(self, identity: WorkspaceIdentity, project_id: str) -> dict:
        record = self._refresh(identity, self.store.get(identity.scope, project_id))
        job = json.loads(record["job"])
        preparation = None
        automation = job.get("automation")
        preparing = (
            automation.get("phase") in {"static", "assisting", "validating", "finalizing"}
            if automation is not None
            else any(
                (job.get(key) or {}).get("status") in {"pending", "running"}
                for key in ("automatic_review", "automatic_resolution")
            )
        )
        if job["status"] == "succeeded" and not preparing:
            preparation = self.client_factory(identity.scope).preparation(project_id)
        return analysis_to_web(project_id, job, preparation)

    def capabilities(self, identity: WorkspaceIdentity) -> dict:
        capabilities = self.client_factory(identity.scope).capabilities()
        value = capabilities["automatic_review"]
        resolution = capabilities.get("automatic_resolution") or {"enabled": False}
        automation = capabilities.get("automation") or {"enabled": False}
        return {
            "automaticReview": {
                "enabled": value["enabled"],
                "provider": value["provider"],
                "budgetSeconds": value["budget_seconds"],
                "features": value.get("features", []),
            },
            "automaticResolution": {
                "enabled": resolution["enabled"],
                "budgetSeconds": resolution.get("budget_seconds", 20),
                "defaultPlatform": resolution.get("default_platform", "linux-x86_64"),
            },
            "automation": {
                "enabled": automation["enabled"],
                "maxAttempts": automation.get("max_attempts", 2),
                "maxHumanRounds": automation.get("max_human_rounds", 1),
                "maxFinalAttempts": automation.get("max_final_attempts", 1),
            },
        }

    def file(self, identity: WorkspaceIdentity, project_id: str, path: str) -> dict:
        with self.store.locked(project_id):
            return self._file(identity, project_id, path)

    def _file(self, identity: WorkspaceIdentity, project_id: str, path: str) -> dict:
        path = SnapshotBuilder.normalize_path(path)
        record = self._refresh(identity, self.store.get(identity.scope, project_id))
        report = self._report(record)
        allowed = {item["path"]: item for item in report["files"]}
        if path not in allowed:
            raise LogicClientError(404, "RESOURCE_NOT_FOUND", "Project file was not found")
        if any(
            item["code"] == "SECRET_DETECTED" and item.get("path") == path
            for item in report["findings"]
        ):
            return {"kind": "hidden"}
        if re.search(
            r"(^|/)(\.env(?:\..*)?|id_rsa|id_ed25519|credentials(?:\..*)?)$", path, re.I
        ) or path.lower().endswith((".pem", ".key", ".p12", ".pfx")):
            return {"kind": "hidden"}
        snapshot = ArchiveReader().from_path(self.store.root / project_id / "source.zip")
        if snapshot.fingerprint != report["fingerprint"]:
            raise LogicClientError(409, "SNAPSHOT_CHANGED", "Project source has changed")
        file = snapshot.get(path)
        if file is None or file.info.sha256 != allowed[path]["sha256"]:
            raise LogicClientError(409, "SNAPSHOT_CHANGED", "Project file has changed")
        try:
            text = file.content.decode("utf-8")
        except UnicodeDecodeError:
            return {"kind": "binary"}
        if "\x00" in text:
            return {"kind": "binary"}
        text = TextRedactor().redact(text)
        return {"kind": "text", "content": text[:65536], "truncated": len(text) > 65536}

    def decisions(self, identity: WorkspaceIdentity, project_id: str) -> dict:
        self.store.get(identity.scope, project_id)
        return document_to_web(self.client_factory(identity.scope).decisions(project_id))

    def save_decisions(
        self, identity: WorkspaceIdentity, project_id: str, decisions: dict, expected_revision: int
    ) -> dict:
        with self.store.locked(project_id):
            self.store.get(identity.scope, project_id)
            result = self.client_factory(identity.scope).save_decisions(
                project_id, decisions_to_core(decisions), expected_revision
            )
            self.store.invalidate(identity.scope, project_id)
            return document_to_web(result)

    def submit_clarifications(
        self,
        identity: WorkspaceIdentity,
        project_id: str,
        decisions: dict,
        expected_revision: int,
        questionnaire_id: str,
        notes: str = "",
    ) -> dict:
        with self.store.locked(project_id):
            self.store.get(identity.scope, project_id)
            result = self.client_factory(identity.scope).submit_clarifications(
                project_id,
                questionnaire_id,
                decisions_to_core(decisions),
                expected_revision,
                notes,
            )
            self.store.invalidate(identity.scope, project_id)
            return document_to_web(result)

    def plan(self, identity: WorkspaceIdentity, project_id: str, decisions: dict | None) -> dict:
        self.store.get(identity.scope, project_id)
        value = self.client_factory(identity.scope).plan(
            project_id, decisions_to_core(decisions) if decisions is not None else None
        )
        return plan_to_web(value)

    def preparation(self, identity: WorkspaceIdentity, project_id: str) -> dict:
        self.store.get(identity.scope, project_id)
        value = self.client_factory(identity.scope).preparation(project_id)
        return {
            "projectId": project_id,
            "plan": plan_to_web(value["plan"]),
            "launch": value["launch"],
        }

    def reanalyze(
        self,
        identity: WorkspaceIdentity,
        project_id: str,
        options: dict,
        author_environment: dict | None = None,
    ) -> dict:
        with self.store.locked(project_id):
            self.store.get(identity.scope, project_id)
            value = self.client_factory(identity.scope).reanalyze(
                project_id, options_to_core(options), author_environment
            )
            self.store.invalidate(identity.scope, project_id)
            return self._project(self.store.update_job(identity.scope, project_id, value))

    def resolve(
        self,
        identity: WorkspaceIdentity,
        project_id: str,
        python_version: str,
        platform: str,
        decisions: dict | None,
    ) -> dict:
        with self.store.locked(project_id):
            self.store.get(identity.scope, project_id)
            result = self.client_factory(identity.scope).resolve(
                project_id,
                python_version,
                platform,
                decisions_to_core(decisions) if decisions is not None else None,
            )
            self.store.invalidate(identity.scope, project_id)
            return result

    def forward(self, identity: WorkspaceIdentity, project_id: str, method: str, **kwargs) -> dict:
        self.store.get(identity.scope, project_id)
        return getattr(self.client_factory(identity.scope), method)(project_id, **kwargs)

    def build(
        self,
        identity: WorkspaceIdentity,
        project_id: str,
        include_dockerfile: bool = False,
        lock_id: str | None = None,
    ) -> dict:
        with self.store.locked(project_id):
            record = self._refresh(identity, self.store.get(identity.scope, project_id))
            report = self._report(record)
            client = self.client_factory(identity.scope)
            revision = client.decisions(project_id)["revision"]
            content = client.bundle(
                project_id, include_dockerfile=include_dockerfile, lock_id=lock_id
            )
            if client.decisions(project_id)["revision"] != revision:
                raise LogicClientError(
                    409, "DECISIONS_REVISION_CONFLICT", "Decisions changed during export"
                )
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                files = archive.namelist()
                if "dependency-lock.json" in files:
                    lock_document = json.loads(archive.read("dependency-lock.json"))
                    included_lock = lock_document.get("id")
                    if not isinstance(included_lock, str) or not re.fullmatch(
                        r"[a-f0-9]{64}", included_lock
                    ):
                        raise LogicClientError(
                            502, "RESPONSE_INVALID", "The archive contains an invalid lock"
                        )
                    lock_id = included_lock
            tag = json.dumps(
                {
                    "analysis": report["analysis_id"],
                    "revision": revision,
                    "lock": lock_id,
                    "docker": include_dockerfile,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "files": files,
                },
                sort_keys=True,
            )
            self.store.save_archive(identity.scope, project_id, tag, content)
            return self._project(self.store.get(identity.scope, project_id))

    def download(self, identity: WorkspaceIdentity, project_id: str) -> bytes:
        with self.store.locked(project_id):
            record = self._refresh(identity, self.store.get(identity.scope, project_id))
            if record["archive_tag"] is None:
                raise LogicClientError(409, "ARCHIVE_NOT_BUILT", "Build the archive first")
            tag = json.loads(record["archive_tag"])
            client = self.client_factory(identity.scope)
            if (
                tag["analysis"] != self._report(record)["analysis_id"]
                or tag["revision"] != client.decisions(project_id)["revision"]
            ):
                self.store.invalidate(identity.scope, project_id)
                raise LogicClientError(
                    409, "ARCHIVE_STALE", "Build an archive from the current decisions"
                )
            if tag["lock"] is not None and client.lock(project_id)["id"] != tag["lock"]:
                self.store.invalidate(identity.scope, project_id)
                raise LogicClientError(
                    409, "ARCHIVE_STALE", "Dependency lock changed; rebuild the archive"
                )
            content = (self.store.root / project_id / "bundle.zip").read_bytes()
            if len(content) != record["archive_size"] or hashlib.sha256(
                content
            ).hexdigest() != tag.get("sha256"):
                raise LogicClientError(409, "ARCHIVE_STALE", "Stored archive has changed")
            return content

    def delete(self, identity: WorkspaceIdentity, project_id: str) -> None:
        with self.store.locked(project_id):
            self.store.get(identity.scope, project_id)
            self.client_factory(identity.scope).delete(project_id)
            self.store.delete(identity.scope, project_id)

    def _refresh(self, identity: WorkspaceIdentity, record: dict) -> dict:
        job = self.client_factory(identity.scope).job(record["job_id"])
        return self.store.update_job(identity.scope, record["id"], job)

    @staticmethod
    def _report(record: dict) -> dict:
        job = json.loads(record["job"])
        if job["status"] != "succeeded":
            raise LogicClientError(409, "ANALYSIS_NOT_READY", "Wait for successful analysis")
        return job["analysis"]

    @staticmethod
    def _team(identity: WorkspaceIdentity, team_id: str) -> None:
        if team_id != identity.workspace:
            raise LogicClientError(404, "RESOURCE_NOT_FOUND", "Workspace was not found")

    @staticmethod
    def _project(record: dict) -> dict:
        job = json.loads(record["job"])
        report = job.get("analysis")
        status = analysis_status(report, job["status"])
        archive = {"status": "unavailable" if status in {"analyzing", "failed"} else "not_built"}
        if record["archive_tag"] is not None:
            archive = {"status": "ready", "sizeBytes": record["archive_size"]}
            files = json.loads(record["archive_tag"]).get("files")
            if files is not None:
                archive["files"] = TextRedactor().redact_value(files)
        return {
            "id": record["id"],
            "name": record["name"],
            "sourceType": record["source_type"],
            "sizeBytes": record["source_size"],
            "filesCount": len((report or {}).get("files", [])),
            "analysis": status,
            "archive": archive,
            "updatedAt": timestamp(record["updated_at"]),
        }
