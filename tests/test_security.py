import pytest

from nishtyak_core.security import SecretScanner
from nishtyak_core.snapshot import SnapshotBuilder


def test_credential_rich_files_produce_one_blocking_finding_per_file():
    token = "sk-" + "a" * 24
    snapshot = SnapshotBuilder().from_mapping(
        {
            "many.txt": (token + "\n") * 20_000,
            "other.txt": "safe text\n" + token,
            "clean.txt": "ordinary data\n" * 20_000,
        }
    )

    findings = SecretScanner().scan(snapshot)

    assert [(finding.path, finding.line) for finding in findings] == [
        ("many.txt", 1),
        ("other.txt", 2),
    ]
    assert all(finding.code == "SECRET_DETECTED" for finding in findings)
    assert all(finding.severity == "error" for finding in findings)
    assert token not in repr(findings)


def test_scanner_reports_the_earliest_match_across_patterns():
    snapshot = SnapshotBuilder().from_mapping(
        {"config.txt": 'safe\napi_key = "private-value-1234"\nhttps://user:password@example.org'}
    )

    (finding,) = SecretScanner().scan(snapshot)

    assert finding.line == 2


@pytest.mark.parametrize(
    "text",
    [
        "a-" * 50_000,
        "KEY_" * 25_000,
        "KEY_" * 25_000 + "=short\n",
    ],
)
def test_long_repeated_key_prefixes_are_scanned_without_unbounded_backtracking(text):
    scanner = SecretScanner()
    assert scanner.scan(SnapshotBuilder().from_mapping({"data.txt": text})) == ()
    assert scanner.redact(text) == text


@pytest.mark.parametrize(
    "assignment",
    [
        'deepseek_api_key = "private-value-1234"',
        'my-access-token: "private-value-1234"',
        '"service_password": "private-value-1234"',
        "PREFIX_TOKEN_SUFFIX=private-value-1234",
        "KEY_" * 25_000 + "=private-value-1234",
    ],
)
def test_prefixed_credential_names_remain_detected_and_redacted(assignment):
    scanner = SecretScanner()
    findings = scanner.scan(SnapshotBuilder().from_mapping({"config.txt": assignment}))

    assert len(findings) == 1
    assert "private-value-1234" not in scanner.redact(assignment)
