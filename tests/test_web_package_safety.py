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
from nishtyak_web.service import BrowserService


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
