import pytest

from nishtyak_core.snapshot import SnapshotBuilder, SnapshotError


def test_snapshot_is_independent_of_mapping_order():
    builder = SnapshotBuilder()
    first = builder.from_mapping({"b.py": "import os", "a.py": "print(1)"})
    second = builder.from_mapping({"a.py": "print(1)", "b.py": "import os"})
    assert first == second


@pytest.mark.parametrize("path", ["../main.py", "/main.py", "C:main.py", "a\\b.py", "."])
def test_paths_cannot_escape_snapshot(path):
    with pytest.raises(SnapshotError):
        SnapshotBuilder().from_mapping({path: ""})


def test_limits_are_enforced_before_reading_large_file(tmp_path):
    (tmp_path / "big.csv").write_bytes(b"a" * 20)
    with pytest.raises(SnapshotError, match="limits"):
        SnapshotBuilder(max_total_bytes=10).from_directory(tmp_path)


def test_directory_reader_rejects_symlinks(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 1")
    (project / "linked.py").symlink_to(outside)
    with pytest.raises(SnapshotError, match="symbolic"):
        SnapshotBuilder().from_directory(project)
