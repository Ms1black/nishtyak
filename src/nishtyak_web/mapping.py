from __future__ import annotations

from datetime import UTC, datetime

from nishtyak_service.dto import AnalysisOptionsDTO, DecisionsDTO

DECISION_FIELDS = {
    "analysisId": "analysis_id",
    "packages": "packages",
    "versions": "versions",
    "excludedModules": "excluded_modules",
    "excludedFiles": "excluded_files",
    "runSpec": "run_spec",
    "pythonVersion": "python_version",
    "description": "description",
    "inputDescription": "input_description",
    "expectedOutput": "expected_output",
    "dataConfirmed": "data_confirmed",
    "selectedExtras": "selected_extras",
    "declarationActions": "declaration_actions",
}


def timestamp(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, UTC).isoformat() if value is not None else None


def decisions_to_core(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - DECISION_FIELDS.keys():
        raise ValueError("Invalid browser decisions")
    result = {DECISION_FIELDS[key]: item for key, item in value.items()}
    if result.get("run_spec") is not None:
        run = result["run_spec"]
        if not isinstance(run, dict) or set(run) - {"target", "kind", "args", "workingDirectory"}:
            raise ValueError("Invalid browser run specification")
        result["run_spec"] = {
            "working_directory" if key == "workingDirectory" else key: item
            for key, item in run.items()
        }
    return DecisionsDTO.model_validate(result).model_dump()


def run_to_web(value: dict | None) -> dict | None:
    if value is None:
        return None
    return {
        "kind": value["kind"],
        "target": value["target"],
        "args": value.get("args", []),
        "workingDirectory": value.get("working_directory", "."),
    }


def decisions_to_web(value: dict | None) -> dict | None:
    if value is None:
        return None
    result = {key: value.get(core) for key, core in DECISION_FIELDS.items()}
    result["runSpec"] = run_to_web(value.get("run_spec"))
    result["selectedExtras"] = value.get("selected_extras", [])
    result["declarationActions"] = value.get("declaration_actions", {})
    return result


def document_to_web(value: dict) -> dict:
    return {
        "revision": value["revision"],
        "decisions": decisions_to_web(value.get("decisions")),
        "updatedAt": timestamp(value.get("updated_at")),
    }


def options_to_core(value: dict) -> dict:
    fields = {
        "pythonVersion": "python_version",
        "pythonVersionAuto": "python_version_auto",
        "sourceRoots": "source_roots",
        "projectRoot": "project_root",
    }
    if not isinstance(value, dict) or set(value) - fields.keys():
        raise ValueError("Invalid browser analysis options")
    return AnalysisOptionsDTO.model_validate(
        {fields[key]: item for key, item in value.items()}
    ).model_dump()


def findings_to_web(values: list[dict], report: dict | None = None) -> list[dict]:
    modules_by_evidence: dict[str, set[str]] = {}
    for dependency in (report or {}).get("dependencies", []):
        for evidence_id in (dependency["id"], *dependency.get("evidence_ids", [])):
            modules_by_evidence.setdefault(evidence_id, set()).add(dependency["module"])

    findings = []
    for item in values:
        finding = {
            key: item.get(key) for key in ("id", "code", "severity", "message", "path", "line")
        }
        modules = set()
        for evidence_id in item.get("evidence_ids", []):
            modules.update(modules_by_evidence.get(evidence_id, ()))
        if len(modules) == 1:
            finding["module"] = next(iter(modules))
        findings.append(finding)
    return findings


def plan_to_web(value: dict) -> dict:
    return {
        "state": value["state"],
        "requirements": value["requirements"],
        "findings": findings_to_web(value["findings"], value.get("report")),
        "runSpec": run_to_web(value.get("run_spec")),
        "runSpecOrigin": value.get("run_spec_origin", "unknown"),
        "pythonVersion": value.get("python_version"),
        "pythonVersionOrigin": value.get("python_version_origin", "unknown"),
        "files": [item["path"] for item in value.get("files", [])],
        "decisions": decisions_to_web(value.get("decisions")),
        "description": value.get("description", ""),
        "descriptionOrigin": value.get("description_origin", "unknown"),
        "automaticReview": review_to_web(value.get("automatic_review")),
        "automaticResolution": resolution_to_web(value.get("automatic_resolution")),
    }


def resolution_to_web(value: dict | None) -> dict | None:
    if value is None:
        return None
    target = value.get("target")
    return {
        "status": value["status"],
        "reason": value.get("reason"),
        "target": {
            "pythonVersion": target["python_version"],
            "platform": target["platform"],
        }
        if target is not None
        else None,
        "lockId": value.get("lock_id"),
        "pythonSource": value.get("python_source"),
        "platformSource": value.get("platform_source"),
        "targetSource": value.get("target_source"),
        "packages": value.get("packages", []),
        "provenance": value.get("provenance"),
        "elapsedMs": value.get("elapsed_ms", 0),
    }


def review_to_web(value: dict | None) -> dict | None:
    if value is None:
        return None
    result = {
        "status": value["status"],
        "provider": value["provider"],
        "applied": value.get("applied", False),
        "reason": value.get("reason"),
        "elapsedMs": value.get("elapsed_ms", 0),
    }
    entrypoint = value.get("entrypoint")
    if entrypoint is not None:
        result["entrypoint"] = {
            "applied": entrypoint.get("applied", False),
            "candidateId": entrypoint.get("candidate_id"),
            "reason": entrypoint.get("reason"),
            "evidenceIds": entrypoint.get("evidence_ids", []),
            "explanation": entrypoint.get("explanation"),
        }
    return result


def analysis_status(report: dict | None, status: str) -> str:
    if status in {"queued", "running"}:
        return "analyzing"
    if status != "succeeded" or report is None:
        return "failed"
    if report.get("status") != "complete" or any(
        item["category"] == "unresolved" for item in report.get("dependencies", [])
    ):
        return "partial"
    return "full"


def analysis_to_web(project_id: str, job: dict, preparation: dict | None = None) -> dict:
    report = job.get("analysis") or {}
    imports = {item["id"]: item for item in report.get("imports", [])}
    declarations = {item["id"]: item for item in report.get("declarations", [])}
    secret_paths = {
        item["path"] for item in report.get("findings", []) if item["code"] == "SECRET_DETECTED"
    }
    dependencies = []
    for dependency in report.get("dependencies", []):
        evidence = dependency.get("evidence_ids", [])
        declaration = next((declarations[e] for e in evidence if e in declarations), None)
        source = dependency.get("status", "inferred")
        if source not in {"declared", "inferred", "unresolved", "author_environment"}:
            source = "unresolved" if dependency["category"] == "unresolved" else "inferred"
        dependencies.append(
            {
                "id": dependency["id"],
                "module": dependency["module"],
                "category": dependency["category"],
                "package": dependency.get("package"),
                "requirement": dependency.get("requirement"),
                "source": source,
                "declaredIn": declaration["path"] if declaration else None,
                "locations": [
                    {"path": imports[e]["path"], "line": imports[e]["line"]}
                    for e in evidence
                    if e in imports
                ],
            }
        )
    options = report.get("options", {})
    layout = report.get("layout") or {}
    plan = (preparation or {}).get("plan", {})
    review = job.get("automatic_review")
    resolution = job.get("automatic_resolution")
    return {
        "analysisId": report.get("analysis_id", ""),
        "projectId": project_id,
        "status": "analyzing"
        if any(
            (item or {}).get("status") in {"pending", "running"} for item in (review, resolution)
        )
        else analysis_status(report or None, job["status"]),
        "automaticReview": review_to_web(plan.get("automatic_review") or review),
        "automaticResolution": resolution_to_web(plan.get("automatic_resolution") or resolution),
        "error": (job.get("error") or {}).get("code"),
        "files": [
            {
                "path": item["path"],
                "size": item["size"],
                **({"secret": "Potential credential"} if item["path"] in secret_paths else {}),
            }
            for item in report.get("files", [])
        ],
        "dependencies": dependencies,
        "entrypoints": report.get("entrypoints", []),
        "pythonRequires": report.get("python_requires"),
        "findings": findings_to_web(report.get("findings", []), report),
        "suggestedRunSpec": run_to_web(plan.get("run_spec")),
        "launchCandidates": [
            {
                "id": item["id"],
                "runSpec": run_to_web(item["run_spec"]),
                "reason": item.get("reason", ""),
                "confidence": item.get("confidence"),
            }
            for item in (preparation or {}).get("launch", {}).get("candidates", [])
        ],
        "options": {
            "pythonVersion": options.get("python_version", "3.11"),
            "pythonVersionAuto": options.get("python_version_auto", False),
            "projectRoot": options.get("project_root"),
            "sourceRoots": options.get("source_roots", ["."]),
        },
        "projectRoots": layout.get("root_candidates", ["."]),
        "optionalGroups": report.get("optional_groups", []),
        "layout": layout,
    }
