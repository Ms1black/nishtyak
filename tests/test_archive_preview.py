from __future__ import annotations

import io
import stat
import struct
import zipfile

import pytest

from nishtyak_client import LogicClientError
from nishtyak_web.archive_preview import ArchiveFilePreview


def bundle(files, compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for name, content in files:
            archive.writestr(name, content)
    return stream.getvalue()


@pytest.mark.parametrize(
    "path",
    ["README.md", "requirements.lock.txt", "dependency-lock.json", "Dockerfile", "project/main.py"],
)
def test_preview_reads_exact_output_member(path):
    content = "# Русский текст\nvalue = 4\n"
    assert ArchiveFilePreview().read(bundle([(path, content)]), path) == {
        "kind": "text",
        "content": content,
        "truncated": False,
    }


@pytest.mark.parametrize("content", [b"\xff\xfe", b"png\x00data", b"a\x01b"])
def test_binary_output_is_not_returned_as_text(content):
    assert ArchiveFilePreview().read(
        bundle([("project/data.bin", content)]), "project/data.bin"
    ) == {"kind": "binary"}


@pytest.mark.parametrize(
    "path", ["project/.env", "project/.env.local", "credentials.json", "x.pem"]
)
def test_secret_filenames_are_hidden_even_in_injected_archive(path):
    assert ArchiveFilePreview().read(bundle([(path, "ordinary value")]), path) == {"kind": "hidden"}


@pytest.mark.parametrize("prefix", ["", "x" * (256 * 1024) + "\n"])
def test_scanner_checks_secrets_before_preview_truncation(prefix):
    content = prefix + "api_key = 'sk-" + "a" * 32 + "'\n"
    assert ArchiveFilePreview().read(
        bundle([("project/config.py", content)]), "project/config.py"
    ) == {"kind": "hidden"}


def test_utf8_preview_is_bounded_in_bytes_and_truncation_is_honest():
    preview = ArchiveFilePreview()
    text = "я" * (preview.preview_bytes // 2 + 1)
    result = preview.read(bundle([("project/input.csv", text)]), "project/input.csv")
    assert result["kind"] == "text" and result["truncated"]
    assert result["content"] == text[:-1]
    assert len(result["content"].encode()) == preview.preview_bytes
    exact = "x" * preview.preview_bytes
    assert not preview.read(bundle([("README.md", exact)]), "README.md")["truncated"]


@pytest.mark.parametrize(
    "path",
    [
        "",
        "../secret",
        "/etc/passwd",
        "C:/secret",
        "project\\main.py",
        "./README.md",
        "a//b",
        "a\x00b",
    ],
)
def test_invalid_or_nonexact_paths_are_rejected(path):
    with pytest.raises(LogicClientError) as caught:
        ArchiveFilePreview().read(bundle([("README.md", "safe")]), path)
    assert caught.value.status == 422 and caught.value.code == "PATH_INVALID"


def test_missing_or_directory_members_are_404():
    content = bundle([("project/", b""), ("project/main.py", "print(4)\n")])
    for path in ("missing.txt", "project"):
        with pytest.raises(LogicClientError) as caught:
            ArchiveFilePreview().read(content, path)
        assert caught.value.status == 404 and caught.value.code == "FILE_NOT_FOUND"


@pytest.mark.parametrize(
    "files", [[("../secret", "bad")], [("a", "one"), ("a", "two")], [("a", "one"), ("a/b", "two")]]
)
def test_unsafe_archive_metadata_is_rejected(files):
    with pytest.raises(LogicClientError) as caught:
        ArchiveFilePreview().read(bundle(files), "README.md")
    assert caught.value.status == 409 and caught.value.code == "ARCHIVE_INVALID"


def test_symlink_entries_are_not_previewed():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        entry = zipfile.ZipInfo("project/link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, "/etc/passwd")
    with pytest.raises(LogicClientError) as caught:
        ArchiveFilePreview().read(stream.getvalue(), "project/link")
    assert caught.value.code == "ARCHIVE_INVALID"


def test_zip_bomb_metadata_and_corrupt_content_are_rejected():
    content = bundle([("README.md", "x" * 100000)], zipfile.ZIP_DEFLATED)
    with pytest.raises(LogicClientError) as caught:
        ArchiveFilePreview().read(content, "README.md")
    assert caught.value.code == "ARCHIVE_INVALID"
    content = bytearray(bundle([("README.md", "safe text")]))
    position = content.index(b"safe text")
    content[position] ^= 1
    with pytest.raises(LogicClientError) as caught:
        ArchiveFilePreview().read(bytes(content), "README.md")
    assert caught.value.code == "ARCHIVE_INVALID"


def test_directory_entry_count_is_bounded_before_zipfile_allocation(monkeypatch):
    content = bytearray(bundle([("README.md", "safe")]))
    end = content.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", content, end + 8, 1101, 1101)
    monkeypatch.setattr(
        zipfile, "ZipFile", lambda *a, **kw: pytest.fail("Unbounded directory parsed")
    )
    with pytest.raises(LogicClientError) as caught:
        ArchiveFilePreview().read(bytes(content), "README.md")
    assert caught.value.code == "ARCHIVE_INVALID"


@pytest.mark.parametrize("change", ["encrypted", "compression", "zip64"])
def test_unsupported_zip_features_are_rejected(change):
    content = bytearray(bundle([("README.md", "safe")]))
    directory = content.index(b"PK\x01\x02")
    if change == "encrypted":
        struct.pack_into("<H", content, directory + 8, 1)
    elif change == "compression":
        struct.pack_into("<H", content, directory + 10, zipfile.ZIP_BZIP2)
    else:
        end = content.rfind(b"PK\x05\x06")
        content[end:end] = b"PK\x06\x07" + b"\x00" * 16
    with pytest.raises(LogicClientError) as caught:
        ArchiveFilePreview().read(bytes(content), "README.md")
    assert caught.value.code == "ARCHIVE_INVALID"


def test_actual_member_reads_are_bounded_and_oversized_input_is_rejected(monkeypatch):
    preview = ArchiveFilePreview()
    monkeypatch.setattr(preview, "member_bytes", 8)
    with pytest.raises(LogicClientError) as caught:
        preview.read(bundle([("README.md", "ninebytes")]), "README.md")
    assert caught.value.code == "ARCHIVE_INVALID"
    monkeypatch.setattr(preview, "archive_bytes", 4)
    with pytest.raises(LogicClientError) as caught:
        preview.read(b"not ZIP", "README.md")
    assert caught.value.code == "ARCHIVE_INVALID"
