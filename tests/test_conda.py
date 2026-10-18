from __future__ import annotations

import pytest

from nishtyak_core.conda import CondaEnvironmentReader
from nishtyak_core.models import ProjectSnapshot, SourceFile


def inspect(text):
    return CondaEnvironmentReader().read(
        ProjectSnapshot((SourceFile("environment.yml", text.encode()),), "snapshot")
    )


def test_conda_and_pip_specs_remain_separate_with_markers_preserved():
    result = inspect("""
name: experiment
channels: [conda-forge, defaults]
dependencies:
  - python=3.11
  - numpy>=1.24
  - conda-forge::pandas=2.2
  - pip
  - pip:
      - 'requests>=2; python_version >= "3.10"'
prefix: /home/author/.conda/envs/experiment
""")
    assert result.complete
    assert result.name == "experiment"
    assert result.channels == ("conda-forge", "defaults")
    assert result.python == "python=3.11"
    assert result.conda_dependencies == (
        "python=3.11",
        "numpy>=1.24",
        "conda-forge::pandas=2.2",
        "pip",
    )
    assert result.pip_dependencies == ('requests>=2; python_version >= "3.10"',)
    assert "/home/author" not in repr(result)


@pytest.mark.parametrize(
    "text",
    [
        '!!python/object/apply:os.system ["touch /tmp/never"]',
        "dependencies: &deps [numpy]\nchannels: *deps",
        "dependencies: [unclosed",
        "dependencies: not-an-array",
        "channels: not-an-array",
    ],
)
def test_invalid_or_executable_yaml_is_rejected(text):
    result = inspect(text)
    assert not result.complete
    assert result.findings


def test_urls_and_pip_options_are_omitted_without_leaking_secrets():
    token = "top-secret-token"
    result = inspect(f"""
channels: ["https://user:{token}@example.org/channel"]
dependencies:
  - pip:
      - "package @ https://user:{token}@example.org/file.whl"
      - "--index-url https://example.org"
""")
    assert not result.complete
    assert not result.channels
    assert not result.pip_dependencies
    assert token not in repr(result.to_dict())


def test_missing_conda_manifest_is_an_empty_inspection():
    result = CondaEnvironmentReader().read(ProjectSnapshot((), "empty"))
    assert result.path is None
    assert result.complete


def test_multiple_python_specs_are_not_silently_resolved():
    result = inspect("dependencies: [python=3.10, python=3.11]")
    assert not result.complete
    assert result.python is None
    assert any(f.code == "CONDA_PYTHON_CONFLICT" for f in result.findings)


def test_yaml_size_and_nesting_are_bounded():
    source = SourceFile("environment.yml", b"name: experiment")
    result = CondaEnvironmentReader(max_bytes=3).read(ProjectSnapshot((source,), "snapshot"))
    assert not result.complete
    assert result.findings[0].code == "CONDA_TOO_LARGE"
    nested = "key: " + "[" * 30 + "value" + "]" * 30
    assert not inspect(nested).complete
