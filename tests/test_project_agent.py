from __future__ import annotations

import json
from dataclasses import replace

import pytest

from nishtyak_core.advisors import DeepSeekClient, DeepSeekConfig, ProjectAdvisoryService
from nishtyak_core.advisors.client import HttpRequest, HttpResponse, InvalidAdvisorResponse
from nishtyak_core.advisors.project_agent import ProjectTools
from nishtyak_core.advisors.project_context import SafeProjectContext
from nishtyak_core.service import ReproducibilityService
from nishtyak_core.snapshot import SnapshotBuilder


class Responses:
    def __init__(self, *documents: dict, status: int = 200):
        self.documents = list(documents)
        self.requests: list[HttpRequest] = []
        self.status = status

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(self.status, json.dumps(self.documents.pop(0)).encode())


def final(context: SafeProjectContext, feature: str = "explain", **changes: object) -> dict:
    document = {
        "schema_version": 2,
        "analysis_id": context.analysis_id,
        "feature": feature,
        "suggestions": [],
        "unknowns": [],
    }
    document.update(changes)
    return {
        "choices": [{"message": {"content": json.dumps(document)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def call(name: str, arguments: dict, identifier: str = "call_1") -> dict:
    return {
        "id": identifier,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def tools(*calls: dict) -> dict:
    return {
        "choices": [
            {"message": {"content": None, "tool_calls": list(calls)}, "finish_reason": "tool_calls"}
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    }


def advisor(transport: Responses, key: str = "provider-key-private") -> ProjectAdvisoryService:
    return ProjectAdvisoryService(
        DeepSeekClient(DeepSeekConfig(model="configured-model", api_key=key), transport)
    )


@pytest.fixture
def context() -> SafeProjectContext:
    snapshot = SnapshotBuilder().from_mapping(
        {
            "experiment.py": "import numpy as np\nprint(np.mean([1, 2]))\n",
            "README.md": "A scientific example",
            "data.csv": "a,b\n1,2",
        }
    )
    report = ReproducibilityService().analyze(snapshot)
    return SafeProjectContext.from_project(report, snapshot)


def test_context_roundtrip_and_no_data_files(context: SafeProjectContext) -> None:
    restored = SafeProjectContext.from_dict(context.to_dict())
    assert restored == context
    assert [file["path"] for file in restored.files] == ["README.md", "experiment.py"]
    assert restored.omitted_files == 1
    assert restored.launches and restored.launches[0]["run_spec"]["target"] == "experiment.py"


def test_actual_tool_roundtrip_returns_evidence_without_running_project(
    context: SafeProjectContext,
) -> None:
    file = next(item for item in context.files if item["path"] == "experiment.py")
    suggestion = {
        "kind": "readme_description",
        "text": "Скрипт вычисляет среднее значений.",
        "evidence_ids": [file["id"]],
        "candidate_id": None,
        "module": None,
        "package": None,
    }
    transport = Responses(
        tools(call("read_source", {"file_id": file["id"], "start_line": 1, "line_count": 20})),
        final(context, "readme", suggestions=[suggestion]),
    )
    service = advisor(transport)
    result = service.advise_context(context, "readme")
    assert result.status == "ok" and result.tool_calls == 1 and result.rounds == 2
    assert result.suggestions[0].requires_confirmation
    assert result.usage and result.usage.total_tokens == 45
    payload = json.loads(transport.requests[1].body)
    assert payload["thinking"] == {"type": "disabled"}
    tool_message = next(item for item in payload["messages"] if item["role"] == "tool")
    assert "np.mean" in tool_message["content"]
    assert {item["function"]["name"] for item in payload["tools"]} == {
        "project_tree",
        "read_source",
        "analysis_facts",
        "launch_candidates",
    }
    service.validate_result(json.loads(json.dumps(result.to_dict())), context, "readme")


def test_secrets_and_provider_key_never_leave_authorization_header() -> None:
    snapshot = SnapshotBuilder().from_mapping(
        {
            "main.py": 'password = "secret-project-value"\nprint("safe")',
            ".env": "TOKEN=private-value",
            "credentials.toml": 'value="hidden"',
            "README.md": "provider-key-private https://user:pass@example.com/a sk-shortvalue",
            "plain.py": 'print("longopaqueCredential123456789012345678901234")',
        }
    )
    context = SafeProjectContext.from_project(ReproducibilityService().analyze(snapshot), snapshot)
    assert "secret-project-value" not in json.dumps(context.to_dict())
    assert "longopaqueCredential123456789012345678901234" not in json.dumps(context.to_dict())
    assert ".env" not in [file["path"] for file in context.files]
    assert "credentials.toml" not in [file["path"] for file in context.files]
    file = next(item for item in context.files if item["path"] == "README.md")
    transport = Responses(
        tools(call("read_source", {"file_id": file["id"], "start_line": 1, "line_count": 2})),
        final(context),
    )
    result = advisor(transport).advise_context(context, description='token="description-private"')
    assert result.status == "ok"
    transmitted = b"".join(request.body for request in transport.requests).decode()
    for secret in (
        "provider-key-private",
        "secret-project-value",
        "description-private",
        "user:pass",
        "longopaqueCredential",
    ):
        assert secret not in transmitted


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("shell", {"command": "cat /etc/passwd"}),
        ("read_source", {"path": "/etc/passwd", "start_line": 1, "line_count": 5}),
        ("read_source", {"file_id": "../../outside", "start_line": 0, "line_count": 5}),
        ("analysis_facts", {"section": "imports", "offset": -1, "count": 1}),
        ("project_tree", {"offset": 0, "count": True}),
        ("launch_candidates", {"extra": "x"}),
    ],
)
def test_provider_cannot_expand_tool_capabilities(
    context: SafeProjectContext, name: str, arguments: dict
) -> None:
    transport = Responses(tools(call(name, arguments)))
    assert advisor(transport).advise_context(context).status == "invalid_response"
    assert len(transport.requests) == 1


def test_unknown_file_id_does_not_read_host_files(context: SafeProjectContext) -> None:
    assert json.loads(
        ProjectTools(context).call(
            "read_source", {"file_id": "/etc/passwd", "start_line": 1, "line_count": 10}
        )
    ) == {"error": "file_not_available"}


def test_launch_suggestion_only_references_existing_candidate(context: SafeProjectContext) -> None:
    identifier = context.launches[0]["id"]
    suggestion = {
        "kind": "entrypoint_hypothesis",
        "text": "Предлагаемый запуск скрипта.",
        "evidence_ids": [identifier],
        "candidate_id": identifier,
        "module": None,
        "package": None,
    }
    assert (
        advisor(Responses(final(context, "entrypoint", suggestions=[suggestion])))
        .advise_context(context, "entrypoint")
        .status
        == "ok"
    )
    suggestion["candidate_id"] = "invented"
    result = advisor(
        Responses(final(context, "entrypoint", suggestions=[suggestion]))
    ).advise_context(context, "entrypoint")
    assert result.status == "invalid_response" and not result.suggestions


def test_unknown_evidence_and_automatic_action_fields_rejected(context: SafeProjectContext) -> None:
    suggestion = {
        "kind": "explanation",
        "text": "Explanation",
        "evidence_ids": ["invented"],
        "candidate_id": None,
        "module": None,
        "package": None,
    }
    assert (
        advisor(Responses(final(context, suggestions=[suggestion]))).advise_context(context).status
        == "invalid_response"
    )
    suggestion["evidence_ids"] = [context.files[0]["id"]]
    suggestion["apply"] = True
    assert (
        advisor(Responses(final(context, suggestions=[suggestion]))).advise_context(context).status
        == "invalid_response"
    )


def test_round_and_call_budgets_stop_tool_loop(context: SafeProjectContext) -> None:
    response = tools(call("launch_candidates", {}))
    transport = Responses(response, response, response, response)
    result = advisor(transport).advise_context(context)
    assert result.status == "invalid_response" and result.rounds == 4
    assert json.loads(transport.requests[-1].body)["tool_choice"] == "none"
    too_many = [call("launch_candidates", {}, f"call_{index}") for index in range(7)]
    assert advisor(Responses(tools(*too_many))).advise_context(context).status == "invalid_response"


def test_initial_facts_avoid_live_provider_redundant_parallel_tool_calls(
    context: SafeProjectContext,
) -> None:
    identifier = context.launches[0]["id"]
    suggestion = {
        "kind": "entrypoint_hypothesis",
        "text": "Вероятная точка входа — experiment.py.",
        "evidence_ids": [identifier],
        "candidate_id": identifier,
        "module": None,
        "package": None,
    }
    transport = Responses(
        tools(
            call("project_tree", {"offset": 0, "count": 30}, "call_tree"),
            call("launch_candidates", {}, "call_launch"),
            call(
                "analysis_facts", {"section": "summary", "offset": 0, "count": 30}, "call_summary"
            ),
        ),
        final(context, "entrypoint", suggestions=[suggestion]),
    )
    result = advisor(transport).advise_context(context, "entrypoint")
    assert result.status == "ok" and result.tool_calls == 3
    first, second = [json.loads(request.body) for request in transport.requests]
    initial = json.loads(first["messages"][1]["content"])["known_project_facts"]
    assert initial["launch_candidates"][0]["id"] == identifier
    assert "text" not in initial["files"][0]
    assert "summary" in initial and "findings" in initial and "declarations" in initial
    assert second["tool_choice"] == "none"
    assert "Remaining tool calls: 3" in second["messages"][-1]["content"]


def test_live_provider_final_pattern_uses_exact_full_suggestion_schema(
    context: SafeProjectContext,
) -> None:
    identifier = context.launches[0]["id"]
    transport = Responses(
        final(
            context,
            "entrypoint",
            suggestions=[
                {
                    "kind": "entrypoint_hypothesis",
                    "text": "Вероятная точка входа — experiment.py.",
                    "evidence_ids": [identifier],
                    "candidate_id": identifier,
                    "module": None,
                    "package": None,
                }
            ],
            unknowns=["Аргументы запуска не проверены."],
        )
    )
    result = advisor(transport).advise_context(context, "entrypoint")
    assert result.status == "ok" and result.rounds == 1 and result.tool_calls == 0
    assert result.suggestions[0].requires_confirmation
    assert result.unknowns == ("Аргументы запуска не проверены.",)


@pytest.mark.parametrize("mistake", ["candidate", "unknowns", "evidence"])
def test_one_final_schema_repair_preserves_strict_validation(
    context: SafeProjectContext,
    mistake: str,
) -> None:
    suggestion = {
        "kind": "readme_description",
        "text": "Краткое описание проекта.",
        "evidence_ids": [context.files[0]["id"]],
        "candidate_id": None,
        "module": None,
        "package": None,
    }
    invalid = dict(suggestion)
    changes = {"suggestions": [invalid]}
    if mistake == "candidate":
        invalid["candidate_id"] = context.launches[0]["id"]
    elif mistake == "unknowns":
        changes["unknowns"] = [{"text": "Неизвестно", "evidence_ids": []}]
    else:
        invalid["evidence_ids"] = ["fabricated"]
    transport = Responses(
        final(context, "readme", **changes),
        final(context, "readme", suggestions=[suggestion]),
    )
    result = advisor(transport).advise_context(context, "readme")
    assert result.status == "ok" and result.rounds == 2 and result.tool_calls == 0
    assert result.suggestions[0].candidate_id is None
    second = json.loads(transport.requests[1].body)
    assert second["tool_choice"] == "none"
    assert "Repair the entire final JSON once" in str(second["messages"])
    assert "unknowns must be an array of strings" in str(second["messages"])


def test_failed_repair_returns_fixed_reason_and_never_accepts_fabricated_evidence(
    context: SafeProjectContext,
) -> None:
    suggestion = {
        "kind": "readme_description",
        "text": "Краткое описание.",
        "evidence_ids": ["invented"],
        "candidate_id": None,
        "module": None,
        "package": None,
    }
    response = final(context, "readme", suggestions=[suggestion])
    transport = Responses(response, response)
    result = advisor(transport).advise_context(context, "readme")
    assert result.status == "invalid_response" and result.reason == "invalid_project_evidence"
    assert result.rounds == 2 and not result.suggestions and len(transport.requests) == 2


def test_json_decode_error_can_be_repaired_without_exposing_error_details(
    context: SafeProjectContext,
) -> None:
    malformed = {"choices": [{"message": {"content": "{"}, "finish_reason": "stop"}]}
    transport = Responses(malformed, final(context, "readme"))
    result = advisor(transport).advise_context(context, "readme")
    assert result.status == "ok" and result.rounds == 2
    repair = json.loads(transport.requests[1].body)
    assert "invalid_final_json" in str(repair["messages"])


def test_schema_repair_consumes_existing_round_budget(context: SafeProjectContext) -> None:
    call_result = tools(call("launch_candidates", {}))
    invalid = final(context, "readme", unknowns=[{"text": "wrong type"}])
    transport = Responses(call_result, call_result, call_result, invalid)
    result = advisor(transport).advise_context(context, "readme")
    assert result.status == "invalid_response" and result.rounds == 4
    assert len(transport.requests) == 4 and result.reason == "invalid_advisory_text"


def test_snapshot_staleness_prevents_any_provider_request(context: SafeProjectContext) -> None:
    snapshot = SnapshotBuilder().from_mapping(
        {"experiment.py": 'raise RuntimeError("must not run")'}
    )
    report = ReproducibilityService().analyze(snapshot)
    transport = Responses()
    result = advisor(transport).advise_project(report, replace(snapshot, fingerprint="stale"))
    assert result.status == "invalid_request" and not transport.requests


def test_absent_configuration_and_http_error_are_nonblocking(context: SafeProjectContext) -> None:
    transport = Responses()
    result = advisor(transport, key="").advise_context(context)
    assert result.status == "unavailable" and result.reason == "missing_api_key"
    assert not transport.requests
    assert advisor(Responses({}, status=429)).advise_context(context).status == "unavailable"


def test_context_rejects_paths_duplicate_ids_and_huge_text(context: SafeProjectContext) -> None:
    for path in ("../../outside.py", "/etc/passwd.py", ".env.py", "secrets.py"):
        document = context.to_dict()
        document["files"][0]["path"] = path
        with pytest.raises(ValueError):
            SafeProjectContext.from_dict(document)
    document = context.to_dict()
    document["files"].append(document["files"][0])
    with pytest.raises(ValueError):
        SafeProjectContext.from_dict(document)
    document = context.to_dict()
    document["files"][0]["text"] = "a" * 16_385
    with pytest.raises(ValueError):
        SafeProjectContext.from_dict(document)


def test_tool_output_budget_is_bounded(context: SafeProjectContext) -> None:
    tool = ProjectTools(context)
    tool.bytes_returned = 24_576
    with pytest.raises(InvalidAdvisorResponse):
        tool.call("launch_candidates", {})


def test_worker_result_validation_rejects_removed_confirmation(context: SafeProjectContext) -> None:
    suggestion = {
        "kind": "explanation",
        "text": "Explanation",
        "evidence_ids": [context.files[0]["id"]],
        "candidate_id": None,
        "module": None,
        "package": None,
    }
    service = advisor(Responses(final(context, suggestions=[suggestion])))
    value = json.loads(json.dumps(service.advise_context(context).to_dict()))
    value["suggestions"][0]["requires_confirmation"] = False
    with pytest.raises(ValueError):
        service.validate_result(value, context, "explain")
