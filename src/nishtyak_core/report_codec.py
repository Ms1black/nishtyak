from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .models import (
    AnalysisOptions,
    AnalysisReport,
    DependencyCandidate,
    FileInfo,
    Finding,
    ImportObservation,
)


class InvalidWorkerReport(ValueError):
    pass


class AnalysisReportCodec:
    """Private worker protocol. Public HTTP responses still use report.to_dict()."""

    def encode(self, report: AnalysisReport) -> dict[str, Any]:
        return asdict(report)

    def decode(self, value: Any) -> AnalysisReport:
        try:
            data = self._object(value, AnalysisReport)
            if data["schema_version"] != 1 or type(data["schema_version"]) is not int:
                raise InvalidWorkerReport("Unsupported worker report schema")
            options = self.decode_options(data["options"])
            files = tuple(self._file(item) for item in self._array(data["files"]))
            imports = tuple(self._import(item) for item in self._array(data["imports"]))
            dependencies = tuple(
                self._dependency(item) for item in self._array(data["dependencies"])
            )
            findings = tuple(self._finding(item) for item in self._array(data["findings"]))
            return AnalysisReport(
                analysis_id=self._text(data["analysis_id"]),
                fingerprint=self._text(data["fingerprint"]),
                options=options,
                files=files,
                imports=imports,
                dependencies=dependencies,
                findings=findings,
                status=self._choice(data["status"], {"complete", "partial"}),
                declared_requirements=self._strings(data["declared_requirements"]),
                python_requires=self._nullable_text(data["python_requires"]),
                entrypoints=self._strings(data["entrypoints"]),
                schema_version=1,
            )
        except (KeyError, TypeError, ValueError, RecursionError) as error:
            raise InvalidWorkerReport("Invalid worker report") from error

    def decode_options(self, value: Any) -> AnalysisOptions:
        data = self._object(value, AnalysisOptions)
        roots = self._strings(data["source_roots"])
        if not 1 <= len(roots) <= 32 or any(len(root) > 1024 for root in roots):
            raise InvalidWorkerReport("Invalid source roots")
        version = self._text(data["python_version"])
        if len(version) > 16:
            raise InvalidWorkerReport("Invalid Python version")
        return AnalysisOptions(
            python_version=version,
            source_roots=roots,
            max_source_bytes=self._integer(data["max_source_bytes"], 1, 1_000_000),
            max_files=self._integer(data["max_files"], 1, 1_000),
        )

    def _file(self, value: Any) -> FileInfo:
        item = self._object(value, FileInfo)
        return FileInfo(
            self._text(item["path"]),
            self._integer(item["size"], 0, 50 * 1024 * 1024),
            self._text(item["sha256"]),
        )

    def _import(self, value: Any) -> ImportObservation:
        item = self._object(value, ImportObservation)
        return ImportObservation(
            module=self._text(item["module"]),
            path=self._text(item["path"]),
            line=self._integer(item["line"], 1),
            names=self._strings(item["names"]),
            level=self._integer(item["level"], 0),
            context=self._text(item["context"]),
            kind=self._text(item["kind"]),
            id=self._text(item["id"]),
        )

    def _dependency(self, value: Any) -> DependencyCandidate:
        item = self._object(value, DependencyCandidate)
        return DependencyCandidate(
            module=self._text(item["module"]),
            category=self._text(item["category"]),
            package=self._nullable_text(item["package"]),
            requirement=self._nullable_text(item["requirement"]),
            status=self._text(item["status"]),
            evidence_ids=self._strings(item["evidence_ids"]),
            reason=self._text(item["reason"]),
            context=self._text(item["context"]),
            id=self._text(item["id"]),
        )

    def _finding(self, value: Any) -> Finding:
        item = self._object(value, Finding)
        line = item["line"]
        return Finding(
            code=self._text(item["code"]),
            message=self._text(item["message"]),
            path=self._nullable_text(item["path"]),
            line=None if line is None else self._integer(line, 1),
            severity=self._text(item["severity"]),
            evidence_ids=self._strings(item["evidence_ids"]),
            id=self._text(item["id"]),
        )

    @staticmethod
    def _object(value: Any, model: type) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != set(model.__dataclass_fields__):
            raise InvalidWorkerReport("Unexpected worker object fields")
        return value

    @staticmethod
    def _array(value: Any) -> list[Any]:
        if not isinstance(value, (list, tuple)) or len(value) > 100_000:
            raise InvalidWorkerReport("Invalid worker array")
        return value

    def _strings(self, value: Any) -> tuple[str, ...]:
        return tuple(self._text(item) for item in self._array(value))

    @staticmethod
    def _text(value: Any) -> str:
        if not isinstance(value, str):
            raise InvalidWorkerReport("Invalid worker string")
        return value

    def _nullable_text(self, value: Any) -> str | None:
        return None if value is None else self._text(value)

    @staticmethod
    def _integer(value: Any, minimum: int, maximum: int = 2**63 - 1) -> int:
        if type(value) is not int or not minimum <= value <= maximum:
            raise InvalidWorkerReport("Invalid worker integer")
        return value

    def _choice(self, value: Any, choices: set[str]) -> str:
        if self._text(value) not in choices:
            raise InvalidWorkerReport("Invalid worker choice")
        return value


def bounded_json(value: Any, maximum_bytes: int) -> bytes:
    """Bound encoding incrementally, including the transient encoded payload."""
    output = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    for chunk in encoder.iterencode(value):
        if len(output) + len(chunk) > maximum_bytes:
            raise InvalidWorkerReport("Worker result exceeds its byte limit")
        output.extend(chunk.encode("ascii"))
    return bytes(output)
