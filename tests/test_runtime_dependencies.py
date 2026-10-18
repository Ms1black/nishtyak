from __future__ import annotations

import pytest

from nishtyak_core.analysis import ProjectAnalyzer
from nishtyak_core.models import RunSpec
from nishtyak_core.runtime_dependencies import RuntimeDependencySelector
from nishtyak_core.snapshot import SnapshotBuilder


def selection_for(files, **kwargs):
    report = ProjectAnalyzer().analyze(SnapshotBuilder().from_mapping(files))
    return report, RuntimeDependencySelector().select(report, **kwargs)


def test_type_checking_and_unreferenced_support_dependencies_are_not_runtime_requirements():
    report, selected = selection_for(
        {
            "experiment.py": (
                "from typing import TYPE_CHECKING\nimport numpy\n"
                "if TYPE_CHECKING:\n    import unavailable_stubs\n"
            ),
            "tests/test_experiment.py": "import pytest\nimport hypothesis\n",
            "docs/conf.py": "import sphinx\n",
            "setup.py": "import setuptools\n",
        }
    )
    assert {item.module for item in selected.dependencies} == {"typing", "numpy"}
    assert selected.ignored_evidence_ids == {
        item.id for item in report.imports if item.module not in {"typing", "numpy"}
    }


def test_support_module_imported_at_runtime_and_its_transitive_dependencies_are_retained():
    _, selected = selection_for(
        {
            "experiment.py": "from tests import helper\n",
            "tests/__init__.py": "",
            "tests/helper.py": "from . import calculations\n",
            "tests/calculations.py": "import numpy\n",
            "tests/test_experiment.py": "import pytest\n",
        }
    )
    assert "numpy" in {item.module for item in selected.dependencies}
    assert "pytest" not in {item.module for item in selected.dependencies}


def test_a_distribution_required_at_runtime_survives_a_separate_type_checking_import():
    _, selected = selection_for(
        {
            "experiment.py": (
                "from typing import TYPE_CHECKING\nimport pandas\n"
                "if TYPE_CHECKING:\n    import pandas\n"
            )
        }
    )
    pandas = [item for item in selected.dependencies if item.module == "pandas"]
    assert len(pandas) == 1
    assert pandas[0].context == "runtime"


def test_declared_dependencies_without_import_evidence_and_optional_imports_remain():
    _, selected = selection_for(
        {
            "experiment.py": "try:\n    import matplotlib\nexcept ImportError:\n    pass\n",
            "pyproject.toml": '[project]\ndependencies=["requests>=2"]\n',
        }
    )
    assert {item.module for item in selected.dependencies} == {"matplotlib", "requests"}


def test_runtime_import_selects_only_its_unambiguous_extra_without_rewriting_constraints():
    report, selected = selection_for(
        {
            "experiment.py": "import numpy\nimport matplotlib\n",
            "tests/test_experiment.py": "import pytest\n",
            "pyproject.toml": (
                '[project]\ndependencies=["numpy>=1.26"]\n'
                "[project.optional-dependencies]\n"
                "plots=[\"matplotlib>=3.8; python_version >= '3.10'\"]\n"
                'dev=["pytest>=8", "hypothesis>=6"]\n'
            ),
        }
    )
    assert selected.automatic_extras == ("plots",)
    assert next(item for item in report.declarations if item.group == "plots").requirement == (
        'matplotlib>=3.8; python_version >= "3.10"'
    )


def test_ambiguous_extras_and_type_checking_or_optional_only_extras_are_not_auto_selected():
    _, selected = selection_for(
        {
            "experiment.py": (
                "from typing import TYPE_CHECKING\nimport torch\n"
                "if TYPE_CHECKING:\n    import pandas\n"
                "try:\n    import matplotlib\nexcept ImportError:\n    pass\n"
            ),
            "pyproject.toml": (
                "[project]\ndependencies=[]\n"
                "[project.optional-dependencies]\n"
                'cpu=["torch>=2"]\ngpu=["torch>=2"]\n'
                'types=["pandas"]\nplots=["matplotlib"]\n'
            ),
        }
    )
    assert selected.automatic_extras == ()


def test_user_selected_launch_in_support_directory_is_kept_in_runtime_scope():
    _, selected = selection_for(
        {"tests/test_experiment.py": "import numpy\n"},
        run_spec=RunSpec("tests/test_experiment.py"),
    )
    assert {item.module for item in selected.dependencies} == {"numpy"}


@pytest.mark.parametrize(
    "run_spec,files",
    [
        (
            RunSpec("tests.test_experiment", kind="module"),
            {"tests/__init__.py": "", "tests/test_experiment.py": "import numpy\n"},
        ),
        (
            RunSpec("tests", kind="module"),
            {"tests/__init__.py": "", "tests/__main__.py": "import numpy\n"},
        ),
        (
            RunSpec("tests.helper:main", kind="callable"),
            {"tests/__init__.py": "", "tests/helper.py": "import numpy\ndef main(): pass\n"},
        ),
    ],
)
def test_selected_module_and_callable_launches_bring_support_modules_into_scope(run_spec, files):
    _, selected = selection_for(files, run_spec=run_spec)
    assert "numpy" in {item.module for item in selected.dependencies}


def test_removed_files_do_not_contribute_runtime_dependencies():
    report, _ = selection_for({"experiment.py": "import numpy\n", "preview.py": "import pandas\n"})
    selected = RuntimeDependencySelector().select(
        report, included_files=tuple(item for item in report.files if item.path != "preview.py")
    )
    assert {item.module for item in selected.dependencies} == {"numpy"}


def test_only_the_selected_project_root_contributes_application_sources():
    from nishtyak_core.models import AnalysisOptions

    snapshot = SnapshotBuilder().from_mapping(
        {
            "a/pyproject.toml": "",
            "a/experiment.py": "import numpy\n",
            "b/pyproject.toml": "",
            "b/experiment.py": "import pandas\n",
        }
    )
    report = ProjectAnalyzer().analyze(snapshot, AnalysisOptions(project_root="a"))
    selected = RuntimeDependencySelector().select(report)
    assert {item.module for item in selected.dependencies} == {"numpy"}
