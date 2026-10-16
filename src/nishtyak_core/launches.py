from __future__ import annotations

import posixpath
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

from .import_graph import LocalModuleIndex
from .models import AnalysisReport, FileInfo, ProjectSnapshot, RunSpec, SourceFile, stable_id


@dataclass(frozen=True)
class LaunchCandidate:
    id: str
    run_spec: RunSpec
    basis: str
    reason: str
    confidence: str


@dataclass(frozen=True)
class LaunchAssessment:
    candidates: tuple[LaunchCandidate, ...]
    recommended: LaunchCandidate | None = None
    provenance: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LaunchDetector:
    """Suggest commands from static facts, without requiring a particular file name."""

    _support_directories = frozenset(
        {
            "tests",
            "test",
            "testing",
            "docs",
            ".git",
            ".venv",
            "__pycache__",
            ".tox",
            "node_modules",
        }
    )
    _support_files = frozenset({"setup.py", "conftest.py", "noxfile.py", "fabfile.py"})

    def detect(
        self, report: AnalysisReport, files: tuple[FileInfo, ...] | None = None
    ) -> LaunchAssessment:
        available = (
            {file.path for file in files if file.path.endswith(".py")}
            if files is not None
            else {file.path for file in report.files if file.path.endswith(".py")}
        )
        root = report.layout.root if report.layout else "."
        roots = report.layout.source_roots if report.layout else report.options.source_roots
        paths = {
            path
            for path in available
            if self._inside(path, root) and self._is_application_source(path, root)
        }
        # Reports from older workers have no parsing facts. New reports must never
        # recommend a source that failed to parse or exceeded the analysis limit.
        if report.schema_version >= 3:
            paths &= set(report.parsed_sources)
        if not paths or (report.layout is not None and report.layout.ambiguous):
            return LaunchAssessment(())

        index = LocalModuleIndex(
            ProjectSnapshot(tuple(SourceFile(path, b"") for path in sorted(available)), ""), roots
        )
        ranked: dict[RunSpec, tuple[int, LaunchCandidate]] = {}
        main_guards = set(getattr(report, "main_guards", ()))
        for target in report.entrypoints:
            if not self._callable_exists(target, paths, index):
                continue
            self._add(
                ranked,
                RunSpec(target, kind="callable", working_directory=root),
                0,
                "declared_script",
                "Declared in [project.scripts]; arguments and output have not been verified.",
                "declared",
            )
        for path in sorted(paths):
            name = PurePosixPath(path).name
            if name == "__main__.py":
                module = self._main_module(path, roots)
                if module and path not in index.resolve_module(module + ".__main__").paths:
                    continue
                run = (
                    RunSpec(module, kind="module", working_directory=root)
                    if module
                    else self._script(path, root)
                )
                self._add(
                    ranked,
                    run,
                    1,
                    "package_main",
                    "A package __main__.py provides a conventional Python launch target.",
                    "conventional",
                )
            elif path in main_guards:
                module = (
                    self._source_module(path, roots)
                    if self._inside_package(path, available)
                    else None
                )
                if module and path not in index.resolve_module(module).paths:
                    continue
                self._add(
                    ranked,
                    RunSpec(module, kind="module", working_directory=root)
                    if module
                    else self._script(path, root),
                    2,
                    "main_guard",
                    "The source contains an if __name__ == '__main__' block.",
                    "structural",
                )

        ordinary = {
            path for path in paths if PurePosixPath(path).name not in {"__init__.py", "__main__.py"}
        }
        executables = ordinary & set(getattr(report, "executable_sources", ()))
        incoming = self._imported_paths(report, paths, roots)
        root_scripts = {
            path for path in executables - incoming if not self._inside_package(path, available)
        }
        for path in sorted(root_scripts):
            self._add(
                ranked,
                self._script(path, root),
                3,
                "import_graph_root",
                "Top-level executable statements occur in a source not imported by other "
                "application sources; required arguments are unknown.",
                "structural",
            )
        if len(ordinary) == 1:
            path = next(iter(ordinary))
            if not self._inside_package(path, available) and (
                path in executables or report.schema_version < 3
            ):
                self._add(
                    ranked,
                    self._script(path, root),
                    3,
                    "standalone_script",
                    "The project contains one standalone Python script; no main function or "
                    "main guard is required to run it.",
                    "structural",
                )
        for path in sorted(ordinary):
            if self._inside_package(path, available):
                continue
            filename_hint = PurePosixPath(path).name in {"main.py", "run.py", "app.py"}
            self._add(
                ranked,
                self._script(path, root),
                4 if filename_hint else 5,
                "filename_hint" if filename_hint else "script_file",
                "The filename suggests a launch target; executable behavior was not established."
                if filename_hint
                else "A standalone source file is available; its intended launch role is unknown.",
                "hint",
            )
        ordered = sorted(ranked.values(), key=lambda item: (item[0], item[1].id))
        candidates = tuple(candidate for _, candidate in ordered)
        preferred = (
            [candidate for rank, candidate in ordered if rank == ordered[0][0]] if ordered else []
        )
        recommendation = preferred[0] if len(preferred) == 1 and ordered[0][0] <= 3 else None
        return LaunchAssessment(
            candidates, recommendation, "inferred" if recommendation else "unknown"
        )

    @staticmethod
    def _inside(path: str, root: str) -> bool:
        return root == "." or path.startswith(root + "/")

    def _is_application_source(self, path: str, root: str) -> bool:
        relative = posixpath.relpath(path, root)
        parts = PurePosixPath(relative).parts
        name = parts[-1]
        return (
            not (set(parts[:-1]) & self._support_directories)
            and name not in self._support_files
            and not name.startswith("test_")
            and not name.endswith("_test.py")
        )

    @staticmethod
    def _inside_package(path: str, paths: set[str]) -> bool:
        return any(str(parent / "__init__.py") in paths for parent in PurePosixPath(path).parents)

    @staticmethod
    def _script(path: str, root: str) -> RunSpec:
        return RunSpec(posixpath.relpath(path, root), working_directory=root)

    @staticmethod
    def _main_module(path: str, roots: tuple[str, ...]) -> str | None:
        return LaunchDetector._module_parts(path, roots, package_main=True)

    @staticmethod
    def _source_module(path: str, roots: tuple[str, ...]) -> str | None:
        return LaunchDetector._module_parts(path, roots, package_main=False)

    @staticmethod
    def _module_parts(path: str, roots: tuple[str, ...], package_main: bool) -> str | None:
        for root in sorted(roots, key=lambda value: len(PurePosixPath(value).parts), reverse=True):
            relative = posixpath.relpath(path, root)
            parts = (
                PurePosixPath(relative).parts[:-1]
                if package_main
                else PurePosixPath(relative[:-3]).parts
            )
            if parts and all(part.isidentifier() for part in parts):
                return ".".join(parts)
        return None

    @staticmethod
    def _callable_exists(target: str, paths: set[str], index: LocalModuleIndex) -> bool:
        module, separator, attribute = target.partition(":")
        pattern = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
        if (
            not separator
            or not re.fullmatch(pattern, module)
            or not re.fullmatch(pattern, attribute)
        ):
            return False
        resolution = index.resolve_module(module)
        return resolution.found and bool(resolution.paths) and resolution.paths[-1] in paths

    @staticmethod
    def _imported_paths(
        report: AnalysisReport, paths: set[str], roots: tuple[str, ...]
    ) -> set[str]:
        if not report.imports:
            return set()
        from .import_graph import ImportGraph

        snapshot = ProjectSnapshot(tuple(SourceFile(file.path, b"") for file in report.files), "")
        graph = ImportGraph(snapshot, roots, report.imports)
        imported = set()
        for edge in graph.edges:
            if edge.source_path in paths:
                imported.update(path for path in edge.target_paths if path != edge.source_path)
        return imported

    @staticmethod
    def _add(
        ranked: dict[RunSpec, tuple[int, LaunchCandidate]],
        run: RunSpec,
        rank: int,
        basis: str,
        reason: str,
        confidence: str,
    ) -> None:
        existing = ranked.get(run)
        if existing is None or rank < existing[0]:
            ranked[run] = (
                rank,
                LaunchCandidate(
                    stable_id("launch", asdict(run), basis), run, basis, reason, confidence
                ),
            )
