from __future__ import annotations

from dataclasses import asdict

from .imports import ImportScanner
from .manifests import ManifestReader
from .models import AnalysisOptions, AnalysisReport, Finding, ProjectSnapshot, stable_id
from .resolution import DependencyResolver, StandardLibraryProfile, normalize_roots


class ProjectAnalyzer:
    RULE_VERSION = "1.0"

    def analyze(
        self, snapshot: ProjectSnapshot, options: AnalysisOptions | None = None
    ) -> AnalysisReport:
        options = options or AnalysisOptions()
        roots = normalize_roots(options.source_roots)
        profile = StandardLibraryProfile(options.python_version)
        if options.max_source_bytes <= 0 or options.max_files <= 0:
            raise ValueError("Analysis limits must be positive")

        sources = sorted(snapshot.files, key=lambda source: source.path)
        findings: list[Finding] = []
        observations = []
        entrypoints = []
        partial = False
        if len(sources) > options.max_files:
            partial = True
            findings.append(
                Finding(
                    "FILE_LIMIT_EXCEEDED",
                    "Only the configured number of project files was analyzed.",
                )
            )
        if not profile.supported:
            findings.append(
                Finding(
                    "PYTHON_PROFILE_UNSUPPORTED",
                    "The requested Python version has no bundled standard-library profile. "
                    "Stdlib classification was skipped; syntax uses the host Python.",
                )
            )
            partial = True
        for root in roots:
            if root != "." and not any(source.path.startswith(root + "/") for source in sources):
                findings.append(
                    Finding(
                        "SOURCE_ROOT_MISSING",
                        "Configured source root contains no project files.",
                        root,
                    )
                )
                partial = True
        for source in sources[: options.max_files]:
            if not source.path.endswith(".py"):
                continue
            if len(source.content) > options.max_source_bytes:
                findings.append(
                    Finding(
                        "SOURCE_TOO_LARGE", "Python source exceeds the parsing limit.", source.path
                    )
                )
                partial = True
                continue
            parsed = ImportScanner.scan(source)
            observations.extend(parsed.imports)
            findings.extend(parsed.findings)
            partial = partial or not parsed.complete
            if parsed.has_main_guard or source.path in {"main.py", "__main__.py"}:
                entrypoints.append(source.path)

        manifests = ManifestReader(snapshot, options.max_source_bytes).read()
        findings.extend(manifests.findings)
        partial = partial or not manifests.complete
        result = DependencyResolver(
            snapshot, roots, options.python_version, manifests.requirements
        ).resolve(tuple(observations))
        findings.extend(result.findings)
        if manifests.python_requires is None:
            findings.append(
                Finding(
                    "PYTHON_VERSION_UNKNOWN",
                    "Project Python version is undeclared; the profile is not evidence.",
                )
            )
        findings.append(
            Finding(
                "DATA_DEPENDENCIES_UNVERIFIED",
                "Input files, configuration and external services need author confirmation.",
            )
        )
        unique_findings = {finding.id: finding for finding in findings}
        return AnalysisReport(
            analysis_id=stable_id(
                self.RULE_VERSION,
                snapshot.fingerprint,
                asdict(options),
                [(source.path, source.info.sha256) for source in sources],
            ),
            fingerprint=snapshot.fingerprint,
            options=options,
            files=tuple(source.info for source in sources),
            imports=tuple(observations),
            dependencies=result.dependencies,
            findings=tuple(
                sorted(
                    unique_findings.values(),
                    key=lambda finding: (
                        finding.path or "",
                        finding.line or 0,
                        finding.code,
                        finding.id,
                    ),
                )
            ),
            status="partial" if partial else "complete",
            declared_requirements=manifests.requirements,
            python_requires=manifests.python_requires,
            entrypoints=tuple(sorted(set(entrypoints))),
        )
