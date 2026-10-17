from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .models import GeneratedFile, GeneratedFiles, PackagePlan
from .rendering import DocumentRenderer
from .report_codec import bounded_json
from .resolver_proxy import RestrictedIndexProxy

MAX_LOCK_BYTES = 384 * 1024
PLATFORMS = {
    "linux-x86_64": "x86_64-unknown-linux-gnu",
    "linux-aarch64": "aarch64-unknown-linux-gnu",
    "macos-arm64": "aarch64-apple-darwin",
    "macos-x86_64": "x86_64-apple-darwin",
    "windows-x86_64": "x86_64-pc-windows-msvc",
}


class ResolutionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResolutionTarget:
    python_version: str
    platform: str

    def __post_init__(self) -> None:
        if not isinstance(self.python_version, str) or not re.fullmatch(
            r"3\.(?:8|9|1[0-4])(?:\.[0-9]{1,3})?", self.python_version
        ):
            raise ResolutionError("TARGET_INVALID", "Choose a supported Python version (3.8–3.14)")
        if self.platform not in PLATFORMS:
            raise ResolutionError("TARGET_INVALID", "Choose a supported target platform")


@dataclass(frozen=True)
class DependencyLock:
    id: str
    analysis_id: str
    input_id: str
    target: ResolutionTarget
    requirements: str
    packages: tuple[str, ...]
    resolver: str = "uv 0.12.3"
    provenance: str = "new_resolution_not_execution_verified"
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> DependencyLock:
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Invalid dependency lock")
        if not isinstance(value["target"], dict) or set(value["target"]) != {
            "python_version",
            "platform",
        }:
            raise ValueError("Invalid resolution target")
        if any(
            not isinstance(value[key], str)
            for key in ("id", "analysis_id", "input_id", "requirements", "resolver", "provenance")
        ) or not re.fullmatch(r"[a-f0-9]{64}", value["id"]):
            raise ValueError("Invalid dependency lock fields")
        if (
            value["schema_version"] != 1
            or value["resolver"] != "uv 0.12.3"
            or value["provenance"] != "new_resolution_not_execution_verified"
        ):
            raise ValueError("Unsupported dependency lock")
        packages = _validate_output(value["requirements"])
        if not isinstance(value["packages"], (list, tuple)) or tuple(value["packages"]) != packages:
            raise ValueError("Invalid locked package list")
        lock = cls(**{**value, "target": ResolutionTarget(**value["target"]), "packages": packages})
        if lock.id != _lock_id(lock.input_id, lock.requirements):
            raise ValueError("Dependency lock checksum mismatch")
        return lock


def _validated_requirements(requirements: tuple[str, ...]) -> tuple[str, ...]:
    if len(requirements) > 500:
        raise ResolutionError(
            "REQUIREMENTS_LIMIT", "At most 500 direct requirements can be resolved"
        )
    normalized = []
    for text in requirements:
        if not isinstance(text, str) or len(text) > 2048 or any(c in text for c in "\r\n\x00"):
            raise ResolutionError("REQUIREMENT_UNSAFE", "Unsupported requirement")
        try:
            requirement = Requirement(text)
        except InvalidRequirement:
            raise ResolutionError(
                "REQUIREMENT_UNSAFE", "Only named PEP 508 requirements are supported"
            ) from None
        if requirement.url:
            raise ResolutionError(
                "REQUIREMENT_UNSAFE", "URL, Git and local dependencies need manual review"
            )
        normalized.append(str(requirement))
    return tuple(sorted(set(normalized)))


def resolution_input_id(plan: PackagePlan, target: ResolutionTarget) -> str:
    payload = [
        plan.analysis_id,
        plan.fingerprint,
        plan.python_version,
        _validated_requirements(plan.requirements),
        asdict(target),
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _lock_id(input_id: str, requirements: str) -> str:
    return hashlib.sha256((input_id + "\0" + requirements).encode()).hexdigest()


def _validate_output(text: str) -> tuple[str, ...]:
    if not isinstance(text, str) or len(text.encode()) > MAX_LOCK_BYTES:
        raise ValueError("Resolution exceeds its output limit")
    packages = []
    names = set()
    # Every record must be a named exact pin followed by one or more SHA-256 hashes.
    for record in text.replace("\\\n", " ").splitlines():
        record = record.strip()
        if not record or record.startswith("#"):
            continue
        parts = re.split(r"\s+--hash=", record)
        requirement = Requirement(parts[0].strip())
        specs = list(requirement.specifier)
        if (
            requirement.url
            or len(specs) != 1
            or specs[0].operator != "=="
            or "*" in specs[0].version
        ):
            raise ValueError("Resolver returned an unsafe or unpinned requirement")
        if len(parts) < 2 or any(
            not re.fullmatch(r"sha256:[a-f0-9]{64}", part.strip()) for part in parts[1:]
        ):
            raise ValueError("Resolver returned invalid hashes")
        name = canonicalize_name(requirement.name)
        if name in names:
            raise ValueError("Resolver returned duplicate package names")
        names.add(name)
        packages.append(str(requirement))
    if len(packages) > 2000 or len(packages) != len(set(packages)):
        raise ValueError("Invalid locked package count")
    return tuple(packages)


def _validate_target(plan: PackagePlan, target: ResolutionTarget) -> None:
    selected = Version(plan.python_version) if plan.python_version else None
    requested = Version(target.python_version)
    if selected is not None and (
        selected.release[:2] != requested.release[:2]
        or (
            len(selected.release) >= 3
            and len(requested.release) >= 3
            and selected.release[2] != requested.release[2]
        )
    ):
        raise ResolutionError(
            "TARGET_PYTHON_MISMATCH", "The resolution target must match the selected project Python"
        )
    if Version(plan.report.options.python_version).release[:2] != requested.release[:2]:
        raise ResolutionError(
            "TARGET_PROFILE_MISMATCH",
            "Reanalyze the stored project with options.python_version matching the target first",
        )
    if not plan.report.python_requires:
        return
    constraint = SpecifierSet(plan.report.python_requires)
    if len(requested.release) >= 3:
        allowed = constraint.contains(requested)
    else:
        # Test every boundary within this minor version, rather than pretending
        # that a minor-only request establishes an exact patch interpreter.
        patches = {0, 1}
        for specifier in constraint:
            try:
                bound = Version(specifier.version.removesuffix(".*"))
            except InvalidVersion:
                continue
            if bound.release[:2] == requested.release[:2]:
                patch = bound.release[2] if len(bound.release) >= 3 else 0
                patches.update({max(0, patch - 1), patch, patch + 1})
        outcomes = {
            constraint.contains(Version(f"{target.python_version}.{patch}")) for patch in patches
        }
        if len(outcomes) > 1:
            raise ResolutionError(
                "TARGET_PATCH_REQUIRED",
                "Project Python constraints vary within this minor version; select an exact patch",
            )
        allowed = outcomes.pop()
    if not allowed:
        raise ResolutionError(
            "TARGET_PYTHON_UNSUPPORTED", "The target does not satisfy the project's requires-python"
        )


def _marker_environment(target: ResolutionTarget) -> dict[str, str]:
    version = Version(target.python_version)
    machine = {
        "linux-x86_64": "x86_64",
        "linux-aarch64": "aarch64",
        "macos-arm64": "arm64",
        "macos-x86_64": "x86_64",
        "windows-x86_64": "AMD64",
    }[target.platform]
    system, platform, os_name = (
        ("Windows", "win32", "nt")
        if target.platform.startswith("windows")
        else ("Darwin", "darwin", "posix")
        if target.platform.startswith("macos")
        else ("Linux", "linux", "posix")
    )
    full_version = ".".join(str(part) for part in (*version.release, 0, 0)[:3])
    return {
        "implementation_name": "cpython",
        "implementation_version": full_version,
        "os_name": os_name,
        "platform_machine": machine,
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": system,
        "platform_version": "",
        "python_full_version": full_version,
        "python_version": ".".join(map(str, version.release[:2])),
        "sys_platform": platform,
        "extra": "",
    }


def _active(requirement: Requirement, target: ResolutionTarget) -> bool:
    if requirement.marker is None:
        return True
    marker = str(requirement.marker)
    if re.search(r"\b(?:platform_release|platform_version)\b", marker):
        raise ResolutionError(
            "TARGET_MARKER_UNSUPPORTED",
            "This target does not specify the OS release required by a marker",
        )
    if (
        re.search(r"\b(?:python_full_version|implementation_version)\b", marker)
        and len(Version(target.python_version).release) < 3
    ):
        raise ResolutionError(
            "TARGET_PATCH_REQUIRED", "A dependency marker requires an exact Python patch version"
        )
    return requirement.marker.evaluate(environment=_marker_environment(target))


def _validate_coverage(
    requirements: tuple[str, ...], packages: tuple[str, ...], target: ResolutionTarget
) -> None:
    pinned = {
        canonicalize_name(requirement.name): requirement
        for package in packages
        if _active(requirement := Requirement(package), target)
    }
    for raw in requirements:
        direct = Requirement(raw)
        if not _active(direct, target):
            continue
        resolved = pinned.get(canonicalize_name(direct.name))
        if resolved is None:
            raise ResolutionError(
                "RESOLUTION_INCOMPLETE", "Resolver output omitted an active direct dependency"
            )
        version = next(iter(resolved.specifier)).version
        if direct.specifier and not direct.specifier.contains(version, prereleases=True):
            raise ResolutionError(
                "RESOLUTION_CONSTRAINT_MISMATCH",
                "Resolver output does not satisfy a direct constraint",
            )


class VersionResolver:
    """Resolve sanitized names with wheel metadata only; uploaded files never reach uv."""

    def __init__(self, timeout: float = 45):
        if not 0 < timeout <= 60:
            raise ValueError("Invalid resolver deadline")
        self.timeout = timeout

    def resolve(self, plan: PackagePlan, target: ResolutionTarget) -> DependencyLock:
        if plan.state == "blocked":
            raise ResolutionError(
                "PLAN_BLOCKED", "Resolve blocking findings before locking dependencies"
            )
        _validate_target(plan, target)
        requirements = _validated_requirements(plan.requirements)
        for raw in requirements:
            _active(Requirement(raw), target)
        input_id = resolution_input_id(plan, target)
        if not requirements:
            return DependencyLock(
                _lock_id(input_id, ""), plan.analysis_id, input_id, target, "", ()
            )
        try:
            import uv

            executable = str(Path(uv.find_uv_bin()).resolve(strict=True))
        except (ImportError, OSError):
            raise ResolutionError(
                "RESOLVER_UNAVAILABLE", "Install the resolve extra to enable uv"
            ) from None
        with tempfile.TemporaryDirectory(prefix="nishtyak-resolve-") as directory:
            root = Path(directory)
            source, output, error_path = (
                root / "requirements.in",
                root / "requirements.lock.txt",
                root / "error.log",
            )
            source.write_text("\n".join(requirements) + "\n")
            command = [
                sys.executable,
                "-I",
                "-m",
                "nishtyak_core.resolver_worker",
                executable,
                str(source),
                str(output),
                target.python_version,
                PLATFORMS[target.platform],
            ]
            environment = {
                "PATH": str(Path(sys.executable).parent),
                "LANG": "C.UTF-8",
                "TMPDIR": str(root),
                "UV_CACHE_DIR": str(root / "cache"),
                "UV_PYTHON_DOWNLOADS": "never",
                "UV_HTTP_TIMEOUT": "10",
                "UV_HTTP_RETRIES": "0",
                "UV_CONCURRENT_DOWNLOADS": "2",
                "UV_CONCURRENT_BUILDS": "1",
                "RAYON_NUM_THREADS": "2",
            }
            with (
                RestrictedIndexProxy(connection_deadline=min(self.timeout, 45)) as proxy,
                error_path.open("wb") as errors,
            ):
                environment.update(
                    {
                        name: proxy.url
                        for name in (
                            "HTTP_PROXY",
                            "HTTPS_PROXY",
                            "ALL_PROXY",
                            "http_proxy",
                            "https_proxy",
                            "all_proxy",
                        )
                    }
                )
                environment.update({"NO_PROXY": "", "no_proxy": ""})
                process = subprocess.Popen(
                    command,
                    cwd=root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=errors,
                    start_new_session=True,
                )
                try:
                    process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    self._kill(process)
                    raise ResolutionError(
                        "RESOLUTION_TIMEOUT", "Version resolution exceeded its deadline"
                    ) from None
                finally:
                    if process.poll() is None:
                        self._kill(process)
            if process.returncode:
                diagnostic = error_path.read_bytes()[:MAX_LOCK_BYTES].lower()
                code = "RESOLUTION_FAILED"
                if b"no solution found" in diagnostic or b"unsatisfiable" in diagnostic:
                    code = "RESOLUTION_CONFLICT"
                elif b"failed to fetch" in diagnostic or b"failed to download" in diagnostic:
                    code = "INDEX_UNAVAILABLE"
                raise ResolutionError(
                    code,
                    "No wheel-only set was resolved for this target; "
                    "check constraints and wheel availability",
                )
            try:
                with output.open("rb") as stream:
                    raw = stream.read(MAX_LOCK_BYTES + 1)
                text = raw.decode("utf-8")
                packages = _validate_output(text)
            except (OSError, ValueError):
                raise ResolutionError(
                    "RESOLUTION_INVALID", "Resolver output is invalid or exceeds its limit"
                ) from None
            _validate_coverage(requirements, packages, target)
        return DependencyLock(
            _lock_id(input_id, text), plan.analysis_id, input_id, target, text, packages
        )

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)


class LockExporter:
    def attach(
        self, generated: GeneratedFiles, plan: PackagePlan, lock: DependencyLock
    ) -> GeneratedFiles:
        if (
            resolution_input_id(plan, lock.target) != lock.input_id
            or lock.analysis_id != plan.analysis_id
        ):
            raise ResolutionError(
                "LOCK_STALE", "The dependency lock belongs to different requirements or analysis"
            )
        _validate_target(plan, lock.target)
        _validate_coverage(_validated_requirements(plan.requirements), lock.packages, lock.target)
        files = []
        for file in generated.files:
            if file.path == "README.md":
                text = DocumentRenderer().readme(plan, target=asdict(lock.target), locked=True)
                files.append(GeneratedFile(file.path, text.encode()))
            elif file.path == "Dockerfile":
                platform = {"linux-x86_64": "linux/amd64", "linux-aarch64": "linux/arm64"}.get(
                    lock.target.platform
                )
                if platform is None:
                    raise ResolutionError(
                        "LOCK_DOCKER_UNSUPPORTED",
                        "The generated Linux image needs a Linux dependency lock",
                    )
                text = file.content.decode("utf-8")
                text = re.sub(r"(?m)^FROM python:", f"FROM --platform={platform} python:", text)
                text = text.replace(
                    "COPY requirements.txt ./requirements.txt",
                    "COPY requirements.lock.txt ./requirements.lock.txt",
                ).replace(
                    "RUN python -m pip install --no-cache-dir -r requirements.txt",
                    "RUN python -m pip install --no-cache-dir --only-binary=:all: "
                    "--require-hashes -r requirements.lock.txt",
                )
                files.append(GeneratedFile(file.path, text.encode()))
            else:
                files.append(file)
        return GeneratedFiles(
            tuple(files)
            + (
                GeneratedFile("requirements.lock.txt", lock.requirements.encode()),
                GeneratedFile(
                    "dependency-lock.json", bounded_json(lock.to_dict(), MAX_LOCK_BYTES * 2)
                ),
            )
        )
