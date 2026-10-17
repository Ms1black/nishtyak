from __future__ import annotations

import io
import json
import time
import zipfile
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from nishtyak_core import ReproducibilityService, SnapshotBuilder
from nishtyak_core.models import DeclarationDecision, UserDecisions
from nishtyak_core.providers import (
    ProviderEvidence,
    ProviderLookupError,
    PublishedDistributionInspector,
    _IndexTransport,
    _NoRedirect,
    _RemoteWheel,
)
from nishtyak_service.automatic_review import AutomaticProjectReview
from nishtyak_service.provider_discovery import AutomaticProviderDiscovery


class PublishedWheelTransport:
    def __init__(self, *, package="example", module="example", padding=0, metadata=None):
        self.package = package
        self.padding = padding
        self.metadata = metadata
        self.calls = []
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(module + "/__init__.py", "raise RuntimeError('never execute')")
            archive.writestr(
                f"{package}-1.0.dist-info/METADATA",
                f"Metadata-Version: 2.5\nName: {package}\nVersion: 1.0\n",
            )
        self.wheel = stream.getvalue()
        self.artifact = {
            "filename": f"{package}-1.0-py3-none-any.whl",
            "packagetype": "bdist_wheel",
            "yanked": False,
            "size": padding + len(self.wheel),
            "url": f"https://files.pythonhosted.org/packages/ab/{package}-1.0-py3-none-any.whl",
            "digests": {"sha256": "a" * 64},
        }
        if metadata is not None:
            import hashlib

            self.artifact["core-metadata"] = {"sha256": hashlib.sha256(metadata).hexdigest()}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self, url, limit, *, start=None, total=None):
        self.calls.append((url, limit, start))
        if start is not None:
            assert total == self.artifact["size"]
            before = max(0, min(limit, self.padding - start))
            offset = max(0, start - self.padding)
            return b"\0" * before + self.wheel[offset : offset + limit - before]
        if url.endswith("releases.xml"):
            return (
                f"<rss><channel><item><link>https://pypi.org/project/{self.package}/1.0/"
                "</link></item></channel></rss>"
            ).encode()
        if url.endswith("/json"):
            return json.dumps(
                {"info": {"name": self.package, "version": "1.0"}, "urls": [self.artifact]}
            ).encode()
        if url.endswith(".metadata"):
            return self.metadata
        raise AssertionError("A whole wheel must never be fetched")


def inspect(transport):
    return PublishedDistributionInspector(lambda deadline: transport).inspect(transport.package)


def test_large_published_wheel_directory_is_bounded_and_not_whole_hash_verified():
    transport = PublishedWheelTransport(
        package="opencv_python_headless", module="cv2", padding=60 * 1024**2
    )
    evidence = inspect(transport)
    assert evidence.provides("cv2")
    assert evidence.package == "opencv_python_headless"
    assert evidence.method == "published_wheel_directory"
    assert evidence.artifact_hash_verified is False
    assert sum(limit for _, limit, start in transport.calls if start is not None) < 100_000
    assert not evidence.provides("opencv_python")


def test_hashed_core_metadata_import_names_preserve_shared_namespaces():
    metadata = (
        b"Metadata-Version: 2.5\nName: example\nVersion: 1.0\n"
        b"Import-Name: shared.feature\nImport-Namespace: shared\n"
    )
    transport = PublishedWheelTransport(metadata=metadata)
    evidence = inspect(transport)
    assert evidence.method == "published_core_metadata"
    assert evidence.modules == ("shared.feature",)
    assert evidence.namespaces == ("shared",)
    assert evidence.provides("shared")
    assert evidence.metadata_sha256
    assert not any(start is not None for _, _, start in transport.calls)


def test_exact_provider_pin_checks_that_release_without_historical_feed():
    transport = PublishedWheelTransport()
    result = PublishedDistributionInspector(lambda deadline: transport).inspect(
        "example", specifier="==1.0"
    )
    assert result.observed_release == "1.0"
    assert not any(url.endswith("releases.xml") for url, _, _ in transport.calls)
    assert any(url.endswith("/example/1.0/json") for url, _, _ in transport.calls)


def test_constraint_cannot_be_relaxed_to_newest_available_provider_release():
    transport = PublishedWheelTransport()
    inspector = PublishedDistributionInspector(lambda deadline: transport)
    with pytest.raises(ProviderLookupError, match="release_unavailable"):
        inspector.inspect("example", specifier=">=2")
    assert not any(url.endswith("/json") for url, _, _ in transport.calls)


def test_oversized_directory_and_metadata_are_bounded_before_decompression():
    transport = PublishedWheelTransport()
    transport.wheel = transport.wheel[:-22] + transport.wheel[-22:]
    # EOCD central-directory length is independently bounded before ZipFile parses it.
    damaged = bytearray(transport.wheel)
    damaged[-10:-6] = (1024**2 + 1).to_bytes(4, "little")
    transport.wheel = bytes(damaged)
    with pytest.raises(ProviderLookupError, match="directory_limit"):
        inspect(transport)


def test_remote_wheel_never_allows_full_artifact_read_or_invalid_seek_mode():
    transport = PublishedWheelTransport(padding=60 * 1024**2)
    remote = _RemoteWheel(transport, transport.artifact["url"], transport.artifact["size"])
    with pytest.raises(ProviderLookupError, match="directory_limit"):
        remote.read()
    with pytest.raises(ValueError, match="seek"):
        remote.seek(0, 77)


def test_metadata_with_only_namespace_and_empty_import_name_are_meaningful():
    transport = PublishedWheelTransport(
        metadata=b"Metadata-Version: 2.5\nName: example\nVersion: 1.0\nImport-Namespace: shared\n"
    )
    assert inspect(transport).provides("shared")
    transport = PublishedWheelTransport(
        metadata=b"Metadata-Version: 2.5\nName: example\nVersion: 1.0\nImport-Name: \n"
    )
    assert not inspect(transport).provides("example")


@pytest.mark.parametrize(
    "extra,reason",
    [
        (b"Import-Name: example\nImport-Namespace: example\n", "provider_metadata_ambiguous"),
        (b"Import-Name: bad-name\n", "provider_metadata_invalid"),
    ],
)
def test_bad_metadata_cannot_prove_provider(extra, reason):
    transport = PublishedWheelTransport(
        metadata=b"Metadata-Version: 2.5\nName: example\nVersion: 1.0\n" + extra
    )
    with pytest.raises(ProviderLookupError, match=reason):
        inspect(transport)


def test_metadata_digest_failure_is_not_silently_downgraded_to_directory_proof():
    transport = PublishedWheelTransport(metadata=b"Name: example\nVersion: 1.0\n")
    transport.artifact["core-metadata"]["sha256"] = "b" * 64
    with pytest.raises(ProviderLookupError, match="checksum"):
        inspect(transport)


@pytest.mark.parametrize(
    "url", ["https://evil.example/x.whl", "http://files.pythonhosted.org/packages/x.whl"]
)
def test_external_artifact_urls_are_rejected(url):
    transport = PublishedWheelTransport()
    transport.artifact["url"] = url
    with pytest.raises(ProviderLookupError, match="wheel_unavailable"):
        inspect(transport)


@pytest.mark.parametrize("status,content_range", [(200, None), (206, "bytes 0-3/100")])
def test_range_requires_exact_206_response(status, content_range):
    class Reply(io.BytesIO):
        headers = {"Content-Range": content_range}

    reply = Reply(b"abcd")
    reply.status = status
    transport = _IndexTransport(time.monotonic() + 5)
    transport.opener = SimpleNamespace(open=lambda *args, **kwargs: reply)
    with pytest.raises(ProviderLookupError, match="range_unsupported"):
        transport.read("https://files.pythonhosted.org/packages/a.whl", 4, start=10, total=100)
    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example") is None


class ReviewRepository:
    settings = SimpleNamespace(automatic_resolution=True)

    def __init__(self):
        self.review = None

    def read_review(self, *args, **kwargs):
        return self.review

    def save_review(self, record, analysis_id, review):
        self.review = review


def project(files, *, decisions=None):
    service = ReproducibilityService()
    report = service.analyze(SnapshotBuilder().from_mapping(files))
    return service.prepare(report, decisions or UserDecisions(report.analysis_id))


def evidence(package, modules):
    return ProviderEvidence(
        package,
        tuple(modules),
        (),
        "published_wheel_directory",
        "1.0",
        f"{package}-1.0-py3-none-any.whl",
        "a" * 64,
    )


class Inspector:
    def __init__(self, providers):
        self.providers = providers
        self.calls = []

    def inspect(self, package, *, specifier="", timeout=5):
        self.calls.append(package)
        if package not in self.providers:
            raise ProviderLookupError("offline")
        return evidence(package, self.providers[package])


def test_declared_headless_provider_wins_without_any_alias_or_identity_lookup():
    plan = project({"main.py": "import cv2\n", "requirements.txt": "opencv-python-headless==1.0\n"})
    inspector = Inspector({"opencv-python-headless": ("cv2",)})
    repository = ReviewRepository()
    AutomaticProviderDiscovery(repository, inspector).ensure(SimpleNamespace(), plan)
    assert inspector.calls == ["opencv-python-headless"]
    mapping = repository.review["packages"]["cv2"]
    assert mapping["package"] == "opencv-python-headless"
    assert mapping["origin"] == "provider_discovery"
    assert mapping["verification"]["artifact_hash_verified"] is False


def test_multiple_declared_providers_are_ambiguous_and_not_chosen():
    plan = project({"main.py": "import shared\n", "requirements.txt": "alpha\nbeta\n"})
    inspector = Inspector({"alpha": ("shared",), "beta": ("shared",)})
    repository = ReviewRepository()
    AutomaticProviderDiscovery(repository, inspector).ensure(SimpleNamespace(), plan)
    assert repository.review["packages"] == {}
    assert (
        repository.review["provider_discovery"]["unresolved"]["shared"]["reason"]
        == "multiple_declared_providers"
    )
    assert set(inspector.calls) == {"alpha", "beta"}


def test_inactive_target_marker_does_not_create_false_provider_ambiguity():
    plan = project(
        {
            "main.py": "import shared\n",
            "requirements.txt": (
                "alpha==1.0; python_version < '3.10'\nbeta==1.0; python_version >= '3.10'\n"
            ),
        }
    )
    inspector = Inspector({"alpha": ("shared",), "beta": ("shared",)})
    repository = ReviewRepository()
    AutomaticProviderDiscovery(repository, inspector).ensure(SimpleNamespace(), plan)
    assert inspector.calls == ["beta"]
    assert repository.review["packages"]["shared"]["package"] == "beta"


def test_optional_extra_provider_cannot_compete_with_known_base_provider():
    plan = project(
        {
            "main.py": "import shared\n",
            "pyproject.toml": (
                '[project]\nname="example"\ndependencies=["alpha"]\n'
                '[project.optional-dependencies]\nunused=["beta"]\n'
            ),
        }
    )
    inspector = Inspector({"alpha": ("shared",), "beta": ("shared",)})
    repository = ReviewRepository()
    AutomaticProviderDiscovery(repository, inspector).ensure(SimpleNamespace(), plan)
    assert inspector.calls == ["alpha"]
    assert repository.review["packages"]["shared"]["package"] == "alpha"


def test_optional_provider_lookup_remains_a_fallback_for_nonidentity_extra_inference():
    plan = project(
        {
            "main.py": "from PIL import Image\n",
            "pyproject.toml": (
                '[project]\nname="example"\n[project.optional-dependencies]\nimaging=["Pillow>=1"]\n'
            ),
        }
    )
    inspector = Inspector({"pillow": ("PIL",)})
    repository = ReviewRepository()
    AutomaticProviderDiscovery(repository, inspector).ensure(SimpleNamespace(), plan)
    assert inspector.calls == ["pillow"]
    assert repository.review["packages"]["PIL"]["package"] == "pillow"


def test_offline_declaration_is_unknown_and_does_not_guess_an_additional_provider():
    plan = project({"main.py": "import shared\n", "requirements.txt": "alpha\n"})
    inspector = Inspector({"shared": ("shared",)})
    repository = ReviewRepository()
    discovery = AutomaticProviderDiscovery(repository, inspector)
    discovery.ensure(SimpleNamespace(), plan)
    discovery.ensure(SimpleNamespace(), plan)
    assert inspector.calls == ["alpha"]
    assert repository.review["packages"] == {}
    assert repository.review["provider_discovery"]["inventory"]["alpha"]["status"] == "unknown"


def test_identity_is_only_accepted_with_published_provider_evidence_and_author_choices_win():
    plan = project({"main.py": "import numpy\n"})
    assert any(item.code == "DEPENDENCY_UNRESOLVED" for item in plan.findings)
    assert plan.requirements == ("numpy",)
    inspector = Inspector({"numpy": ("numpy",)})
    repository = ReviewRepository()
    AutomaticProviderDiscovery(repository, inspector).ensure(SimpleNamespace(), plan)
    assert AutomaticProjectReview(repository).package_mappings(SimpleNamespace(), plan.report) == {
        "numpy": "numpy"
    }
    authored = project({"main.py": "import numpy\n"})
    authored = ReproducibilityService().prepare(
        authored.report, UserDecisions(authored.analysis_id, packages={"numpy": "custom-provider"})
    )
    AutomaticProviderDiscovery(repository, inspector).ensure(SimpleNamespace(), authored)
    assert inspector.calls == ["numpy"]


def test_replaced_and_excluded_declarations_are_not_queried():
    files = {"main.py": "import shared\n", "requirements.txt": "old-provider\nunused\n"}
    initial = project(files)
    declarations = {item.requirement: item.id for item in initial.report.declarations}
    decisions = UserDecisions(
        initial.analysis_id,
        declaration_actions={
            declarations["old-provider"]: DeclarationDecision("replace", "new-provider"),
            declarations["unused"]: DeclarationDecision("exclude"),
        },
    )
    plan = project(files, decisions=decisions)
    inspector = Inspector({"new-provider": ("shared",)})
    repository = ReviewRepository()
    AutomaticProviderDiscovery(repository, inspector).ensure(SimpleNamespace(), plan)
    assert inspector.calls == ["new-provider"]


def test_changing_a_declaration_drops_previous_automatic_provider_before_rechecking():
    initial = project({"main.py": "import shared\n", "requirements.txt": "old-provider\n"})
    repository = ReviewRepository()
    inspector = Inspector({"old-provider": ("shared",), "new-provider": ("shared",)})
    discovery = AutomaticProviderDiscovery(repository, inspector)
    discovery.ensure(SimpleNamespace(), initial)
    decisions = UserDecisions(
        initial.analysis_id,
        declaration_actions={
            initial.report.declarations[0].id: DeclarationDecision("replace", "new-provider")
        },
    )
    pending = ReproducibilityService().prepare(
        initial.report, decisions, automatic_packages={"shared": "old-provider"}
    )
    assert "DEPENDENCY_UNRESOLVED" not in {item.code for item in pending.findings}
    discovery.ensure(SimpleNamespace(), pending)
    assert inspector.calls == ["old-provider", "new-provider"]
    assert repository.review["packages"]["shared"]["package"] == "new-provider"


def test_ambiguous_new_declarations_cannot_keep_stale_automatic_provider():
    initial = project({"main.py": "import shared\n", "requirements.txt": "old-provider\nother\n"})
    repository = ReviewRepository()
    inspector = Inspector({"old-provider": ("shared",), "other": (), "new-provider": ("shared",)})
    discovery = AutomaticProviderDiscovery(repository, inspector)
    discovery.ensure(SimpleNamespace(), initial)
    declaration = next(item for item in initial.report.declarations if item.requirement == "other")
    decisions = UserDecisions(
        initial.analysis_id,
        declaration_actions={declaration.id: DeclarationDecision("replace", "new-provider")},
    )
    pending = ReproducibilityService().prepare(
        initial.report, decisions, automatic_packages={"shared": "old-provider"}
    )
    discovery.ensure(SimpleNamespace(), pending)
    assert repository.review["packages"] == {}
    assert (
        repository.review["provider_discovery"]["unresolved"]["shared"]["reason"]
        == "multiple_declared_providers"
    )


def test_discovery_does_not_erase_ai_budget_or_claim_a_model_call():
    plan = project({"main.py": "import numpy\n"})
    repository = ReviewRepository()
    repository.review = {
        "status": "skipped",
        "packages": {},
        "project_passes": {"pre_human": 2},
        "pass_results": {"stable": {"attempted": True}},
    }
    AutomaticProviderDiscovery(repository, Inspector({"numpy": ("numpy",)})).ensure(
        SimpleNamespace(), plan
    )
    assert repository.review["project_passes"] == {"pre_human": 2}
    assert repository.review["pass_results"] == {"stable": {"attempted": True}}
    assert repository.review["status"] == "skipped"


def test_published_metadata_proof_from_model_without_discovery_origin_is_not_trusted():
    repository = ReviewRepository()
    repository.review = {
        "packages": {
            "numpy": {"package": "numpy", "verification": asdict(evidence("numpy", ("numpy",)))}
        }
    }
    plan = project({"main.py": "import numpy\n"})
    assert AutomaticProjectReview(repository).package_mappings(SimpleNamespace(), plan.report) == {}


@pytest.mark.parametrize(
    "settings",
    [
        "[tool.uv]\ncompile-bytecode=true\n",
        '[tool.poetry]\nname="example"\n',
        "[tool.pdm]\ndistribution=false\n",
    ],
)
def test_unrelated_tool_settings_do_not_block_standard_project_metadata(settings):
    plan = project(
        {
            "main.py": "print(1)\n",
            "pyproject.toml": '[project]\nname="example"\ndependencies=[]\n' + settings,
        }
    )
    assert "TOOL_METADATA_UNSUPPORTED" not in {item.code for item in plan.findings}


@pytest.mark.parametrize(
    "settings",
    [
        '[tool.uv.sources]\nexample={path="../example"}\n',
        '[tool.uv]\nconstraint-dependencies=["example<2"]\n',
        '[tool.poetry.dependencies]\nexample="^1"\n',
        '[tool.pdm.source]\nname="private"\n',
    ],
)
def test_tool_settings_that_change_dependency_sources_or_constraints_remain_explicit(settings):
    plan = project(
        {
            "main.py": "print(1)\n",
            "pyproject.toml": '[project]\nname="example"\ndependencies=[]\n' + settings,
        }
    )
    assert "TOOL_METADATA_UNSUPPORTED" in {item.code for item in plan.findings}


def test_verified_nonidentity_provider_automatically_selects_matching_extra():
    plan = project(
        {
            "main.py": "from PIL import Image\n",
            "pyproject.toml": (
                '[project]\nname="example"\n[project.optional-dependencies]\n'
                'imaging=["Pillow>=1"]\n'
            ),
        }
    )
    proven = ReproducibilityService().prepare(
        plan.report, UserDecisions(plan.analysis_id), automatic_packages={"PIL": "Pillow"}
    )
    assert proven.requirements == ("Pillow>=1",)
    assert "DEPENDENCY_UNRESOLVED" not in {item.code for item in proven.findings}
    assert "EXTRAS_AUTOMATIC" in {item.code for item in proven.findings}
    assert proven.decisions.selected_extras == ()
