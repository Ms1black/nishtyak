from __future__ import annotations

import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from nishtyak_core.advisors.client import DeepSeekConfig
from nishtyak_core.advisors.models import AdvisoryResult
from nishtyak_core.models import AnalysisReport
from nishtyak_core.redaction import TextRedactor
from nishtyak_core.report_codec import AnalysisReportCodec, bounded_json

MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_DESCRIPTION_CHARACTERS = 8 * 1024
FEATURE_KINDS = {
    "explain": "explanation",
    "readme": "readme_description",
    "packages": "package_hypothesis",
}


class AdviceJobRunner:
    """Keep network reads inside a fresh process with a hard wall deadline."""

    def __init__(self, wall_time_seconds: float = 20.0):
        if (
            isinstance(wall_time_seconds, bool)
            or not isinstance(wall_time_seconds, (int, float))
            or not math.isfinite(wall_time_seconds)
            or not 0 < wall_time_seconds <= 60
        ):
            raise ValueError("Advice deadline must be positive and at most 60 seconds")
        self.wall_time_seconds = wall_time_seconds

    def run(
        self, report: AnalysisReport, feature: str = "explain", description: str = ""
    ) -> dict[str, Any]:
        unavailable = self._unavailable(report, feature)
        if (
            feature not in FEATURE_KINDS
            or not isinstance(description, str)
            or len(description) > MAX_DESCRIPTION_CHARACTERS
        ):
            return AdvisoryResult(report.analysis_id, feature, "invalid_request").to_dict()
        try:
            config = DeepSeekConfig.from_env()
            if not config.api_key or not config.model:
                return unavailable
            request = bounded_json(
                {
                    "report": AnalysisReportCodec().encode(report),
                    "feature": feature,
                    "description": description,
                },
                MAX_REQUEST_BYTES,
            )
            with tempfile.TemporaryDirectory(prefix="nishtyak-advice-") as directory:
                root = Path(directory)
                input_path, output_path = root / "request.json", root / "result.json"
                input_path.write_bytes(request)
                with input_path.open("rb") as source:
                    process = subprocess.Popen(
                        self._command(output_path),
                        cwd=root,
                        env=self._environment(root),
                        stdin=source,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        close_fds=True,
                    )
                    try:
                        process.wait(timeout=self.wall_time_seconds)
                    except subprocess.TimeoutExpired:
                        self._kill(process)
                        return unavailable
                    finally:
                        if process.poll() is None:
                            self._kill(process)
                if process.returncode:
                    return unavailable
                return self._read(output_path, report, feature, config.api_key)
        except (OSError, ValueError, TypeError, RecursionError, MemoryError):
            return unavailable

    @staticmethod
    def _command(output_path: Path) -> list[str]:
        return [
            sys.executable,
            "-I",
            "-m",
            "nishtyak_service.advisor_worker",
            "--output",
            str(output_path),
        ]

    @staticmethod
    def _environment(root: Path) -> dict[str, str]:
        environment = {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": str(root),
            "TMP": str(root),
            "TEMP": str(root),
        }
        for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"):
            if name in os.environ:
                environment[name] = os.environ[name]
        return environment

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    @staticmethod
    def _unavailable(report: AnalysisReport, feature: str) -> dict[str, Any]:
        return AdvisoryResult(
            report.analysis_id, feature, "unavailable", reason="advisor_unavailable"
        ).to_dict()

    def _read(
        self, path: Path, report: AnalysisReport, feature: str, api_key: str
    ) -> dict[str, Any]:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("Invalid advisor result")
        with path.open("rb") as stream:
            raw = stream.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Advisor result too large")
        value = json.loads(raw)
        self._validate_result(value, report, feature)
        if value["status"] == "unavailable":
            return self._unavailable(report, feature)
        value["reason"] = None
        return self._redact(value, api_key)

    @staticmethod
    def _validate_result(value: Any, report: AnalysisReport, feature: str) -> None:
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "analysis_id",
                "feature",
                "status",
                "suggestions",
                "usage",
                "reason",
                "schema_version",
            }
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["analysis_id"] != report.analysis_id
            or value["feature"] != feature
            or value["status"] not in {"ok", "unavailable", "invalid_response", "invalid_request"}
            or not isinstance(value["suggestions"], list)
            or len(value["suggestions"]) > 20
            or (value["status"] != "ok" and value["suggestions"])
            or (value["reason"] is not None and not isinstance(value["reason"], str))
        ):
            raise ValueError("Invalid advisor response")
        evidence = {
            item.id
            for items in (report.imports, report.dependencies, report.findings)
            for item in items
        }
        modules = {item.module for items in (report.imports, report.dependencies) for item in items}
        for item in value["suggestions"]:
            AdviceJobRunner._validate_suggestion(item, feature, evidence, modules)
        usage = value["usage"]
        if usage is not None and (
            not isinstance(usage, dict)
            or set(usage) != {"prompt_tokens", "completion_tokens", "total_tokens"}
            or any(type(count) is not int or not 0 <= count <= 10**9 for count in usage.values())
        ):
            raise ValueError("Invalid advisor usage")

    @staticmethod
    def _validate_suggestion(
        item: Any, feature: str, evidence: set[str], modules: set[str]
    ) -> None:
        if (
            not isinstance(item, dict)
            or set(item)
            != {"kind", "text", "evidence_ids", "module", "package", "requires_confirmation"}
            or item["kind"] != FEATURE_KINDS[feature]
            or item["requires_confirmation"] is not True
            or not isinstance(item["text"], str)
            or not 0 < len(item["text"]) <= 4000
            or not isinstance(item["evidence_ids"], list)
            or len(item["evidence_ids"]) > 1000
            or any(not isinstance(key, str) or key not in evidence for key in item["evidence_ids"])
        ):
            raise ValueError("Invalid advisor suggestion")
        if feature == "packages":
            if (
                not isinstance(item["module"], str)
                or item["module"] not in modules
                or not item["evidence_ids"]
                or not isinstance(item["package"], str)
                or re.fullmatch(
                    r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?", item["package"]
                )
                is None
            ):
                raise ValueError("Invalid package hypothesis")
        elif item["module"] is not None or item["package"] is not None:
            raise ValueError("Unexpected package hypothesis")

    @staticmethod
    def _redact(value: Any, api_key: str) -> Any:
        if isinstance(value, str):
            return TextRedactor().redact(value.replace(api_key, "[REDACTED]"))
        if isinstance(value, list):
            return [AdviceJobRunner._redact(item, api_key) for item in value]
        if isinstance(value, dict):
            return {key: AdviceJobRunner._redact(item, api_key) for key, item in value.items()}
        return value
