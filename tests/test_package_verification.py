from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from nishtyak_core.advisors.package_verification import (
    PackageVerificationError,
    PublishedPackageVerifier,
)


def wheel_files(module="unknown_lab", *, actual=True):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "research_provider-1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: research-provider\nVersion: 1.0\n",
        )
        archive.writestr("research_provider-1.0.dist-info/top_level.txt", module + "\n")
        if actual:
            archive.writestr(module + "/__init__.py", 'raise RuntimeError("never executed")\n')
    content = stream.getvalue()
    record = {
        "filename": "research_provider-1.0-py3-none-any.whl",
        "packagetype": "bdist_wheel",
        "yanked": False,
        "size": len(content),
        "url": "https://files.pythonhosted.org/packages/ab/research_provider-1.0-py3-none-any.whl",
        "digests": {"sha256": hashlib.sha256(content).hexdigest()},
    }
    document = {"info": {"name": "research-provider", "version": "1.0"}, "urls": [record]}
    return content, document


def verifier_for(content, document):
    requests = []

    def fetch(url, limit, timeout):
        requests.append((url, limit, timeout))
        return json.dumps(document).encode() if url.endswith("/json") else content

    return PublishedPackageVerifier(fetch), requests


def test_verifies_actual_wheel_provider_without_import_install_or_extraction():
    verifier, requests = verifier_for(*wheel_files())
    result = verifier.verify("unknown_lab", "research-provider")
    assert result["method"] == "published_wheel_contents"
    assert result["module"] == "unknown_lab"
    assert result["package"] == "research-provider"
    assert result["observed_release"] == "1.0"
    assert result["execution_verified"] is False
    assert [url.split("/")[2] for url, _, _ in requests] == ["pypi.org", "files.pythonhosted.org"]
    assert all(0 < timeout <= 4 for _, _, timeout in requests)


def test_top_level_claim_without_actual_module_does_not_prove_provider():
    verifier, _ = verifier_for(*wheel_files(actual=False))
    with pytest.raises(PackageVerificationError, match="does_not_provide"):
        verifier.verify("unknown_lab", "research-provider")


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.invalid/packages/research_provider-1.0-py3-none-any.whl",
        "https://files.pythonhosted.org@127.0.0.1/packages/research_provider-1.0-py3-none-any.whl",
        "http://files.pythonhosted.org/packages/research_provider-1.0-py3-none-any.whl",
        "https://files.pythonhosted.org:8443/packages/research_provider-1.0-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/../research_provider-1.0-py3-none-any.whl",
    ],
)
def test_rejects_non_allowlisted_artifacts_before_any_download(url):
    content, document = wheel_files()
    document["urls"][0]["url"] = url
    verifier, requests = verifier_for(content, document)
    with pytest.raises(PackageVerificationError):
        verifier.verify("unknown_lab", "research-provider")
    assert len(requests) == 1


@pytest.mark.parametrize(
    "package",
    [
        "provider>=1",
        "provider[extra]",
        "provider @ https://evil.invalid/x",
        "../provider",
        "provider\n--index-url",
    ],
)
def test_only_plain_distribution_names_can_reach_the_index(package):
    verifier, requests = verifier_for(*wheel_files())
    with pytest.raises(PackageVerificationError, match="mapping_invalid"):
        verifier.verify("unknown_lab", package)
    assert requests == []


@pytest.mark.parametrize("change", ["digest", "name", "size", "wheel_name", "yanked", "oversized"])
def test_rejects_artifact_identity_checksum_and_size_violations(change):
    content, document = wheel_files()
    record = document["urls"][0]
    if change == "digest":
        record["digests"]["sha256"] = "b" * 64
    elif change == "name":
        document["info"]["name"] = "unrelated-provider"
    elif change == "size":
        record["size"] += 1
    elif change == "wheel_name":
        record["filename"] = "unrelated_provider-1.0-py3-none-any.whl"
    elif change == "yanked":
        record["yanked"] = True
    elif change == "oversized":
        record["size"] = PublishedPackageVerifier.max_wheel_bytes + 1
    verifier, _ = verifier_for(content, document)
    with pytest.raises(PackageVerificationError):
        verifier.verify("unknown_lab", "research-provider")
