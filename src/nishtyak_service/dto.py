from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from nishtyak_core.models import AnalysisOptions, AnalysisReport, RunSpec, UserDecisions

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
    source_roots: Annotated[list[PathText], Field(min_length=1, max_length=32)] = Field(
        default_factory=lambda: ["."]
    )
    max_source_bytes: Annotated[int, Field(ge=1, le=1_000_000)] = 1_000_000
    max_files: Annotated[int, Field(ge=1, le=1000)] = 1000

    def to_core(self) -> AnalysisOptions:
        return AnalysisOptions(
            python_version=self.python_version,
            source_roots=tuple(self.source_roots),
            max_source_bytes=self.max_source_bytes,
            max_files=self.max_files,
        )


class RunSpecDTO(StrictDTO):
    target: PathText
    kind: Literal["script", "module"] = "script"
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
        )


class BundleDTO(StrictDTO):
    decisions: DecisionsDTO
    include_dockerfile: bool = False


class AdviceDTO(StrictDTO):
    feature: Literal["explain", "readme", "packages"] = "explain"
    description: DescriptionText = ""


class SubmittedProjectDTO(StrictDTO):
    project_id: ObjectId
    job_id: ObjectId
    status: Literal["queued", "running"]


class ErrorDTO(StrictDTO):
    code: str
    message: str


class ProjectReportDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: ObjectId
    analysis: AnalysisReport


class JobDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: ObjectId
    job_id: ObjectId
    status: Literal["queued", "running", "succeeded", "failed", "timed_out", "resource_limited"]
    created_at: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    expires_at: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    analysis: AnalysisReport | None = None
    error: ErrorDTO | None = None
