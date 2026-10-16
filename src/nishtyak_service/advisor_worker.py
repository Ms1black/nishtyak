from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from nishtyak_core.advisors import AdvisoryService, DeepSeekClient
from nishtyak_core.report_codec import AnalysisReportCodec, bounded_json

from .advice_jobs import (
    FEATURE_KINDS,
    MAX_DESCRIPTION_CHARACTERS,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
)


class AdvisorWorker:
    def run(self, output_path: Path) -> int:
        try:
            self._set_limits()
            request = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
            if len(request) > MAX_REQUEST_BYTES:
                return 1
            document = json.loads(request)
            if not isinstance(document, dict) or set(document) != {
                "report",
                "feature",
                "description",
            }:
                return 1
            feature, description = document["feature"], document["description"]
            if (
                feature not in FEATURE_KINDS
                or not isinstance(description, str)
                or len(description) > MAX_DESCRIPTION_CHARACTERS
            ):
                return 1
            report = AnalysisReportCodec().decode(document["report"])
            advisor = AdvisoryService(DeepSeekClient.from_env())
            result = advisor.advise(report, feature=feature, description=description)
            output = bounded_json(result.to_dict(), MAX_RESPONSE_BYTES)
            with output_path.open("xb") as stream:
                stream.write(output)
            return 0
        except Exception:
            return 1

    @staticmethod
    def _set_limits() -> None:
        if os.name != "posix":
            raise OSError("POSIX resource limits required")
        import resource

        limits = [
            (resource.RLIMIT_CPU, 10),
            (resource.RLIMIT_FSIZE, MAX_RESPONSE_BYTES),
            (resource.RLIMIT_NOFILE, 64),
            (resource.RLIMIT_CORE, 0),
        ]
        if sys.platform.startswith("linux"):
            limits.append((resource.RLIMIT_AS, 512 * 1024 * 1024))
        for kind, configured in limits:
            _, inherited_hard = resource.getrlimit(kind)
            limit = (
                configured
                if inherited_hard == resource.RLIM_INFINITY
                else min(configured, inherited_hard)
            )
            resource.setrlimit(kind, (limit, limit))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    raise SystemExit(AdvisorWorker().run(options.output))


if __name__ == "__main__":
    main()
