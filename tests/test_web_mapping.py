import pytest

from nishtyak_web.mapping import (
    analysis_status,
    analysis_to_web,
    automation_to_web,
    findings_to_web,
    resolution_to_web,
)


def test_finding_module_uses_structured_evidence_and_remains_optional():
    report = {
        "dependencies": [
            {"id": "dependency-a", "module": "numpy", "evidence_ids": ["import-a", "shared"]},
            {"id": "dependency-b", "module": "pandas", "evidence_ids": ["import-b", "shared"]},
        ]
    }
    values = [
        {"code": "VERSION_UNKNOWN", "evidence_ids": ["import-a"]},
        {"code": "DEPENDENCY_UNRESOLVED", "evidence_ids": ["dependency-b"]},
        {"code": "DEPENDENCY_UNRESOLVED", "evidence_ids": ["shared"]},
        {"code": "VERSION_NOT_PINNED", "message": "The tested version of numpy is unknown."},
    ]
    findings = findings_to_web(values, report)
    assert findings[0]["module"] == "numpy"
    assert findings[1]["module"] == "pandas"
    assert "module" not in findings[2]
    assert "module" not in findings[3]
    assert findings[3]["message"] == values[3]["message"]
    assert "module" not in findings_to_web(values)[0]


def test_automatic_environment_preserves_target_and_provenance_for_browser():
    resolution = resolution_to_web(
        {
            "status": "ready",
            "target": {"python_version": "3.12", "platform": "linux-x86_64"},
            "python_source": "declared",
            "platform_source": "service_default",
            "lock_id": "a" * 64,
            "packages": ["numpy==2.2.0"],
            "provenance": "newly_resolved_not_author_environment",
            "elapsed_ms": 1200,
        }
    )
    assert resolution["target"] == {"pythonVersion": "3.12", "platform": "linux-x86_64"}
    assert resolution["pythonSource"] == "declared"
    assert resolution["platformSource"] == "service_default"
    assert resolution["packages"] == ["numpy==2.2.0"]
    assert resolution["lockId"] == "a" * 64
    assert resolution["provenance"] == "newly_resolved_not_author_environment"


def test_analysis_waits_for_background_environment_after_static_analysis():
    job = {
        "status": "succeeded",
        "analysis": {"analysis_id": "b" * 20, "status": "complete"},
        "automatic_resolution": {"status": "running"},
    }
    result = analysis_to_web("a" * 32, job)
    assert result["status"] == "analyzing"
    assert result["automaticResolution"]["status"] == "running"
    job["automatic_resolution"] = {"status": "unavailable", "reason": "INDEX_UNAVAILABLE"}
    result = analysis_to_web("a" * 32, job)
    assert result["status"] == "full"
    assert result["automaticResolution"]["reason"] == "INDEX_UNAVAILABLE"


def test_targeted_question_is_mapped_without_restarting_legacy_pending_review():
    flow = {
        "phase": "needs_input",
        "aspect": "dependencies",
        "attempt": 2,
        "max_attempts": 2,
        "question": {
            "id": "question-one",
            "kind": "package_mapping",
            "module": "research_lib",
            "message": "Which distribution provides research_lib?",
        },
    }
    result = automation_to_web(flow)
    assert result["maxAttempts"] == 2
    assert result["question"]["module"] == "research_lib"
    job = {
        "status": "succeeded",
        "analysis": {"analysis_id": "b" * 20, "status": "complete"},
        "automatic_review": {"status": "pending", "provider": "deepseek"},
        "automation": flow,
    }
    result = analysis_to_web("a" * 32, job)
    assert result["status"] != "analyzing"
    assert result["automation"]["question"]["id"] == "question-one"


def resolved_preparation(*, findings=(), phase="ready"):
    return {
        "plan": {
            "report": {"analysis_id": "analysis-one"},
            "state": "ready_to_verify",
            "findings": list(findings),
            "automatic_packages": {"research_lib": "research-distribution"},
            "automatic_resolution": {"status": "ready"},
            "automation": {"phase": phase},
        }
    }


def unresolved_report():
    return {
        "analysis_id": "analysis-one",
        "status": "complete",
        "dependencies": [{"id": "dep-one", "module": "research_lib", "category": "unresolved"}],
        "findings": [{"code": "DEPENDENCY_UNRESOLVED"}],
    }


def test_effective_verified_mapping_clears_raw_unresolved_analysis_status():
    report = unresolved_report()
    job = {
        "status": "succeeded",
        "analysis": report,
        "automatic_resolution": {"status": "ready"},
        "automation": {"phase": "ready"},
    }
    preparation = resolved_preparation(findings=[{"code": "RUN_AUTOMATIC", "severity": "info"}])
    assert analysis_status(report, "succeeded") == "partial"
    assert analysis_status(report, "succeeded", preparation, job) == "full"
    result = analysis_to_web("project-one", job, preparation)
    assert result["status"] == "full"
    assert result["dependencies"][0]["category"] == "external"
    assert result["dependencies"][0]["package"] == "research-distribution"
    assert result["automation"]["phase"] == "ready"


@pytest.mark.parametrize(
    "code",
    [
        "DEPENDENCY_UNRESOLVED",
        "DECLARATION_IMPORT_UNRESOLVED",
        "LOCAL_MODULE_MISSING",
        "LOCAL_IMPORT_CONTEXT_REQUIRED",
        "PROJECT_ROOT_AMBIGUOUS",
        "SOURCE_PARSE_ERROR",
        "DYNAMIC_IMPORT_UNRESOLVED",
    ],
)
def test_ready_preparation_does_not_hide_effective_incomplete_findings(code):
    report = unresolved_report()
    preparation = resolved_preparation(findings=[{"code": code}])
    assert analysis_status(report, "succeeded", preparation) == "partial"


@pytest.mark.parametrize(
    "code",
    [
        "PROJECT_ROOT_AMBIGUOUS",
        "SOURCE_PARSE_ERROR",
        "MANIFEST_PARSE_ERROR",
        "SOURCE_TOO_LARGE",
        "FILE_LIMIT_EXCEEDED",
        "PYTHON_PROFILE_UNSUPPORTED",
        "FUTURE_COVERAGE_LIMIT",
    ],
)
def test_raw_partial_coverage_cannot_be_fixed_by_package_mapping_or_ready_lock(code):
    report = {**unresolved_report(), "status": "partial", "findings": [{"code": code}]}
    assert analysis_status(report, "succeeded", resolved_preparation()) == "partial"


def test_ready_background_metadata_alone_does_not_claim_unknown_imports_resolved():
    report = unresolved_report()
    job = {"automation": {"phase": "ready"}, "automatic_resolution": {"status": "ready"}}
    assert analysis_status(report, "succeeded", job=job) == "partial"


def test_preparation_for_another_analysis_cannot_clear_current_unknown_imports():
    preparation = resolved_preparation()
    preparation["plan"]["report"]["analysis_id"] = "obsolete-analysis"
    assert analysis_status(unresolved_report(), "succeeded", preparation) == "partial"


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled"])
def test_job_lifecycle_takes_precedence_over_ready_preparation(status):
    expected = "analyzing" if status in {"queued", "running"} else "failed"
    assert analysis_status(unresolved_report(), status, resolved_preparation()) == expected


@pytest.mark.parametrize("phase", ["static", "assisting", "validating", "finalizing"])
def test_active_effective_preparation_keeps_analysis_in_progress(phase):
    assert (
        analysis_status(unresolved_report(), "succeeded", resolved_preparation(phase=phase))
        == "analyzing"
    )


def test_effective_question_and_blocked_plan_remain_partial():
    assert (
        analysis_status(unresolved_report(), "succeeded", resolved_preparation(phase="needs_input"))
        == "partial"
    )
    preparation = resolved_preparation()
    preparation["plan"]["state"] = "blocked"
    assert analysis_status(unresolved_report(), "succeeded", preparation) == "partial"


def test_resolved_analysis_warnings_do_not_claim_execution_was_verified():
    preparation = resolved_preparation(
        findings=[
            {"code": "VERSION_UNKNOWN"},
            {"code": "DATA_DEPENDENCIES_UNVERIFIED"},
            {"code": "RUN_NOT_EXECUTED"},
        ]
    )
    preparation["plan"]["state"] = "draft"
    assert analysis_status(unresolved_report(), "succeeded", preparation) == "full"


def test_job_preparation_summary_is_a_bound_fallback_for_project_listing():
    summary = resolved_preparation()["plan"]
    job = {"preparation_summary": summary, "automation": {"phase": "ready"}}
    assert analysis_status(unresolved_report(), "succeeded", job=job) == "full"
    summary["report"]["analysis_id"] = "old-analysis"
    assert analysis_status(unresolved_report(), "succeeded", job=job) == "partial"


def test_current_preparation_takes_precedence_over_a_cached_job_summary():
    job = {"preparation_summary": resolved_preparation()["plan"]}
    current = resolved_preparation(findings=[{"code": "DEPENDENCY_UNRESOLVED"}])
    assert analysis_status(unresolved_report(), "succeeded", current, job) == "partial"


def test_job_summary_automation_preserves_current_preparation_progress():
    job = {
        "preparation_summary": resolved_preparation(phase="validating")["plan"],
        "automation": {"phase": "ready"},
    }
    assert analysis_status(unresolved_report(), "succeeded", job=job) == "analyzing"
