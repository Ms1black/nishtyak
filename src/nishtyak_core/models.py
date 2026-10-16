from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from .redaction import TextRedactor


def stable_id(*values: object) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


@dataclass(frozen=True)
class FileInfo:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class SourceFile:
    path: str
    content: bytes

    @property
    def info(self) -> FileInfo:
        return FileInfo(self.path, len(self.content), hashlib.sha256(self.content).hexdigest())


@dataclass(frozen=True)
class ProjectSnapshot:
    files: tuple[SourceFile, ...]
    fingerprint: str

    def get(self, path: str) -> SourceFile | None:
        return next((file for file in self.files if file.path == path), None)


@dataclass(frozen=True)
class AnalysisOptions:
    python_version: str = f"{sys.version_info.major}.{sys.version_info.minor}"
    source_roots: tuple[str, ...] = (".",)
    max_source_bytes: int = 1_000_000
    max_files: int = 1_000
    project_root: str | None = None


@dataclass(frozen=True)
class ProjectLayout:
    root: str = "."
    source_roots: tuple[str, ...] = (".",)
    root_candidates: tuple[str, ...] = (".",)
    ambiguous: bool = False


@dataclass(frozen=True)
class DependencyDeclaration:
    requirement: str
    path: str
    line: int | None = None
    group: str | None = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(self, "id", stable_id(asdict(self)))


@dataclass(frozen=True)
class DeclarationDecision:
    action: str = "keep"
    requirement: str | None = None


@dataclass(frozen=True)
class ImportObservation:
    module: str
    path: str
    line: int
    names: tuple[str, ...] = ()
    level: int = 0
    context: str = "runtime"
    kind: str = "import"
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(self, "id", stable_id(asdict(self)))


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    path: str | None = None
    line: int | None = None
    severity: str = "warning"
    evidence_ids: tuple[str, ...] = ()
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(self, "id", stable_id(asdict(self)))


@dataclass(frozen=True)
class DependencyCandidate:
    module: str
    category: str
    package: str | None = None
    requirement: str | None = None
    status: str = "inferred"
    evidence_ids: tuple[str, ...] = ()
    reason: str = ""
    context: str = "runtime"
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(self, "id", stable_id(asdict(self)))


@dataclass(frozen=True)
class AnalysisReport:
    analysis_id: str
    fingerprint: str
    options: AnalysisOptions
    files: tuple[FileInfo, ...]
    imports: tuple[ImportObservation, ...] = ()
    dependencies: tuple[DependencyCandidate, ...] = ()
    findings: tuple[Finding, ...] = ()
    status: str = "complete"
    declared_requirements: tuple[str, ...] = ()
    python_requires: str | None = None
    entrypoints: tuple[str, ...] = ()
    schema_version: int = 2
    layout: ProjectLayout | None = None
    declarations: tuple[DependencyDeclaration, ...] = ()
    optional_groups: tuple[str, ...] = ()
    author_environment_id: str | None = None
    author_python_version: str | None = None
    author_platform: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return TextRedactor().redact_value(asdict(self))


@dataclass(frozen=True)
class RunSpec:
    target: str
    kind: str = "script"
    args: tuple[str, ...] = ()
    working_directory: str = "."


@dataclass(frozen=True)
class UserDecisions:
    analysis_id: str
    packages: dict[str, str] = field(default_factory=dict)
    versions: dict[str, str] = field(default_factory=dict)
    excluded_modules: tuple[str, ...] = ()
    excluded_files: tuple[str, ...] = ()
    run_spec: RunSpec | None = None
    python_version: str | None = None
    description: str = ""
    input_description: str = ""
    expected_output: str = ""
    data_confirmed: bool = False
    selected_extras: tuple[str, ...] = ()
    declaration_actions: dict[str, DeclarationDecision] = field(default_factory=dict)


@dataclass(frozen=True)
class PackagePlan:
    report: AnalysisReport
    requirements: tuple[str, ...]
    run_spec: RunSpec | None
    python_version: str | None
    files: tuple[FileInfo, ...]
    excluded_files: tuple[str, ...]
    findings: tuple[Finding, ...]
    state: str
    description: str = ""
    input_description: str = ""
    expected_output: str = ""
    decisions: UserDecisions | None = None

    @property
    def analysis_id(self) -> str:
        return self.report.analysis_id

    @property
    def fingerprint(self) -> str:
        return self.report.fingerprint

    def to_dict(self) -> dict[str, Any]:
        return TextRedactor().redact_value(asdict(self))


@dataclass(frozen=True)
class GeneratedFile:
    path: str
    content: bytes


@dataclass(frozen=True)
class GeneratedFiles:
    files: tuple[GeneratedFile, ...]

    def get(self, path: str) -> bytes:
        for file in self.files:
            if file.path == path:
                return file.content
        raise KeyError(path)
