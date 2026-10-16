from nishtyak_web.mapping import findings_to_web


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
