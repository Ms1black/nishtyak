from __future__ import annotations

import pytest

from nishtyak_core.analysis import ProjectAnalyzer
from nishtyak_core.import_graph import ImportGraph, LocalModuleIndex
from nishtyak_core.models import AnalysisOptions
from nishtyak_core.resolution import StandardLibraryProfile, normalize_roots
from nishtyak_core.snapshot import SnapshotBuilder


def analyze(files, **options):
    snapshot = SnapshotBuilder().from_mapping(files)
    report = ProjectAnalyzer().analyze(snapshot, AnalysisOptions(**options))
    graph = ImportGraph(snapshot, report.layout.source_roots, report.imports)
    return report, graph


def test_missing_dotted_local_module_is_not_mistaken_for_external_package():
    report, graph = analyze({"main.py": "import lab.missing\n", "lab/__init__.py": ""})
    assert report.dependencies[0].category == "local"
    assert report.dependencies[0].package is None
    assert report.dependencies[0].status == "unresolved"
    finding = next(item for item in report.findings if item.code == "LOCAL_MODULE_MISSING")
    assert finding.evidence_ids == (report.imports[0].id,)
    assert graph.edges[0].status == "missing_local"
    assert graph.edges[0].target_paths == ("lab/__init__.py",)


def test_local_module_file_blocks_dotted_children_even_if_directory_exists():
    _, graph = analyze({"main.py": "import lab.child\n", "lab.py": "", "lab/child.py": ""})
    assert graph.edges[0].status == "missing_local"
    assert graph.edges[0].target_paths == ("lab.py",)


def test_from_import_distinguishes_exported_symbol_submodule_and_unknown_name():
    report, graph = analyze(
        {
            "main.py": "from lab import VALUE, helpers, missing\n",
            "lab/__init__.py": "VALUE = 42\n",
            "lab/helpers.py": "",
        }
    )
    edge = graph.edges[0]
    assert edge.target_paths == ("lab/__init__.py", "lab/helpers.py")
    assert edge.missing_names == ("missing",)
    assert edge.status == "unverified_symbol"
    warnings = [item for item in report.findings if item.code == "LOCAL_IMPORT_NAME_UNRESOLVED"]
    assert len(warnings) == 1
    assert "missing" in warnings[0].message
    assert "VALUE" not in warnings[0].message


def test_relative_from_import_inside_initializer_checks_actual_child():
    report, graph = analyze(
        {"lab/__init__.py": "from . import helpers, missing\n", "lab/helpers.py": ""}
    )
    assert graph.edges[0].target_paths == ("lab/__init__.py", "lab/helpers.py")
    assert graph.edges[0].missing_names == ("missing",)
    assert any(item.code == "LOCAL_IMPORT_NAME_UNRESOLVED" for item in report.findings)


def test_script_sibling_import_is_local_and_carries_launch_context():
    report, graph = analyze(
        {
            "tools/main.py": "import helper\n",
            "tools/helper.py": "import numpy\n",
            "README.md": "project",
        }
    )
    dependency = next(item for item in report.dependencies if item.module == "helper")
    assert dependency.category == "local"
    assert graph.successors("tools/main.py") == ("tools/helper.py",)
    assert (
        next(edge for edge in graph.edges if edge.source_path == "tools/main.py").status
        == "script_local"
    )
    assert any(item.code == "LOCAL_IMPORT_CONTEXT_REQUIRED" for item in report.findings)


def test_search_root_order_respects_shadowing_instead_of_merging_modules():
    files = {
        "main.py": "import lab.child\n",
        "a/lab.py": "",
        "b/lab/__init__.py": "",
        "b/lab/child.py": "",
    }
    first, graph_first = analyze(files, source_roots=("a", "b"))
    second, graph_second = analyze(files, source_roots=("b", "a"))
    assert first.layout.source_roots == ("a", "b")
    assert second.layout.source_roots == ("b", "a")
    assert graph_first.edges[0].status == "missing_local"
    assert graph_first.edges[0].target_paths == ("a/lab.py",)
    assert graph_second.edges[0].status == "local"
    assert graph_second.edges[0].target_paths == ("b/lab/__init__.py", "b/lab/child.py")
    assert normalize_roots(("b", "a", "b")) == ("b", "a")


def test_namespace_portions_merge_but_regular_package_shadows_previous_namespace():
    snapshot = SnapshotBuilder().from_mapping({"a/lab/first.py": "", "b/lab/second.py": ""})
    index = LocalModuleIndex(snapshot, ("a", "b"))
    assert index.resolve_module("lab.first").paths == ("a/lab/first.py",)
    assert index.resolve_module("lab.second").paths == ("b/lab/second.py",)
    regular = SnapshotBuilder().from_mapping(
        {"a/lab/first.py": "", "b/lab/__init__.py": "", "b/lab/second.py": ""}
    )
    index = LocalModuleIndex(regular, ("a", "b"))
    assert index.resolve_module("lab.first").status == "missing_local"
    assert index.resolve_module("lab.second").paths == ("b/lab/__init__.py", "b/lab/second.py")


@pytest.mark.parametrize(
    "call",
    [
        "importlib.import_module('.helpers', 'otherpkg')",
        "importlib.import_module(name='.helpers', package='otherpkg')",
        "load('.helpers', 'otherpkg')",
    ],
)
def test_literal_dynamic_relative_import_uses_explicit_package_not_importer(call):
    report, graph = analyze(
        {
            "caller/main.py": "import importlib\nfrom importlib import import_module as load\n"
            + call,
            "caller/__init__.py": "",
            "caller/helpers.py": "",
            "otherpkg/__init__.py": "",
            "otherpkg/helpers.py": "",
        }
    )
    observation = next(item for item in report.imports if item.kind == "dynamic")
    assert observation.module == "otherpkg.helpers"
    assert observation.level == 0
    edge = next(item for item in graph.edges if item.evidence_id == observation.id)
    assert edge.target_paths == ("otherpkg/__init__.py", "otherpkg/helpers.py")
    assert "caller/helpers.py" not in edge.target_paths


@pytest.mark.parametrize(
    "call",
    [
        "importlib.import_module('.helpers')",
        "importlib.import_module('.helpers', package=unknown)",
        "__import__('helpers', level=1)",
    ],
)
def test_dynamic_unknown_package_context_never_borrows_importer_package(call):
    report, _ = analyze(
        {
            "caller/main.py": "import importlib\n" + call,
            "caller/__init__.py": "",
            "caller/helpers.py": "",
        }
    )
    assert not [item for item in report.imports if item.kind == "dynamic"]
    assert any(item.code == "DYNAMIC_IMPORT_UNRESOLVED" for item in report.findings)


def test_graph_reachability_covers_package_initializers_and_cycles_without_execution(tmp_path):
    marker = tmp_path / "executed"
    report, graph = analyze(
        {
            "main.py": "import lab.first\n",
            "lab/__init__.py": f"open({str(marker)!r}, 'w').write('bad')\n",
            "lab/first.py": "from . import second\n",
            "lab/second.py": "from . import first\nimport pandas\n",
            "unrelated.py": "import scipy\n",
        }
    )
    assert graph.reachable(("main.py",)) == (
        "lab/__init__.py",
        "lab/first.py",
        "lab/second.py",
        "main.py",
    )
    assert {item.package for item in report.dependencies if item.category == "external"} == {
        "pandas",
        "scipy",
    }
    assert graph.reachable(("absent.py",)) == ()
    assert not marker.exists()


@pytest.mark.parametrize("version", ["3.8", "3.10", "3.11", "3.12", "3.13", "3.14"])
def test_stdlib_profile_is_selected_python_table_not_host_only(version):
    profile = StandardLibraryProfile(version)
    assert profile.supported
    assert profile.contains("os")
    assert profile.contains("tomllib") == (tuple(map(int, version.split("."))) >= (3, 11))
    assert profile.contains("cgi") == (tuple(map(int, version.split("."))) < (3, 13))


def test_analysis_records_launch_facts_without_executing_or_mistaking_nested_guard():
    report, _ = analyze(
        {
            "experiment.py": "import numpy\nprint(numpy.arange(3))\n",
            "helper.py": "def helper():\n    if __name__ == '__main__':\n        print('nested')\n",
            "guarded.py": "if __name__ == '__main__':\n    print('run')\n",
            "broken.py": "import (\n",
        }
    )
    assert report.main_guards == ("guarded.py",)
    assert report.parsed_sources == ("experiment.py", "guarded.py", "helper.py")
    assert report.executable_sources == ("experiment.py", "guarded.py")
