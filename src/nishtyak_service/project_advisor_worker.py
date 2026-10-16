from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nishtyak_core.advisors import DeepSeekClient, ProjectAdvisoryService, SafeProjectContext
from nishtyak_core.advisors.project_agent import FEATURE_KINDS
from nishtyak_core.report_codec import bounded_json

from .advice_jobs import MAX_DESCRIPTION_CHARACTERS, MAX_RESPONSE_BYTES
from .advisor_worker import AdvisorWorker


def run(destination: Path) -> int:
    try:
        AdvisorWorker._set_limits()
        raw = sys.stdin.buffer.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            return 1
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"context", "feature", "description"}:
            return 1
        if (
            value["feature"] not in FEATURE_KINDS
            or not isinstance(value["description"], str)
            or len(value["description"]) > MAX_DESCRIPTION_CHARACTERS
        ):
            return 1
        context = SafeProjectContext.from_dict(value["context"])
        result = ProjectAdvisoryService(DeepSeekClient.from_env()).advise_context(
            context, value["feature"], value["description"]
        )
        with destination.open("xb") as stream:
            stream.write(bounded_json(result.to_dict(), MAX_RESPONSE_BYTES))
        return 0
    except Exception:
        return 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    raise SystemExit(run(options.output))


if __name__ == "__main__":
    main()
