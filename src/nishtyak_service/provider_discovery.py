from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from nishtyak_core.locking import ResolutionError, ResolutionTarget, _active
from nishtyak_core.models import DeclarationDecision, PackagePlan, stable_id
from nishtyak_core.providers import (
    ProviderEvidence,
    ProviderLookupError,
    PublishedDistributionInspector,
)
from nishtyak_core.python_targets import select_python_target
from nishtyak_core.runtime_dependencies import RuntimeDependencySelector

from .automatic_resolution import AutomaticDependencyResolution
from .storage import JobRecord, SQLiteJobRepository


class AutomaticProviderDiscovery:
    """Resolve import providers from published evidence, independently of model suggestions."""

    budget_seconds = 8
    max_distributions = 12
    policy = "source_description_and_entrypoint"

    def __init__(self, repository: SQLiteJobRepository, inspector=None):
        self.repository = repository
        self.inspector = inspector or PublishedDistributionInspector()

    def ensure(self, record: JobRecord, plan: PackagePlan) -> None:
        if not self.repository.settings.automatic_resolution:
            return
        modules = self._unresolved(plan)
        if not modules:
            return
        declared, optional = self._declared(plan)
        context_id = stable_id(plan.analysis_id, declared, optional, modules)
        result = self.repository.read_review(
            record, plan.analysis_id, reuse_policy=self.policy
        ) or {
            "analysis_id": plan.analysis_id,
            "policy": self.policy,
            "provider": "deepseek",
            "status": "skipped",
            "reason": "static_sufficient",
            "paragraphs": [],
            "packages": {},
        }
        previous = result.get("provider_discovery", {})
        if previous.get("context_id") == context_id:
            return
        wanted = set(declared) | set(optional) | {canonicalize_name(module) for module in modules}
        inventory = {
            name: entry for name, entry in previous.get("inventory", {}).items() if name in wanted
        }
        scope = {item.module for item in plan.report.dependencies if item.module.isidentifier()}
        started = time.monotonic()
        deadline = started + self.budget_seconds
        attempted: list[str] = []
        self._inspect(declared, inventory, attempted, deadline, scope)
        mappings = {}
        unresolved = {}
        identity = []
        for module in modules:
            providers = self._providers(module, declared, inventory)
            if len(providers) == 1:
                mappings[module] = providers[0]
            elif len(providers) > 1:
                unresolved[module] = {
                    "reason": "multiple_declared_providers",
                    "providers": providers,
                }
            elif any(inventory.get(name, {}).get("status") != "ready" for name in declared):
                # A failed or bounded lookup does not establish absence of a provider.
                unresolved[module] = {"reason": "declared_provider_evidence_incomplete"}
        remaining = set(modules) - mappings.keys() - unresolved.keys()
        if remaining:
            self._inspect(optional, inventory, attempted, deadline, scope)
        for module in sorted(remaining):
            providers = self._providers(module, optional, inventory)
            if len(providers) == 1:
                mappings[module] = providers[0]
            elif len(providers) > 1:
                unresolved[module] = {
                    "reason": "multiple_optional_providers",
                    "providers": providers,
                }
            elif any(inventory.get(name, {}).get("status") != "ready" for name in optional):
                unresolved[module] = {"reason": "optional_provider_evidence_incomplete"}
            elif PublishedDistributionInspector.name_pattern.fullmatch(module):
                identity.append(canonicalize_name(module))
            else:
                unresolved[module] = {"reason": "provider_name_unavailable"}
        self._inspect(identity, inventory, attempted, deadline, scope)
        for module in modules:
            if module in mappings or module in unresolved:
                continue
            name = canonicalize_name(module)
            if self._providers(module, (name,), inventory):
                mappings[module] = name
            else:
                unresolved[module] = {
                    "reason": inventory.get(name, {}).get("reason", "provider_evidence_incomplete")
                }
        packages = result.setdefault("packages", {})
        for module in modules:
            if packages.get(module, {}).get("origin") == "provider_discovery":
                del packages[module]
        for module, name in mappings.items():
            proof = inventory[name]["evidence"]
            # Keep independently verified model choices; author choices are never written here.
            existing = packages.get(module, {})
            if existing and existing.get("origin") != "provider_discovery":
                continue
            packages[module] = {
                "package": proof["package"],
                "origin": "provider_discovery",
                "verification": {
                    **{
                        key: value
                        for key, value in proof.items()
                        if key not in {"modules", "namespaces"}
                    },
                    "module": module,
                    "execution_verified": False,
                },
                "evidence_ids": list(modules[module][:20]),
            }
        # Evidence is bounded and cached separately from the author's decisions and AI budget.
        result["provider_discovery"] = {
            "context_id": context_id,
            "status": "ready" if not unresolved else "incomplete",
            "inventory": inventory,
            "unresolved": unresolved,
            "attempted": attempted,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
        self.repository.save_review(record, plan.analysis_id, result)

    def _inspect(self, names, inventory, attempted, deadline, scope):
        pending = [name for name in dict.fromkeys(names) if name not in inventory]
        pending = pending[: max(0, self.max_distributions - len(attempted))]
        for offset in range(0, len(pending), 2):
            if deadline - time.monotonic() <= 0.1:
                break
            batch = pending[offset : offset + 2]
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="provider-evidence") as pool:
                futures = {name: pool.submit(self._lookup, name, deadline, scope) for name in batch}
                attempted.extend(batch)
                for name, future in futures.items():
                    inventory[name] = future.result()

    def _lookup(self, name, deadline, scope):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0.1:
                raise ProviderLookupError("provider_lookup_timeout")
            requirement = Requirement(name)
            evidence = self.inspector.inspect(
                requirement.name, specifier=str(requirement.specifier), timeout=min(5, remaining)
            )
            proof = asdict(evidence)
            proof["modules"] = [name for name in evidence.modules if name.split(".", 1)[0] in scope]
            proof["namespaces"] = [
                name for name in evidence.namespaces if name.split(".", 1)[0] in scope
            ]
            if len(json.dumps(proof).encode()) > 8192:
                raise ProviderLookupError("provider_inventory_limit")
            return {"status": "ready", "evidence": proof}
        except ProviderLookupError as error:
            return {"status": "unknown", "reason": str(error)}

    @staticmethod
    def _providers(module, names, inventory):
        providers = []
        for name in names:
            entry = inventory.get(name, {})
            if entry.get("status") != "ready":
                continue
            if ProviderEvidence(**entry["evidence"]).provides(module):
                providers.append(name)
        return providers

    @staticmethod
    def _unresolved(plan):
        runtime = RuntimeDependencySelector().select(
            plan.report, included_files=plan.files, run_spec=plan.run_spec
        )
        decisions = plan.decisions
        return {
            candidate.module: candidate.evidence_ids
            for candidate in runtime.dependencies
            if candidate.category == "unresolved"
            and candidate.module.isidentifier()
            and not (
                decisions
                and (
                    candidate.module in decisions.packages
                    or candidate.module in decisions.excluded_modules
                )
            )
        }

    @staticmethod
    def _declared(plan):
        decisions = plan.decisions
        runtime = RuntimeDependencySelector().select(
            plan.report,
            included_files=plan.files,
            run_spec=plan.run_spec,
            package_mappings={
                **plan.automatic_packages,
                **(decisions.packages if decisions else {}),
            },
        )
        selected = set(runtime.automatic_extras) | set(
            decisions.selected_extras if decisions else ()
        )
        target_version = plan.python_version or select_python_target(
            plan.report.python_requires, plan.report.options.python_version
        )
        target_platform = ((plan.automatic_resolution or {}).get("target") or {}).get("platform")
        target_platform = (
            target_platform
            or AutomaticDependencyResolution._author_platform(plan.report.author_platform)
            or AutomaticDependencyResolution.default_platform
        )
        target = ResolutionTarget(target_version, target_platform) if target_version else None
        declarations: dict[str, list[str]] = {}
        optional: dict[str, list[str]] = {}

        def collect(requirement, group=None):
            if requirement.url:
                return
            if target is not None:
                try:
                    if not _active(requirement, target):
                        return
                except ResolutionError:
                    # An undecidable marker is not evidence that this provider is absent.
                    pass
            name = canonicalize_name(requirement.name)
            destination = optional if group is not None and group not in selected else declarations
            effective = (
                [
                    Requirement(raw)
                    for raw in plan.requirements
                    if canonicalize_name(Requirement(raw).name) == name
                    and str(Requirement(raw).marker or "") == str(requirement.marker or "")
                ]
                if destination is declarations
                else []
            )
            destination.setdefault(name, []).extend(
                str(item.specifier) for item in effective or [requirement]
            )

        for declaration in plan.report.declarations:
            action = (
                decisions.declaration_actions.get(declaration.id, DeclarationDecision())
                if decisions
                else DeclarationDecision()
            )
            if action.action == "exclude":
                continue
            value = action.requirement if action.action == "replace" else declaration.requirement
            requirement = Requirement(value)
            collect(requirement, declaration.group)
        if not plan.report.declarations:
            for value in plan.report.declared_requirements:
                requirement = Requirement(value)
                collect(requirement)

        def names(values):
            return tuple(
                sorted(
                    name + str(SpecifierSet(",".join(value for value in constraints if value)))
                    for name, constraints in values.items()
                )
            )

        return names(declarations), names(optional)
