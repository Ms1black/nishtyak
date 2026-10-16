from __future__ import annotations

import posixpath
import tomllib
from dataclasses import dataclass

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet

from .models import Finding, ProjectSnapshot


@dataclass(frozen=True)
class ManifestData:
    requirements: tuple[str, ...]
    findings: tuple[Finding, ...]
    python_requires: str | None
    complete: bool = True


class ManifestReader:
    """Read declarative runtime dependencies; never evaluate build configuration."""

    def __init__(self, snapshot: ProjectSnapshot, max_bytes: int = 1_000_000) -> None:
        self.snapshot = snapshot
        self.max_bytes = max_bytes
        self.requirements: list[str] = []
        self.findings: list[Finding] = []
        self.python_requires: str | None = None
        self.complete = True
        self._visited: set[str] = set()

    def read(self) -> ManifestData:
        if self.snapshot.get("requirements.txt"):
            self._requirements_file("requirements.txt")
        if self.snapshot.get("pyproject.toml"):
            self._pyproject()
        for unsupported in ("setup.py", "setup.cfg", "environment.yml", "environment.yaml"):
            if self.snapshot.get(unsupported):
                self.findings.append(
                    Finding(
                        "MANIFEST_UNSUPPORTED",
                        "This dependency declaration needs manual review and was not evaluated.",
                        unsupported,
                    )
                )
        return ManifestData(
            tuple(sorted(set(self.requirements))),
            tuple(self.findings),
            self.python_requires,
            self.complete,
        )

    def _text(self, path: str) -> str | None:
        source = self.snapshot.get(path)
        if source is None:
            self._failure("MANIFEST_INCLUDE_MISSING", "Referenced manifest is missing.", path)
            return None
        if len(source.content) > self.max_bytes:
            self._failure("MANIFEST_TOO_LARGE", "Manifest exceeds the parsing limit.", path)
            return None
        try:
            return source.content.decode("utf-8-sig")
        except UnicodeError:
            self._failure("MANIFEST_PARSE_ERROR", "Manifest is not valid UTF-8.", path)
            return None

    def _requirements_file(self, path: str) -> None:
        if path in self._visited:
            return
        self._visited.add(path)
        if len(self._visited) > 50:
            self._failure("MANIFEST_INCLUDE_LIMIT", "Too many referenced manifests.", path)
            return
        text = self._text(path)
        if text is None:
            return
        for number, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("-r ", "--requirement ", "--requirement=")):
                target = line.split("=", 1)[1] if "=" in line else line.split(maxsplit=1)[1]
                target = target.strip()
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
                if (
                    target.startswith("/")
                    or "\\" in target
                    or ":" in target
                    or resolved == ".."
                    or resolved.startswith("../")
                ):
                    self._failure(
                        "MANIFEST_INCLUDE_UNSUPPORTED",
                        "Manifest include path is unsupported.",
                        path,
                        number,
                    )
                else:
                    self._requirements_file(resolved)
                continue
            if line.startswith("-") or line.endswith("\\"):
                self._failure(
                    "REQUIREMENT_UNSUPPORTED",
                    "Pip options or continued lines need manual review.",
                    path,
                    number,
                )
                continue
            self._requirement(line.split(" #", 1)[0].strip(), path, number)

    def _pyproject(self) -> None:
        text = self._text("pyproject.toml")
        if text is None:
            return
        try:
            data = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, RecursionError):
            self._failure(
                "MANIFEST_PARSE_ERROR", "TOML manifest could not be parsed.", "pyproject.toml"
            )
            return
        project = data.get("project", {})
        if not isinstance(project, dict):
            self._failure(
                "MANIFEST_PARSE_ERROR", "Project metadata must be a table.", "pyproject.toml"
            )
            return
        dependencies = project.get("dependencies", [])
        if not isinstance(dependencies, list):
            self._failure(
                "MANIFEST_PARSE_ERROR", "Dependencies must be an array.", "pyproject.toml"
            )
        else:
            for requirement in dependencies:
                if isinstance(requirement, str):
                    self._requirement(requirement, "pyproject.toml")
                else:
                    self._failure(
                        "MANIFEST_PARSE_ERROR", "Dependency must be a string.", "pyproject.toml"
                    )
        dynamic = project.get("dynamic", [])
        if isinstance(dynamic, list) and any(
            item in {"dependencies", "requires-python"} for item in dynamic if isinstance(item, str)
        ):
            self._failure(
                "DYNAMIC_DEPENDENCIES",
                "Dynamic dependency metadata needs manual review.",
                "pyproject.toml",
            )
        requires_python = project.get("requires-python")
        if requires_python is not None:
            try:
                if not isinstance(requires_python, str):
                    raise InvalidSpecifier("invalid metadata")
                SpecifierSet(requires_python)
                self.python_requires = requires_python
            except InvalidSpecifier:
                self._failure(
                    "PYTHON_REQUIREMENT_INVALID",
                    "Python version declaration is invalid.",
                    "pyproject.toml",
                )
        if isinstance(data.get("build-system"), dict) and data["build-system"].get("requires"):
            self.findings.append(
                Finding(
                    "BUILD_DEPENDENCIES_SEPARATE",
                    "Build dependencies were kept separate from runtime dependencies.",
                    "pyproject.toml",
                    severity="info",
                )
            )
        if project.get("optional-dependencies"):
            self.findings.append(
                Finding(
                    "OPTIONAL_DEPENDENCIES_UNSELECTED",
                    "Optional dependency groups require an explicit choice.",
                    "pyproject.toml",
                )
            )
        tool = data.get("tool", {})
        if isinstance(tool, dict) and any(name in tool for name in ("poetry", "pdm", "uv")):
            self.findings.append(
                Finding(
                    "TOOL_METADATA_UNSUPPORTED",
                    "Tool-specific dependency metadata needs manual review.",
                    "pyproject.toml",
                )
            )

    def _requirement(self, value: str, path: str, line: int | None = None) -> None:
        try:
            requirement = Requirement(value)
        except InvalidRequirement:
            self._failure("REQUIREMENT_INVALID", "Dependency declaration is invalid.", path, line)
            return
        if requirement.url:
            self._failure(
                "REQUIREMENT_URL_UNSUPPORTED",
                "Direct dependency URLs need manual review.",
                path,
                line,
            )
            return
        self.requirements.append(str(requirement))

    def _failure(self, code: str, message: str, path: str, line: int | None = None) -> None:
        self.complete = False
        self.findings.append(Finding(code, message, path, line))
