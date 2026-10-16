import io
import stat
import warnings
import zipfile

import pytest

from nishtyak_core.archives import ArchiveError, ArchivePolicy, ArchiveReader


def archive_bytes(entries, compression=zipfile.ZIP_DEFLATED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return stream.getvalue()


def test_archive_becomes_a_snapshot_without_extracting_or_executing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = archive_bytes(
        [("main.py", "from pathlib import Path; Path('executed').touch()"), ("data/x.csv", "a\n1")]
    )
    snapshot = ArchiveReader().from_bytes(data)
    assert [file.path for file in snapshot.files] == ["data/x.csv", "main.py"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["../x.py", "/x.py", "C:/x.py", "a\\x.py"])
def test_unsafe_archive_paths_are_rejected(name):
    with pytest.raises(ArchiveError) as result:
        ArchiveReader().from_bytes(archive_bytes([(name, "print(1)")]))
    assert result.value.code == "ARCHIVE_PATH_INVALID"


def test_normalized_duplicate_paths_are_rejected():
    data = archive_bytes([("a//main.py", "print(1)"), ("a/main.py", "print(2)")])
    with pytest.raises(ArchiveError, match="duplicated"):
        ArchiveReader().from_bytes(data)


def test_duplicate_zip_members_are_rejected():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        data = archive_bytes([("main.py", "print(1)"), ("main.py", "print(2)")])
    with pytest.raises(ArchiveError, match="duplicated"):
        ArchiveReader().from_bytes(data)


def test_file_and_directory_prefix_conflict_is_rejected():
    data = archive_bytes([("data", "file"), ("data/x.csv", "child")])
    with pytest.raises(ArchiveError, match="conflicting"):
        ArchiveReader().from_bytes(data)


@pytest.mark.parametrize("entries", [[("a", "file"), ("a/b/", "")], [("a/b/", ""), ("a", "file")]])
def test_file_and_empty_directory_prefix_conflict_is_rejected(entries):
    with pytest.raises(ArchiveError) as result:
        ArchiveReader().from_bytes(archive_bytes(entries))
    assert result.value.code == "ARCHIVE_PATH_CONFLICT"


def test_symlink_is_rejected():
    entry = zipfile.ZipInfo("linked.py")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ArchiveError, match="link"):
        ArchiveReader().from_bytes(archive_bytes([(entry, "../../outside")]))


@pytest.mark.parametrize(
    "policy,entries",
    [
        (ArchivePolicy(max_archive_bytes=1), [("a", "x")]),
        (ArchivePolicy(max_entries=1), [("a", "x"), ("b", "x")]),
        (ArchivePolicy(max_expanded_bytes=5), [("a", "123"), ("b", "456")]),
        (ArchivePolicy(max_file_bytes=2), [("a", "123")]),
        (ArchivePolicy(max_compression_ratio=1), [("a", "x" * 10_000)]),
    ],
)
def test_archive_limits_are_enforced(policy, entries):
    with pytest.raises(ArchiveError) as result:
        ArchiveReader(policy).from_bytes(archive_bytes(entries))
    assert result.value.code == "ARCHIVE_LIMIT"


def test_unsupported_compression_and_invalid_zip_are_rejected():
    with pytest.raises(ArchiveError) as result:
        ArchiveReader().from_bytes(archive_bytes([("a", "x")], compression=zipfile.ZIP_BZIP2))
    assert result.value.code == "ARCHIVE_COMPRESSION"
    with pytest.raises(ArchiveError) as result:
        ArchiveReader().from_bytes(b"not a zip")
    assert result.value.code == "ARCHIVE_INVALID"


def test_regular_directory_entries_are_accepted():
    data = archive_bytes([("pkg/", b""), ("pkg/main.py", "print(1)")])
    assert ArchiveReader().from_bytes(data).get("pkg/main.py") is not None


def test_corrupt_deflate_is_classified_as_an_invalid_archive():
    data = bytearray(archive_bytes([("main.py", "print(1)")]))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entry = archive.getinfo("main.py")
        offset = entry.header_offset + 30 + len(entry.filename.encode()) + len(entry.extra)
    data[offset] |= 0x06  # Reserved DEFLATE block type.
    with pytest.raises(ArchiveError) as result:
        ArchiveReader().from_bytes(bytes(data))
    assert result.value.code == "ARCHIVE_INVALID"
