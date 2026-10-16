from __future__ import annotations

import re
from pathlib import PurePosixPath

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .models import AnalysisReport, FileInfo, Finding, PackagePlan, RunSpec, UserDecisions
from .security import SecretScanner


class PackagePlanner:
    """Prepare a reviewable package without reading or executing project files."""

    _excluded_directories = frozenset(
        {
            ".git",
            ".hg",
            ".svn",
            ".venv",
            "venv",
            "env",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".tox",
            "node_modules",
        }
    )
    _excluded_suffixes = frozenset({".pyc", ".pyo", ".pem", ".key", ".p12", ".pfx"})
    _uncertain_findings = frozenset(
        {
            "DYNAMIC_IMPORT",
            "DYNAMIC_IMPORT_UNRESOLVED",
            "SYNTAX_ERROR",
            "SOURCE_PARSE_ERROR",
            "REQUIREMENTS_UNSUPPORTED",
            "UNSUPPORTED_REQUIREMENT",
            "MISSING_DATA",
            "MISSING_INPUT_FILE",
            "DEPENDENCY_CONFLICT",
            "MANIFEST_UNSUPPORTED",
            "TOOL_METADATA_UNSUPPORTED",
            "AUTHOR_ENVIRONMENT_CONFLICT",
            "AUTHOR_MAPPING_CONFLICT",
            "AUTHOR_VERSION_AMBIGUOUS",
            "AUTHOR_PYTHON_CONFLICT",
        }
    )

    def prepare(self, report: AnalysisReport, decisions: UserDecisions) -> PackagePlan:
        if decisions.analysis_id != report.analysis_id:
            raise ValueError("Decisions belong to another analysis; analyze the project again")

        self._validate_decisions(report, decisions)
        included, excluded = self._select_files(report.files, decisions.excluded_files)
        findings = list(report.findings)
        draft = report.status != "complete"
        blocked = False

        for finding in report.findings:
            if finding.code == "SECRET_DETECTED":
                if finding.path is None or finding.path not in excluded:
                    blocked = True
                else:
                    draft = True
                    findings.append(
                        Finding(
                            "SECRET_FILE_EXCLUDED",
                            "A file containing a detected secret was excluded; "
                            "review project completeness.",
                            path=finding.path,
                        )
                    )
            elif finding.code in self._uncertain_findings:
                draft = True

        requirements, dependency_findings, unresolved = self._requirements(report, decisions)
        findings.extend(dependency_findings)
        draft = draft or unresolved

        if not decisions.data_confirmed:
            draft = True
            findings.append(
                Finding(
                    "DATA_NOT_CONFIRMED",
                    "The author has not confirmed that required input data "
                    "and configuration are included.",
                )
            )

        run_spec = decisions.run_spec
        if run_spec is None:
            draft = True
            findings.append(Finding("RUN_NOT_SELECTED", "Select the project launch target."))
        else:
            run_findings = self._validate_run(run_spec, included)
            findings.extend(run_findings)
            blocked = blocked or bool(run_findings)

        python_version = decisions.python_version
        if python_version is None:
            findings.append(
                Finding(
                    "PYTHON_VERSION_UNKNOWN",
                    "The project author has not supplied a tested Python version.",
                )
            )
        elif report.python_requires and not SpecifierSet(report.python_requires).contains(
            Version(python_version), prereleases=True
        ):
            draft = True
            findings.append(
                Finding(
                    "PYTHON_VERSION_CONFLICT",
                    f"Python {python_version} does not satisfy declared constraint "
                    f"{report.python_requires}.",
                )
            )

        if not included:
            blocked = True
            findings.append(
                Finding(
                    "NO_PROJECT_FILES", "No project files remain in the package.", severity="error"
                )
            )

        state = "blocked" if blocked else "draft" if draft else "ready_to_verify"
        return PackagePlan(
            report=report,
            requirements=tuple(requirements),
            run_spec=run_spec,
            python_version=python_version,
            files=included,
            excluded_files=excluded,
            findings=tuple(findings),
            state=state,
            description=decisions.description,
            input_description=decisions.input_description,
            expected_output=decisions.expected_output,
            decisions=decisions,
        )

    def _validate_decisions(self, report: AnalysisReport, decisions: UserDecisions) -> None:
        candidates = {candidate.module: candidate for candidate in report.dependencies}
        for module, raw_requirement in decisions.packages.items():
            if module not in candidates:
                raise ValueError(f"Unknown import module in package decisions: {module}")
            if candidates[module].category in {"local", "stdlib"}:
                raise ValueError(f"Cannot assign an external package to {module}")
            requirement = self._parse_requirement(raw_requirement)
            if requirement.url:
                raise ValueError("Package overrides must use package names, not direct URLs")

        version_keys = set(candidates)
        for candidate in report.dependencies:
            if candidate.package:
                version_keys.add(canonicalize_name(candidate.package))
        for raw_requirement in (*report.declared_requirements, *decisions.packages.values()):
            try:
                version_keys.add(canonicalize_name(Requirement(raw_requirement).name))
            except InvalidRequirement:
                continue
        for key, value in decisions.versions.items():
            if key not in version_keys and canonicalize_name(key) not in version_keys:
                raise ValueError(f"Unknown package in version decisions: {key}")
            self._version_specifier(value)
        if set(decisions.excluded_modules) - candidates.keys():
            raise ValueError("Excluded modules must belong to the analysis")
        if decisions.python_version is not None:
            try:
                version = Version(decisions.python_version)
            except InvalidVersion as error:
                raise ValueError("Invalid Python version") from error
            if version.major != 3 or version.is_prerelease or version.local:
                raise ValueError("Choose a stable Python 3 version")

    def _requirements(
        self, report: AnalysisReport, decisions: UserDecisions
    ) -> tuple[list[str], list[Finding], bool]:
        requirements: dict[str, Requirement] = {}
        findings: list[Finding] = []
        unresolved = False
        declared_names: set[str] = set()

        for raw_requirement in report.declared_requirements:
            try:
                requirement = self._parse_requirement(raw_requirement)
            except ValueError:
                unresolved = True
                findings.append(
                    Finding(
                        "INVALID_DECLARED_REQUIREMENT",
                        "A declared requirement could not be represented as a PEP 508 requirement.",
                    )
                )
                continue
            declared_names.add(canonicalize_name(requirement.name))
            self._apply_version(requirement, decisions.versions)
            requirements[str(requirement)] = requirement

        for candidate in report.dependencies:
            if candidate.category in {"local", "stdlib"}:
                continue
            if candidate.module in decisions.excluded_modules:
                findings.append(
                    Finding(
                        "MODULE_EXCLUDED",
                        f"The author excluded import module {candidate.module}; "
                        "verify runtime behavior.",
                        evidence_ids=candidate.evidence_ids,
                    )
                )
                continue
            override = decisions.packages.get(candidate.module)
            raw_requirement = override or candidate.requirement or candidate.package
            if not raw_requirement:
                unresolved = True
                findings.append(
                    Finding(
                        "DEPENDENCY_UNRESOLVED",
                        f"Specify the installable package for import module {candidate.module}.",
                        evidence_ids=candidate.evidence_ids,
                    )
                )
                continue
            try:
                requirement = self._parse_requirement(raw_requirement)
            except ValueError:
                unresolved = True
                findings.append(
                    Finding(
                        "DEPENDENCY_UNRESOLVED",
                        f"The package requirement for {candidate.module} is invalid.",
                        evidence_ids=candidate.evidence_ids,
                    )
                )
                continue
            name = canonicalize_name(requirement.name)
            if not override and name in declared_names:
                if candidate.module in decisions.versions:
                    for declared in requirements.values():
                        if canonicalize_name(declared.name) == name:
                            self._apply_version(declared, decisions.versions, candidate.module)
                    requirements = {str(item): item for item in requirements.values()}
                continue
            if override and candidate.requirement:
                original = self._parse_requirement(candidate.requirement)
                if requirement.marker is None:
                    requirement.marker = original.marker
            self._apply_version(requirement, decisions.versions, candidate.module)
            requirements[str(requirement)] = requirement

        for requirement in requirements.values():
            if requirement.url:
                unresolved = True
                findings.append(
                    Finding(
                        "DIRECT_REFERENCE_UNVERIFIED",
                        f"The declared direct reference for {requirement.name} "
                        "needs author review.",
                    )
                )
            elif not any(
                spec.operator in {"==", "==="} and "*" not in spec.version
                for spec in requirement.specifier
            ):
                findings.append(
                    Finding(
                        "VERSION_NOT_PINNED",
                        f"The exact tested version of {requirement.name} is unknown.",
                    )
                )

        groups: dict[tuple[str, str, tuple[str, ...]], list[Requirement]] = {}
        for requirement in requirements.values():
            key = (
                canonicalize_name(requirement.name),
                str(requirement.marker or ""),
                tuple(sorted(requirement.extras)),
            )
            groups.setdefault(key, []).append(requirement)
        for (name, _, _), group in groups.items():
            pinned = [
                spec.version
                for requirement in group
                for spec in requirement.specifier
                if spec.operator == "==" and "*" not in spec.version
            ]
            if pinned and any(
                not requirement.specifier.contains(version, prereleases=True)
                for version in pinned
                for requirement in group
            ):
                unresolved = True
                findings.append(
                    Finding("DEPENDENCY_CONFLICT", f"Declared constraints for {name} conflict.")
                )

        return sorted(requirements, key=str.casefold), findings, unresolved

    def _apply_version(
        self, requirement: Requirement, versions: dict[str, str], module: str | None = None
    ) -> None:
        normalized = {canonicalize_name(key): value for key, value in versions.items()}
        value = versions.get(module) if module else None
        if value is None:
            value = normalized.get(canonicalize_name(requirement.name))
        if value is not None:
            if requirement.url:
                raise ValueError("Cannot set a version on a direct-reference requirement")
            requirement.specifier = self._version_specifier(value)

    @staticmethod
    def _parse_requirement(value: str) -> Requirement:
        if not isinstance(value, str) or any(ord(char) < 32 for char in value):
            raise ValueError("Requirements must be single-line strings")
        try:
            return Requirement(value)
        except InvalidRequirement as error:
            raise ValueError("Invalid package requirement") from error

    @staticmethod
    def _version_specifier(value: str) -> SpecifierSet:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("A version override cannot be empty")
        if value[0].isdigit():
            value = f"=={value}"
        try:
            return SpecifierSet(value)
        except InvalidSpecifier as error:
            raise ValueError("Invalid package version specifier") from error

    def _select_files(
        self, files: tuple[FileInfo, ...], requested_exclusions: tuple[str, ...]
    ) -> tuple[tuple[FileInfo, ...], tuple[str, ...]]:
        prefixes = tuple(self._safe_path(path.rstrip("/")) for path in requested_exclusions)
        included = []
        excluded = []
        for file in sorted(files, key=lambda item: item.path):
            path = self._safe_path(file.path)
            parts = PurePosixPath(path).parts
            filename = parts[-1]
            omit = bool(set(parts[:-1]) & self._excluded_directories)
            omit = omit or filename == ".env" or filename.startswith(".env.")
            omit = omit or PurePosixPath(path).suffix.lower() in self._excluded_suffixes
            omit = omit or filename in {"id_rsa", "id_ed25519", "id_dsa", ".DS_Store"}
            omit = omit or any(
                path == prefix or path.startswith(prefix + "/") for prefix in prefixes
            )
            (excluded if omit else included).append(file.path if omit else file)
        return tuple(included), tuple(excluded)

    @staticmethod
    def _safe_path(value: str, allow_dot: bool = False) -> str:
        if not isinstance(value, str) or not value or "\\" in value:
            raise ValueError("Use nonempty relative POSIX paths")
        if any(ord(char) < 32 for char in value):
            raise ValueError("Paths cannot contain control characters")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", value):
            raise ValueError("Paths must stay inside the project")
        if str(path) == "." and not allow_dot:
            raise ValueError("A file path cannot be the project root")
        return str(path)

    def _validate_run(self, run_spec: RunSpec, files: tuple[FileInfo, ...]) -> list[Finding]:
        scanner = SecretScanner()
        values = (run_spec.target, run_spec.working_directory, *run_spec.args)
        if any(isinstance(value, str) and scanner.redact(value) != value for value in values):
            raise ValueError("Launch settings must not contain credentials")
        credential_flags = {"--token", "--password", "--api-key", "--api_key", "--secret"}
        for index, argument in enumerate(run_spec.args):
            if not isinstance(argument, str):
                raise ValueError("Launch arguments must be single-line strings")
            flag, separator, _ = argument.partition("=")
            if flag.lower() in credential_flags and (separator or index + 1 < len(run_spec.args)):
                raise ValueError("Launch arguments must not contain credentials")
        if run_spec.kind not in {"script", "module"}:
            raise ValueError("Launch kind must be script or module")
        cwd = self._safe_path(run_spec.working_directory, allow_dot=True)
        if any(
            not isinstance(arg, str) or any(ord(char) < 32 for char in arg) for arg in run_spec.args
        ):
            raise ValueError("Launch arguments must be single-line strings")
        included = {file.path for file in files}
        base = PurePosixPath(cwd)
        if run_spec.kind == "script":
            target = self._safe_path(run_spec.target)
            if not target.endswith(".py"):
                raise ValueError("The script target must be a Python file")
            found = str(base / target) in included
        else:
            if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", run_spec.target):
                raise ValueError("Invalid Python module target")
            module = base.joinpath(*run_spec.target.split("."))
            found = str(module) + ".py" in included or str(module / "__main__.py") in included
        if not found:
            return [
                Finding(
                    "RUN_TARGET_MISSING",
                    "The selected launch target is absent from included project files "
                    "at the chosen cwd.",
                    severity="error",
                )
            ]
        return []
