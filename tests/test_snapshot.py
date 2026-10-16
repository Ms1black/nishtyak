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


@pytest.mark.parametrize(
    "paths",
    [
        ("data", "data/x.csv"),
        ("data/x.csv", "data"),
        ("data", "data.1", "data/nested/x.csv"),
        ("data/nested/x.csv", "data.1", "data"),
    ],
)
def test_file_paths_cannot_also_name_parent_directories(paths):
    with pytest.raises(SnapshotError, match="both a file and a directory"):
        SnapshotBuilder().from_mapping(dict.fromkeys(paths, b""))


def test_shared_directory_names_and_filename_prefixes_are_valid():
    snapshot = SnapshotBuilder().from_mapping(
        {"data.csv": b"", "data/a.csv": b"", "data/nested/b.csv": b"", "dataset": b""}
    )
    assert len(snapshot.files) == 4


@pytest.mark.parametrize("name", ["env", "venv"])
def test_directory_reader_retains_ordinary_environment_named_packages(tmp_path, name):
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").write_text("value = 1")

    snapshot = SnapshotBuilder().from_directory(tmp_path)

    assert snapshot.get(f"{name}/__init__.py") is not None


def test_directory_reader_skips_virtual_environment_with_regular_marker(tmp_path):
    environment = tmp_path / "custom-environment"
    environment.mkdir()
    (environment / "pyvenv.cfg").write_text("home = /usr/bin")
    (environment / "dependency.py").write_text("value = 1")
    (tmp_path / "main.py").write_text("print(1)")

    snapshot = SnapshotBuilder(max_files=1).from_directory(tmp_path)

    assert [file.path for file in snapshot.files] == ["main.py"]


def test_symlink_marker_does_not_hide_an_ordinary_package(tmp_path):
    package = tmp_path / "venv"
    package.mkdir()
    (package / "__init__.py").write_text("value = 1")
    (tmp_path / "config.txt").write_text("not a virtual environment")
    (package / "pyvenv.cfg").symlink_to(tmp_path / "config.txt")

    with pytest.raises(SnapshotError, match="symbolic"):
        SnapshotBuilder().from_directory(tmp_path)
