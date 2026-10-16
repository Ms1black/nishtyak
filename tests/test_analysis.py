from __future__ import annotations

import hashlib
import sys

import pytest

from nishtyak_core.analysis import ProjectAnalyzer
from nishtyak_core.models import AnalysisOptions, ProjectSnapshot, SourceFile


def project(**files: str) -> ProjectSnapshot:
    sources = tuple(SourceFile(path, text.encode()) for path, text in files.items())
    return ProjectSnapshot(sources, hashlib.sha256(repr(files).encode()).hexdigest())


def test_imports_resolve_without_loading_any_project_source(tmp_path):
    marker = tmp_path / "executed"
    snapshot = project(
        **{
            "main.py": (
                f"open({str(marker)!r}, 'w').write('executed')\n"
                "import os\nimport pandas as pd\nfrom helpers import clean\n"
                "import research_unknown\nimport pandas\n"
            ),
            "helpers.py": "def clean(): pass\n",
            "data.csv": "x,y\n1,2\n",
        }
    )
    report = ProjectAnalyzer().analyze(snapshot)
    deps = {dependency.module: dependency for dependency in report.dependencies}
    assert deps["os"].category == "stdlib"
    assert deps["helpers"].category == "local"
    assert deps["pandas"].package == "pandas"
    assert len(deps["pandas"].evidence_ids) == 2
    assert deps["research_unknown"].category == "unresolved"
    assert deps["research_unknown"].package is None
    assert len(report.files) == 3
    assert not marker.exists()
    assert {"VERSION_UNKNOWN", "PYTHON_VERSION_UNKNOWN", "DATA_DEPENDENCIES_UNVERIFIED"} <= {
        finding.code for finding in report.findings
    }


def test_relative_imports_and_configured_source_roots():
    report = ProjectAnalyzer().analyze(
        project(
            **{
                "src/lab/__init__.py": "from . import helpers\n",
                "src/lab/main.py": "from .helpers import clean\nfrom ..missing import x\n",
                "src/lab/helpers.py": "from lab import main\n",
            }
        ),
        AnalysisOptions(source_roots=("src",)),
    )
    dependencies = {item.module: item for item in report.dependencies}
    assert dependencies["."].category == "local"
    assert dependencies[".helpers"].category == "local"
    assert dependencies["lab"].category == "local"
    assert dependencies["..missing"].category == "unresolved"


def test_local_collision_is_explicit_and_does_not_install_shadowed_package():
    report = ProjectAnalyzer().analyze(
        project(
            **{
                "main.py": "import pandas\nimport json\n",
                "pandas.py": "VALUE = 1\n",
                "json.py": "VALUE = 2\n",
            }
        )
    )
    assert all(item.category == "local" for item in report.dependencies)
    assert all(item.package is None for item in report.dependencies)
    assert sum(f.code == "LOCAL_NAME_COLLISION" for f in report.findings) == 2


def test_contexts_dynamic_aliases_and_multiline_imports():
    report = ProjectAnalyzer().analyze(
        project(
            **{
                "main.py": """
import importlib as il
from importlib import import_module as load
from typing import TYPE_CHECKING as TC
if TC:
    from pandas import (
        DataFrame,
    )
try:
    import yaml
except (ImportError, ModuleNotFoundError):
    pass
il.import_module("numpy.linalg")
load("PIL.Image")
__import__(module_name)
"""
            }
        )
    )
    observations = {item.module: item for item in report.imports}
    assert observations["pandas"].context == "type_checking"
    assert observations["yaml"].context == "optional"
    assert observations["numpy.linalg"].kind == "dynamic"
    deps = {item.module: item for item in report.dependencies}
    assert deps["yaml"].package == "PyYAML"
    assert deps["PIL"].package == "Pillow"
    assert sum(f.code == "DYNAMIC_IMPORT" for f in report.findings) == 3


def test_manifests_preserve_markers_and_separate_build_dependencies():
    report = ProjectAnalyzer().analyze(
        project(
            **{
                "main.py": "import pandas\nimport custom_lib\n",
                "requirements.txt": "-r requirements/runtime.txt\ncustom-lib==1.2\n",
                "requirements/runtime.txt": 'pandas>=2; python_version >= "3.10"\n',
                "pyproject.toml": """
[build-system]
requires = ["setuptools>=68"]
[project]
requires-python = ">=3.10"
dependencies = ["numpy==2.0.0"]
""",
            }
        )
    )
    assert report.status == "complete"
    assert report.python_requires == ">=3.10"
    assert 'pandas>=2; python_version >= "3.10"' in report.declared_requirements
    assert not any("setuptools" in value for value in report.declared_requirements)
    deps = {item.module: item for item in report.dependencies}
    assert deps["custom_lib"].package == "custom-lib"
    assert deps["numpy"].status == "declared"
    assert not any(f.code == "PYTHON_VERSION_UNKNOWN" for f in report.findings)


def test_syntax_errors_and_source_limits_are_partial():
    snapshot = project(
        **{
            "a.py": "import (\n",
            "b.py": "import pandas\n",
            "data.csv": "1,2\n",
        }
    )
    report = ProjectAnalyzer().analyze(snapshot)
    assert report.status == "partial"
    assert any(f.code == "SOURCE_PARSE_ERROR" and f.path == "a.py" for f in report.findings)
    assert report.dependencies[0].module == "pandas"
    limited = ProjectAnalyzer().analyze(snapshot, AnalysisOptions(max_source_bytes=2, max_files=1))
    assert limited.status == "partial"
    assert len(limited.files) == 3
    assert {"SOURCE_TOO_LARGE", "FILE_LIMIT_EXCEEDED"} <= {f.code for f in limited.findings}


def test_no_manifest_secret_is_echoed_in_findings_or_dependency_values():
    token = "secret-token-value"
    report = ProjectAnalyzer().analyze(
        project(
            **{
                "requirements.txt": f"private @ https://user:{token}@example.org/package.whl\n",
                "bad.py": f"password = '{token}'\nimport (\n",
                "pyproject.toml": f"invalid='{token}\n",
            }
        )
    )
    assert report.status == "partial"
    assert token not in repr(report.to_dict())
    assert not report.declared_requirements


def test_deterministic_id_changes_with_options_and_content():
    snapshot = project(**{"main.py": "import os\n"})
    analyzer = ProjectAnalyzer()
    first = analyzer.analyze(snapshot)
    assert first == analyzer.analyze(snapshot)
    different = analyzer.analyze(snapshot, AnalysisOptions(python_version="3.12"))
    assert first.analysis_id != different.analysis_id
    changed = ProjectSnapshot((SourceFile("main.py", b"import numpy\n"),), snapshot.fingerprint)
    assert first.analysis_id != analyzer.analyze(changed).analysis_id


@pytest.mark.parametrize("root", ["../outside", "/tmp", "a/../../b", "C:\\temp", ""])
def test_source_roots_cannot_escape_snapshot(root):
    with pytest.raises(ValueError):
        ProjectAnalyzer().analyze(project(), AnalysisOptions(source_roots=(root,)))


def test_unsupported_python_profile_is_partial_and_never_claims_host_modules_are_known():
    snapshot = project(**{"main.py": "import os\nimport pandas\n"})
    version = "3.12" if sys.version_info[:2] != (3, 12) else "3.11"
    report = ProjectAnalyzer().analyze(snapshot, AnalysisOptions(python_version=version))
    assert report.status == "partial"
    assert {d.module: d.category for d in report.dependencies} == {
        "os": "unresolved",
        "pandas": "external",
    }
    assert any(f.code == "PYTHON_PROFILE_UNSUPPORTED" for f in report.findings)


def test_manifest_invalid_fields_and_dynamic_metadata_are_partial():
    report = ProjectAnalyzer().analyze(
        project(
            **{
                "pyproject.toml": """
[project]
dependencies = "not an array"
requires-python = "invalid"
dynamic = ["dependencies"]
"""
            }
        )
    )
    assert report.status == "partial"
    assert {"MANIFEST_PARSE_ERROR", "DYNAMIC_DEPENDENCIES", "PYTHON_REQUIREMENT_INVALID"} <= {
        finding.code for finding in report.findings
    }
