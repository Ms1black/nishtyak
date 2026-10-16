from __future__ import annotations

import json
import posixpath
from pathlib import PurePosixPath

from .models import AnalysisOptions, GeneratedFile, PackagePlan, ProjectLayout, ProjectSnapshot
from .resolution import normalize_roots


class LayoutDetector:
    """Select a project boundary while preserving the archive's original paths."""

    _manifests = frozenset(
        {
            "pyproject.toml",
            "requirements.txt",
            "setup.py",
            "setup.cfg",
            "environment.yml",
            "environment.yaml",
        }
    )

    def detect(self, snapshot: ProjectSnapshot, options: AnalysisOptions) -> ProjectLayout:
        paths = tuple(file.path for file in snapshot.files)
        candidates = self._candidates(paths)
        ambiguous = False
        if options.project_root is not None:
            root = normalize_roots((options.project_root,))[0]
            if root != "." and not any(self.contains(path, root) for path in paths):
                raise ValueError("Project root contains no project files")
        elif len(candidates) == 1:
            root = candidates[0]
        elif candidates:
            root = self._wrapper(paths)
            ambiguous = True
        else:
            root = self._wrapper(paths)
            candidates = (root,)

        if not candidates:
            candidates = (root,)
        configured = normalize_roots(options.source_roots)
        roots = [self.join(root, item) for item in configured]
        src = self.join(root, "src")
        if configured == (".",) and any(
            self.contains(path, src) and path.endswith(".py") for path in paths
        ):
            roots.append(src)
        return ProjectLayout(
            root=root,
            source_roots=tuple(dict.fromkeys(roots)),
            root_candidates=candidates,
            ambiguous=ambiguous,
        )

    def _candidates(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        roots = {
            str(PurePosixPath(path).parent)
            for path in paths
            if PurePosixPath(path).name in self._manifests
        }
        # Nested example projects do not make a declared parent project ambiguous.
        return tuple(
            sorted(
                root
                for root in roots
                if not any(other != root and self.contains(root, other) for other in roots)
            )
        )

    @staticmethod
    def _wrapper(paths: tuple[str, ...]) -> str:
        if not paths:
            return "."
        first = {path.split("/", 1)[0] for path in paths}
        if len(first) == 1 and all("/" in path for path in paths):
            root = next(iter(first))
            if root == "src" or f"{root}/__init__.py" in paths:
                return "."
            return root
        return "."

    @staticmethod
    def contains(path: str, root: str) -> bool:
        return root == "." or path.startswith(root + "/")

    @staticmethod
    def join(root: str, relative: str) -> str:
        return posixpath.normpath(posixpath.join(root, relative))


class ProjectLauncher:
    """Produce a portable launch command for reviewed import roots and entry points."""

    @staticmethod
    def required(plan: PackagePlan) -> bool:
        run = plan.run_spec
        if run is None:
            return False
        if run.kind == "callable":
            return True
        layout = plan.report.layout
        if layout is None:
            return False
        implicit = run.working_directory
        if run.kind == "script":
            implicit = LayoutDetector.join(implicit, str(PurePosixPath(run.target).parent))
        return any(root != implicit for root in layout.source_roots)

    def generate(self, plan: PackagePlan) -> GeneratedFile:
        run = plan.run_spec
        if run is None:
            raise ValueError("A launch target is required")
        layout = plan.report.layout
        configuration = {
            "source_roots": layout.source_roots if layout else plan.report.options.source_roots,
            "working_directory": run.working_directory,
            "kind": run.kind,
            "target": run.target,
            "args": run.args,
        }
        content = (
            '''"""Launch the packaged command; run only on a trusted project."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import runpy
import sys


class ProjectRunner:
    def run(self) -> None:
        configuration = json.loads(CONFIGURATION)
        project = Path(__file__).resolve().parent / "project"
        roots = [str(project / root) for root in configuration["source_roots"]]
        sys.path[:0] = roots
        working_directory = project / configuration["working_directory"]
        os.chdir(working_directory)
        target = configuration["target"]
        sys.argv = [target, *configuration["args"]]
        if configuration["kind"] == "module":
            runpy.run_module(target, run_name="__main__", alter_sys=True)
        elif configuration["kind"] == "callable":
            module, attribute = target.split(":", 1)
            entrypoint = importlib.import_module(module)
            for part in attribute.split("."):
                entrypoint = getattr(entrypoint, part)
            raise SystemExit(entrypoint())
        else:
            script = working_directory / target
            sys.path.insert(0, str(script.parent))
            runpy.run_path(str(script), run_name="__main__")


CONFIGURATION = '''
            + repr(json.dumps(configuration, ensure_ascii=True, sort_keys=True))
            + """

if __name__ == "__main__":
    ProjectRunner().run()
"""
        )
        return GeneratedFile("run_project.py", content.encode("utf-8"))
