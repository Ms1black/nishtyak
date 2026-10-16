from nishtyak_web.mapping import analysis_to_web, findings_to_web, resolution_to_web


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
