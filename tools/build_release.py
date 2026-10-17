"""Build a deterministic deployment archive from an explicit runtime allowlist."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import stat
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class ReleaseFile:
    path: str
    content: bytes

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class ReleaseBuilder:
    ROOT_FILES = frozenset(
        {
            ".dockerignore",
            "LICENSE",
            "README.md",
            "env.d.ts",
            "index.html",
            "package.json",
            "package-lock.json",
            "pyproject.toml",
            "tsconfig.json",
            "tsconfig.app.json",
            "tsconfig.node.json",
            "vite.config.ts",
        }
    )
    DEPLOY_FILES = frozenset(
        {
            "deploy/.env.example",
            "deploy/production-compose.yaml",
            "deploy/Dockerfile",
            "deploy/Dockerfile.service",
            "deploy/Dockerfile.web",
            "deploy/Caddyfile",
            "deploy/configure.py",
            "tools/build_release.py",
        }
    )
    SOURCE_SUFFIXES = frozenset({".py", ".ts", ".vue", ".css", ".svg"})
    PUBLIC_SUFFIXES = frozenset({".svg", ".jpg", ".png", ".ico", ".woff", ".woff2"})
    PUBLIC_EXAMPLES = frozenset(
        {
            "public/examples/manifest.json",
            "public/examples/scientific-measurements.zip",
            "public/examples/local-calibration.zip",
            "public/examples/scientific-measurements-with-data.zip",
            "public/examples/local-calibration-with-data.zip",
        }
    )
    EXCLUDED_PARTS = frozenset(
        {
            ".git",
            ".venv",
            "__pycache__",
            "__tests__",
            "node_modules",
            "tests",
            "docs",
            "secrets",
            "credentials",
        }
    )
    MAX_FILE_BYTES = 16 * 1024**2

    def __init__(self, source: Path):
        self.source = source.resolve(strict=True)

    def collect(self) -> tuple[ReleaseFile, ...]:
        collected: list[ReleaseFile] = []
        for relative in sorted(self.ROOT_FILES | self.DEPLOY_FILES | self.PUBLIC_EXAMPLES):
            collected.append(self._read(relative))
        for directory in ("src", "public"):
            base = self.source / directory
            if not base.is_dir() or base.is_symlink():
                raise ValueError(f"Missing regular runtime directory: {directory}")
            for parent, directories, files in os.walk(base, followlinks=False):
                root = Path(parent)
                for name in directories:
                    if (root / name).is_symlink():
                        raise ValueError(
                            "Deployment source cannot contain symbolic link directories"
                        )
                directories[:] = sorted(
                    name
                    for name in directories
                    if name not in self.EXCLUDED_PARTS and not name.endswith(".egg-info")
                )
                for name in sorted(files):
                    relative = (root / name).relative_to(self.source).as_posix()
                    if self._allowed(relative):
                        collected.append(self._read(relative))
        return tuple(sorted(collected, key=lambda item: item.path))

    def _allowed(self, relative: str) -> bool:
        path = PurePosixPath(relative)
        if (
            any(part in self.EXCLUDED_PARTS or part.startswith(".") for part in path.parts)
            or path.name.endswith((".test.ts", ".spec.ts", ".test.py", ".spec.py"))
            or path.name.startswith("test_")
        ):
            return False
        if path.parts[0] == "src":
            return path.suffix in self.SOURCE_SUFFIXES
        return path.suffix in self.PUBLIC_SUFFIXES and relative not in self.PUBLIC_EXAMPLES

    def _read(self, relative: str) -> ReleaseFile:
        path = self.source / relative
        for parent in (path, *path.parents):
            if parent == self.source:
                break
            if parent.is_symlink():
                raise ValueError(f"Runtime file must not use symbolic links: {relative}")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as error:
            raise ValueError(f"Missing regular runtime file: {relative}") from error
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"Runtime path is not a regular file: {relative}")
            content = stream.read(self.MAX_FILE_BYTES + 1)
        if len(content) > self.MAX_FILE_BYTES:
            raise ValueError(f"Runtime file exceeds the release size limit: {relative}")
        return ReleaseFile(relative, content)

    def build(self, output: Path) -> tuple[Path, Path]:
        output = output.resolve()
        if output == self.source or self.source in output.parents:
            raise ValueError("Release output must be outside the source checkout")
        files = self.collect()
        metadata = next(item for item in files if item.path == "pyproject.toml")
        version = tomllib.loads(metadata.content.decode("utf-8"))["project"]["version"]
        if (
            not isinstance(version, str)
            or not version
            or any(char not in "0123456789abcdefghijklmnopqrstuvwxyz.-" for char in version)
        ):
            raise ValueError("Project version is not safe for a release filename")
        output.mkdir(parents=True, exist_ok=True)
        archive = output / f"nishtyak-{version}.tar.gz"
        manifest = output / f"nishtyak-{version}.manifest.json"
        descriptor, temporary = tempfile.mkstemp(prefix=".nishtyak-release-", dir=output)
        try:
            with os.fdopen(descriptor, "wb") as destination:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=destination, mtime=0
                ) as compressed:
                    with tarfile.open(
                        fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT
                    ) as bundle:
                        for item in files:
                            entry = tarfile.TarInfo(f"nishtyak/{item.path}")
                            entry.mode = 0o644
                            entry.size = len(item.content)
                            entry.mtime = 0
                            bundle.addfile(entry, io.BytesIO(item.content))
                destination.flush()
                os.fsync(destination.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, archive)
        finally:
            Path(temporary).unlink(missing_ok=True)
        data = {
            "format": 1,
            "version": version,
            "archive": archive.name,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "files": [
                {"path": item.path, "size": len(item.content), "sha256": item.checksum}
                for item in files
            ],
        }
        self._write_manifest(manifest, data)
        return archive, manifest

    @staticmethod
    def _write_manifest(path: Path, data: dict) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".nishtyak-manifest-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="Directory outside the source checkout")
    arguments = parser.parse_args()
    try:
        output = arguments.output or arguments.source.resolve().parent / "nishtyak-final"
        archive, manifest = ReleaseBuilder(arguments.source).build(output)
    except (ValueError, OSError, KeyError, UnicodeError, tomllib.TOMLDecodeError) as error:
        parser.exit(2, f"Release build failed: {error}\n")
    print(f"Archive: {archive}\nManifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
