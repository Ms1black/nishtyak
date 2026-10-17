from __future__ import annotations

import io
import re
import stat
import struct
import zipfile
import zlib
from pathlib import PurePosixPath

from nishtyak_client import LogicClientError
from nishtyak_core.redaction import TextRedactor
from nishtyak_core.security import SecretScanner
from nishtyak_core.snapshot import SnapshotBuilder, SnapshotError


class ArchiveFilePreview:
    preview_bytes = 256 * 1024
    archive_bytes = 64 * 1024**2
    expanded_bytes = 64 * 1024**2
    member_bytes = 10 * 1024**2
    max_entries = 1100

    def read(self, content: bytes, path: str) -> dict:
        self.validate_path(path)
        if len(content) > self.archive_bytes:
            self._invalid()
        self._directory_limit(content)
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = self._entries(archive)
                entry = entries.get(path)
                if entry is None or entry.is_dir():
                    raise LogicClientError(404, "FILE_NOT_FOUND", "Archive file was not found")
                if self._hidden_path(path):
                    return {"kind": "hidden"}
                with archive.open(entry) as stream:
                    data = stream.read(self.member_bytes + 1)
                if len(data) > self.member_bytes or len(data) != entry.file_size:
                    self._invalid()
        except (
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
            zlib.error,
            RuntimeError,
            NotImplementedError,
            EOFError,
            ValueError,
        ) as error:
            raise LogicClientError(
                409, "ARCHIVE_INVALID", "Archive cannot be previewed safely"
            ) from error
        return self._text(data)

    def _directory_limit(self, content: bytes) -> None:
        # Bound the directory before ZipFile allocates one object per member.
        tail = content[-(65535 + 22) :]
        offset = tail.rfind(b"PK\x05\x06")
        if offset < 0 or len(tail) - offset < 22:
            self._invalid()
        _, disk, directory_disk, disk_entries, entries, size, _, comment = struct.unpack_from(
            "<4s4H2IH", tail, offset
        )
        if (
            disk != 0
            or directory_disk != 0
            or disk_entries != entries
            or entries > self.max_entries
            or size > 4 * 1024**2
            or offset + 22 + comment != len(tail)
            or (offset >= 20 and tail[offset - 20 : offset - 16] == b"PK\x06\x07")
        ):
            self._invalid()

    @staticmethod
    def validate_path(path: str) -> None:
        try:
            if (
                not isinstance(path, str)
                or len(path) > 4096
                or SnapshotBuilder.normalize_path(path) != path
                or any(ord(char) == 127 for char in path)
            ):
                raise SnapshotError("Expected an exact archive member path")
        except SnapshotError as error:
            raise LogicClientError(
                422, "PATH_INVALID", "Expected an exact archive member path"
            ) from error

    def _entries(self, archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
        entries = archive.infolist()
        if len(entries) > self.max_entries:
            self._invalid()
        result = {}
        files, directories = set(), set()
        expanded = 0
        for entry in entries:
            path = entry.filename.rstrip("/")
            try:
                self.validate_path(path)
            except LogicClientError:
                self._invalid()
            mode = stat.S_IFMT(entry.external_attr >> 16)
            parents = {str(parent) for parent in PurePosixPath(path).parents if str(parent) != "."}
            if (
                entry.orig_filename != entry.filename
                or path in result
                or mode not in {0, stat.S_IFREG, stat.S_IFDIR}
                or (mode == stat.S_IFDIR and not entry.is_dir())
                or entry.flag_bits & 1
                or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or entry.file_size > self.member_bytes
                or entry.file_size > max(1, entry.compress_size) * 200
                or parents & files
                or (not entry.is_dir() and path in directories)
            ):
                self._invalid()
            expanded += entry.file_size
            if expanded > self.expanded_bytes:
                self._invalid()
            directories.update(parents)
            (directories if entry.is_dir() else files).add(path)
            result[path] = entry
        return result

    def _text(self, data: bytes) -> dict:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return {"kind": "binary"}
        if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
            return {"kind": "binary"}
        if any(pattern.search(text) for pattern in SecretScanner.patterns):
            return {"kind": "hidden"}
        safe = TextRedactor().redact(text).encode("utf-8")
        return {
            "kind": "text",
            "content": safe[: self.preview_bytes].decode("utf-8", errors="ignore"),
            "truncated": len(safe) > self.preview_bytes,
        }

    @staticmethod
    def _hidden_path(path: str) -> bool:
        return bool(
            re.search(r"(^|/)(\.env(?:\..*)?|id_rsa|id_ed25519|credentials(?:\..*)?)$", path, re.I)
        ) or path.lower().endswith((".pem", ".key", ".p12", ".pfx"))

    @staticmethod
    def _invalid() -> None:
        raise LogicClientError(409, "ARCHIVE_INVALID", "Archive cannot be previewed safely")
