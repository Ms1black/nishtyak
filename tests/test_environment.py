from __future__ import annotations

from dataclasses import replace

import pytest

from nishtyak_core.analysis import ProjectAnalyzer
from nishtyak_core.environment import (
    AuthorEnvironmentInspector,
    AuthorEnvironmentSnapshot,
    InstalledDistribution,
)
from nishtyak_core.models import ProjectSnapshot, SourceFile


def report(source="import pandas\n", requirements=""):
    return ProjectAnalyzer().analyze(
        ProjectSnapshot(
            (
                SourceFile("main.py", source.encode()),
                SourceFile("requirements.txt", requirements.encode()),
            ),
            "project-fingerprint",
        )
    )


def environment(*distributions):
    return AuthorEnvironmentSnapshot("3.11.1", "Linux-x86_64", tuple(distributions))


def test_enrich_uses_only_project_dependencies_and_changes_analysis_identity():
    original = report()
    observed = environment(
        InstalledDistribution("pandas", "2.2.0", ("pandas",)),
        InstalledDistribution("unrelated", "99.0", ("unrelated",)),
    )
    enriched = AuthorEnvironmentInspector().enrich(original, observed)
    assert enriched.analysis_id != original.analysis_id
    assert enriched.dependencies[0].requirement == "pandas==2.2.0"
    assert enriched.dependencies[0].evidence_ids == original.dependencies[0].evidence_ids
    assert len(enriched.dependencies) == 1
    assert enriched.python_requires is None
    assert any(f.code == "AUTHOR_ENVIRONMENT_UNVERIFIED" for f in enriched.findings)
    different = replace(observed, distributions=(InstalledDistribution("pandas", "2.3.0"),))
    assert (
        AuthorEnvironmentInspector().enrich(original, different).analysis_id != enriched.analysis_id
    )


def test_unique_author_module_mapping_resolves_unknown_distribution():
    original = report("import research_module\n")
    enriched = AuthorEnvironmentInspector().enrich(
        original,
        environment(
            InstalledDistribution("research-package", "1.2", ("research_module",)),
        ),
    )
    dependency = enriched.dependencies[0]
    assert dependency.category == "external"
    assert dependency.package == "research-package"
    assert dependency.requirement == "research-package==1.2"


def test_ambiguous_module_mapping_stays_unresolved():
    enriched = AuthorEnvironmentInspector().enrich(
        report("import research\n"),
        environment(
            InstalledDistribution("research-one", "1.0", ("research",)),
            InstalledDistribution("research-two", "2.0", ("research",)),
        ),
    )
    assert enriched.dependencies[0].category == "unresolved"
    assert any(f.code == "AUTHOR_MAPPING_AMBIGUOUS" for f in enriched.findings)


def test_declared_conflicting_pin_is_preserved():
    original = report(requirements='pandas==1.0; python_version >= "3.10"')
    enriched = AuthorEnvironmentInspector().enrich(
        original,
        environment(
            InstalledDistribution("pandas", "2.2.0", ("pandas",)),
        ),
    )
    assert enriched.dependencies == original.dependencies
    assert enriched.declared_requirements == original.declared_requirements
    assert any(f.code == "AUTHOR_ENVIRONMENT_CONFLICT" for f in enriched.findings)


def test_enrich_preserves_markers_when_pinning_unversioned_metadata():
    original = report(requirements='pandas; python_version >= "3.10"')
    enriched = AuthorEnvironmentInspector().enrich(
        original,
        environment(
            InstalledDistribution("pandas", "2.2.0", ("pandas",)),
        ),
    )
    assert enriched.dependencies[0].requirement == 'pandas==2.2.0; python_version >= "3.10"'


def test_collect_only_reads_metadata_and_never_imports_modules(monkeypatch):
    class FakeDistribution:
        metadata = {"Name": "research-package"}
        version = "1.2"

    monkeypatch.setattr(
        "nishtyak_core.environment.metadata.distributions", lambda: [FakeDistribution()]
    )
    monkeypatch.setattr(
        "nishtyak_core.environment.metadata.packages_distributions",
        lambda: {
            "never_import_me": ["research-package"],
        },
    )
    snapshot = AuthorEnvironmentInspector().collect()
    assert snapshot.distributions == (
        InstalledDistribution("research-package", "1.2", ("never_import_me",)),
    )
    assert AuthorEnvironmentSnapshot.from_dict(snapshot.to_dict()) == snapshot


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"python_version": "secret", "platform": "Linux", "distributions": []},
        {"python_version": "3.11.1", "platform": "/home/user", "distributions": []},
        {
            "python_version": "3.11.1",
            "platform": "Linux",
            "distributions": [
                {"name": "package", "version": "invalid", "modules": []},
            ],
        },
    ],
)
def test_snapshot_json_validation_has_generic_errors(bad):
    with pytest.raises(ValueError):
        AuthorEnvironmentSnapshot.from_dict(bad)
