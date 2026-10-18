from __future__ import annotations

import io
import stat
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .models import ProjectSnapshot
from .snapshot import SnapshotBuilder, SnapshotError


class ArchiveError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ArchivePolicy:
    max_archive_bytes: int = 10 * 1024 * 1024
    max_expanded_bytes: int = 50_000_000
    max_entries: int = 1_000
    max_file_bytes: int = 10 * 1024 * 1024
    max_compression_ratio: int = 200

    def __post_init__(self) -> None:
        if any(value <= 0 for value in vars(self).values()):
            raise ValueError("Archive limits must be positive")


class ArchiveReader:
    supported_compression = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
    chunk_bytes = 64 * 1024

    def __init__(self, policy: ArchivePolicy | None = None):
        self.policy = policy or ArchivePolicy()

    def from_bytes(self, content: bytes) -> ProjectSnapshot:
        return self.read(io.BytesIO(content))

    def from_path(self, path: str | Path) -> ProjectSnapshot:
        with Path(path).open("rb") as stream:
            return self.read(stream)

    def read(self, stream: BinaryIO) -> ProjectSnapshot:
        stream.seek(0, io.SEEK_END)
        if stream.tell() > self.policy.max_archive_bytes:
            raise ArchiveError("ARCHIVE_LIMIT", "Uploaded archive exceeds the size limit")
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream) as archive:
                entries = archive.infolist()
                if len(entries) > self.policy.max_entries:
                    raise ArchiveError("ARCHIVE_LIMIT", "Archive has too many entries")
                normalized: dict[str, zipfile.ZipInfo] = {}
                file_paths: set[str] = set()
                directory_paths: set[str] = set()
                total = 0
                for entry in entries:
                    path = self._validate_entry(entry)
                    if path in normalized:
                        raise ArchiveError("ARCHIVE_PATH_CONFLICT", "Archive paths are duplicated")
                    parents = {
                        parent.as_posix()
                        for parent in PurePosixPath(path).parents
                        if parent.as_posix() != "."
                    }
                    if parents & file_paths or (not entry.is_dir() and path in directory_paths):
                        raise ArchiveError(
                            "ARCHIVE_PATH_CONFLICT", "Archive contains conflicting paths"
                        )
                    directory_paths.update(parents)
                    if entry.is_dir():
                        directory_paths.add(path)
                    else:
                        file_paths.add(path)
                    normalized[path] = entry
                    total += entry.file_size
                    if total > self.policy.max_expanded_bytes:
                        raise ArchiveError(
                            "ARCHIVE_LIMIT", "Expanded archive exceeds the size limit"
                        )

                files: dict[str, bytes] = {}
                total = 0
                for path, entry in normalized.items():
                    if entry.is_dir():
                        continue
                    data = bytearray()
                    with archive.open(entry) as source:
                        while chunk := source.read(self.chunk_bytes):
                            data.extend(chunk)
                            total += len(chunk)
                            if (
                                len(data) > self.policy.max_file_bytes
                                or total > self.policy.max_expanded_bytes
                            ):
                                raise ArchiveError(
                                    "ARCHIVE_LIMIT", "Actual expanded bytes exceed the size limit"
                                )
                    files[path] = bytes(data)
                builder = SnapshotBuilder(
                    max_files=self.policy.max_entries,
                    max_total_bytes=self.policy.max_expanded_bytes,
                )
                return builder.from_mapping(files)
        except SnapshotError as error:
            raise ArchiveError(
                "ARCHIVE_PATH_CONFLICT", "Archive contains conflicting paths"
            ) from error
        except (
            zipfile.BadZipFile,
            zlib.error,
            NotImplementedError,
            RuntimeError,
            EOFError,
        ) as error:
            raise ArchiveError("ARCHIVE_INVALID", "Archive cannot be read safely") from error

    def _validate_entry(self, entry: zipfile.ZipInfo) -> str:
        try:
            path = SnapshotBuilder.normalize_path(entry.filename.rstrip("/"))
        except SnapshotError as error:
            raise ArchiveError("ARCHIVE_PATH_INVALID", "Archive path is not allowed") from error
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise ArchiveError("ARCHIVE_ENTRY_INVALID", "Archive contains a link or special file")
        if mode == stat.S_IFDIR and not entry.is_dir():
            raise ArchiveError("ARCHIVE_ENTRY_INVALID", "Archive entry type is inconsistent")
        if entry.flag_bits & 1:
            raise ArchiveError("ARCHIVE_ENCRYPTED", "Encrypted archives are unsupported")
        if entry.compress_type not in self.supported_compression:
            raise ArchiveError("ARCHIVE_COMPRESSION", "Archive compression is unsupported")
        if entry.file_size > self.policy.max_file_bytes:
            raise ArchiveError("ARCHIVE_LIMIT", "An archive file exceeds the size limit")
        if entry.file_size > max(entry.compress_size, 1) * self.policy.max_compression_ratio:
            raise ArchiveError("ARCHIVE_LIMIT", "Archive compression ratio exceeds the limit")
        return path
