from __future__ import annotations

import re
from pathlib import PurePosixPath

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .launches import LaunchDetector
from .models import (
    AnalysisReport,
    DeclarationDecision,
    DependencyDeclaration,
    FileInfo,
    Finding,
    PackagePlan,
    ProjectSnapshot,
    RunSpec,
    SourceFile,
    UserDecisions,
)
from .resolution import LocalModuleIndex
from .security import SecretScanner


class DecisionError(ValueError):
    def __init__(self, code: str, field: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


class PackagePlanner:
    """Prepare a reviewable package without reading or executing project files."""

    _excluded_directories = frozenset(
        {
            ".git",
            ".hg",
            ".svn",
            ".venv",
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
            "AUTHOR_MAPPING_AMBIGUOUS",
            "AUTHOR_VERSION_AMBIGUOUS",
            "AUTHOR_PYTHON_CONFLICT",
            "LOCAL_MODULE_MISSING",
            "LOCAL_IMPORT_NAME_UNRESOLVED",
            "LOCAL_IMPORT_CONTEXT_AMBIGUOUS",
            "LOCAL_IMPORT_CONTEXT_REQUIRED",
        }
    )

    def prepare(
        self,
        report: AnalysisReport,
        decisions: UserDecisions,
        *,
        automatic_candidate_id: str | None = None,
    ) -> PackagePlan:
        if decisions.analysis_id != report.analysis_id:
            raise DecisionError(
                "ANALYSIS_STALE",
                "analysis_id",
                "Decisions belong to another analysis; analyze the project again",
            )

        self._validate_decisions(report, decisions)
        try:
            included, excluded = self._select_files(report.files, decisions.excluded_files)
        except ValueError as error:
            raise DecisionError(
                "EXCLUDED_FILE_PATH_INVALID",
                "excluded_files",
                "Excluded files must be relative paths inside the project",
            ) from error
        launch = LaunchDetector().detect(report, included)
        run_spec = decisions.run_spec
        run_spec_origin = "author" if run_spec is not None else "unknown"
        source_paths = {file.path for file in report.files if file.path.endswith(".py")}
        retained_paths = {file.path for file in included}
        automatic_candidate = next(
            (
                candidate
                for candidate in launch.candidates
                if candidate.id == automatic_candidate_id and source_paths.issubset(retained_paths)
            ),
            None,
        )
        if run_spec is None and automatic_candidate is not None:
            run_spec = automatic_candidate.run_spec
            run_spec_origin = "automatic"
        if run_spec is None and launch.recommended is not None:
            run_spec = launch.recommended.run_spec
            run_spec_origin = "inferred"
        findings = [
            finding
            for finding in report.findings
            if not self._finding_resolved(finding, report, decisions, run_spec)
        ]
        draft = report.status != "complete"
        local_findings = self._validate_local_imports(report, included)
        findings.extend(local_findings)
        blocked = bool(local_findings)

        for finding in report.findings:
            if self._finding_resolved(finding, report, decisions, run_spec):
                continue
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

        if run_spec_origin == "automatic":
            findings.append(
                Finding(
                    "RUN_AUTOMATIC",
                    "The assistant selected an existing static launch candidate from project "
                    "evidence. The command was not confirmed by the author or executed.",
                    severity="info",
                    evidence_ids=(automatic_candidate.id,),
                )
            )
        elif run_spec_origin == "inferred":
            findings.append(
                Finding(
                    "RUN_INFERRED",
                    launch.recommended.reason
                    + " This command was inferred, not confirmed by the author or executed.",
                    severity="info",
                    evidence_ids=(launch.recommended.id,),
                )
            )
        if run_spec is None:
            draft = True
            if len(launch.candidates) > 1:
                code = "RUN_TARGET_AMBIGUOUS"
                message = "Several possible launch commands remain; the draft contains candidates."
            elif launch.candidates:
                code = "RUN_TARGET_UNCERTAIN"
                message = (
                    "A possible launch command is listed; static facts do not establish "
                    "an executable entry point."
                )
            else:
                code = "RUN_TARGET_UNKNOWN"
                message = (
                    "No launch command was established statically; a dependency draft "
                    "can still be exported."
                )
            findings.append(Finding(code, message))
        else:
            run_findings = self._validate_run(run_spec, included, report)
            findings.extend(run_findings)
            blocked = blocked or bool(run_findings)

        python_version = decisions.python_version or report.author_python_version
        if python_version is None:
            if not any(finding.code == "PYTHON_VERSION_UNKNOWN" for finding in findings):
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
        if python_version is not None:
            target = Version(python_version)
            profile = Version(report.options.python_version)
            if (target.major, target.minor) != (profile.major, profile.minor):
                draft = True
                findings.append(
                    Finding(
                        "PYTHON_PROFILE_MISMATCH",
                        "Dependencies were classified for Python "
                        f"{report.options.python_version}, but the selected Python is "
                        f"{python_version}. Repeat analysis with "
                        f"options.python_version={target.major}.{target.minor} before locking.",
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
            description_origin="author" if decisions.description.strip() else "unknown",
            input_description=decisions.input_description,
            expected_output=decisions.expected_output,
            decisions=decisions,
            run_spec_origin=run_spec_origin,
        )

    def _validate_decisions(self, report: AnalysisReport, decisions: UserDecisions) -> None:
        declarations = {item.id: item for item in report.declarations}
        for key, action in decisions.declaration_actions.items():
            field = f"declaration_actions.{key}"
            if key not in declarations:
                raise DecisionError("DECLARATION_UNKNOWN", field, "Unknown dependency declaration")
            if not isinstance(action, DeclarationDecision) or action.action not in {
                "keep",
                "replace",
                "exclude",
            }:
                raise DecisionError(
                    "DECLARATION_ACTION_INVALID", field, "Invalid declaration action"
                )
            if action.action == "replace":
                self._replacement(declarations[key], action, field)
            elif action.requirement is not None:
                raise DecisionError(
                    "DECLARATION_ACTION_INVALID", field, "Only replacements accept a requirement"
                )
        groups = set(report.optional_groups)
        for group in decisions.selected_extras:
            if not isinstance(group, str) or canonicalize_name(group) not in groups:
                raise DecisionError("EXTRA_UNKNOWN", "selected_extras", "Unknown optional group")
        candidates = {candidate.module: candidate for candidate in report.dependencies}
        for module, raw_requirement in decisions.packages.items():
            if module not in candidates:
                raise DecisionError("PACKAGE_MODULE_UNKNOWN", "packages", "Unknown import module")
            if candidates[module].category in {"local", "stdlib"}:
                raise DecisionError(
                    "PACKAGE_MODULE_NOT_EXTERNAL",
                    f"packages.{module}",
                    "Local and standard library imports cannot be assigned external packages",
                )
            try:
                requirement = self._parse_requirement(raw_requirement)
            except ValueError as error:
                raise DecisionError(
                    "PACKAGE_REQUIREMENT_INVALID",
                    f"packages.{module}",
                    "Package override must be a valid single-line PEP 508 requirement",
                ) from error
            if requirement.url:
                raise DecisionError(
                    "PACKAGE_DIRECT_REFERENCE_UNSUPPORTED",
                    f"packages.{module}",
                    "Package overrides must use package names, not direct URLs",
                )

        version_keys = set(candidates)
        for candidate in report.dependencies:
            if candidate.package:
                version_keys.add(canonicalize_name(candidate.package))
        for raw_requirement in (
            *report.declared_requirements,
            *(item.requirement for item in report.declarations),
            *decisions.packages.values(),
            *(
                action.requirement
                for action in decisions.declaration_actions.values()
                if action.requirement is not None
            ),
        ):
            try:
                version_keys.add(canonicalize_name(Requirement(raw_requirement).name))
            except InvalidRequirement:
                continue
        for key, value in decisions.versions.items():
            if key not in version_keys and canonicalize_name(key) not in version_keys:
                raise DecisionError("VERSION_PACKAGE_UNKNOWN", "versions", "Unknown package")
            try:
                self._version_specifier(value)
            except ValueError as error:
                raise DecisionError(
                    "VERSION_SPECIFIER_INVALID",
                    f"versions.{key}",
                    "Version override must be a valid nonempty version specifier",
                ) from error
        if set(decisions.excluded_modules) - candidates.keys():
            raise DecisionError(
                "EXCLUDED_MODULE_UNKNOWN",
                "excluded_modules",
                "Excluded modules must belong to the analysis",
            )
        if decisions.python_version is not None:
            try:
                version = Version(decisions.python_version)
            except (InvalidVersion, TypeError) as error:
                raise DecisionError(
                    "PYTHON_VERSION_INVALID", "python_version", "Invalid Python version"
                ) from error
            if (
                not re.fullmatch(r"3\.\d+(?:\.\d+)?", decisions.python_version)
                or version.is_prerelease
                or version.local
            ):
                raise DecisionError(
                    "PYTHON_VERSION_INVALID", "python_version", "Choose a stable Python 3 version"
                )

    def _requirements(
        self, report: AnalysisReport, decisions: UserDecisions
    ) -> tuple[list[str], list[Finding], bool]:
        requirements: dict[str, Requirement] = {}
        findings: list[Finding] = []
        unresolved = False
        declared_names: set[str] = set()
        removed_names: set[str] = set()
        declaration_values: list[str] = []
        selected = {canonicalize_name(group) for group in decisions.selected_extras}
        if report.declarations:
            for declaration in report.declarations:
                original = self._parse_requirement(declaration.requirement)
                action = decisions.declaration_actions.get(declaration.id, DeclarationDecision())
                if (
                    declaration.group is not None and declaration.group not in selected
                ) or action.action == "exclude":
                    removed_names.add(canonicalize_name(original.name))
                    continue
                effective = (
                    self._replacement(declaration, action, f"declaration_actions.{declaration.id}")
                    if action.action == "replace"
                    else original
                )
                if canonicalize_name(effective.name) != canonicalize_name(original.name):
                    removed_names.add(canonicalize_name(original.name))
                declaration_values.append(str(effective))
        else:
            declaration_values.extend(report.declared_requirements)

        for raw_requirement in declaration_values:
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
            mapped_name = canonicalize_name(candidate.package or candidate.module)
            if not override and mapped_name in removed_names and mapped_name not in declared_names:
                if candidate.evidence_ids:
                    unresolved = True
                    findings.append(
                        Finding(
                            "DECLARATION_IMPORT_UNRESOLVED",
                            f"Import {candidate.module} still needs a package after its "
                            "declaration "
                            "was removed or its optional group was not selected. Map it explicitly "
                            "or exclude the import.",
                            evidence_ids=candidate.evidence_ids,
                        )
                    )
                continue
            if not raw_requirement and mapped_name in declared_names:
                continue
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
                changed = False
                for declared in requirements.values():
                    if canonicalize_name(declared.name) == name:
                        if candidate.module in decisions.versions:
                            self._apply_version(declared, decisions.versions, candidate.module)
                            changed = True
                        elif (
                            candidate.status == "author_environment"
                            and not declared.specifier
                            and requirement.specifier
                        ):
                            declared.specifier = requirement.specifier
                            changed = True
                if changed:
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

        groups: dict[tuple[str, str], list[Requirement]] = {}
        for requirement in requirements.values():
            key = (
                canonicalize_name(requirement.name),
                str(requirement.marker or ""),
            )
            groups.setdefault(key, []).append(requirement)
        for (name, _), group in groups.items():
            pinned = {
                Version(spec.version)
                for requirement in group
                for spec in requirement.specifier
                if spec.operator == "==" and "*" not in spec.version
            }
            conflict = len(pinned) > 1
            if len(pinned) == 1:
                version = next(iter(pinned))
                conflict = any(
                    not requirement.specifier.contains(version, prereleases=True)
                    for requirement in group
                )
            if conflict:
                unresolved = True
                findings.append(
                    Finding("DEPENDENCY_CONFLICT", f"Declared constraints for {name} conflict.")
                )

        return sorted(requirements, key=str.casefold), findings, unresolved

    def _replacement(
        self, declaration: DependencyDeclaration, action: DeclarationDecision, field: str
    ) -> Requirement:
        try:
            replacement = self._parse_requirement(action.requirement)
            if replacement.url:
                raise ValueError("Direct references are unsupported")
        except ValueError as error:
            raise DecisionError(
                "DECLARATION_REQUIREMENT_INVALID",
                field + ".requirement",
                "Replacement must be a named PEP 508 requirement",
            ) from error
        original = self._parse_requirement(declaration.requirement)
        if replacement.marker is None:
            replacement.marker = original.marker
        if not replacement.extras:
            replacement.extras = original.extras
        return replacement

    @staticmethod
    def _finding_resolved(
        finding: Finding,
        report: AnalysisReport,
        decisions: UserDecisions,
        run_spec: RunSpec | None = None,
    ) -> bool:
        if finding.code == "DATA_DEPENDENCIES_UNVERIFIED":
            return decisions.data_confirmed
        if finding.code == "PYTHON_VERSION_UNKNOWN":
            return decisions.python_version is not None or report.author_python_version is not None
        if finding.code != "SECRET_DETECTED" and finding.path is not None:
            if any(
                finding.path == prefix.rstrip("/")
                or finding.path.startswith(prefix.rstrip("/") + "/")
                for prefix in decisions.excluded_files
            ):
                return True
        if finding.code in {
            "DEPENDENCY_CONFLICT",
            "DEPENDENCY_UNRESOLVED",
            "DEPENDENCY_DECLARATIONS_MULTIPLE",
            "VERSION_UNKNOWN",
        }:
            # Effective requirements and version uncertainties are recomputed
            # below from the current mappings and declaration decisions.
            return True
        if finding.code == "LOCAL_IMPORT_CONTEXT_REQUIRED":
            return PackagePlanner._script_context_selected(finding, report, run_spec)
        if finding.code == "OPTIONAL_DEPENDENCIES_UNSELECTED":
            return True
        if finding.code == "AUTHOR_PYTHON_CONFLICT":
            return decisions.python_version is not None
        if finding.code not in {
            "AUTHOR_ENVIRONMENT_CONFLICT",
            "AUTHOR_VERSION_AMBIGUOUS",
            "AUTHOR_MAPPING_CONFLICT",
            "AUTHOR_MAPPING_AMBIGUOUS",
        }:
            return False
        candidates = [
            candidate
            for candidate in report.dependencies
            if {candidate.id, *candidate.evidence_ids} & set(finding.evidence_ids)
        ]
        if not candidates:
            return False
        versions = {canonicalize_name(key) for key in decisions.versions}
        changed = {
            canonicalize_name(Requirement(declaration.requirement).name)
            for declaration in report.declarations
            if declaration.id in decisions.declaration_actions
            and decisions.declaration_actions[declaration.id].action != "keep"
        }
        for candidate in candidates:
            if candidate.module in decisions.excluded_modules:
                continue
            if candidate.module in decisions.packages:
                continue
            if finding.code in {"AUTHOR_MAPPING_CONFLICT", "AUTHOR_MAPPING_AMBIGUOUS"}:
                return False
            if canonicalize_name(candidate.module) in versions:
                continue
            package = canonicalize_name(candidate.package or candidate.module)
            if package not in versions and package not in changed:
                return False
        return True

    @staticmethod
    def _script_context_selected(
        finding: Finding, report: AnalysisReport, run: RunSpec | None
    ) -> bool:
        if run is None or run.kind != "script":
            return False
        script_path = str(PurePosixPath(run.working_directory) / run.target)
        if finding.path != script_path:
            return False
        observations = [
            item for item in report.imports if item.id in finding.evidence_ids and not item.level
        ]
        if not observations:
            return False
        roots = report.layout.source_roots if report.layout else report.options.source_roots
        index = LocalModuleIndex(
            ProjectSnapshot(tuple(SourceFile(file.path, b"") for file in report.files), ""), roots
        )
        parent = str(PurePosixPath(script_path).parent)
        # Only the selected entry script is guaranteed to add its own directory.
        # Retain warnings for imports in other files or conflicting providers.
        return all(
            index.resolve_module(item.module).status == "external"
            and index.resolve_module(item.module, (parent, *roots)).found
            for item in observations
        )

    def _apply_version(
        self, requirement: Requirement, versions: dict[str, str], module: str | None = None
    ) -> None:
        normalized = {canonicalize_name(key): value for key, value in versions.items()}
        value = versions.get(module) if module else None
        if value is None:
            value = normalized.get(canonicalize_name(requirement.name))
        if value is not None:
            if requirement.url:
                raise DecisionError(
                    "VERSION_DIRECT_REFERENCE_UNSUPPORTED",
                    "versions",
                    "Cannot set a version on a direct-reference requirement",
                )
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
        if (
            not isinstance(value, str)
            or not value.strip()
            or any(ord(character) < 32 for character in value)
        ):
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
        environment_roots = self._virtual_environment_roots(files)
        included = []
        excluded = []
        for file in sorted(files, key=lambda item: item.path):
            path = self._safe_path(file.path)
            parts = PurePosixPath(path).parts
            filename = parts[-1]
            omit = bool(set(parts[:-1]) & self._excluded_directories)
            omit = omit or any(path.startswith(root + "/") for root in environment_roots)
            omit = omit or filename == ".env" or filename.startswith(".env.")
            omit = omit or PurePosixPath(path).suffix.lower() in self._excluded_suffixes
            omit = omit or filename in {"id_rsa", "id_ed25519", "id_dsa", ".DS_Store"}
            omit = omit or any(
                path == prefix or path.startswith(prefix + "/") for prefix in prefixes
            )
            (excluded if omit else included).append(file.path if omit else file)
        return tuple(included), tuple(excluded)

    @staticmethod
    def _virtual_environment_roots(files: tuple[FileInfo, ...]) -> set[str]:
        paths = {file.path for file in files}
        roots = {
            str(PurePosixPath(path).parent)
            for path in paths
            if PurePosixPath(path).name == "pyvenv.cfg" and str(PurePosixPath(path).parent) != "."
        }
        candidates = set()
        for path in paths:
            parts = PurePosixPath(path).parts
            for index, part in enumerate(parts[:-1]):
                if part in {"env", "venv"}:
                    candidates.add("/".join(parts[: index + 1]))
        for root in candidates:
            activation = {root + "/bin/activate", root + "/Scripts/activate"}
            interpreters = {root + "/bin/python", root + "/Scripts/python.exe"}
            site_packages = any(
                path.startswith(root + "/") and "site-packages" in PurePosixPath(path).parts
                for path in paths
            )
            if paths & activation and (paths & interpreters or site_packages):
                roots.add(root)
        return roots

    @staticmethod
    def _validate_local_imports(
        report: AnalysisReport, included: tuple[FileInfo, ...]
    ) -> list[Finding]:
        original = LocalModuleIndex(
            ProjectSnapshot(tuple(SourceFile(file.path, b"") for file in report.files), ""),
            report.layout.source_roots if report.layout else report.options.source_roots,
        )
        retained = LocalModuleIndex(
            ProjectSnapshot(tuple(SourceFile(file.path, b"") for file in included), ""),
            report.layout.source_roots if report.layout else report.options.source_roots,
        )
        included_paths = {file.path for file in included}
        original_providers = set(original.file_modules.values())
        retained_providers = set(retained.file_modules.values())
        findings = []
        for observation in report.imports:
            if observation.path not in included_paths:
                continue
            if observation.level:
                module = original.resolve_relative(observation)
                if module is None:
                    continue
            else:
                module = observation.module
                if module not in original.modules:
                    module = module.split(".", 1)[0]
                if module not in original.modules:
                    continue
            required = {module}
            if observation.kind == "from":
                required.update(
                    f"{module}.{name}"
                    for name in observation.names
                    if f"{module}.{name}" in original.modules
                )
            missing = set()
            for target in required:
                if target not in retained.modules:
                    missing.add(target)
                parts = target.split(".")
                for length in range(1, len(parts) + 1):
                    parent = ".".join(parts[:length])
                    if parent in original_providers and parent not in retained_providers:
                        missing.add(parent)
            if missing:
                findings.append(
                    Finding(
                        "LOCAL_MODULE_EXCLUDED",
                        "An included source imports local modules removed from the package: "
                        + ", ".join(sorted(missing))
                        + ". Restore their files or exclude the importer.",
                        path=observation.path,
                        line=observation.line,
                        severity="error",
                        evidence_ids=(observation.id,),
                    )
                )
        return findings

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

    def _validate_run(
        self, run_spec: RunSpec, files: tuple[FileInfo, ...], report: AnalysisReport
    ) -> list[Finding]:
        scanner = SecretScanner()
        values = (run_spec.target, run_spec.working_directory, *run_spec.args)
        if any(isinstance(value, str) and scanner.redact(value) != value for value in values):
            raise DecisionError(
                "RUN_CREDENTIALS_UNSUPPORTED",
                "run_spec",
                "Launch settings must not contain credentials",
            )
        credential_flags = {"--token", "--password", "--api-key", "--api_key", "--secret"}
        for index, argument in enumerate(run_spec.args):
            if not isinstance(argument, str):
                raise DecisionError(
                    "RUN_ARGUMENT_INVALID",
                    "run_spec.args",
                    "Launch arguments must be single-line strings",
                )
            flag, separator, _ = argument.partition("=")
            if flag.lower() in credential_flags and (separator or index + 1 < len(run_spec.args)):
                raise DecisionError(
                    "RUN_CREDENTIALS_UNSUPPORTED",
                    "run_spec.args",
                    "Launch arguments must not contain credentials",
                )
        if run_spec.kind not in {"script", "module", "callable"}:
            raise DecisionError(
                "RUN_KIND_INVALID",
                "run_spec.kind",
                "Launch kind must be script, module or callable",
            )
        try:
            cwd = self._safe_path(run_spec.working_directory, allow_dot=True)
        except ValueError as error:
            raise DecisionError(
                "RUN_DIRECTORY_INVALID",
                "run_spec.working_directory",
                "Working directory must be a relative path inside the project",
            ) from error
        if any(
            not isinstance(arg, str) or any(ord(char) < 32 for char in arg) for arg in run_spec.args
        ):
            raise DecisionError(
                "RUN_ARGUMENT_INVALID",
                "run_spec.args",
                "Launch arguments must be single-line strings",
            )
        included = {file.path for file in files}
        base = PurePosixPath(cwd)
        if cwd != "." and not any(path.startswith(cwd + "/") for path in included):
            return [
                Finding(
                    "RUN_WORKING_DIRECTORY_MISSING",
                    "The selected working directory is absent from included project files.",
                    severity="error",
                )
            ]
        if run_spec.kind == "script":
            try:
                target = self._safe_path(run_spec.target)
            except ValueError as error:
                raise DecisionError(
                    "RUN_TARGET_INVALID",
                    "run_spec.target",
                    "Script target must be a relative path inside the project",
                ) from error
            if not target.endswith(".py"):
                raise DecisionError(
                    "RUN_TARGET_INVALID",
                    "run_spec.target",
                    "The script target must be a Python file",
                )
            found = str(base / target) in included
        else:
            target = run_spec.target
            if run_spec.kind == "callable":
                target, separator, attribute = target.partition(":")
                if not separator or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", attribute):
                    raise DecisionError(
                        "RUN_TARGET_INVALID",
                        "run_spec.target",
                        "Callable target must be module:attribute",
                    )
            if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", target):
                raise DecisionError(
                    "RUN_TARGET_INVALID", "run_spec.target", "Invalid Python module target"
                )
            roots = report.layout.source_roots if report.layout else report.options.source_roots
            found = False
            for root in set((cwd, *roots)):
                module = PurePosixPath(root).joinpath(*target.split("."))
                candidates = {str(module) + ".py"}
                candidates.add(
                    str(module / ("__init__.py" if run_spec.kind == "callable" else "__main__.py"))
                )
                found = found or bool(candidates & included)
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
