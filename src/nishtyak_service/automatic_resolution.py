from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, replace

from nishtyak_core.decisions import DecisionsCodec
from nishtyak_core.locking import (
    DependencyLock,
    ResolutionError,
    ResolutionTarget,
    VersionResolver,
    resolution_input_id,
)
from nishtyak_core.models import PackagePlan, UserDecisions, stable_id
from nishtyak_core.python_targets import select_python_target

from .errors import ServiceError
from .storage import JobRecord, SQLiteJobRepository


class AutomaticDependencyResolution:
    """Resolve one new wheel-only target in the background, retaining author decisions."""

    budget_seconds = 20
    default_platform = "linux-x86_64"
    provenance = "newly_resolved_not_author_environment"
    _incomplete = frozenset(
        {
            "DEPENDENCY_UNRESOLVED",
            "DECLARATION_IMPORT_UNRESOLVED",
            "DIRECT_REFERENCE_UNVERIFIED",
            "INVALID_DECLARED_REQUIREMENT",
            "DEPENDENCY_CONFLICT",
            "AUTHOR_ENVIRONMENT_CONFLICT",
            "AUTHOR_MAPPING_CONFLICT",
            "AUTHOR_MAPPING_AMBIGUOUS",
            "AUTHOR_VERSION_AMBIGUOUS",
            "AUTHOR_PYTHON_CONFLICT",
            "PYTHON_VERSION_CONFLICT",
            "PYTHON_PROFILE_MISMATCH",
            "PROJECT_ROOT_AMBIGUOUS",
        }
    )
    _unsupported = frozenset(
        {
            "REQUIREMENT_URL_UNSUPPORTED",
            "DYNAMIC_DEPENDENCIES",
            "MANIFEST_PARSE_ERROR",
            "MANIFEST_INCLUDE_MISSING",
            "MANIFEST_INCLUDE_LIMIT",
            "MANIFEST_INCLUDE_UNSUPPORTED",
            "MANIFEST_TOO_LARGE",
            "MANIFEST_UNSUPPORTED",
            "REQUIREMENT_UNSUPPORTED",
            "REQUIREMENT_INVALID",
            "TOOL_METADATA_UNSUPPORTED",
            "PYTHON_REQUIREMENT_INVALID",
            "SYNTAX_ERROR",
            "SOURCE_PARSE_ERROR",
            "SOURCE_TOO_LARGE",
            "FILE_LIMIT_EXCEEDED",
            "PYTHON_PROFILE_UNSUPPORTED",
        }
    )

    def __init__(self, repository: SQLiteJobRepository):
        self.repository = repository

    def capabilities(self) -> dict:
        return {
            "enabled": self.repository.settings.automatic_resolution,
            "budget_seconds": self.budget_seconds,
            "default_platform": self.default_platform,
        }

    def status(self, record: JobRecord) -> dict:
        return self.repository.read_resolution(record) or {"status": "disabled"}

    def enrich(self, plan: PackagePlan, record: JobRecord) -> PackagePlan:
        cached = self.repository.read_resolution(record)
        if not self.repository.settings.automatic_resolution or cached is None:
            return replace(plan, automatic_resolution={"status": "disabled"})
        if plan.python_version is None:
            selected = select_python_target(
                plan.report.python_requires, plan.report.options.python_version
            )
            if selected is not None:
                plan = replace(plan, python_version=selected, python_version_origin="inferred")
        try:
            target, metadata = self._target(plan, cached)
            input_id = resolution_input_id(plan, target)
            if cached.get("input_id") not in {None, input_id} or cached.get("context_id") not in {
                None,
                self._context_id(plan),
            }:
                cached = {
                    "status": "needs_input",
                    "reason": "DECISIONS_NOT_SAVED",
                    "lock_id": None,
                    "packages": [],
                }
            metadata.update(cached, target=asdict(target))
        except ResolutionError as error:
            metadata = {"status": "needs_input", "reason": error.code}
        return replace(plan, automatic_resolution=metadata)

    def queue_if_changed(self, record: JobRecord, plan: PackagePlan) -> None:
        if not self.repository.settings.automatic_resolution:
            return
        cached = self.repository.read_resolution(record)
        if cached is None:
            return
        try:
            target, _ = self._target(plan, cached)
            identifier = resolution_input_id(plan, target)
        except ResolutionError:
            identifier = None
        if cached.get("input_id") != identifier or cached.get("context_id") != self._context_id(
            plan
        ):
            self.repository.save_resolution(
                record,
                {
                    **cached,
                    "status": "pending",
                    "input_id": identifier,
                    "context_id": self._context_id(plan),
                    "lock_id": None,
                    "packages": [],
                },
            )

    def process_pending(self, prepare: Callable) -> None:
        if not self.repository.settings.automatic_resolution:
            return
        for record in self.repository.pending_resolutions():
            try:
                with self.repository.operation(record.owner_id, record.project_id) as current:
                    state = self.repository.read_resolution(current)
                    if state is None or state.get("status") not in {"pending", "running"}:
                        continue
                    report = self.repository.read_report(current)
                    document = self.repository.read_decisions(current.owner_id, current.project_id)
                    decisions = (
                        DecisionsCodec().decode(document["decisions"])
                        if document["decisions"]
                        else UserDecisions(report.analysis_id)
                    )
                    self._resolve(current, prepare(report, decisions, current))
            except ServiceError:
                continue
            return

    def ensure(self, record: JobRecord, plan: PackagePlan) -> None:
        cached = self.repository.read_resolution(record)
        if cached is None:
            return
        try:
            target, _ = self._target(plan, cached)
            identifier = resolution_input_id(plan, target)
        except ResolutionError:
            identifier = None
        if (
            cached.get("status") in {"ready", "needs_input", "unavailable"}
            and cached.get("input_id") == identifier
            and cached.get("context_id") == self._context_id(plan)
        ):
            return
        self._resolve(record, plan)

    def _resolve(self, record: JobRecord, plan: PackagePlan) -> None:
        started = time.monotonic()
        result = {
            "status": "unavailable",
            "provenance": self.provenance,
            "context_id": self._context_id(plan),
        }
        try:
            target, metadata = self._target(plan, self.repository.read_resolution(record))
            result.update(
                metadata,
                target=asdict(target),
                input_id=resolution_input_id(plan, target),
                context_id=self._context_id(plan),
            )
            unresolved = next(
                (finding.code for finding in plan.findings if finding.code in self._incomplete),
                None,
            )
            if plan.state == "blocked" or unresolved:
                result.update(status="needs_input", reason=unresolved or "PLAN_BLOCKED")
                return
            unsupported = next(
                (finding.code for finding in plan.findings if finding.code in self._unsupported),
                None,
            )
            if unsupported:
                result.update(status="unavailable", reason=unsupported)
                return
            self.repository.consume_rate(record.owner_id, "automatic-resolve", 5)
            self.repository.consume_rate("_service", "automatic-resolve-global", 20)
            self.repository.save_resolution(record, {**result, "status": "running"})
            lock = VersionResolver(timeout=self.budget_seconds).resolve(plan, target)
            self.repository.save_lock(record.owner_id, record.project_id, lock)
            result.update(
                status="ready", reason=None, lock_id=lock.id, packages=list(lock.packages)
            )
        except ResolutionError as error:
            result.update(
                status="needs_input" if error.code.startswith("TARGET_") else "unavailable",
                reason=error.code,
            )
        except ServiceError as error:
            result.update(
                status="unavailable",
                reason="RESOLUTION_QUOTA" if error.status == 429 else "RESOLUTION_UNAVAILABLE",
            )
        except Exception:
            # A resolver or index failure leaves a usable, honestly labelled dependency draft.
            result.update(status="unavailable", reason="RESOLUTION_UNAVAILABLE")
        finally:
            result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            self.repository.save_resolution(record, result)

    def record_lock(self, record: JobRecord, plan: PackagePlan, lock: DependencyLock) -> None:
        if self.repository.read_resolution(record) is None:
            return
        _, metadata = self._target(plan)
        incomplete = next(
            (finding.code for finding in plan.findings if finding.code in self._incomplete), None
        )
        unsupported = next(
            (finding.code for finding in plan.findings if finding.code in self._unsupported), None
        )
        self.repository.save_resolution(
            record,
            {
                **metadata,
                "status": "needs_input"
                if incomplete
                else "unavailable"
                if unsupported
                else "ready",
                "reason": incomplete or unsupported,
                "target": asdict(lock.target),
                "target_source": "author",
                "platform_source": "author",
                "lock_id": lock.id,
                "packages": list(lock.packages),
                "input_id": lock.input_id,
                "context_id": self._context_id(plan),
                "elapsed_ms": 0,
            },
        )

    def valid_lock(self, record: JobRecord, plan: PackagePlan) -> DependencyLock | None:
        if plan.state == "blocked" or any(
            finding.code in self._incomplete | self._unsupported for finding in plan.findings
        ):
            return None
        try:
            lock = self.repository.read_lock(record.owner_id, record.project_id)
            if resolution_input_id(plan, lock.target) == lock.input_id:
                return lock
        except (ServiceError, ResolutionError):
            pass
        return None

    def _target(
        self, plan: PackagePlan, cached: dict | None = None
    ) -> tuple[ResolutionTarget, dict]:
        if plan.python_version is None:
            raise ResolutionError(
                "TARGET_PYTHON_UNSUPPORTED", "No compatible supported Python target"
            )
        report = plan.report
        platform = report.options.target_platform or self._author_platform(report.author_platform)
        manual = bool(report.options.target_platform) or (
            cached is not None and cached.get("target_source") == "author"
        )
        if manual and not report.options.target_platform:
            platform = cached.get("target", {}).get("platform")
        target = ResolutionTarget(plan.python_version, platform or self.default_platform)
        return target, {
            "python_source": "author"
            if plan.python_version_origin == "author"
            else "author_environment"
            if report.author_python_version
            else "declared"
            if report.python_requires
            else "analysis_default",
            "platform_source": "author"
            if manual
            else "author_environment"
            if platform
            else "service_default",
            "target_source": "author" if manual else "automatic",
            "provenance": self.provenance,
        }

    def _context_id(self, plan: PackagePlan) -> str:
        relevant = self._incomplete | self._unsupported | {"SECRET_DETECTED"}
        return stable_id(
            plan.state == "blocked",
            sorted(
                finding.id
                for finding in plan.findings
                if finding.code in relevant or finding.severity == "error"
            ),
        )

    @staticmethod
    def _author_platform(platform: str | None) -> str | None:
        value = (platform or "").lower()
        if value in {
            "linux-x86_64",
            "linux-aarch64",
            "macos-arm64",
            "macos-x86_64",
            "windows-x86_64",
        }:
            return value
        x86 = any(machine in value for machine in ("x86_64", "amd64"))
        arm = any(machine in value for machine in ("aarch64", "arm64"))
        if "linux" in value:
            return "linux-x86_64" if x86 else "linux-aarch64" if arm else None
        if "macos" in value or "darwin" in value:
            return "macos-x86_64" if x86 else "macos-arm64" if arm else None
        if "windows" in value and x86:
            return "windows-x86_64"
        return None
