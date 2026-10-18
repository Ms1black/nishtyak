from __future__ import annotations

import ast
import json
import posixpath
import re
from pathlib import PurePosixPath, PureWindowsPath

from .layout import LayoutDetector
from .models import Finding, ProjectSnapshot
from .redaction import TextRedactor


class ProjectInspector:
    _document_suffixes = frozenset({"", ".md", ".txt", ".rst", ".markdown", ".adoc"})
    _input_functions = frozenset(
        {"open", "read_csv", "read_json", "read_excel", "loadtxt", "genfromtxt"}
    )

    def __init__(self, max_source_bytes: int = 1_000_000) -> None:
        if not 0 < max_source_bytes <= 1_000_000:
            raise ValueError("Inspection source limit must be between 1 and 1000000 bytes")
        self.max_source_bytes = max_source_bytes

    def inspect(self, snapshot: ProjectSnapshot, project_root: str = ".") -> tuple[Finding, ...]:
        findings: list[Finding] = []
        paths = {
            file.path for file in snapshot.files if LayoutDetector.contains(file.path, project_root)
        }
        root_documents = {
            (path.stem.casefold(), path.suffix.casefold())
            for value in paths
            if str((path := PurePosixPath(value)).parent) == project_root
        }
        for stems, code, message in (
            ({"readme"}, "README_MISSING", "No README was found at the project root."),
            (
                {"license", "licence", "copying"},
                "LICENSE_MISSING",
                "No license document was found at the project root; clarify reuse terms.",
            ),
        ):
            if not any(
                stem in stems and suffix in self._document_suffixes
                for stem, suffix in root_documents
            ):
                findings.append(Finding(code, message, severity="info"))

        counts = {".csv": 0, ".json": 0}
        for path in paths:
            suffix = PurePosixPath(path).suffix.casefold()
            if suffix in counts:
                counts[suffix] += 1
        findings.append(
            Finding(
                "DATA_FILES_SUMMARY",
                f"Project includes {counts['.csv']} CSV and {counts['.json']} JSON files; "
                "their role as experiment inputs needs author confirmation.",
                severity="info",
            )
        )

        for source in sorted(snapshot.files, key=lambda file: file.path):
            if (
                not LayoutDetector.contains(source.path, project_root)
                or not source.path.endswith(".py")
                or len(source.content) > self.max_source_bytes
            ):
                continue
            try:
                tree = ast.parse(source.content, filename=source.path)
            except (SyntaxError, UnicodeError, LookupError, ValueError, RecursionError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                function = node.func
                name = (
                    function.id
                    if isinstance(function, ast.Name)
                    else function.attr
                    if isinstance(function, ast.Attribute)
                    else None
                )
                if name not in self._input_functions:
                    continue
                value = node.args[0]
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    continue
                if name == "open" and self._writes_file(node):
                    continue
                finding = self._input_finding(
                    value.value, source.path, node.lineno, paths, project_root
                )
                if finding is not None:
                    findings.append(finding)

        return tuple({finding.id: finding for finding in findings}.values())

    def _writes_file(self, node: ast.Call) -> bool:
        mode = (
            node.args[1]
            if len(node.args) > 1
            else next((keyword.value for keyword in node.keywords if keyword.arg == "mode"), None)
        )
        return (
            isinstance(mode, ast.Constant)
            and isinstance(mode.value, str)
            and bool(set(mode.value) & {"w", "a", "x"})
        )

    def _input_finding(
        self, value: str, source: str, line: int, paths: set[str], project_root: str = "."
    ) -> Finding | None:
        normalized = posixpath.normpath(value.replace("\\", "/"))
        external = (
            bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value))
            or value.startswith(("/", "~", "\\\\"))
            or bool(PureWindowsPath(value).drive)
            or normalized == ".."
            or normalized.startswith("../")
        )
        if external:
            return Finding(
                "EXTERNAL_INPUT_REFERENCE",
                "Код обращается к данным вне проекта. "
                "Опишите доступный источник или добавьте пример данных в ZIP.",
                path=source,
                line=line,
            )
        if LayoutDetector.join(project_root, normalized) not in paths:
            return Finding(
                "INPUT_PATH_UNVERIFIED",
                "В коде указан входной путь "
                + TextRedactor().redact(json.dumps(normalized[:200], ensure_ascii=False))
                + "; его доступность из папки запуска не подтверждена. "
                "Добавьте пример данных в ZIP или уточните папку запуска.",
                path=source,
                line=line,
            )
        return None
