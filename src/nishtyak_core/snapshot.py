from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath

from .models import ProjectSnapshot, SourceFile, stable_id


class SnapshotError(ValueError):
    pass


class SnapshotBuilder:
    excluded_directories = frozenset(
        {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
    )

    def __init__(self, max_files: int = 1_000, max_total_bytes: int = 50_000_000):
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes

    @staticmethod
    def normalize_path(path: str) -> str:
        if not path or "\\" in path or any(ord(character) < 32 for character in path):
            raise SnapshotError("File paths must be nonempty relative POSIX paths")
        parts = PurePosixPath(path)
        if parts.is_absolute() or ".." in parts.parts or PureWindowsPath(path).drive:
            raise SnapshotError("File path escapes the project")
        normalized = parts.as_posix()
        if normalized == ".":
            raise SnapshotError("A file path must name a file")
        return normalized

    def from_mapping(self, files: Mapping[str, bytes | str]) -> ProjectSnapshot:
        records = []
        seen: set[str] = set()
        directories: set[str] = set()
        total = 0
        for raw_path, value in files.items():
            path = self.normalize_path(raw_path)
            if path in seen:
                raise SnapshotError("Duplicate normalized file path")
            parents = {
                parent.as_posix()
                for parent in PurePosixPath(path).parents
                if parent.as_posix() != "."
            }
            if path in directories or parents & seen:
                raise SnapshotError("A project path is both a file and a directory")
            seen.add(path)
            directories.update(parents)
            content = value.encode("utf-8") if isinstance(value, str) else bytes(value)
            total += len(content)
            if len(seen) > self.max_files or total > self.max_total_bytes:
                raise SnapshotError("Project exceeds the configured snapshot limits")
            records.append(SourceFile(path, content))
        records.sort(key=lambda file: file.path)
        fingerprint = stable_id([(file.path, file.info.sha256) for file in records])
        return ProjectSnapshot(tuple(records), fingerprint)

    def from_directory(self, directory: str | Path) -> ProjectSnapshot:
        root = Path(directory).resolve(strict=True)
        if not root.is_dir():
            raise SnapshotError("Project path must be a directory")
        files: dict[str, bytes] = {}
        total = 0
        for current, directories, filenames in os.walk(root, followlinks=False):
            for name in directories:
                if name not in self.excluded_directories and (Path(current) / name).is_symlink():
                    raise SnapshotError("Project contains a symbolic link directory")
            directories[:] = sorted(
                name
                for name in directories
                if name not in self.excluded_directories
                and not self._is_virtual_environment(Path(current) / name)
            )
            for name in sorted(filenames):
                path = Path(current) / name
                if path.is_symlink() or not path.is_file():
                    raise SnapshotError("Project contains a symbolic link or special file")
                size = path.stat().st_size
                if len(files) >= self.max_files or total + size > self.max_total_bytes:
                    raise SnapshotError("Project exceeds the configured snapshot limits")
                content = path.read_bytes()
                total += len(content)
                files[path.relative_to(root).as_posix()] = content
        return self.from_mapping(files)

    @staticmethod
    def _is_virtual_environment(directory: Path) -> bool:
        marker = directory / "pyvenv.cfg"
        return not marker.is_symlink() and marker.is_file()

    def validate(self, snapshot: ProjectSnapshot) -> None:
        if len({file.path for file in snapshot.files}) != len(snapshot.files):
            raise SnapshotError("Duplicate file path")
        rebuilt = self.from_mapping({file.path: file.content for file in snapshot.files})
        if rebuilt.fingerprint != snapshot.fingerprint:
            raise SnapshotError("Project fingerprint does not match its files")
