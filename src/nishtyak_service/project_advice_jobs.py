from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from nishtyak_core.advisors import (
    DeepSeekClient,
    DeepSeekConfig,
    ProjectAdvisoryResult,
    ProjectAdvisoryService,
    SafeProjectContext,
)
from nishtyak_core.advisors.project_agent import FEATURE_KINDS
from nishtyak_core.report_codec import bounded_json

from .advice_jobs import MAX_DESCRIPTION_CHARACTERS, MAX_RESPONSE_BYTES, AdviceJobRunner


class ProjectAdviceJobRunner(AdviceJobRunner):
    """Only a finite sanitized context crosses into the provider-enabled process."""

    def run(
        self, context: SafeProjectContext, feature: str = "explain", description: str = ""
    ) -> dict:
        started = time.monotonic()
        unavailable = ProjectAdvisoryResult(
            context.analysis_id, feature, "unavailable", reason="advisor_unavailable"
        ).to_dict()
        if (
            feature not in FEATURE_KINDS
            or not isinstance(description, str)
            or len(description) > MAX_DESCRIPTION_CHARACTERS
        ):
            return ProjectAdvisoryResult(context.analysis_id, feature, "invalid_request").to_dict()
        try:
            config = DeepSeekConfig.from_env()
            if not config.api_key or not config.model:
                return unavailable
            context = SafeProjectContext.from_dict(context.to_dict())
            payload = bounded_json(
                {"context": context.to_dict(), "feature": feature, "description": description},
                512 * 1024,
            )
            with tempfile.TemporaryDirectory(prefix="nishtyak-project-advice-") as directory:
                root = Path(directory)
                source, destination = root / "request.json", root / "result.json"
                source.write_bytes(payload)
                environment = self._environment(root)
                environment["NISHTYAK_ADVISOR_WALL_SECONDS"] = str(
                    min(13.5, max(0.1, self.wall_time_seconds - (time.monotonic() - started) - 0.5))
                )
                with source.open("rb") as stream:
                    process = subprocess.Popen(
                        self._command(destination),
                        cwd=root,
                        env=environment,
                        stdin=stream,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        close_fds=True,
                    )
                    try:
                        process.wait(
                            timeout=max(0.01, self.wall_time_seconds - (time.monotonic() - started))
                        )
                    except subprocess.TimeoutExpired:
                        self._kill(process)
                        return unavailable
                    finally:
                        if process.poll() is None:
                            self._kill(process)
                if process.returncode:
                    return unavailable
                descriptor = os.open(
                    destination,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                )
                with os.fdopen(descriptor, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        return unavailable
                    raw = stream.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    return unavailable
                result = json.loads(raw)
                ProjectAdvisoryService(DeepSeekClient(config)).validate_result(
                    result, context, feature
                )
                return self._redact(result, config.api_key)
        except (OSError, ValueError, TypeError, RecursionError, MemoryError):
            return unavailable

    @staticmethod
    def _command(output_path: Path) -> list[str]:
        return [
            sys.executable,
            "-I",
            "-m",
            "nishtyak_service.project_advisor_worker",
            "--output",
            str(output_path),
        ]
