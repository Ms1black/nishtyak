from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

from ..models import AnalysisReport, ProjectSnapshot, stable_id
from ..redaction import TextRedactor
from ..snapshot import SnapshotBuilder

MAX_CONTEXT_BYTES = 196_608
MAX_TOOL_BYTES = 6_144


class ProjectTextFilter(TextRedactor):
    _credentials = re.compile(r"\bBearer\s+[^\s,;]+|\bsk[-_][A-Za-z0-9_-]+", re.I)
    _assignments = re.compile(
        r"(?im)\b[\w-]*(?:token|password|secret|api[_-]?key|credential)[\w-]*"
        r"[\"']?\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)"
    )
    _urls = re.compile(r"(?:https?|ssh|git)://[^\s<>\"']+|git@[^\s<>\"']+", re.I)
    _opaque = re.compile(r"(?<![\w/])[A-Za-z0-9_+/=-]{24,}(?![\w/])")

    def redact(self, text: str) -> str:
        text = super().redact(text)
        text = self._credentials.sub("[REDACTED]", text)
        text = self._urls.sub("[URL REMOVED]", self._assignments.sub("[REDACTED]", text))
        return self._opaque.sub(self._replace_opaque, text)

    @staticmethod
    def _replace_opaque(match: re.Match[str]) -> str:
        value = match.group()
        return "[REDACTED]" if any(c.isdigit() for c in value) else value


@dataclass(frozen=True)
class SafeProjectContext:
    """Bounded, redacted virtual files; never gives the provider a filesystem handle."""

    analysis_id: str
    fingerprint: str
    files: tuple[dict[str, Any], ...]
    facts: dict[str, Any]
    launches: tuple[dict[str, Any], ...]
    omitted_files: int = 0

    @classmethod
    def from_project(cls, report: AnalysisReport, snapshot: ProjectSnapshot) -> SafeProjectContext:
        from ..launches import LaunchDetector

        SnapshotBuilder().validate(snapshot)
        if report.fingerprint != snapshot.fingerprint:
            raise ValueError("Project does not match analysis")
        expected = {item.path: item for item in report.files}
        if len(expected) != len(snapshot.files) or any(
            expected.get(item.path) != item.info for item in snapshot.files
        ):
            raise ValueError("Project files do not match analysis")
        redactor = ProjectTextFilter()
        blocked = {
            item.path for item in report.findings if item.code == "SECRET_DETECTED" and item.path
        }
        files = []
        text_budget = 65_536
        omitted = 0
        for file in snapshot.files:
            if not cls._allowed_path(file.path) or redactor.redact(file.path) != file.path:
                omitted += 1
                continue
            if len(files) >= 200:
                omitted += 1
                continue
            text: str | None = None
            truncated = True
            if file.path not in blocked and len(file.content) <= 1_000_000:
                try:
                    decoded = file.content.decode("utf-8")
                except UnicodeDecodeError:
                    decoded = ""
                if decoded and "\0" not in decoded:
                    # Redact before truncating so a boundary cannot hide half a credential.
                    safe = redactor.redact(decoded)
                    encoded = "\n".join(safe.splitlines()[:160]).encode("utf-8")
                    encoded = encoded[: min(16_384, text_budget)]
                    text = encoded.decode("utf-8", errors="ignore")
                    text_budget -= len(encoded)
                    truncated = text != safe
            files.append(
                {
                    "id": stable_id("file", file.path, file.info.sha256),
                    "path": file.path,
                    "size": len(file.content),
                    "text": text,
                    "truncated": truncated,
                }
            )
        facts: dict[str, Any] = {
            "imports": [],
            "dependencies": [],
            "findings": [],
            "declarations": [],
        }
        for item in report.imports[:200]:
            facts["imports"].append(
                {
                    "id": item.id,
                    "module": item.module,
                    "path": item.path,
                    "line": item.line,
                    "names": list(item.names[:20]),
                    "level": item.level,
                    "context": item.context,
                    "kind": item.kind,
                }
            )
        for item in report.dependencies[:200]:
            facts["dependencies"].append(
                {
                    "id": item.id,
                    "module": item.module,
                    "category": item.category,
                    "package": item.package,
                    "requirement": item.requirement,
                    "status": item.status,
                    "evidence_ids": list(item.evidence_ids[:20]),
                }
            )
        for item in report.findings[:100]:
            facts["findings"].append(
                {"id": item.id, "code": item.code, "path": item.path, "line": item.line}
            )
        for item in report.declarations[:100]:
            facts["declarations"].append(
                {
                    "id": item.id,
                    "requirement": item.requirement,
                    "path": item.path,
                    "line": item.line,
                    "group": item.group,
                }
            )
        facts["summary"] = {
            "project_root": report.layout.root if report.layout else ".",
            "source_roots": list(report.layout.source_roots) if report.layout else ["."],
            "python_requires": report.python_requires,
            "author_python_version": report.author_python_version,
            "status": report.status,
            "partial": any(
                len(getattr(report, name)) > limit
                for name, limit in (
                    ("imports", 200),
                    ("dependencies", 200),
                    ("findings", 100),
                    ("declarations", 100),
                )
            ),
        }
        assessment = LaunchDetector().detect(report)
        launches = tuple(
            {
                "id": candidate.id,
                "run_spec": asdict(candidate.run_spec),
                "basis": candidate.basis,
                "confidence": candidate.confidence,
                "reason": candidate.reason,
                "recommended": assessment.recommended is not None
                and candidate.id == assessment.recommended.id,
            }
            for candidate in assessment.candidates[:50]
        )
        document = {
            "schema_version": 1,
            "analysis_id": report.analysis_id,
            "fingerprint": report.fingerprint,
            "files": files,
            "facts": redactor.redact_value(facts),
            "launches": redactor.redact_value(launches),
            "omitted_files": omitted,
        }
        return cls.from_dict(document)

    @staticmethod
    def _allowed_path(path: str) -> bool:
        value = PurePosixPath(path)
        denied = {".git", ".venv", "__pycache__", "node_modules", ".ssh", ".aws"}
        if any(part.startswith(".env") or part.lower() in denied for part in value.parts):
            return False
        if any(word in value.name.lower() for word in ("secret", "credential", "id_rsa", "token")):
            return False
        return value.suffix.lower() in {".py", ".toml", ".yaml", ".yml", ".md", ".rst", ".cfg"} or (
            value.name.lower().startswith("requirements") and value.suffix.lower() == ".txt"
        )

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps({"schema_version": 1, **asdict(self)}, ensure_ascii=False))

    @classmethod
    def from_dict(cls, value: Any) -> SafeProjectContext:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_CONTEXT_BYTES:
            raise ValueError("Project advisor context too large")
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "analysis_id",
            "fingerprint",
            "files",
            "facts",
            "launches",
            "omitted_files",
        }:
            raise ValueError("Invalid project advisor context")
        if value["schema_version"] != 1 or type(value["schema_version"]) is not int:
            raise ValueError("Unsupported project advisor context")
        for key in ("analysis_id", "fingerprint"):
            if not isinstance(value[key], str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,128}", value[key]
            ):
                raise ValueError("Invalid project advisor identity")
        if not isinstance(value["files"], (tuple, list)) or len(value["files"]) > 200:
            raise ValueError("Invalid project advisor files")
        seen = set()
        for file in value["files"]:
            if not isinstance(file, dict) or set(file) != {
                "id",
                "path",
                "size",
                "text",
                "truncated",
            }:
                raise ValueError("Invalid project advisor file")
            if not isinstance(file["id"], str) or not re.fullmatch(r"[a-f0-9]{20}", file["id"]):
                raise ValueError("Invalid project advisor file identity")
            if file["id"] in seen:
                raise ValueError("Duplicate project advisor file identity")
            seen.add(file["id"])
            if not isinstance(file["path"], str) or len(file["path"]) > 1024:
                raise ValueError("Invalid project advisor file path")
            if SnapshotBuilder.normalize_path(file["path"]) != file[
                "path"
            ] or not cls._allowed_path(file["path"]):
                raise ValueError("Forbidden project advisor file path")
            if type(file["size"]) is not int or not 0 <= file["size"] <= 50_000_000:
                raise ValueError("Invalid project advisor file size")
            if type(file["truncated"]) is not bool or (
                file["text"] is not None
                and (
                    not isinstance(file["text"], str) or len(file["text"].encode("utf-8")) > 16_384
                )
            ):
                raise ValueError("Invalid project advisor file text")
        facts = value["facts"]
        if not isinstance(facts, dict) or set(facts) != {
            "imports",
            "dependencies",
            "findings",
            "declarations",
            "summary",
        }:
            raise ValueError("Invalid project advisor facts")
        for key in ("imports", "dependencies", "findings", "declarations"):
            if not isinstance(facts[key], list) or len(facts[key]) > 200:
                raise ValueError("Invalid project advisor facts")
            for item in facts[key]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item["id"])
                ):
                    raise ValueError("Invalid project advisor evidence")
        if not isinstance(facts["summary"], dict):
            raise ValueError("Invalid project advisor summary")
        if not isinstance(value["launches"], (tuple, list)) or len(value["launches"]) > 50:
            raise ValueError("Invalid project advisor launches")
        for item in value["launches"]:
            if not isinstance(item, dict) or set(item) != {
                "id",
                "run_spec",
                "basis",
                "confidence",
                "reason",
                "recommended",
            }:
                raise ValueError("Invalid project advisor launch")
            if not isinstance(item["id"], str) or not re.fullmatch(r"[a-f0-9]{20}", item["id"]):
                raise ValueError("Invalid project advisor launch identity")
            run = item["run_spec"]
            if (
                not isinstance(run, dict)
                or set(run) != {"target", "kind", "args", "working_directory"}
                or run["kind"] not in {"script", "module", "callable"}
            ):
                raise ValueError("Invalid project advisor launch target")
            if (
                not isinstance(run["target"], str)
                or not 0 < len(run["target"]) <= 1024
                or run["args"] not in ([], ())
            ):
                raise ValueError("Invalid project advisor launch target")
            if not isinstance(run["working_directory"], str) or (
                run["working_directory"] != "."
                and SnapshotBuilder.normalize_path(run["working_directory"])
                != run["working_directory"]
            ):
                raise ValueError("Invalid project advisor launch directory")
        if type(value["omitted_files"]) is not int or not 0 <= value["omitted_files"] <= 1000:
            raise ValueError("Invalid project advisor omitted count")
        # Apply redaction again after crossing a process boundary, including unknown fact fields.
        safe = ProjectTextFilter().redact_value(json.loads(raw))
        return cls(
            safe["analysis_id"],
            safe["fingerprint"],
            tuple(safe["files"]),
            safe["facts"],
            tuple(safe["launches"]),
            safe["omitted_files"],
        )

    @property
    def evidence_ids(self) -> set[str]:
        return (
            {file["id"] for file in self.files}
            | {
                item["id"]
                for key in ("imports", "dependencies", "findings", "declarations")
                for item in self.facts[key]
            }
            | {item["id"] for item in self.launches}
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()
