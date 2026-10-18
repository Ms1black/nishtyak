from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

from packaging.requirements import InvalidRequirement, Requirement

from nishtyak_core import package_safety
from nishtyak_core.locking import DependencyLock, ResolutionTarget, _active
from nishtyak_core.models import Finding, PackagePlan

from .errors import ServiceError


class DependencySafety:
    finding_codes = frozenset(
        {"PACKAGE_MALICIOUS", "PACKAGE_SAFETY_UNAVAILABLE", "PACKAGE_VULNERABILITY_KNOWN"}
    )

    def inspect(self, requirements, target: ResolutionTarget | None = None) -> dict:
        packages = []
        try:
            for raw in requirements:
                requirement = Requirement(raw)
                if requirement.url:
                    return self.unavailable("package_source_unsupported")
                if target is not None and not _active(requirement, target):
                    continue
                specs = list(requirement.specifier)
                version = (
                    specs[0].version
                    if len(specs) == 1 and specs[0].operator == "==" and "*" not in specs[0].version
                    else None
                )
                packages.append(package_safety.PackageVersion(requirement.name, version))
            return package_safety.get_package_safety_client().check(packages).to_dict()
        except (InvalidRequirement, ValueError):
            return self.unavailable("package_identity_invalid")

    def inspect_lock(self, lock: DependencyLock) -> dict:
        return self.inspect(lock.packages, lock.target)

    @staticmethod
    def compact(report: dict) -> dict:
        if "checked_package_count" in report:
            return report
        packages = report.get("packages", [])
        alerts = [
            item for item in packages if item.get("status") != "checked" or item.get("advisory_ids")
        ]
        alerts.sort(key=lambda item: not bool(item.get("malicious_ids")))
        result = {
            **report,
            "checked_package_count": len(packages),
            "omitted_package_count": len(packages) - min(len(alerts), 100),
            "packages": [
                {
                    **item,
                    "advisory_count": len(item.get("advisory_ids", [])),
                    "malicious_count": len(item.get("malicious_ids", [])),
                    "advisory_ids": item.get("advisory_ids", [])[:10],
                    "malicious_ids": item.get("malicious_ids", [])[:10],
                }
                for item in alerts[:100]
            ],
        }
        while result["packages"] and len(json.dumps(result).encode()) > 32 * 1024:
            result["packages"].pop()
            result["omitted_package_count"] += 1
        return result

    @staticmethod
    def unavailable(reason: str) -> dict:
        return {
            "status": "unavailable",
            "source": "https://api.osv.dev/v1/querybatch",
            "checked_at": datetime.now(UTC).isoformat(),
            "packages": [],
            "reason": reason,
        }

    @staticmethod
    def status(report: dict) -> str:
        items = report.get("packages", [])
        if report.get("status") == "blocked" or any(
            item.get("status") == "blocked" or item.get("malicious_ids") for item in items
        ):
            return "blocked"
        if report.get("status") != "checked" or any(
            item.get("status") != "checked" for item in items
        ):
            return "unavailable"
        return "checked"

    @classmethod
    def require_checked(cls, report: dict) -> None:
        status = cls.status(report)
        if status == "blocked":
            raise ServiceError(
                409,
                "PACKAGE_MALICIOUS",
                "В зависимостях найден известный вредоносный релиз. Исправьте зависимости.",
            )
        if status != "checked":
            raise ServiceError(
                503,
                "PACKAGE_SAFETY_UNAVAILABLE",
                "Проверка зависимостей по OSV не завершена. Повторите попытку позже.",
            )

    def enrich(self, plan: PackagePlan, report: dict | None) -> PackagePlan:
        if report is None:
            return plan
        report = self.compact(report)
        findings = [item for item in plan.findings if item.code not in self.finding_codes]
        status = self.status(report)
        for item in report.get("packages", []):
            if item.get("malicious_ids"):
                findings.append(
                    Finding(
                        "PACKAGE_MALICIOUS",
                        f"Пакет {item['name']}"
                        + (f"=={item['version']}" if item.get("version") else "")
                        + " отмечен как вредоносный в OSV: "
                        + ", ".join(item["malicious_ids"][:10])
                        + ". Удалите зависимость или выберите проверенный релиз.",
                        severity="error",
                    )
                )
            elif item.get("advisory_ids") and item.get("version"):
                findings.append(
                    Finding(
                        "PACKAGE_VULNERABILITY_KNOWN",
                        f"Для {item['name']}=={item['version']} есть известные уязвимости: "
                        + ", ".join(item["advisory_ids"][:10])
                        + ". Проверьте возможность обновления.",
                    )
                )
        if status != "checked" and not any(item.code == "PACKAGE_MALICIOUS" for item in findings):
            findings.append(
                Finding(
                    "PACKAGE_SAFETY_UNAVAILABLE",
                    "Проверка зависимостей по базе OSV не завершена. Повторите анализ позже; "
                    "выдача архива с инструкциями установки пока заблокирована.",
                    severity="error",
                )
            )
        metadata = {
            **(plan.automatic_resolution or {}),
            "package_safety": report,
            "safety_source_state": plan.state,
        }
        return replace(
            plan,
            findings=tuple(findings),
            state="blocked" if status != "checked" else plan.state,
            automatic_resolution=metadata,
        )
