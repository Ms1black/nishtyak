from __future__ import annotations

import posixpath
import sys
from collections import defaultdict
from dataclasses import dataclass

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from .models import DependencyCandidate, Finding, ImportObservation, ProjectSnapshot

PACKAGE_NAMES = {
    "numpy": "numpy",
    "pandas": "pandas",
    "scipy": "scipy",
    "matplotlib": "matplotlib",
    "seaborn": "seaborn",
    "sympy": "sympy",
    "requests": "requests",
    "httpx": "httpx",
    "yaml": "PyYAML",
    "sklearn": "scikit-learn",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "pytest": "pytest",
    "torch": "torch",
    "tensorflow": "tensorflow",
    "pydantic": "pydantic",
    "fastapi": "fastapi",
    "flask": "Flask",
    "streamlit": "streamlit",
    "plotly": "plotly",
    "tqdm": "tqdm",
    "openpyxl": "openpyxl",
    "packaging": "packaging",
    "jinja2": "Jinja2",
}


class LocalModuleIndex:
    def __init__(self, snapshot: ProjectSnapshot, source_roots: tuple[str, ...]) -> None:
        self.modules: set[str] = set()
        self.file_modules: dict[str, str] = {}
        self.package_files: set[str] = set()
        for source in snapshot.files:
            if not source.path.endswith(".py"):
                continue
            for root in sorted(source_roots, key=len, reverse=True):
                prefix = root.rstrip("/") + "/" if root != "." else ""
                if not source.path.startswith(prefix):
                    continue
                relative = source.path[len(prefix) :]
                parts = relative[:-3].split("/")
                if parts[-1] == "__init__":
                    parts.pop()
                    self.package_files.add(source.path)
                if not parts or not all(part.isidentifier() for part in parts):
                    continue
                module = ".".join(parts)
                self.file_modules.setdefault(source.path, module)
                for length in range(1, len(parts) + 1):
                    self.modules.add(".".join(parts[:length]))

    def resolve_relative(self, observation: ImportObservation) -> str | None:
        current = self.file_modules.get(observation.path)
        if current is None:
            return None
        package = current.split(".")
        if observation.path not in self.package_files:
            package.pop()
        if observation.level > len(package):
            return None
        prefix = package[: len(package) - observation.level + 1]
        suffix = observation.module.split(".") if observation.module else []
        module = ".".join((*prefix, *suffix))
        return module if module in self.modules else None


class StandardLibraryProfile:
    def __init__(self, python_version: str) -> None:
        parts = python_version.split(".")
        if len(parts) != 2 or not all(part.isascii() and part.isdigit() for part in parts):
            raise ValueError("python_version must have major.minor format")
        major, minor = (int(part) for part in parts)
        if major != 3 or minor < 8:
            raise ValueError("python_version must refer to Python 3.8 or later")
        self.supported = (major, minor) == sys.version_info[:2]
        self.modules = set(sys.stdlib_module_names)

    def contains(self, module: str) -> bool:
        return self.supported and module in self.modules


@dataclass(frozen=True)
class ResolutionResult:
    dependencies: tuple[DependencyCandidate, ...]
    findings: tuple[Finding, ...]


class DependencyResolver:
    def __init__(
        self,
        snapshot: ProjectSnapshot,
        roots: tuple[str, ...],
        python_version: str,
        requirements: tuple[str, ...],
    ) -> None:
        self.local = LocalModuleIndex(snapshot, roots)
        self.stdlib = StandardLibraryProfile(python_version)
        self.declared: dict[str, list[str]] = defaultdict(list)
        for value in requirements:
            requirement = Requirement(value)
            self.declared[canonicalize_name(requirement.name)].append(value)

    def resolve(self, observations: tuple[ImportObservation, ...]) -> ResolutionResult:
        groups: dict[tuple[str, str, str, str | None], list[ImportObservation]] = defaultdict(list)
        findings: list[Finding] = []
        for observation in observations:
            module = observation.module.split(".", 1)[0]
            package = None
            if observation.level:
                resolved = self.local.resolve_relative(observation)
                module = "." * observation.level + observation.module
                category = "local" if resolved else "unresolved"
            elif module in self.local.modules:
                category = "local"
                if self.stdlib.contains(module) or module in PACKAGE_NAMES:
                    findings.append(
                        Finding(
                            "LOCAL_NAME_COLLISION",
                            "A local module shadows a known module; review intent.",
                            observation.path,
                            observation.line,
                            evidence_ids=(observation.id,),
                        )
                    )
            elif self.stdlib.contains(module):
                category = "stdlib"
            else:
                package = PACKAGE_NAMES.get(module)
                declared_name = canonicalize_name(package or module)
                if declared_name in self.declared:
                    package = Requirement(self.declared[declared_name][0]).name
                category = "external" if package else "unresolved"
            groups[(module, category, observation.context, package)].append(observation)

        candidates: list[DependencyCandidate] = []
        used_declarations: set[str] = set()
        for (module, category, context, package), evidence in sorted(
            groups.items(), key=lambda item: tuple(str(part) for part in item[0])
        ):
            values = self.declared.get(canonicalize_name(package), []) if package else []
            if package:
                used_declarations.add(canonicalize_name(package))
            requirement = values[0] if len(values) == 1 else None
            status = "declared" if values else "inferred"
            if category == "unresolved":
                status = "unresolved"
                findings.append(
                    Finding(
                        "DEPENDENCY_UNRESOLVED",
                        "Import could not be mapped to a local module or installable package.",
                        evidence[0].path,
                        evidence[0].line,
                        evidence_ids=tuple(item.id for item in evidence),
                    )
                )
            if len(values) > 1:
                status = "unresolved"
                findings.append(
                    Finding(
                        "DEPENDENCY_DECLARATIONS_MULTIPLE",
                        "Multiple declarations need reconciliation; markers are preserved.",
                        evidence_ids=tuple(item.id for item in evidence),
                    )
                )
            if category == "external" and not any(Requirement(value).specifier for value in values):
                findings.append(
                    Finding(
                        "VERSION_UNKNOWN",
                        "A verified package version has not been supplied.",
                        evidence_ids=tuple(item.id for item in evidence),
                    )
                )
            reason = {
                "local": "Resolved against the configured project source roots.",
                "stdlib": "Recognized by the selected Python standard-library profile.",
                "external": "Matched declared metadata."
                if values
                else "Matched the curated import mapping.",
                "unresolved": "Manual package or source-root clarification is required.",
            }[category]
            candidates.append(
                DependencyCandidate(
                    module,
                    category,
                    package,
                    requirement,
                    status,
                    tuple(item.id for item in evidence),
                    reason,
                    context,
                )
            )

        for name, values in sorted(self.declared.items()):
            if name in used_declarations:
                continue
            for value in values:
                requirement = Requirement(value)
                candidates.append(
                    DependencyCandidate(
                        requirement.name,
                        "external",
                        requirement.name,
                        value,
                        "declared",
                        reason="Declared runtime dependency without static import evidence.",
                    )
                )
                if not requirement.specifier:
                    findings.append(
                        Finding(
                            "VERSION_UNKNOWN",
                            "A declared dependency has no verified version constraint.",
                        )
                    )
        return ResolutionResult(tuple(candidates), tuple(findings))


def normalize_roots(roots: tuple[str, ...]) -> tuple[str, ...]:
    if not roots:
        raise ValueError("At least one source root is required")
    normalized = []
    for root in roots:
        if not isinstance(root, str) or not root or "\\" in root or ":" in root:
            raise ValueError("Source roots must be relative POSIX directory paths")
        path = posixpath.normpath(root)
        if root.startswith("/") or ".." in root.split("/"):
            raise ValueError("Source roots must stay inside the project")
        normalized.append(path)
    return tuple(sorted(set(normalized)))
