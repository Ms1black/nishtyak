from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nishtyak_client import LogicClient, LogicClientError

TOKEN = "client-test-" + "a" * 32
OWNER = "owner-one"
PROJECT_ID = "a" * 32


@pytest.fixture
def local_servers(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    servers = []

    def start(respond):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                respond(self)

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}"

    yield start
    for server, thread in reversed(servers):
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_never_forward_service_credentials_to_another_server(local_servers, status):
    spy_requests = []
    source_requests = []

    def spy(handler):
        spy_requests.append(dict(handler.headers))
        handler.send_response(200)
        handler.end_headers()
        handler.wfile.write(b"{}")

    spy_url = local_servers(spy)

    def redirect(handler):
        source_requests.append(dict(handler.headers))
        handler.send_response(status)
        handler.send_header("Location", spy_url + "/capture")
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    client = LogicClient(local_servers(redirect), TOKEN, OWNER)
    with pytest.raises(LogicClientError) as caught:
        client.project(PROJECT_ID)
    assert caught.value.status == status
    assert source_requests[0]["Authorization"] == "Bearer " + TOKEN
    assert source_requests[0]["X-Owner-Id"] == OWNER
    assert len(source_requests) == 1
    assert spy_requests == []
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize("body", [b'{"broken"', b"\xffinvalid UTF-8", b"[]", b"null"])
def test_malformed_success_json_raises_a_safe_protocol_error(local_servers, body):
    def malformed(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    client = LogicClient(local_servers(malformed), TOKEN, OWNER)
    with pytest.raises(LogicClientError) as caught:
        client.project(PROJECT_ID)
    assert caught.value.status == 502
    assert caught.value.code == "RESPONSE_INVALID"
    assert str(caught.value) == "Invalid logic service response"


def test_valid_plan_json_can_exceed_the_original_ten_mib_limit(local_servers):
    body = json.dumps({"report": "x" * (11 * 1024 * 1024)}).encode("ascii")

    def large_plan(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    client = LogicClient(local_servers(large_plan), TOKEN, OWNER)
    assert len(client.project(PROJECT_ID)["report"]) == 11 * 1024 * 1024


@pytest.mark.parametrize("owner", ["owner\r\nX-Evil: yes", "owner\n", "two owners", "я", "a" * 129])
def test_owner_header_injection_is_rejected_at_construction(owner):
    with pytest.raises(ValueError, match="owner"):
        LogicClient("http://127.0.0.1:8010", TOKEN, owner)


@pytest.mark.parametrize(
    "token", ["a" * 32 + "\r\nX-Evil: yes", "a" * 32 + "\n", "a" * 32 + " ", "я" * 32, "short"]
)
def test_token_header_injection_is_rejected_at_construction(token):
    with pytest.raises(ValueError, match="token"):
        LogicClient("http://127.0.0.1:8010", token, OWNER)


@pytest.mark.parametrize(
    "identifier", [PROJECT_ID + "\r\nX-Evil: yes", "../secret", "a" * 31, "A" * 32]
)
def test_object_identifiers_are_rejected_before_any_request(monkeypatch, identifier):
    client = LogicClient("http://127.0.0.1:8010", TOKEN, OWNER)

    def unexpected_request(*args, **kwargs):
        pytest.fail("Invalid object identifiers must never reach the transport")

    monkeypatch.setattr(client, "_request", unexpected_request)
    for operation in (client.project, client.job, client.decisions, client.delete):
        with pytest.raises(ValueError, match="object ID"):
            operation(identifier)


def test_json_request_limits_apply_before_any_request(monkeypatch):
    client = LogicClient("http://127.0.0.1:8010", TOKEN, OWNER)

    def unexpected_request(*args, **kwargs):
        pytest.fail("Oversized JSON must never reach the transport")

    monkeypatch.setattr(client, "_request", unexpected_request)
    with pytest.raises(ValueError, match="byte limit"):
        client.plan(PROJECT_ID, {"description": "x" * 262144})
    with pytest.raises(ValueError, match="byte limit"):
        client.submit(b"PK", options={"root": "x" * 65536})
    with pytest.raises(ValueError, match="byte limit"):
        client.submit(b"PK", author_environment={"platform": "x" * 262144})
    with pytest.raises(ValueError):
        client.plan(PROJECT_ID, {"value": float("nan")})


def test_wait_includes_automatic_environment_preparation(monkeypatch):
    client = LogicClient("http://127.0.0.1:8010", TOKEN, OWNER)
    results = iter(
        [
            {"status": "succeeded", "automatic_resolution": {"status": "pending"}},
            {"status": "succeeded", "automatic_resolution": {"status": "running"}},
            {"status": "succeeded", "automatic_resolution": {"status": "ready"}},
        ]
    )
    monkeypatch.setattr(client, "job", lambda _job_id: next(results))
    monkeypatch.setattr("nishtyak_client.client.time.sleep", lambda _seconds: None)
    assert client.wait(PROJECT_ID)["automatic_resolution"]["status"] == "ready"


def test_wait_returns_cached_resolution_failure_without_retrying(monkeypatch):
    client = LogicClient("http://127.0.0.1:8010", TOKEN, OWNER)
    result = {"status": "succeeded", "automatic_resolution": {"status": "unavailable"}}
    monkeypatch.setattr(client, "job", lambda _job_id: result)
    assert client.wait(PROJECT_ID) == result


def test_wait_returns_one_human_question_after_server_attempt_budget(monkeypatch):
    client = LogicClient("http://127.0.0.1:8010", TOKEN, OWNER)
    result = {
        "status": "succeeded",
        "automatic_review": {"status": "pending"},
        "automation": {"phase": "needs_input", "attempt": 2, "max_attempts": 2},
    }
    monkeypatch.setattr(client, "job", lambda _job_id: result)
    assert client.wait(PROJECT_ID) == result


def test_batch_submission_uses_one_mutation_with_notes_and_revision(monkeypatch):
    client = LogicClient("http://127.0.0.1:8010", TOKEN, OWNER)
    requests = []
    expected = {"revision": 4, "decisions": {"analysis_id": "c" * 20}}

    def request(method, path, body=None):
        requests.append((method, path, json.loads(body)))
        return expected

    monkeypatch.setattr(client, "_request", request)
    result = client.submit_clarifications(
        PROJECT_ID, "d" * 20, expected["decisions"], 3, "Это внутренний модуль лаборатории."
    )
    assert result == expected
    assert requests == [
        (
            "POST",
            f"/v1/projects/{PROJECT_ID}/clarifications",
            {
                "questionnaire_id": "d" * 20,
                "expected_revision": 3,
                "decisions": expected["decisions"],
                "notes": "Это внутренний модуль лаборатории.",
            },
        )
    ]


def test_wait_includes_the_single_final_review(monkeypatch):
    client = LogicClient("http://127.0.0.1:8010", TOKEN, OWNER)
    results = iter(
        [
            {"status": "succeeded", "automation": {"phase": "finalizing"}},
            {"status": "succeeded", "automation": {"phase": "draft", "human_round": 1}},
        ]
    )
    monkeypatch.setattr(client, "job", lambda _identifier: next(results))
    monkeypatch.setattr("nishtyak_client.client.time.sleep", lambda _seconds: None)
    assert client.wait(PROJECT_ID)["automation"]["phase"] == "draft"
