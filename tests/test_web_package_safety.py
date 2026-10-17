from __future__ import annotations

import hashlib
import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_client import PROJECT_ID, safety_report

from nishtyak_client import LogicClientError
from nishtyak_web.auth import WorkspaceIdentity
from nishtyak_web.config import WebSettings
from nishtyak_web.service import ARCHIVE_FORMAT_VERSION, ARCHIVE_SECURITY_POLICY, BrowserService


@pytest.fixture
def cached_package(tmp_path):
    def create(lock_id=None):
        identity = WorkspaceIdentity("author", "laboratory", "local")
        job = {
            "project_id": PROJECT_ID,
            "job_id": "e" * 32,
            "status": "succeeded",
            "analysis": {"analysis_id": "analysis-for-cached-archive", "files": []},
        }
        core = SimpleNamespace(
            job=Mock(return_value=job),
            decisions=Mock(return_value={"revision": 2}),
            lock=Mock(return_value={"id": lock_id}),
            package_safety=Mock(return_value=safety_report(lock_id)),
        )
        settings = WebSettings(
            data_dir=tmp_path / ("locked" if lock_id else "unlocked"),
            service_token="test-service-token-" + "x" * 32,
        )
        service = BrowserService(settings, lambda scope: core)
        source = b"PK-original-source-fixture"
        service.store.create(identity.scope, job, "experiment.zip", "zip", len(source), source)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("README.md", "# Проверка\n")
            archive.writestr("requirements.txt", "numpy==2.4.6\n")
            archive.writestr("project/main.py", "import numpy\n")
        content = stream.getvalue()
        archive_tag = hashlib.sha256(content).hexdigest()
        tag = json.dumps(
            {
                "analysis": job["analysis"]["analysis_id"],
                "revision": 2,
                "lock": lock_id,
                "sha256": archive_tag,
                "files": ["README.md", "requirements.txt", "project/main.py"],
                "security_policy": ARCHIVE_SECURITY_POLICY,
                "format_version": ARCHIVE_FORMAT_VERSION,
            }
        )
        service.store.save_archive(identity.scope, PROJECT_ID, tag, content)
        return service, identity, core, content, archive_tag, source

    return create


@pytest.mark.parametrize("lock_id", [None, "b" * 64])
def test_download_rechecks_cached_archive_with_its_exact_lock(cached_package, lock_id):
    service, identity, core, content, _, _ = cached_package(lock_id)
    assert service.download(identity, PROJECT_ID) == content
    assert service.download(identity, PROJECT_ID) == content
    assert core.package_safety.call_count == 2
    core.package_safety.assert_called_with(PROJECT_ID, lock_id=lock_id)


@pytest.mark.parametrize("operation", ["download", "readme", "archive_file"])
@pytest.mark.parametrize(
    "status,expected_status,code",
    [
        ("blocked", 409, "PACKAGE_MALICIOUS"),
        ("unavailable", 503, "PACKAGE_SAFETY_UNAVAILABLE"),
        ("malformed", 503, "PACKAGE_SAFETY_UNAVAILABLE"),
    ],
)
def test_cached_zip_and_previews_fail_closed_without_deleting_source_or_artifact(
    cached_package, operation, status, expected_status, code
):
    service, identity, core, content, archive_tag, source = cached_package("b" * 64)
    core.package_safety.return_value = (
        {} if status == "malformed" else safety_report("b" * 64, status)
    )
    with pytest.raises(LogicClientError) as caught:
        if operation == "archive_file":
            service.archive_file(identity, PROJECT_ID, "project/main.py", archive_tag)
        else:
            getattr(service, operation)(identity, PROJECT_ID)
    assert (caught.value.status, caught.value.code) == (expected_status, code)
    directory = service.store.root / PROJECT_ID
    assert (directory / "bundle.zip").read_bytes() == content
    assert (directory / "source.zip").read_bytes() == source
    assert service.store.get(identity.scope, PROJECT_ID)["archive_tag"] is not None
    core.package_safety.assert_called_once_with(PROJECT_ID, lock_id="b" * 64)


def test_manual_unlocked_requirements_cannot_bypass_the_gate(cached_package):
    service, identity, core, _, _, _ = cached_package()
    core.package_safety.return_value = safety_report(status="blocked")
    with pytest.raises(LogicClientError) as caught:
        service.download(identity, PROJECT_ID)
    assert caught.value.code == "PACKAGE_MALICIOUS"
    core.package_safety.assert_called_once_with(PROJECT_ID, lock_id=None)


def test_old_installation_instructions_require_rebuild_without_deleting_archive(cached_package):
    service, identity, core, content, _, source = cached_package()
    record = service.store.get(identity.scope, PROJECT_ID)
    tag = json.loads(record["archive_tag"])
    del tag["security_policy"]
    service.store.save_archive(identity.scope, PROJECT_ID, json.dumps(tag), content)
    with pytest.raises(LogicClientError) as caught:
        service.download(identity, PROJECT_ID)
    assert caught.value.code == "ARCHIVE_STALE"
    assert (service.store.root / PROJECT_ID / "bundle.zip").read_bytes() == content
    assert (service.store.root / PROJECT_ID / "source.zip").read_bytes() == source
    assert (
        service._project(service.store.get(identity.scope, PROJECT_ID))["archive"]["status"]
        == "not_built"
    )
    core.package_safety.assert_not_called()


def test_safety_receipt_for_another_lock_does_not_authorize_the_cached_zip(cached_package):
    service, identity, core, _, _, _ = cached_package("b" * 64)
    core.package_safety.return_value = safety_report("c" * 64)
    with pytest.raises(LogicClientError) as caught:
        service.download(identity, PROJECT_ID)
    assert (caught.value.status, caught.value.code) == (503, "PACKAGE_SAFETY_UNAVAILABLE")


def test_safety_check_waits_until_archive_revision_and_lock_checks_pass(cached_package):
    service, identity, core, _, _, _ = cached_package("b" * 64)
    core.lock.return_value = {"id": "c" * 64}
    with pytest.raises(LogicClientError) as caught:
        service.download(identity, PROJECT_ID)
    assert caught.value.code == "ARCHIVE_STALE"
    core.package_safety.assert_not_called()


def test_unavailable_legacy_or_custom_client_cannot_authorize_a_download(cached_package):
    service, identity, core, _, _, _ = cached_package()
    core.package_safety.side_effect = LogicClientError(503, "SERVICE_UNAVAILABLE", "Unavailable")
    with pytest.raises(LogicClientError) as caught:
        service.download(identity, PROJECT_ID)
    assert (caught.value.status, caught.value.code) == (503, "PACKAGE_SAFETY_UNAVAILABLE")


def test_old_archive_format_requires_rebuild_without_deleting_files(cached_package):
    service, identity, core, content, _, source = cached_package()
    record = service.store.get(identity.scope, PROJECT_ID)
    tag = json.loads(record["archive_tag"])
    tag.pop("format_version")
    service.store.save_archive(identity.scope, PROJECT_ID, json.dumps(tag), content)
    with pytest.raises(LogicClientError) as caught:
        service.download(identity, PROJECT_ID)
    assert caught.value.code == "ARCHIVE_STALE"
    assert (service.store.root / PROJECT_ID / "bundle.zip").read_bytes() == content
    assert (service.store.root / PROJECT_ID / "source.zip").read_bytes() == source
    core.package_safety.assert_not_called()


@pytest.fixture
def export_with_lock(tmp_path):
    from nishtyak_core.locking import DependencyLock, ResolutionTarget

    analysis_id = "f" * 20
    requirements = "numpy==2.4.6 --hash=sha256:" + "a" * 64 + "\n"
    input_id = "c" * 64
    lock = DependencyLock(
        hashlib.sha256((input_id + "\0" + requirements).encode()).hexdigest(),
        analysis_id,
        input_id,
        ResolutionTarget("3.11", "linux-x86_64"),
        requirements,
        ("numpy==2.4.6",),
    )
    job = {
        "project_id": PROJECT_ID,
        "job_id": "e" * 32,
        "status": "succeeded",
        "analysis": {"analysis_id": analysis_id, "status": "complete", "files": []},
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("README.md", "# Проверка\n")
        archive.writestr("requirements.lock.txt", requirements)
        archive.writestr("project/main.py", "import numpy\n")
    content = stream.getvalue()
    core = SimpleNamespace(
        job=Mock(return_value=job),
        decisions=Mock(return_value={"revision": 2}),
        lock=Mock(return_value=lock.to_dict()),
        bundle=Mock(return_value=content),
        package_safety=Mock(return_value=safety_report(lock.id)),
    )
    identity = WorkspaceIdentity("author", "laboratory", "local")
    settings = WebSettings(data_dir=tmp_path, service_token="test-service-token-" + "x" * 32)
    service = BrowserService(settings, lambda scope: core)
    service.store.create(identity.scope, job, "experiment.zip", "zip", 4, b"source")
    return service, identity, core, lock, content


def test_build_binds_zip_to_authoritative_lock_without_json_inside(export_with_lock):
    service, identity, core, lock, content = export_with_lock
    service.build(identity, PROJECT_ID)
    tag = json.loads(service.store.get(identity.scope, PROJECT_ID)["archive_tag"])
    assert tag["lock"] == lock.id
    assert tag["format_version"] == ARCHIVE_FORMAT_VERSION
    assert "dependency-lock.json" not in tag["files"]
    assert service.download(identity, PROJECT_ID) == content
    core.package_safety.assert_called_once_with(PROJECT_ID, lock_id=lock.id)


@pytest.mark.parametrize("mismatch", ["requirements", "analysis", "checksum", "requested_id"])
def test_lock_binding_rejects_changed_or_invalid_authoritative_lock(export_with_lock, mismatch):
    from dataclasses import replace

    service, identity, core, lock, _ = export_with_lock
    requested = None
    if mismatch == "requirements":
        requirements = lock.requirements.replace("2.4.6", "2.4.5")
        lock = replace(
            lock,
            requirements=requirements,
            packages=("numpy==2.4.5",),
            id=hashlib.sha256((lock.input_id + "\0" + requirements).encode()).hexdigest(),
        )
    elif mismatch == "analysis":
        lock = replace(lock, analysis_id="b" * 20)
    elif mismatch == "checksum":
        lock = replace(lock, id="0" * 64)
    else:
        requested = "0" * 64
    core.lock.return_value = lock.to_dict()
    with pytest.raises(LogicClientError) as caught:
        service.build(identity, PROJECT_ID, lock_id=requested)
    assert caught.value.code == ("RESPONSE_INVALID" if mismatch == "checksum" else "LOCK_STALE")
    assert service.store.get(identity.scope, PROJECT_ID)["archive_tag"] is None
