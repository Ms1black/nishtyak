from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

AdvisoryFeature = Literal["explain", "readme", "packages"]
AdvisoryStatus = Literal["ok", "unavailable", "invalid_response", "invalid_request"]
SuggestionKind = Literal["explanation", "readme_description", "package_hypothesis"]


@dataclass(frozen=True)
class AdvisorySuggestion:
    kind: SuggestionKind
    text: str
    evidence_ids: tuple[str, ...] = ()
    module: str | None = None
    package: str | None = None
    requires_confirmation: bool = field(default=True, init=False)


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class AdvisoryResult:
    analysis_id: str
    feature: str
    status: AdvisoryStatus
    suggestions: tuple[AdvisorySuggestion, ...] = ()
    usage: TokenUsage | None = None
    reason: str | None = None
    schema_version: int = field(default=1, init=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
