from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .models import GeneratedFiles, PackagePlan, ProjectSnapshot
from .snapshot import SnapshotBuilder


@dataclass(frozen=True)
class PackageArtifact:
    path: str
    sha256: str
    size: int
    state: str


class BundleWriter:
    def write(
        self,
        snapshot: ProjectSnapshot,
        plan: PackagePlan,
        generated: GeneratedFiles,
        destination: str | Path,
    ) -> PackageArtifact:
        SnapshotBuilder().validate(snapshot)
        if plan.fingerprint != snapshot.fingerprint:
            raise ValueError("Package plan belongs to a different project snapshot")
        if plan.state == "blocked":
            raise ValueError("Package is blocked; resolve its findings before exporting")
        files = {file.path: file for file in snapshot.files}
        entries: dict[str, bytes] = {}
        for info in plan.files:
            path = SnapshotBuilder.normalize_path(info.path)
            file = files.get(path)
            if file is None or file.info != info:
                raise ValueError("An exported file does not match the package plan")
            entries[f"project/{path}"] = file.content
        for file in generated.files:
            path = SnapshotBuilder.normalize_path(file.path)
            if path == "project" or path.startswith("project/") or path in entries:
                raise ValueError("Generated file conflicts with a project file")
            entries[path] = file.content
        output = Path(destination).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
                temporary = Path(stream.name)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path, content in sorted(entries.items()):
                    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    archive.writestr(info, content)
            content = temporary.read_bytes()
            os.replace(temporary, output)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return PackageArtifact(
            str(output), hashlib.sha256(content).hexdigest(), len(content), plan.state
        )
