from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import replace

import pytest

from nishtyak_core.advisors.package_verification import (
    PackageVerificationError,
    PublishedPackageVerifier,
)
from nishtyak_core.providers import (
    ProviderEvidence,
    ProviderLookupError,
    PublishedDistributionInspector,
)


def provider_evidence(**changes):
    evidence = ProviderEvidence(
        package="research-provider",
        modules=("unknown_lab",),
        namespaces=(),
        method="published_wheel_directory",
        observed_release="1.0",
        wheel="research_provider-1.0-py3-none-any.whl",
        sha256="a" * 64,
    )
    return replace(evidence, **changes)


class Inspector:
    def __init__(self, evidence=None, error=None):
        self.evidence = evidence if evidence is not None else provider_evidence()
        self.error = error
        self.calls = []

    def inspect(self, package, *, timeout):
        self.calls.append((package, timeout))
        if self.error is not None:
            raise self.error
        return self.evidence


@pytest.mark.parametrize("method", ["published_core_metadata", "published_wheel_directory"])
def test_returns_published_evidence_without_claiming_execution_or_artifact_hash(method):
    inspector = Inspector(provider_evidence(method=method, metadata_sha256="b" * 64))
    result = PublishedPackageVerifier(inspector).verify("unknown_lab", "Research_Provider")

    assert inspector.calls == [("Research_Provider", 4)]
    assert result["method"] == method
    assert result["module"] == "unknown_lab"
    assert result["package"] == "research-provider"
    assert result["observed_release"] == "1.0"
    assert result["sha256"] == "a" * 64
    assert result["metadata_sha256"] == "b" * 64
    assert result["artifact_hash_verified"] is False
    assert result["execution_verified"] is False


def test_accepts_a_published_namespace_as_top_level_provider():
    inspector = Inspector(provider_evidence(modules=(), namespaces=("unknown_lab.tools",)))
    assert PublishedPackageVerifier(inspector).verify("unknown_lab", "research-provider")[
        "namespaces"
    ] == ("unknown_lab.tools",)


@pytest.mark.parametrize("timeout, expected", [(0.1, 0.1), (4, 4), (10, 10), (15, 10)])
def test_preserves_verifier_timeout_contract_with_inspector_limit(timeout, expected):
    inspector = Inspector()
    PublishedPackageVerifier(inspector).verify("unknown_lab", "research-provider", timeout=timeout)
    assert inspector.calls == [("research-provider", expected)]


@pytest.mark.parametrize(
    "module, package, timeout",
    [
        ("unknown_lab", "provider>=1", 4),
        ("unknown_lab", "provider[extra]", 4),
        ("unknown_lab", "provider @ https://evil.invalid/x", 4),
        ("unknown_lab", "../provider", 4),
        ("unknown_lab", "provider\n--index-url", 4),
        ("unknown_lab", "", 4),
        ("unknown_lab", "a" * 129, 4),
        ("unknown_lab", None, 4),
        ("unknown_lab.child", "research-provider", 4),
        ("../unknown_lab", "research-provider", 4),
        ("", "research-provider", 4),
        (None, "research-provider", 4),
        ("unknown_lab", "research-provider", 0),
        ("unknown_lab", "research-provider", -1),
        ("unknown_lab", "research-provider", 15.1),
        ("unknown_lab", "research-provider", float("nan")),
        ("unknown_lab", "research-provider", float("inf")),
        ("unknown_lab", "research-provider", True),
        ("unknown_lab", "research-provider", "4"),
    ],
)
def test_invalid_mapping_never_reaches_the_inspector(module, package, timeout):
    inspector = Inspector()
    with pytest.raises(PackageVerificationError, match="^package_mapping_invalid$"):
        PublishedPackageVerifier(inspector).verify(module, package, timeout=timeout)
    assert inspector.calls == []


def test_rejects_a_real_distribution_that_does_not_provide_the_requested_import():
    inspector = Inspector(provider_evidence(modules=("other_lab",)))
    with pytest.raises(PackageVerificationError, match="^package_does_not_provide_module$"):
        PublishedPackageVerifier(inspector).verify("unknown_lab", "research-provider")


@pytest.mark.parametrize(
    "evidence",
    [
        provider_evidence(package="unrelated-provider"),
        provider_evidence(method="unverified_guess"),
        object(),
    ],
)
def test_untrusted_or_malformed_inspector_evidence_fails_closed(evidence):
    with pytest.raises(PackageVerificationError, match="^package_mapping_unverified$"):
        PublishedPackageVerifier(Inspector(evidence)).verify("unknown_lab", "research-provider")


@pytest.mark.parametrize(
    "reason",
    [
        "provider_lookup_timeout",
        "provider_index_unavailable",
        "provider_metadata_checksum_mismatch",
    ],
)
def test_provider_failures_remain_unverified_with_diagnostic_reason(reason):
    with pytest.raises(PackageVerificationError, match="^" + reason + "$"):
        PublishedPackageVerifier(Inspector(error=ProviderLookupError(reason))).verify(
            "unknown_lab", "research-provider"
        )


def test_unexpected_inspection_failure_cannot_accept_a_mapping():
    with pytest.raises(PackageVerificationError, match="^package_mapping_unverified$"):
        PublishedPackageVerifier(Inspector(error=RuntimeError("unavailable"))).verify(
            "unknown_lab", "research-provider"
        )


def test_large_wheel_uses_bounded_ranges_instead_of_a_full_download():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "research_provider-1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: research-provider\nVersion: 1.0\n",
        )
        archive.writestr("large_data.bin", b"x" * (5 * 1024**2))
        archive.writestr("unknown_lab/__init__.py", 'raise RuntimeError("never execute")\n')
    content = stream.getvalue()
    wheel_url = "https://files.pythonhosted.org/packages/ab/research_provider-1.0-py3-none-any.whl"
    wheel = {
        "filename": "research_provider-1.0-py3-none-any.whl",
        "packagetype": "bdist_wheel",
        "yanked": False,
        "size": len(content),
        "url": wheel_url,
        "digests": {"sha256": hashlib.sha256(content).hexdigest()},
    }
    calls = []

    class RangeTransport:
        def __init__(self, deadline):
            self.deadline = deadline

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, url, limit, *, start=None, total=None):
            calls.append((url, limit, start, total))
            if url == "https://pypi.org/rss/project/research-provider/releases.xml":
                return (
                    b"<rss><channel><item><link>https://pypi.org/project/research-provider/1.0/"
                    b"</link></item></channel></rss>"
                )
            if url == "https://pypi.org/pypi/research-provider/1.0/json":
                return json.dumps(
                    {"info": {"name": "research-provider", "version": "1.0"}, "urls": [wheel]}
                ).encode()
            assert url == wheel_url
            assert start is not None, "The adapter must never request the whole wheel"
            assert total == len(content)
            return content[start : start + limit]

    inspector = PublishedDistributionInspector(transport_factory=RangeTransport)
    result = PublishedPackageVerifier(inspector).verify("unknown_lab", "research-provider")

    assert len(content) > 4 * 1024**2
    assert result["method"] == "published_wheel_directory"
    assert result["artifact_hash_verified"] is False
    assert result["execution_verified"] is False
    artifact_reads = [call for call in calls if call[0] == wheel_url]
    assert artifact_reads
    assert sum(limit for _, limit, _, _ in artifact_reads) < 256 * 1024
