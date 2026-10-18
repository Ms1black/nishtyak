import json
import os
import subprocess
import sys
import time
from dataclasses import asdict

import pytest

from nishtyak_core import ReproducibilityService, SnapshotBuilder
from nishtyak_core.advisors.models import AdvisoryResult, AdvisorySuggestion
from nishtyak_service.advice_jobs import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    AdviceJobRunner,
)


@pytest.fixture
def report():
    return ReproducibilityService().analyze(
        SnapshotBuilder().from_mapping({"main.py": "import pandas\n"})
    )


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "a-private-test-key-no-paid-calls")
    monkeypatch.setenv("DEEPSEEK_MODEL", "a-test-model")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)


class ChildProbe(AdviceJobRunner):
    def __init__(self, code, wall_time_seconds=2):
        super().__init__(wall_time_seconds)
        self.code = code

    def _command(self, output_path):
        return [sys.executable, "-I", "-c", self.code, str(output_path)]


def result_code(value):
    return (
        "import json,pathlib,sys; "
        f"pathlib.Path(sys.argv[1]).write_text({json.dumps(json.dumps(value))})"
    )


def test_missing_key_or_model_is_disabled_without_spawning(monkeypatch, report):
    def no_spawn(*args, **kwargs):
        raise AssertionError("Disabled advice must not spawn a child")

    monkeypatch.setattr(subprocess, "Popen", no_spawn)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_MODEL", "test")
    assert AdviceJobRunner().run(report)["status"] == "unavailable"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    assert AdviceJobRunner().run(report)["status"] == "unavailable"


def test_invalid_feature_or_description_never_spawns(monkeypatch, report, configured):
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Must validate before spawn")
    )
    assert AdviceJobRunner().run(report, feature="execute")["status"] == "invalid_request"
    assert AdviceJobRunner().run(report, description="я" * 8193)["status"] == "invalid_request"


def test_non_ascii_description_uses_the_same_character_limit_as_http(report, configured):
    value = AdvisoryResult(report.analysis_id, "explain", "ok").to_dict()
    assert ChildProbe(result_code(value)).run(report, description="я" * 8192)["status"] == "ok"


def test_advice_has_a_hard_network_independent_wall_deadline(report, configured, tmp_path):
    marker = tmp_path / "child.pid"
    runner = ChildProbe(
        "import os,pathlib,time; "
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); "
        "time.sleep(60)",
        wall_time_seconds=0.2,
    )
    started = time.monotonic()
    result = runner.run(report)
    assert time.monotonic() - started < 3
    assert result["status"] == "unavailable"
    assert result["reason"] == "advisor_unavailable"
    if marker.exists():
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)


def test_advice_child_receives_only_allowlisted_environment(report, configured, monkeypatch):
    monkeypatch.setenv("NISHTYAK_SERVICE_TOKEN", "private-service-token")
    monkeypatch.setenv("HTTP_PROXY", "http://private-proxy")
    monkeypatch.setenv("PYTHONPATH", "/private/python-module-injection")
    monkeypatch.setenv("OTHER_SECRET", "private-secret")
    value = AdvisoryResult(report.analysis_id, "explain", "ok").to_dict()
    runner = ChildProbe(
        "import os,json,pathlib,sys; "
        "allowed={'LANG','LC_ALL','TMPDIR','TMP','TEMP','DEEPSEEK_API_KEY','DEEPSEEK_MODEL',"
        "'DEEPSEEK_BASE_URL','__CF_USER_TEXT_ENCODING','LC_CTYPE'}; "
        "assert not set(os.environ)-allowed; "
        "assert os.environ['DEEPSEEK_API_KEY']=='a-private-test-key-no-paid-calls'; "
        f"pathlib.Path(sys.argv[1]).write_text({json.dumps(json.dumps(value))})"
    )
    assert runner.run(report)["status"] == "ok"


def test_successful_advice_requires_confirmation_and_redacts_keys(report, configured):
    suggestion = AdvisorySuggestion(
        "explanation",
        "a-private-test-key-no-paid-calls password='a-secret-password'",
        (report.imports[0].id,),
    )
    value = AdvisoryResult(report.analysis_id, "explain", "ok", (suggestion,)).to_dict()
    result = ChildProbe(result_code(value)).run(report)
    assert result["status"] == "ok"
    assert result["suggestions"][0]["requires_confirmation"] is True
    assert "a-private-test-key" not in json.dumps(result)
    assert "a-secret-password" not in json.dumps(result)


@pytest.mark.parametrize("change", ["analysis", "confirmation", "evidence", "unknown", "usage"])
def test_parent_rejects_invalid_child_advice(report, configured, change):
    value = AdvisoryResult(
        report.analysis_id,
        "explain",
        "ok",
        (AdvisorySuggestion("explanation", "description", (report.imports[0].id,)),),
    ).to_dict()
    value = json.loads(json.dumps(value))
    if change == "analysis":
        value["analysis_id"] = "other-analysis"
    elif change == "confirmation":
        value["suggestions"][0]["requires_confirmation"] = False
    elif change == "evidence":
        value["suggestions"][0]["evidence_ids"] = ["invented"]
    elif change == "unknown":
        value["commands"] = ["execute-this"]
    else:
        value["usage"] = {"prompt_tokens": True, "completion_tokens": 1, "total_tokens": 1}
    assert ChildProbe(result_code(value)).run(report)["status"] == "unavailable"


def test_parent_rejects_oversized_and_symlink_output(report, configured):
    oversized = ChildProbe(
        f"import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'x'*{MAX_RESPONSE_BYTES + 1})"
    )
    assert oversized.run(report)["status"] == "unavailable"
    symlink = ChildProbe(
        "import pathlib,sys; "
        "pathlib.Path(sys.argv[1]).symlink_to('/private/non-public-server-config')"
    )
    assert symlink.run(report)["status"] == "unavailable"


def test_child_failure_never_returns_raw_error_text(report, configured):
    result = ChildProbe("raise RuntimeError('private-key-and-server-path')").run(report)
    assert result["status"] == "unavailable"
    assert "private-key-and-server-path" not in json.dumps(result)


def test_real_advisor_worker_without_credentials_does_not_call_network(
    report, monkeypatch, tmp_path
):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    output = tmp_path / "result.json"
    process = subprocess.run(
        [sys.executable, "-I", "-m", "nishtyak_service.advisor_worker", "--output", str(output)],
        input=json.dumps(
            {"report": asdict(report), "feature": "explain", "description": ""}
        ).encode(),
        capture_output=True,
        timeout=5,
    )
    assert process.returncode == 0
    assert json.loads(output.read_bytes())["status"] == "unavailable"
    assert process.stdout == b"" and process.stderr == b""


def test_real_advisor_worker_rejects_oversized_stdin_without_network(tmp_path):
    output = tmp_path / "result.json"
    process = subprocess.run(
        [sys.executable, "-I", "-m", "nishtyak_service.advisor_worker", "--output", str(output)],
        input=b"x" * (MAX_REQUEST_BYTES + 1),
        capture_output=True,
        timeout=5,
    )
    assert process.returncode == 1
    assert not output.exists()


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf"), 61])
def test_advice_deadline_is_bounded(timeout):
    with pytest.raises(ValueError):
        AdviceJobRunner(timeout)
