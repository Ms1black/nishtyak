from __future__ import annotations

import io
import json
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace

import pytest

import nishtyak_core.package_safety as safety
from nishtyak_core.package_safety import (
    OSV_ENDPOINT,
    PackageSafetyClient,
    PackageVersion,
    _OSVTransport,
    get_package_safety_client,
)


class Transport:
    def __init__(self, response=None):
        self.response = response
        self.calls = []

    def post(self, payload, *, deadline, max_bytes):
        document = json.loads(payload)
        self.calls.append((document, deadline, max_bytes))
        result = self.response(document) if callable(self.response) else self.response
        if isinstance(result, Exception):
            raise result
        if result is None:
            result = {"results": [{} for _ in document["queries"]]}
        return result if isinstance(result, bytes) else json.dumps(result).encode()


def test_checks_exact_version_with_fixed_ecosystem_and_records_only_lookup_completion():
    transport = Transport(
        {"results": [{"vulns": [{"id": "CVE-2026-1234"}, {"id": "GHSA-abcd-efgh-1234"}]}]}
    )
    report = PackageSafetyClient(transport).check([PackageVersion("NumPy", "1.26.4")])
    assert transport.calls[0][0] == {
        "queries": [{"package": {"name": "numpy", "ecosystem": "PyPI"}, "version": "1.26.4"}]
    }
    assert report.status == "checked"
    assert report.results == report.packages
    result = report.packages[0]
    assert result.advisory_ids == ("CVE-2026-1234", "GHSA-abcd-efgh-1234")
    assert result.malicious_ids == () and result.scope == "version"
    assert result.source == report.source == OSV_ENDPOINT
    assert (
        report.checked_at.endswith("Z")
        and datetime.fromisoformat(report.checked_at.replace("Z", "+00:00")).tzinfo is not None
    )
    assert "safe" not in report.to_dict()
    assert report.to_dict()["packages"][0]["advisory_ids"] == list(result.advisory_ids)


def test_malicious_package_is_distinguished_from_ordinary_vulnerabilities():
    transport = Transport(
        {"results": [{"vulns": [{"id": "MAL-2023-2149"}, {"id": "CVE-2026-1234"}]}]}
    )
    report = PackageSafetyClient(transport).check([PackageVersion("reqeusts-toolbelt", "0.1")])
    assert report.status == "blocked"
    assert report.packages[0].malicious_ids == ("MAL-2023-2149",)
    assert report.packages[0].advisory_ids == ("CVE-2026-1234", "MAL-2023-2149")


def test_name_only_queries_are_explicitly_package_wide_and_do_not_invent_a_version():
    transport = Transport({"results": [{"vulns": [{"id": "MAL-2023-2149"}]}]})
    result = PackageSafetyClient(transport).check([PackageVersion("reqeusts-toolbelt")]).packages[0]
    assert "version" not in transport.calls[0][0]["queries"][0]
    assert result.version is None and result.scope == "package" and result.status == "blocked"


def test_malicious_results_are_version_specific_and_do_not_poison_other_versions():
    def response(document):
        return {
            "results": [
                {"vulns": [{"id": "MAL-2023-2149"}]} if query["version"] == "0.1" else {}
                for query in document["queries"]
            ]
        }

    report = PackageSafetyClient(Transport(response)).check(
        [PackageVersion("example", "0.1"), PackageVersion("example", "0.2")]
    )
    assert [result.status for result in report.packages] == ["blocked", "checked"]


def test_batches_at_most_100_queries_and_preserves_input_order():
    transport = Transport()
    packages = [PackageVersion(f"example-{index}", "1.0") for index in range(205)]
    result = PackageSafetyClient(transport).check(packages)
    assert [len(call[0]["queries"]) for call in transport.calls] == [100, 100, 5]
    assert [item.name for item in result.packages] == [item.name for item in packages]
    assert result.status == "checked"
    assert all(0 < call[2] <= 1024**2 for call in transport.calls)


def test_duplicate_normalized_queries_share_one_request_but_preserve_result_order():
    transport = Transport()
    report = PackageSafetyClient(transport).check(
        [PackageVersion("Example_Pkg", "1.0"), PackageVersion("example-pkg", "1.0")]
    )
    assert len(transport.calls[0][0]["queries"]) == 1
    assert len(report.packages) == 2 and report.packages[0] == report.packages[1]


def test_query_limit_is_checked_before_any_network_request():
    transport = Transport()
    client = PackageSafetyClient(transport)
    with pytest.raises(ValueError, match="2000"):
        client.check(PackageVersion(f"example-{index}", "1") for index in range(2001))
    assert transport.calls == []


@pytest.mark.parametrize(
    "name",
    [
        "",
        "https://example.test/x",
        "example[extra]",
        "-bad",
        "bad-",
        "bad name",
        "bad\nname",
        "bad/name",
        "name@secret",
        "x" * 129,
        "пакет",
    ],
)
def test_invalid_package_names_are_never_serialized_or_sent(name):
    with pytest.raises(ValueError):
        PackageVersion(name, "1.0")


@pytest.mark.parametrize(
    "version",
    [
        "",
        ">=1",
        "==1",
        "https://example.test/x",
        "1.0 secret",
        "1.0\n",
        "1+private_token",
        "sk-secret",
        "x" * 129,
        1,
        True,
    ],
)
def test_only_exact_public_versions_can_be_sent(version):
    with pytest.raises(ValueError):
        PackageVersion("example", version)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'{"results": [], "results": [{}]}',
        b'{"results": [{"vulns": [], "vulns": []}]}',
        b'{"results": [NaN]}',
        b"\xff",
        {"results": []},
        {"results": [{}, {}]},
        {"results": None},
        {"results": {}, "error": "failure"},
        {"error": "unavailable"},
    ],
)
def test_malformed_json_and_wrong_result_counts_are_unavailable_not_clean(body):
    report = PackageSafetyClient(Transport(body)).check([PackageVersion("example", "1.0")])
    assert report.status == "unavailable" and report.packages[0].status == "unavailable"


@pytest.mark.parametrize(
    "result",
    [
        None,
        [],
        True,
        {"vulns": None},
        {"vulns": {}},
        {"vulns": [None]},
        {"vulns": [{"id": 12}]},
        {"vulns": [{"id": "MAL-"}]},
        {"vulns": [{"id": "mal-2026-1"}]},
        {"vulns": [{"id": "MAL-2026-1/evil"}]},
        {"vulns": [{"id": "MAL-2026-1\n"}]},
        {"package": {"name": "different"}},
        {"vulns": [{"id": "CVE-2026-1", "modified": "not-a-time"}]},
        {"vulns": [{"id": "MAL-2026-1", "withdrawn": False}]},
        {"next_page_token": []},
    ],
)
def test_invalid_result_shapes_and_identifiers_fail_closed(result):
    report = PackageSafetyClient(Transport({"results": [result]})).check(
        [PackageVersion("example", "1")]
    )
    assert report.status == "unavailable"


def test_one_invalid_result_does_not_erase_another_known_malicious_result():
    transport = Transport(
        {"results": [{"vulns": [{"id": "MAL-2026-1"}]}, {"vulns": [{"id": None}]}]}
    )
    report = PackageSafetyClient(transport).check(
        [PackageVersion("malicious", "1"), PackageVersion("unknown", "1")]
    )
    assert report.status == "blocked"
    assert [item.status for item in report.packages] == ["blocked", "unavailable"]


def test_withdrawn_advisory_is_ignored_but_active_record_still_blocks():
    response = {
        "results": [
            {
                "vulns": [
                    {"id": "MAL-2026-1", "withdrawn": "2026-10-17T12:00:00Z"},
                    {"id": "MAL-2026-2", "withdrawn": None},
                ]
            }
        ]
    }
    result = (
        PackageSafetyClient(Transport(response)).check([PackageVersion("example", "1")]).packages[0]
    )
    assert result.malicious_ids == ("MAL-2026-2",)


def test_pagination_is_unavailable_and_never_treated_as_a_complete_negative_lookup():
    transport = Transport(
        {"results": [{"vulns": [{"id": "CVE-2026-1"}], "next_page_token": "more"}]}
    )
    client = PackageSafetyClient(transport)
    result = client.check([PackageVersion("example", "1")]).packages[0]
    assert result.status == "unavailable" and result.reason == "safety_response_incomplete"
    assert result.advisory_ids == ("CVE-2026-1",)
    client.check([PackageVersion("example", "1")])
    assert len(transport.calls) == 2  # An incomplete response is never cached as checked.


def test_known_malicious_result_remains_blocked_even_if_response_is_paginated():
    result = (
        PackageSafetyClient(
            Transport({"results": [{"vulns": [{"id": "MAL-2026-1"}], "next_page_token": "more"}]})
        )
        .check([PackageVersion("example", "1")])
        .packages[0]
    )
    assert result.status == "blocked" and result.reason == "safety_response_incomplete"


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("private-token"),
        OSError("private-address"),
        RuntimeError("private-service-body"),
    ],
)
def test_failures_are_unavailable_sanitized_and_not_cached(error):
    transport = Transport(error)
    client = PackageSafetyClient(transport)
    report = client.check([PackageVersion("example", "1")])
    assert report.status == "unavailable" and "private" not in json.dumps(report.to_dict())
    transport.response = None
    assert client.check([PackageVersion("example", "1")]).status == "checked"
    assert len(transport.calls) == 2


def test_oversized_custom_transport_response_is_unavailable():
    client = PackageSafetyClient(Transport(b"x" * (1024**2 + 1)))
    assert (
        client.check([PackageVersion("example", "1")]).packages[0].reason == "safety_response_limit"
    )


def test_expired_deadline_avoids_network_and_never_claims_checked():
    transport = Transport()
    report = PackageSafetyClient(transport).check(
        [PackageVersion("example", "1")], deadline=time.monotonic() - 1
    )
    assert report.status == "unavailable" and report.packages[0].reason == "safety_lookup_timeout"
    assert transport.calls == []


def test_deadline_is_shared_across_all_batches_and_transport_overrun_is_not_checked(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(safety, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def response(document):
        clock[0] += 3
        return {"results": [{} for _ in document["queries"]]}

    transport = Transport(response)
    report = PackageSafetyClient(transport).check(
        [PackageVersion(f"example-{index}", "1") for index in range(201)]
    )
    assert report.status == "unavailable"
    assert len(transport.calls) == 2 and all(call[1] == 105 for call in transport.calls)
    assert report.packages[0].status == "checked" and report.packages[-1].status == "unavailable"


def test_cache_is_bounded_expires_and_preserves_original_checked_timestamp(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(safety, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    transport = Transport()
    client = PackageSafetyClient(transport, cache_ttl=10, max_cache_entries=2)
    original = client.check([PackageVersion("first", "1")]).packages[0]
    clock[0] += 5
    assert client.check([PackageVersion("first", "1")]).packages[0] == original
    assert len(transport.calls) == 1
    client.check([PackageVersion("second", "1"), PackageVersion("third", "1")])
    assert len(client._cache) == 2
    client.check([PackageVersion("first", "1")])
    assert len(transport.calls) == 3
    clock[0] += 11
    client.check([PackageVersion("first", "1")])
    assert len(transport.calls) == 4


def test_cache_remains_bounded_under_concurrent_use():
    client = PackageSafetyClient(Transport(), max_cache_entries=8)
    with ThreadPoolExecutor(max_workers=8) as executor:
        reports = list(
            executor.map(
                lambda index: client.check([PackageVersion(f"example-{index}", "1")]), range(40)
            )
        )
    assert all(report.status == "checked" for report in reports)
    assert len(client._cache) == 8


def test_default_client_is_shared_across_hypothesis_instances():
    with ThreadPoolExecutor(max_workers=8) as executor:
        clients = list(executor.map(lambda _: get_package_safety_client(), range(20)))
    assert all(client is clients[0] for client in clients)


class Reply(io.BytesIO):
    def __init__(self, body=b'{"results":[{}]}', *, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}
        self.reads = []

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        self.reads.append(size)
        return super().read1(size)


class Connection:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        self.sock = SimpleNamespace(settimeout=lambda _: None, shutdown=lambda _: None)
        self.closed = False

    def connect(self):
        pass

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def getresponse(self):
        return self.reply

    def close(self):
        self.closed = True


def test_transport_uses_only_fixed_verified_https_post_without_ambient_credentials(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@127.0.0.1:9999")
    connection = Connection(Reply())
    created = []

    def create(host, **kwargs):
        created.append((host, kwargs))
        return connection

    client = PackageSafetyClient(_OSVTransport(create))
    assert client.check([PackageVersion("example", "1")]).status == "checked"
    host, options = created[0]
    assert host == "api.osv.dev" and options["port"] == 443
    assert options["context"].check_hostname and options["context"].verify_mode == ssl.CERT_REQUIRED
    arguments, request = connection.requests[0]
    assert arguments == ("POST", "/v1/querybatch")
    assert set(request["headers"]) == {"Content-Type", "Accept", "Accept-Encoding"}
    assert b"secret" not in request["body"] and connection.closed


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 429, 500, 503])
def test_transport_never_follows_redirects_or_reads_error_bodies(status):
    reply = Reply(status=status, headers={"Location": "https://evil.example/token"})
    connection = Connection(reply)
    result = PackageSafetyClient(_OSVTransport(lambda *args, **kwargs: connection)).check(
        [PackageVersion("example", "1")]
    )
    assert result.status == "unavailable" and len(connection.requests) == 1 and reply.reads == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Encoding": "gzip"},
        {"Content-Length": str(1024**2 + 1)},
        {"Content-Length": "-1"},
        {"Content-Length": "invalid"},
    ],
)
def test_transport_rejects_oversized_encoded_or_malformed_content_before_reading(headers):
    reply = Reply(headers=headers)
    result = PackageSafetyClient(_OSVTransport(lambda *args, **kwargs: Connection(reply))).check(
        [PackageVersion("example", "1")]
    )
    assert result.status == "unavailable" and reply.reads == []


def test_transport_bounds_streamed_bodies_without_content_length():
    reply = Reply(b"x" * (1024**2 + 1))
    report = PackageSafetyClient(_OSVTransport(lambda *args, **kwargs: Connection(reply))).check(
        [PackageVersion("example", "1")]
    )
    assert report.status == "unavailable"
    assert sum(reply.reads) <= 1024**2 + 1


def test_transport_timer_interrupts_a_stalled_body_at_absolute_deadline():
    interrupted = threading.Event()

    class StalledReply(Reply):
        def read1(self, size):
            assert interrupted.wait(1)
            return b""

    connection = Connection(StalledReply())
    connection.sock.shutdown = lambda _: interrupted.set()
    started = time.monotonic()
    report = PackageSafetyClient(
        _OSVTransport(lambda *args, **kwargs: connection), timeout=0.03
    ).check([PackageVersion("example", "1")])
    assert time.monotonic() - started < 0.5
    assert report.status == "unavailable" and interrupted.is_set()
