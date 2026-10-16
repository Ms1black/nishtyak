from __future__ import annotations

import platform
import re
from dataclasses import asdict, dataclass, replace
from importlib import metadata
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .models import AnalysisReport, DependencyCandidate, Finding, stable_id
from .redaction import TextRedactor


@dataclass(frozen=True)
class InstalledDistribution:
    name: str
    version: str
    modules: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthorEnvironmentSnapshot:
    python_version: str
    platform: str
    distributions: tuple[InstalledDistribution, ...]
    findings: tuple[Finding, ...] = ()

    @property
    def id(self) -> str:
        return stable_id(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return TextRedactor().redact_value(asdict(self))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AuthorEnvironmentSnapshot:
        if not isinstance(value, dict):
            raise ValueError("Author environment must be an object")
        if set(value) - {"python_version", "platform", "distributions", "findings"}:
            raise ValueError("Unknown author environment fields")
        python_version = value.get("python_version")
        platform_name = value.get("platform")
        distributions = value.get("distributions")
        try:
            if not isinstance(python_version, str) or len(python_version) > 32:
                raise ValueError("Invalid Python version")
            parsed_version = Version(python_version)
            if parsed_version.major != 3:
                raise ValueError("Invalid Python version")
        except InvalidVersion as error:
            raise ValueError("Author Python version is invalid") from error
        if (
            not isinstance(platform_name, str)
            or len(platform_name) > 200
            or not re.fullmatch(r"[A-Za-z0-9_. -]+", platform_name)
        ):
            raise ValueError("Author platform is invalid")
        if not isinstance(distributions, (list, tuple)) or len(distributions) > 20_000:
            raise ValueError("Author distributions must be a bounded array")
        parsed = []
        for distribution in distributions:
            if not isinstance(distribution, dict):
                raise ValueError("Installed distribution must be an object")
            if set(distribution) - {"name", "version", "modules"}:
                raise ValueError("Unknown installed distribution fields")
            name, version = distribution.get("name"), distribution.get("version")
            modules = distribution.get("modules", [])
            if (
                not isinstance(name, str)
                or len(name) > 200
                or not AuthorEnvironmentInspector.NAME.fullmatch(name)
            ):
                raise ValueError("Installed distribution name is invalid")
            try:
                if not isinstance(version, str) or len(version) > 128:
                    raise ValueError("Installed version is invalid")
                Version(version)
            except InvalidVersion as error:
                raise ValueError("Installed version is invalid") from error
            if (
                not isinstance(modules, (list, tuple))
                or len(modules) > 1000
                or not all(
                    isinstance(module, str) and len(module) <= 200 and module.isidentifier()
                    for module in modules
                )
            ):
                raise ValueError("Installed module names are invalid")
            parsed.append(InstalledDistribution(name, version, tuple(sorted(set(modules)))))
        return cls(
            python_version,
            platform_name,
            tuple(sorted(parsed, key=lambda item: (canonicalize_name(item.name), item.version))),
        )


class AuthorEnvironmentInspector:
    """Collect local metadata only when explicitly called on the author's machine."""

    NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\Z")

    def collect(self) -> AuthorEnvironmentSnapshot:
        findings = []
        try:
            mappings = metadata.packages_distributions()
        except (OSError, ValueError):
            mappings = {}
            findings.append(
                Finding(
                    "AUTHOR_MODULE_MAPPING_UNAVAILABLE", "Installed module metadata is unavailable."
                )
            )
        modules: dict[str, set[str]] = {}
        for module, packages in mappings.items():
            if not isinstance(module, str) or not module.isidentifier():
                continue
            for package in packages:
                if isinstance(package, str) and self.NAME.fullmatch(package):
                    modules.setdefault(canonicalize_name(package), set()).add(module)
        distributions = {}
        for distribution in metadata.distributions():
            try:
                name = distribution.metadata.get("Name", "")
                version = distribution.version
                if not isinstance(name, str) or not self.NAME.fullmatch(name):
                    raise ValueError("Invalid package metadata")
                version = str(Version(version))
            except (OSError, ValueError, InvalidVersion, TypeError):
                findings.append(
                    Finding(
                        "AUTHOR_METADATA_INVALID", "An installed distribution has invalid metadata."
                    )
                )
                continue
            canonical = canonicalize_name(name)
            distributions[(canonical, version)] = InstalledDistribution(
                name, version, tuple(sorted(modules.get(canonical, ())))
            )
        return AuthorEnvironmentSnapshot(
            platform.python_version(),
            f"{platform.system()}-{platform.machine()}",
            tuple(value for _, value in sorted(distributions.items())),
            tuple({finding.id: finding for finding in findings}.values()),
        )

    def enrich(self, report: AnalysisReport, snapshot: AuthorEnvironmentSnapshot) -> AnalysisReport:
        by_name: dict[str, list[InstalledDistribution]] = {}
        by_module: dict[str, set[str]] = {}
        findings = list(report.findings) + list(snapshot.findings)
        for distribution in snapshot.distributions:
            try:
                if not self.NAME.fullmatch(distribution.name):
                    raise ValueError("Invalid package name")
                Version(distribution.version)
            except (InvalidVersion, ValueError, TypeError):
                findings.append(
                    Finding(
                        "AUTHOR_METADATA_INVALID",
                        "Author environment contains invalid package metadata.",
                    )
                )
                continue
            name = canonicalize_name(distribution.name)
            by_name.setdefault(name, []).append(distribution)
            for module in distribution.modules:
                if module.isidentifier():
                    by_module.setdefault(module, set()).add(name)

        dependencies = []
        for candidate in report.dependencies:
            if candidate.category not in {"external", "unresolved"} or candidate.module.startswith(
                "."
            ):
                dependencies.append(candidate)
                continue
            package = canonicalize_name(candidate.package) if candidate.package else None
            mapped = by_module.get(candidate.module, set())
            if not package:
                if len(mapped) != 1:
                    if len(mapped) > 1:
                        findings.append(
                            self._finding(
                                "AUTHOR_MAPPING_AMBIGUOUS",
                                "Several installed packages provide this module.",
                                candidate,
                            )
                        )
                    dependencies.append(candidate)
                    continue
                package = next(iter(mapped))
            elif mapped and package not in mapped:
                findings.append(
                    self._finding(
                        "AUTHOR_MAPPING_CONFLICT",
                        "Installed module mapping differs from the candidate package.",
                        candidate,
                    )
                )
                dependencies.append(candidate)
                continue
            installed = by_name.get(package, [])
            if len(installed) != 1:
                code = (
                    "AUTHOR_DISTRIBUTION_MISSING" if not installed else "AUTHOR_VERSION_AMBIGUOUS"
                )
                findings.append(
                    self._finding(
                        code,
                        "A unique installed version is unavailable for this candidate.",
                        candidate,
                    )
                )
                dependencies.append(candidate)
                continue
            distribution = installed[0]
            requirement = Requirement(candidate.requirement or distribution.name)
            if requirement.specifier and not requirement.specifier.contains(
                distribution.version, prereleases=True
            ):
                findings.append(
                    self._finding(
                        "AUTHOR_ENVIRONMENT_CONFLICT",
                        "Installed version conflicts with the retained declaration.",
                        candidate,
                    )
                )
                dependencies.append(candidate)
                continue
            if not requirement.specifier:
                requirement.specifier = Requirement(f"x=={distribution.version}").specifier
            dependencies.append(
                replace(
                    candidate,
                    category="external",
                    package=distribution.name,
                    requirement=str(requirement),
                    status="author_environment",
                    reason=f"Author snapshot {snapshot.id}: version {distribution.version}. "
                    "Runtime is unverified.",
                    id="",
                )
            )
        try:
            python_version = str(Version(snapshot.python_version))
        except InvalidVersion:
            python_version = None
        if python_version:
            findings.append(
                Finding(
                    "AUTHOR_PYTHON_OBSERVED",
                    f"Author Python: {python_version}. Project execution is unverified.",
                    severity="info",
                )
            )
            if report.python_requires:
                if not Requirement(f"python{report.python_requires}").specifier.contains(
                    python_version
                ):
                    findings.append(
                        Finding(
                            "AUTHOR_PYTHON_CONFLICT",
                            "Author Python conflicts with the declared requirement.",
                        )
                    )
        findings.append(
            Finding(
                "AUTHOR_ENVIRONMENT_UNVERIFIED",
                "Author metadata does not demonstrate project reproducibility.",
            )
        )
        return replace(
            report,
            analysis_id=stable_id(report.analysis_id, "author-environment-v1", snapshot.id),
            dependencies=tuple(dependencies),
            findings=tuple({finding.id: finding for finding in findings}.values()),
            author_environment_id=snapshot.id,
            author_python_version=snapshot.python_version,
            author_platform=snapshot.platform,
        )

    @staticmethod
    def _finding(code: str, message: str, candidate: DependencyCandidate) -> Finding:
        return Finding(code, message, evidence_ids=(candidate.id, *candidate.evidence_ids))
