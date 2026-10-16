from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from nishtyak_core.environment import AuthorEnvironmentSnapshot
from nishtyak_core.import_graph import ImportEdge
from nishtyak_core.launches import LaunchAssessment
from nishtyak_core.models import (
    AnalysisOptions,
    AnalysisReport,
    DeclarationDecision,
    PackagePlan,
    RunSpec,
    UserDecisions,
)

ShortText = Annotated[str, StringConstraints(max_length=2048)]
PathText = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
DescriptionText = Annotated[str, StringConstraints(max_length=8192)]
ArgumentText = Annotated[str, StringConstraints(max_length=1024)]
PythonVersion = Annotated[str, StringConstraints(min_length=1, max_length=16)]
AnalysisId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{20}$")]
ObjectId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]


class StrictDTO(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class AnalysisOptionsDTO(StrictDTO):
    python_version: PythonVersion = AnalysisOptions().python_version
    python_version_auto: bool = False
    source_roots: Annotated[list[PathText], Field(min_length=1, max_length=32)] = Field(
        default_factory=lambda: ["."]
    )
    max_source_bytes: Annotated[int, Field(ge=1, le=1_000_000)] = 1_000_000
    max_files: Annotated[int, Field(ge=1, le=1000)] = 1000
    project_root: PathText | None = None

    def to_core(self) -> AnalysisOptions:
        return AnalysisOptions(
            python_version=self.python_version,
            source_roots=tuple(self.source_roots),
            max_source_bytes=self.max_source_bytes,
            max_files=self.max_files,
            project_root=self.project_root,
            python_version_auto=self.python_version_auto
            if {"python_version", "python_version_auto"} & self.model_fields_set
            else True,
        )


class InstalledDistributionDTO(StrictDTO):
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    version: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    modules: Annotated[list[ShortText], Field(max_length=1000)] = Field(default_factory=list)


class AuthorEnvironmentDTO(StrictDTO):
    python_version: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    platform: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    distributions: Annotated[list[InstalledDistributionDTO], Field(max_length=20000)]
    # Local collection may include metadata warnings. These are not trusted as analysis facts.
    findings: Annotated[list[dict], Field(max_length=1000)] = Field(default_factory=list)

    def to_core(self) -> AuthorEnvironmentSnapshot:
        return AuthorEnvironmentSnapshot.from_dict(self.model_dump(exclude={"findings"}))


class ReanalyzeDTO(StrictDTO):
    options: AnalysisOptionsDTO = Field(default_factory=AnalysisOptionsDTO)
    author_environment: AuthorEnvironmentDTO | None = None


class DeclarationDecisionDTO(StrictDTO):
    action: Literal["keep", "replace", "exclude"] = "keep"
    requirement: ShortText | None = None

    def to_core(self) -> DeclarationDecision:
        return DeclarationDecision(self.action, self.requirement)


class RunSpecDTO(StrictDTO):
    target: PathText
    kind: Literal["script", "module", "callable"] = "script"
    args: Annotated[list[ArgumentText], Field(max_length=64)] = Field(default_factory=list)
    working_directory: PathText = "."

    def to_core(self) -> RunSpec:
        return RunSpec(self.target, self.kind, tuple(self.args), self.working_directory)


class DecisionsDTO(StrictDTO):
    analysis_id: AnalysisId
    packages: Annotated[dict[ShortText, ShortText], Field(max_length=1000)] = Field(
        default_factory=dict
    )
    versions: Annotated[dict[ShortText, ShortText], Field(max_length=1000)] = Field(
        default_factory=dict
    )
    excluded_modules: Annotated[list[ShortText], Field(max_length=1000)] = Field(
        default_factory=list
    )
    excluded_files: Annotated[list[PathText], Field(max_length=1000)] = Field(default_factory=list)
    run_spec: RunSpecDTO | None = None
    python_version: PythonVersion | None = None
    description: DescriptionText = ""
    input_description: DescriptionText = ""
    expected_output: DescriptionText = ""
    data_confirmed: bool = False
    selected_extras: Annotated[list[ShortText], Field(max_length=1000)] = Field(
        default_factory=list
    )
    declaration_actions: Annotated[
        dict[AnalysisId, DeclarationDecisionDTO], Field(max_length=10000)
    ] = Field(default_factory=dict)

    def to_core(self) -> UserDecisions:
        return UserDecisions(
            analysis_id=self.analysis_id,
            packages=self.packages,
            versions=self.versions,
            excluded_modules=tuple(self.excluded_modules),
            excluded_files=tuple(self.excluded_files),
            run_spec=self.run_spec.to_core() if self.run_spec else None,
            python_version=self.python_version,
            description=self.description,
            input_description=self.input_description,
            expected_output=self.expected_output,
            data_confirmed=self.data_confirmed,
            selected_extras=tuple(self.selected_extras),
            declaration_actions={
                key: value.to_core() for key, value in self.declaration_actions.items()
            },
        )


class SaveDecisionsDTO(StrictDTO):
    expected_revision: Annotated[int, Field(ge=0, le=2**53 - 1)]
    decisions: DecisionsDTO


class DecisionDocumentDTO(StrictDTO):
    analysis_id: AnalysisId
    revision: Annotated[int, Field(ge=0)]
    decisions: DecisionsDTO | None
    updated_at: float | None


class BundleDTO(StrictDTO):
    decisions: DecisionsDTO | None = None
    include_dockerfile: bool = False
    lock_id: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None = None


class ResolutionTargetDTO(StrictDTO):
    python_version: Annotated[str, StringConstraints(pattern=r"^3\.(8|9|1[0-4])(\.[0-9]{1,3})?$")]
    platform: Literal[
        "linux-x86_64", "linux-aarch64", "macos-arm64", "macos-x86_64", "windows-x86_64"
    ]


class ResolveDTO(StrictDTO):
    target: ResolutionTargetDTO
    decisions: DecisionsDTO | None = None


class AdviceDTO(StrictDTO):
    feature: Literal["explain", "readme", "packages"] = "explain"
    description: DescriptionText = ""


class ProjectAdviceDTO(StrictDTO):
    feature: Literal["explain", "readme", "packages", "entrypoint"] = "explain"
    description: DescriptionText = ""
    share_source: Literal[True]


class SubmittedProjectDTO(StrictDTO):
    project_id: ObjectId
    job_id: ObjectId
    status: Literal["queued", "running"]


class ErrorDTO(StrictDTO):
    code: str
    message: str
    field: str | None = None


class ProjectReportDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: ObjectId
    analysis: AnalysisReport


class PreparationDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: ObjectId
    plan: PackagePlan
    launch: LaunchAssessment
    automatic_review: dict | None = None
    automatic_resolution: dict | None = None
    automation: dict | None = None


class ImportGraphDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[str]
    edges: list[ImportEdge]


class JobDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: ObjectId
    job_id: ObjectId
    status: Literal["queued", "running", "succeeded", "failed", "timed_out", "resource_limited"]
    created_at: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    expires_at: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    analysis: AnalysisReport | None = None
    error: ErrorDTO | None = None
    automatic_review: dict | None = None
    automatic_resolution: dict | None = None
    automation: dict | None = None
