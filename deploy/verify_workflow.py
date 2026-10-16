"""Verify two pinned public projects. Source execution requires --reproduce-only.

Inputs: sampleproject-input.zip, click-repo-input.zip and the two explicitly
collected sample/click-author-environment.json snapshots. No paid AI calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from nishtyak_client import LogicClient, LogicClientError
from nishtyak_core import ArchiveReader

CASES = (
    {
        "name": "sample",
        "repository": "https://github.com/pypa/sampleproject",
        "commit": "621e4974ca25ce531773def586ba3ed8e736b3fc",
        "archive": "sampleproject-input.zip",
        "root": "sampleproject-621e4974ca25ce531773def586ba3ed8e736b3fc",
        "run": {"kind": "module", "target": "tests.test_simple"},
        "expected": "OK",
        "extras": ["test"],
        "versions": {"coverage": "==7.6.12"},
    },
    {
        "name": "click",
        "repository": "https://github.com/pallets/click",
        "commit": "2247b35ea1c47c727d7a06e51fa280e12a863ff6",
        "subproject": "examples/repo",
        "archive": "click-repo-input.zip",
        "root": "repo-example",
        "run": {
            "kind": "callable",
            "target": "repo:cli",
            "args": ["clone", "demo-source", "demo-destination", "--rev", "REV-42"],
        },
        "expected": "Checking out revision REV-42",
        "extras": [],
        "versions": {},
    },
)


class WorkflowVerifier:
    def __init__(self, client: LogicClient, inputs: Path, output: Path):
        self.client, self.inputs, self.output = client, inputs, output

    def verify(self, case: dict) -> dict:
        archive = (self.inputs / case["archive"]).read_bytes()
        environment = json.loads(
            (self.inputs / (case["name"] + "-author-environment.json")).read_text()
        )
        project = self.client.submit(archive, author_environment=environment)
        identity = project["project_id"]
        try:
            job = self.client.wait(project["job_id"], timeout=60, interval=1)
            assert job["status"] == "succeeded", job.get("error")
            report = job["analysis"]
            assert report["layout"]["root"] == case["root"]
            assert report["author_environment_id"]
            decisions = {
                "analysis_id": report["analysis_id"],
                "run_spec": {**case["run"], "working_directory": case["root"]},
                "python_version": environment["python_version"],
                "selected_extras": case["extras"],
                "versions": case["versions"],
                "description": "Public project verification: " + case["repository"],
                "input_description": "No external input data; fixed command arguments.",
                "expected_output": case["expected"],
                "data_confirmed": True,
            }
            if case["name"] == "sample":
                decisions["excluded_modules"] = ["nox"]
                decisions["excluded_files"] = [case["root"] + "/noxfile.py"]
            else:
                declaration = next(
                    item for item in report["declarations"] if item["requirement"] == "click>=8.1"
                )
                decisions["declaration_actions"] = {
                    declaration["id"]: {"action": "replace", "requirement": "click==8.1.8"}
                }
            saved = self.client.save_decisions(
                identity, decisions, self.client.decisions(identity)["revision"]
            )
            restored = self.client.decisions(identity)
            assert restored == saved
            try:
                self.client.save_decisions(identity, decisions, saved["revision"] - 1)
                raise AssertionError("A stale revision was accepted")
            except LogicClientError as error:
                assert error.status == 409 and error.code == "DECISIONS_REVISION_CONFLICT"
            plan = self.client.plan(identity, restored["decisions"])
            assert plan["state"] == "ready_to_verify", plan["findings"]
            bundle = self.client.bundle(identity, restored["decisions"], include_dockerfile=True)
            (self.output / (case["name"] + "-bundle.zip")).write_bytes(bundle)
            (self.output / (case["name"] + "-plan.json")).write_text(
                json.dumps(plan, indent=2) + "\n"
            )
            return {
                "name": case["name"],
                "repository": case["repository"],
                "commit": case["commit"],
                "subproject": case.get("subproject"),
                "input_sha256": hashlib.sha256(archive).hexdigest(),
                "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
                "analysis_id": report["analysis_id"],
                "author_environment_id": report["author_environment_id"],
                "layout": report["layout"],
                "requirements": plan["requirements"],
                "decision_revision": saved["revision"],
                "saved_reload_equal": True,
                "stale_revision_rejected": True,
                "state": plan["state"],
            }
        finally:
            self.client.delete(identity)


def reproduce(case: dict, output: Path) -> dict:
    root = output / (case["name"] + "-colleague")
    if root.exists():
        raise ValueError("Use a new output directory for each clean-environment run")
    root.mkdir()
    snapshot = ArchiveReader().from_path(output / (case["name"] + "-bundle.zip"))
    for source in snapshot.files:
        destination = root / source.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.content)
    subprocess.run(
        [sys.executable, "-m", "venv", str(root / ".venv")],
        check=True,
        capture_output=True,
        timeout=60,
    )
    python = root / ".venv/bin/python"
    installed = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            "requirements.txt",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=180,
    )
    (root / "installation.log").write_text(installed.stdout + installed.stderr)
    installed.check_returncode()
    # Both reviewed README files explicitly prescribe this command.
    assert "python run_project.py" in (root / "README.md").read_text()
    result = subprocess.run(
        [str(python), "run_project.py"], cwd=root, capture_output=True, text=True, timeout=30
    )
    (root / "execution.log").write_text(result.stdout + result.stderr)
    result.check_returncode()
    assert case["expected"] in result.stdout + result.stderr
    version = subprocess.check_output([str(python), "--version"], text=True).strip()
    return {
        "name": case["name"],
        "python": version,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "expected_matched": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reproduce-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.reproduce_only:
        result = {"status": "passed", "reproductions": [reproduce(case, output) for case in CASES]}
        (output / "reproduction-verification.json").write_text(json.dumps(result, indent=2) + "\n")
    else:
        if args.inputs is None:
            parser.error("--inputs must contain reviewed ZIPs and author snapshots")
        client = LogicClient(
            args.url, os.environ["NISHTYAK_SERVICE_TOKEN"], "workflow-" + uuid.uuid4().hex
        )
        verifier = WorkflowVerifier(client, args.inputs, output)
        result = {"status": "passed", "cases": [verifier.verify(case) for case in CASES]}
        (output / "workflow-verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
