from __future__ import annotations

import posixpath
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from .import_graph import ImportGraph
from .models import (
    AnalysisReport,
    DependencyCandidate,
    FileInfo,
    ProjectSnapshot,
    RunSpec,
    SourceFile,
)


@dataclass(frozen=True)
class RuntimeDependencySelection:
    dependencies: tuple[DependencyCandidate, ...]
    automatic_extras: tuple[str, ...]
    ignored_evidence_ids: frozenset[str]


class RuntimeDependencySelector:
    """Separate execution requirements from evidence used only by project tooling.

    Every application source remains in scope, rather than treating one selected
    command as proof that other scripts are unused. A support module imported by
    application code is brought back into scope through the static local graph.
    Explicit base declarations are retained independently by the package planner.
    """

    _support_directories = frozenset(
        {
            "tests",
            "test",
            "testing",
            "docs",
            "doc",
            ".git",
            ".venv",
            "__pycache__",
            ".tox",
            "node_modules",
        }
    )
    _support_files = frozenset({"setup.py", "conftest.py", "noxfile.py", "fabfile.py"})

    def select(
        self,
        report: AnalysisReport,
        *,
        included_files: tuple[FileInfo, ...] | None = None,
        run_spec: RunSpec | None = None,
        package_mappings: dict[str, str] | None = None,
    ) -> RuntimeDependencySelection:
        files = report.files if included_files is None else included_files
        available = {file.path for file in files}
        root = report.layout.root if report.layout else "."
        roots = report.layout.source_roots if report.layout else report.options.source_roots
        observations = tuple(
            item
            for item in report.imports
            if item.context != "type_checking" and item.path in available
        )
        graph = ImportGraph(
            ProjectSnapshot(tuple(SourceFile(file.path, b"") for file in files), ""),
            roots,
            observations,
        )
        seeds = {path for path in graph.nodes if self._application_source(path, root)}
        if run_spec is not None:
            seeds.update(self._launch_sources(run_spec, graph))
        active_paths = set(graph.reachable(tuple(seeds)))
        active = {item.id for item in observations if item.path in active_paths}
        runtime = {
            item.id
            for item in observations
            if item.path in active_paths and item.context == "runtime"
        }
        import_ids = {item.id for item in report.imports}
        dependencies = tuple(
            candidate
            for candidate in report.dependencies
            if not (set(candidate.evidence_ids) & import_ids)
            or bool(set(candidate.evidence_ids) & active)
        )
        return RuntimeDependencySelection(
            dependencies,
            self._automatic_extras(report, dependencies, runtime, package_mappings or {}),
            frozenset(import_ids - active),
        )

    def _application_source(self, path: str, root: str) -> bool:
        if root != "." and not path.startswith(root + "/"):
            return False
        parts = PurePosixPath(posixpath.relpath(path, root)).parts
        name = parts[-1]
        return (
            not (set(parts[:-1]) & self._support_directories)
            and name not in self._support_files
            and not name.startswith("test_")
            and not name.endswith("_test.py")
        )

    @staticmethod
    def _launch_sources(run: RunSpec, graph: ImportGraph) -> tuple[str, ...]:
        if run.kind == "script":
            return (posixpath.normpath(posixpath.join(run.working_directory, run.target)),)
        module = run.target.split(":", 1)[0]
        if run.kind == "module":
            package_main = graph.index.resolve_module(module + ".__main__")
            if package_main.found:
                return package_main.paths
        return graph.index.resolve_module(module).paths

    @staticmethod
    def _automatic_extras(
        report: AnalysisReport,
        dependencies: tuple[DependencyCandidate, ...],
        runtime: set[str],
        package_mappings: dict[str, str],
    ) -> tuple[str, ...]:
        groups: dict[str, set[str]] = defaultdict(set)
        base = set()
        for declaration in report.declarations:
            name = canonicalize_name(Requirement(declaration.requirement).name)
            if declaration.group is None:
                base.add(name)
            else:
                groups[name].add(declaration.group)
        selected = set()
        for candidate in dependencies:
            if candidate.category not in {"external", "unresolved"}:
                continue
            if not set(candidate.evidence_ids) & runtime:
                continue
            name = canonicalize_name(
                package_mappings.get(candidate.module) or candidate.package or candidate.module
            )
            matches = groups.get(name, set())
            if name not in base and len(matches) == 1:
                selected.update(matches)
        return tuple(sorted(selected))
