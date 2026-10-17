from __future__ import annotations

import json

import pytest

from nishtyak_core import package_safety
from nishtyak_core.providers import ProviderEvidence, ProviderLookupError


@pytest.fixture(autouse=True)
def offline_package_safety(monkeypatch):
    """Workflow tests use explicit empty advisory data; transport tests cover real parsing."""

    class AdvisoryTransport:
        def post(self, payload, *, deadline, max_bytes):
            queries = json.loads(payload)["queries"]
            return json.dumps({"results": [{} for _ in queries]}).encode()

    client = package_safety.PackageSafetyClient(AdvisoryTransport(), cache_ttl=0)
    monkeypatch.setattr(package_safety, "get_package_safety_client", lambda: client)
    return client


@pytest.fixture(autouse=True)
def offline_provider_discovery(monkeypatch):
    """Unit tests never contact PyPI through the new published-evidence inspector."""

    def unavailable(self):
        raise ProviderLookupError("test_offline_provider_evidence")

    monkeypatch.setattr("nishtyak_core.providers._IndexTransport.__enter__", unavailable)


@pytest.fixture
def verified_identity_discovery(monkeypatch):
    """Synthetic wheel proof for workflow tests; real inspection has separate transport tests."""
    calls = []

    def inspect(self, package, *, specifier="", timeout=5):
        calls.append(package)
        if package not in {"numpy", "pandas"}:
            raise ProviderLookupError("test_missing_published_evidence")
        return ProviderEvidence(
            package=package,
            modules=(package,),
            namespaces=(),
            method="published_wheel_directory",
            observed_release="1.0",
            wheel=f"{package}-1.0-py3-none-any.whl",
            sha256="a" * 64,
        )

    monkeypatch.setattr("nishtyak_core.providers.PublishedDistributionInspector.inspect", inspect)
    return calls
