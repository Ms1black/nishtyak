from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .analysis import ProjectAnalyzer
from .inspection import ProjectInspector
from .models import (
    AnalysisOptions,
    AnalysisReport,
    GeneratedFiles,
    PackagePlan,
    ProjectSnapshot,
    UserDecisions,
)
from .planning import PackagePlanner
from .rendering import DocumentRenderer
from .security import SecretScanner
from .snapshot import SnapshotBuilder

if TYPE_CHECKING:
    from .environment import AuthorEnvironmentSnapshot


class ReproducibilityService:
    def __init__(
        self,
        analyzer: ProjectAnalyzer | None = None,
        planner: PackagePlanner | None = None,
        renderer: DocumentRenderer | None = None,
        scanner: SecretScanner | None = None,
    ):
        self.analyzer = analyzer or ProjectAnalyzer()
        self.planner = planner or PackagePlanner()
        self.renderer = renderer or DocumentRenderer()
        self.scanner = scanner or SecretScanner()

    def analyze(
        self,
        snapshot: ProjectSnapshot,
        options: AnalysisOptions | None = None,
        author_environment: AuthorEnvironmentSnapshot | None = None,
    ) -> AnalysisReport:
        SnapshotBuilder().validate(snapshot)
        report = self.analyzer.analyze(snapshot, options)
        if author_environment is not None:
            from .environment import AuthorEnvironmentInspector

            report = AuthorEnvironmentInspector().enrich(report, author_environment)
        findings = (
            *report.findings,
            *ProjectInspector().inspect(snapshot, report.layout.root if report.layout else "."),
            *self.scanner.scan(snapshot),
        )
        return replace(report, findings=findings)

    def prepare(
        self,
        report: AnalysisReport,
        decisions: UserDecisions,
        *,
        automatic_candidate_id: str | None = None,
    ) -> PackagePlan:
        cleaned = replace(
            decisions,
            description=self.scanner.redact(decisions.description),
            input_description=self.scanner.redact(decisions.input_description),
            expected_output=self.scanner.redact(decisions.expected_output),
        )
        return self.planner.prepare(report, cleaned, automatic_candidate_id=automatic_candidate_id)

    def render(self, plan: PackagePlan, include_dockerfile: bool = False) -> GeneratedFiles:
        generated = self.renderer.render(plan)
        if include_dockerfile:
            from .docker import DockerfileGenerator

            generated = GeneratedFiles((*generated.files, DockerfileGenerator().generate(plan)))
        return generated
