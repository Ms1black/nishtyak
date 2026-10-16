from __future__ import annotations

import json
from dataclasses import replace

import pytest

from nishtyak_core.advisors import AdvisoryService, DeepSeekClient, DeepSeekConfig
from nishtyak_core.advisors.client import HttpRequest, HttpResponse
from nishtyak_core.models import (
    AnalysisOptions,
    AnalysisReport,
    DependencyCandidate,
    Finding,
    ImportObservation,
)


class FakeTransport:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(self.status, self.body)


class OfflineTransport:
    def send(self, request: HttpRequest) -> HttpResponse:
        raise TimeoutError()


@pytest.fixture
def report() -> AnalysisReport:
    return AnalysisReport(
        analysis_id="analysis-1",
        fingerprint="fingerprint",
        options=AnalysisOptions(),
        files=(),
        imports=(ImportObservation("yaml", "main.py", 1, id="import-1"),),
        findings=(Finding("UNKNOWN_PACKAGE", "Do not transmit raw text", id="finding-1"),),
    )


def response(
    *, feature: str = "explain", suggestions: list[dict] | None = None, **changes: object
) -> bytes:
    document = {
        "schema_version": 1,
        "analysis_id": "analysis-1",
        "feature": feature,
        "suggestions": suggestions
        if suggestions is not None
        else [
            {
                "kind": "explanation",
                "text": "Уточните зависимость.",
                "evidence_ids": ["finding-1"],
            }
        ],
    }
    document.update(changes)
    return json.dumps(
        {
            "choices": [{"message": {"content": json.dumps(document)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 25, "completion_tokens": 15, "total_tokens": 40},
        }
    ).encode()


def service(transport: FakeTransport | OfflineTransport, **kwargs: object) -> AdvisoryService:
    config = DeepSeekConfig(model="configured-model", api_key="private-api-key", **kwargs)
    return AdvisoryService(DeepSeekClient(config, transport))


def test_no_key_leaves_report_usable_and_makes_no_request(report: AnalysisReport) -> None:
    transport = FakeTransport(response())
    advisor = AdvisoryService(DeepSeekClient(DeepSeekConfig(model="configured-model"), transport))
    before = report.to_dict()

    result = advisor.advise(report)

    assert result.status == "unavailable"
    assert result.reason == "missing_api_key"
    assert transport.requests == []
    assert report.to_dict() == before


def test_model_must_be_explicit_and_env_is_read_on_request(
    report: AnalysisReport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    assert DeepSeekConfig.from_env().model == ""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "runtime-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "runtime-model")
    client = DeepSeekClient.from_env(FakeTransport(response()))
    assert client.config.model == "runtime-model"
    assert "runtime-key" not in repr(client.config)
    no_model = AdvisoryService(DeepSeekClient(DeepSeekConfig(api_key="key")))
    assert no_model.advise(report).reason == "missing_model"


def test_valid_response_exposes_usage_and_requires_confirmation(report: AnalysisReport) -> None:
    transport = FakeTransport(response())

    result = service(transport).advise(report)

    assert result.status == "ok"
    assert result.usage is not None and result.usage.total_tokens == 40
    assert result.suggestions[0].requires_confirmation
    request = transport.requests[0]
    payload = json.loads(request.body)
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["model"] == "configured-model"
    assert request.url == "https://api.deepseek.com/chat/completions"
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"broken",
        b"[]",
        b"{}",
        b'{"choices":[4]}',
        b'{"choices":[{"finish_reason":"stop","message":[]}]}',
        b'{"choices":[{"finish_reason":"stop","message":{"content":""}}]}',
        b'{"choices":[{"finish_reason":"stop","message":{"content":"bad json"}}]}',
        b'{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}',
    ],
)
def test_bad_json_and_envelopes_are_contained(report: AnalysisReport, body: bytes) -> None:
    assert service(FakeTransport(body)).advise(report).status == "invalid_response"


@pytest.mark.parametrize(
    "changes",
    [
        {"analysis_id": "another-analysis"},
        {"schema_version": 2},
        {"schema_version": True},
        {"feature": "packages"},
        {"extra": "unexpected"},
        {"suggestions": [{"kind": "explanation", "text": "Guess", "evidence_ids": ["made-up"]}]},
        {"suggestions": [{"kind": "package_hypothesis", "text": "Guess", "evidence_ids": []}]},
        {
            "suggestions": [
                {
                    "kind": "explanation",
                    "text": "Guess",
                    "evidence_ids": [],
                    "command": "execute this",
                }
            ]
        },
    ],
)
def test_unknown_identity_evidence_or_fields_are_rejected(
    report: AnalysisReport, changes: dict[str, object]
) -> None:
    assert service(FakeTransport(response(**changes))).advise(report).status == "invalid_response"


@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_unavailable_http_status_has_no_retry(report: AnalysisReport, status: int) -> None:
    transport = FakeTransport(b"sensitive provider error body", status)
    result = service(transport).advise(report)
    assert result.status == "unavailable"
    assert result.reason == "http_" + str(status)
    assert len(transport.requests) == 1


def test_offline_and_oversized_response_are_contained(report: AnalysisReport) -> None:
    assert service(OfflineTransport()).advise(report).status == "unavailable"
    result = service(FakeTransport(response()), max_response_bytes=10).advise(report)
    assert result.status == "invalid_response"


def test_allowlist_redacts_description_and_does_not_send_source(report: AnalysisReport) -> None:
    transport = FakeTransport(response())
    description = (
        'DEEPSEEK_API_KEY="secret-test-value" token=another-secret-value '
        "https://user:private@example.internal/project sk-abcd1234short private-api-key"
    )
    service(transport).advise(report, description=description)
    transmitted = transport.requests[0].body.decode()
    for secret in (
        "secret-test-value",
        "another-secret-value",
        "private@example.internal",
        "sk-abcd1234short",
        "private-api-key",
        "Do not transmit raw text",
        "fingerprint",
    ):
        assert secret not in transmitted
    assert "[REDACTED]" in transmitted
    assert "yaml" in transmitted


def test_package_suggestion_is_hypothesis_and_never_changes_dependencies(
    report: AnalysisReport,
) -> None:
    transport = FakeTransport(
        response(
            feature="packages",
            suggestions=[
                {
                    "kind": "package_hypothesis",
                    "module": "yaml",
                    "package": "PyYAML",
                    "text": "Проверьте соответствие yaml и PyYAML.",
                    "evidence_ids": ["import-1"],
                }
            ],
        )
    )
    before = report.to_dict()

    result = service(transport).advise(report, "packages")

    assert result.status == "ok"
    assert result.suggestions[0].package == "PyYAML"
    assert result.suggestions[0].requires_confirmation
    assert report.dependencies == ()
    assert report.to_dict() == before


def test_unknown_feature_or_schema_never_calls_provider(report: AnalysisReport) -> None:
    transport = FakeTransport(response())
    advisor = service(transport)
    assert advisor.advise(report, "execute").status == "invalid_request"
    assert advisor.advise(replace(report, schema_version=4)).status == "invalid_request"
    assert not transport.requests


@pytest.mark.parametrize("secret", ["github_pat_" + "a" * 25, "sk_" + "a" * 25])
def test_credential_shaped_dynamic_imports_and_packages_are_not_transmitted(
    report: AnalysisReport, secret: str
) -> None:
    report = replace(
        report,
        imports=(*report.imports, ImportObservation(secret, "main.py", 2, kind="dynamic")),
        dependencies=(DependencyCandidate("yaml", "unknown", package=secret),),
    )
    transport = FakeTransport(response())

    service(transport).advise(report)

    assert secret.encode() not in transport.requests[0].body
    metadata = json.loads(json.loads(transport.requests[0].body)["messages"][1]["content"])
    assert metadata["dependencies"][0]["package"] is None
    assert [item["module"] for item in metadata["imports"]] == ["yaml"]


def test_identifier_matching_configured_key_is_not_transmitted(report: AnalysisReport) -> None:
    key = "configured_secret_identifier"
    report = replace(
        report, imports=(*report.imports, ImportObservation(key, "main.py", 2, kind="dynamic"))
    )
    transport = FakeTransport(response())
    advisor = AdvisoryService(
        DeepSeekClient(DeepSeekConfig(model="configured-model", api_key=key), transport)
    )

    advisor.advise(report)

    assert key.encode() not in transport.requests[0].body
