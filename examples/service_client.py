"""Run a reviewed upload-to-bundle workflow against the internal logic service.

The service token comes from the backend environment. This script does not execute
the exported project. Install the wheel, then run with --help for an example.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from nishtyak_client import LogicClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--entry")
    parser.add_argument("--kind", choices=("script", "module", "callable"), default="script")
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--python", dest="python_version")
    parser.add_argument("--extra", action="append", default=[])
    parser.add_argument("--exclude-module", action="append", default=[])
    parser.add_argument("--exclude-file", action="append", default=[])
    parser.add_argument("--author-environment", type=Path)
    parser.add_argument("--options", type=Path)
    parser.add_argument("--description", default="")
    parser.add_argument("--input-description", default="")
    parser.add_argument("--expected-output", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    client = LogicClient(args.url, os.environ["NISHTYAK_SERVICE_TOKEN"], args.owner)
    options = json.loads(args.options.read_text()) if args.options else None
    environment = (
        json.loads(args.author_environment.read_text()) if args.author_environment else None
    )
    project = client.submit(args.archive, options, environment)
    job = client.wait(project["job_id"])
    if job["status"] != "succeeded":
        raise RuntimeError(f"Analysis failed: {job.get('error')}")
    report = job["analysis"]
    decisions = {
        "analysis_id": report["analysis_id"],
        "run_spec": (
            {"kind": args.kind, "target": args.entry, "working_directory": args.cwd}
            if args.entry
            else None
        ),
        "python_version": args.python_version,
        "selected_extras": args.extra,
        "excluded_modules": args.exclude_module,
        "excluded_files": args.exclude_file,
        "description": args.description,
        "input_description": args.input_description,
        "expected_output": args.expected_output,
        "data_confirmed": True,
    }
    document = client.decisions(project["project_id"])
    saved = client.save_decisions(project["project_id"], decisions, document["revision"])
    # Reopening a page uses these same saved choices, including its current revision.
    restored = client.decisions(project["project_id"])
    assert restored == saved
    plan = client.plan(project["project_id"], restored["decisions"])
    if plan["state"] == "blocked":
        print(json.dumps({"project": project, "plan": plan}, ensure_ascii=False, indent=2))
        raise SystemExit("Resolve blocking findings before export")
    args.output.write_bytes(client.bundle(project["project_id"], restored["decisions"]))
    print(
        json.dumps({**project, "revision": saved["revision"], "bundle": str(args.output)}, indent=2)
    )


if __name__ == "__main__":
    main()
