from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from nishtyak_core import ArchiveReader, ReproducibilityService, package_safety
from nishtyak_core.advisors.package_verification import (
    PackageVerificationError,
    PublishedPackageVerifier,
)
from nishtyak_core.jobs import AnalysisJobResult
from nishtyak_core.locking import DependencyLock, ResolutionTarget, resolution_input_id
from nishtyak_core.models import AnalysisOptions, UserDecisions
from nishtyak_core.providers import ProviderEvidence
from nishtyak_service.api import create_app
from nishtyak_service.config import Settings
from nishtyak_service.errors import ServiceError
from nishtyak_service.package_safety import DependencySafety
from nishtyak_service.service import LogicService


@pytest.fixture
def advisory_data(monkeypatch):
    calls = []
    malicious = {"bad-provider", "bad-transitive"}
    unavailable = set()

    class AdvisoryTransport:
        def post(self, payload, *, deadline, max_bytes):
            queries = json.loads(payload)["queries"]
            calls.extend(queries)
            if any(item["package"]["name"] in unavailable for item in queries):
                raise OSError("Synthetic advisory service outage")
            return json.dumps(
                {
                    "results": [
                        {"vulns": [{"id": "MAL-2026-1234"}]}
                        if item["package"]["name"] in malicious
                        else {}
                        for item in queries
                    ]
                }
            ).encode()

    client = package_safety.PackageSafetyClient(AdvisoryTransport(), cache_ttl=0)
    monkeypatch.setattr(package_safety, "get_package_safety_client", lambda: client)
    return calls, malicious, unavailable


def project(tmp_path, *, automatic=False, requirements=""):
    settings = Settings(
        data_dir=tmp_path,
        runner="subprocess",
        automatic_resolution=automatic,
        api_token="security-test-token-" + "x" * 32,
    )
    logic = LogicService(settings)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "experiment.py",
            'import numpy\n\nif __name__ == "__main__":\n    print(numpy.mean([1, 2]))\n',
        )
        if requirements:
            archive.writestr("requirements.txt", requirements)
    content = stream.getvalue()
    options = AnalysisOptions(python_version="3.11")
    record = logic.repository.submit("alice", content, options)
    logic.repository.claim()
    report = ReproducibilityService().analyze(ArchiveReader().from_bytes(content), options)
    logic.repository.finish(record.job_id, AnalysisJobResult("succeeded", report))
    record = logic.repository.get_project("alice", record.project_id)
    return logic, record, report


def lock_for(plan, target, packages):
    text = "".join(item + " --hash=sha256:" + "a" * 64 + "\n" for item in packages)
    identifier = resolution_input_id(plan, target)
    return DependencyLock(
        hashlib.sha256((identifier + "\0" + text).encode()).hexdigest(),
        plan.analysis_id,
        identifier,
        target,
        text,
        tuple(packages),
    )


def test_model_cannot_adopt_a_real_provider_with_a_known_malicious_release(advisory_data):
    class Inspector:
        def inspect(self, package, *, timeout):
            return ProviderEvidence(
                package,
                ("numpy",),
                (),
                "published_wheel_directory",
                "1.0",
                "bad_provider-1.0-py3-none-any.whl",
                "a" * 64,
            )

    with pytest.raises(PackageVerificationError, match="package_known_malicious"):
        PublishedPackageVerifier(Inspector()).verify("numpy", "bad-provider")
    assert advisory_data[0][0]["version"] == "1.0"


def test_model_cannot_adopt_provider_when_threat_database_is_unavailable(advisory_data):
    advisory_data[2].add("bad-provider")

    class Inspector:
        def inspect(self, package, *, timeout):
            return ProviderEvidence(
                package,
                ("numpy",),
                (),
                "published_wheel_directory",
                "1.0",
                "bad_provider-1.0-py3-none-any.whl",
                "a" * 64,
            )

    with pytest.raises(PackageVerificationError, match="package_safety_unavailable"):
        PublishedPackageVerifier(Inspector()).verify("numpy", "bad-provider")


def test_manual_mapping_cannot_bypass_malware_export_gate(tmp_path, advisory_data):
    logic, record, report = project(tmp_path)
    decisions = UserDecisions(report.analysis_id, packages={"numpy": "bad-provider==1.0"})
    with pytest.raises(ServiceError) as error:
        logic.bundle("alice", record.project_id, decisions)
    assert error.value.code == "PACKAGE_MALICIOUS"


def test_unavailable_database_prevents_export_instead_of_claiming_checked(tmp_path, advisory_data):
    logic, record, report = project(tmp_path)
    advisory_data[2].add("numpy")
    decisions = UserDecisions(report.analysis_id, packages={"numpy": "numpy==2.2.0"})
    with pytest.raises(ServiceError) as error:
        logic.bundle("alice", record.project_id, decisions)
    assert error.value.code == "PACKAGE_SAFETY_UNAVAILABLE"


def test_transitive_malware_blocks_automatic_lock_and_plan(
    tmp_path, advisory_data, monkeypatch, verified_identity_discovery
):
    logic, record, _ = project(tmp_path, automatic=True)
    monkeypatch.setattr(
        "nishtyak_service.automatic_resolution.VersionResolver.resolve",
        lambda self, plan, target: lock_for(plan, target, ["numpy==2.2.0", "bad-transitive==1.0"]),
    )
    logic.review_pending()
    plan = logic.plan("alice", record.project_id)
    assert plan["state"] == "blocked"
    assert any(item["code"] == "PACKAGE_MALICIOUS" for item in plan["findings"])
    assert any(
        item["package"]["name"] == "bad-transitive" and item["version"] == "1.0"
        for item in advisory_data[0]
    )
    with pytest.raises(ServiceError):
        logic.repository.read_lock("alice", record.project_id)


def test_manual_resolve_cannot_persist_unchecked_transitive_lock(
    tmp_path, advisory_data, monkeypatch
):
    logic, record, report = project(tmp_path)
    monkeypatch.setattr(
        "nishtyak_service.service.VersionResolver.resolve",
        lambda self, plan, target: lock_for(plan, target, ["numpy==2.2.0", "bad-transitive==1.0"]),
    )
    with pytest.raises(ServiceError) as error:
        logic.resolve(
            "alice",
            record.project_id,
            ResolutionTarget("3.11", "linux-x86_64"),
            UserDecisions(report.analysis_id, packages={"numpy": "numpy"}),
        )
    assert error.value.code == "PACKAGE_MALICIOUS"
    with pytest.raises(ServiceError):
        logic.repository.read_lock("alice", record.project_id)


def test_inactive_target_requirement_does_not_create_false_malware_block(advisory_data):
    report = DependencySafety().inspect(
        ['bad-provider==1.0; sys_platform == "win32"', "numpy==2.2.0"],
        ResolutionTarget("3.11", "linux-x86_64"),
    )
    assert report["status"] == "checked"
    assert [item["name"] for item in report["packages"]] == ["numpy"]


def test_security_enrichment_does_not_change_resolution_context(tmp_path, advisory_data):
    logic, record, report = project(tmp_path)
    plan = logic.core.prepare(report, UserDecisions(report.analysis_id))
    safety = DependencySafety().inspect(["bad-provider==1.0"])
    enriched = DependencySafety().enrich(plan, safety)
    assert enriched.state == "blocked"
    assert logic.automatic_resolution._context_id(plan) == logic.automatic_resolution._context_id(
        enriched
    )


def test_checked_label_cannot_hide_a_malware_item(advisory_data):
    report = DependencySafety().inspect(["bad-provider==1.0"])
    report["status"] = "checked"
    with pytest.raises(ServiceError) as error:
        DependencySafety.require_checked(report)
    assert error.value.code == "PACKAGE_MALICIOUS"


def test_safety_endpoint_is_owned_and_ignores_client_safety_claims(tmp_path, advisory_data):
    logic, record, _ = project(tmp_path)
    headers = {"Authorization": "Bearer " + logic.settings.api_token, "X-Owner-ID": "alice"}
    with TestClient(create_app(logic.settings, logic)) as client:
        result = client.post(
            f"/v1/projects/{record.project_id}/package-safety", headers=headers, json={}
        )
        assert result.status_code == 200, result.text
        assert result.json()["project_id"] == record.project_id
        assert result.json()["lock_id"] is None
        assert result.json()["status"] == "checked"
        assert (
            client.post(
                f"/v1/projects/{record.project_id}/package-safety",
                headers=headers,
                json={"status": "checked"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/v1/projects/{record.project_id}/package-safety",
                headers={**headers, "X-Owner-ID": "bob"},
                json={},
            ).status_code
            == 404
        )
        assert (
            client.post(f"/v1/projects/{record.project_id}/package-safety", json={}).status_code
            == 401
        )
