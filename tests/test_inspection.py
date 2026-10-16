from __future__ import annotations

from dataclasses import asdict

import pytest

from nishtyak_core.inspection import ProjectInspector
from nishtyak_core.snapshot import SnapshotBuilder


def inspect(files: dict[str, bytes | str]):
    return ProjectInspector().inspect(SnapshotBuilder().from_mapping(files))


def test_root_documents_are_case_insensitive_and_nonblocking() -> None:
    findings = inspect({"ReAdMe.MARKDOWN": "", "LiCeNcE.TXT": ""})
    assert {finding.code for finding in findings} == {"DATA_FILES_SUMMARY"}
    missing = inspect({"docs/README.md": ""})
    assert {finding.code for finding in missing} == {
        "README_MISSING",
        "LICENSE_MISSING",
        "DATA_FILES_SUMMARY",
    }
    assert all(finding.severity == "info" for finding in missing)


def test_data_summary_counts_names_without_parsing_contents() -> None:
    findings = inspect({"data.CSV": b"\x00", "nested/a.json": b"invalid", "b.JSON": b""})
    summary = next(finding for finding in findings if finding.code == "DATA_FILES_SUMMARY")
    assert "1 CSV and 2 JSON" in summary.message


@pytest.mark.parametrize(
    "function",
    ["open", "pd.read_csv", "pd.read_json", "pd.read_excel", "np.loadtxt", "np.genfromtxt"],
)
def test_literal_input_paths_are_checked_against_snapshot(function: str) -> None:
    findings = inspect({"main.py": f"{function}('data.csv')\n", "data.csv": ""})
    assert not {"INPUT_PATH_UNVERIFIED", "EXTERNAL_INPUT_REFERENCE"} & {
        finding.code for finding in findings
    }


def test_unmatched_input_is_unverified_with_source_location_not_claimed_missing() -> None:
    findings = inspect({"src/main.py": "import pandas as pd\npd.read_csv('data.csv')\n"})
    finding = next(item for item in findings if item.code == "INPUT_PATH_UNVERIFIED")
    assert (finding.path, finding.line) == ("src/main.py", 2)
    assert "working directory" in finding.message
    assert "missing" not in finding.message.lower()
    assert "data.csv" not in repr(asdict(finding))


@pytest.mark.parametrize("mode", ["w", "wb", "w+", "a", "a+", "x"])
@pytest.mark.parametrize("keyword", [False, True])
def test_output_open_calls_are_ignored(mode: str, keyword: bool) -> None:
    argument = f"mode={mode!r}" if keyword else repr(mode)
    findings = inspect({"main.py": f"open('new.txt', {argument})"})
    assert "INPUT_PATH_UNVERIFIED" not in {finding.code for finding in findings}


@pytest.mark.parametrize(
    "path",
    [
        "https://user:secret-token@example.org/input.csv",
        "s3://bucket/data.csv",
        "/mnt/private/data.csv",
        "../data.csv",
        "C:\\private\\data.csv",
        "~/data.csv",
        "\\\\server\\share\\data.csv",
    ],
)
def test_external_paths_do_not_echo_reference_or_credentials(path: str) -> None:
    findings = inspect({"main.py": f"read_csv({path!r})"})
    finding = next(item for item in findings if item.code == "EXTERNAL_INPUT_REFERENCE")
    assert finding.path == "main.py"
    assert finding.line == 1
    assert path not in repr(asdict(finding))
    assert "secret-token" not in repr(asdict(finding))


def test_computed_paths_keywords_and_invalid_python_are_ignored() -> None:
    findings = inspect(
        {
            "main.py": "read_csv(variable)\nread_csv(filepath_or_buffer='data.csv')\n",
            "bad.py": "import (\n",
        }
    )
    assert not {"INPUT_PATH_UNVERIFIED", "EXTERNAL_INPUT_REFERENCE"} & {
        finding.code for finding in findings
    }


def test_source_limit_is_enforced() -> None:
    snapshot = SnapshotBuilder().from_mapping({"main.py": "open('data.csv')"})
    findings = ProjectInspector(max_source_bytes=5).inspect(snapshot)
    assert "INPUT_PATH_UNVERIFIED" not in {finding.code for finding in findings}
    with pytest.raises(ValueError):
        ProjectInspector(max_source_bytes=1_000_001)


def test_inspection_never_executes_source(tmp_path) -> None:
    marker = tmp_path / "should-not-exist"
    inspect({"main.py": f"open({str(marker)!r}, 'w').write('executed')"})
    assert not marker.exists()
