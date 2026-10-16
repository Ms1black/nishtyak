"""Operator acceptance using only the two small reviewed fixture projects below.

Uploads known code, resolves wheels, exports and optionally repeats it in a clean venv.
Never use this script to execute arbitrary uploaded projects.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from nishtyak_client import LogicClient

FIXTURES = {
    "scientific": {
        "experiment.py": (
            "import numpy as np\nimport pandas as pd\n"
            'frame = pd.read_csv("data.csv")\n'
            'print(float(np.mean(frame["value"])))\n'
        ),
        "data.csv": "value\n2\n4\n6\n",
    },
    "local": {
        "experiment.py": "from helpers import mean\nprint(mean([2, 4, 6]))\n",
        "helpers.py": "def mean(values):\n    return sum(values) / len(values)\n",
    },
}


def command(args: list[str], directory: Path) -> str:
    result = subprocess.run(args, cwd=directory, text=True, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError("A reviewed fixture verification command failed")
    return result.stdout.strip()


def reproduce(bundle: bytes, directory: Path, expected_files: dict[str, str]) -> dict:
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        # The exported archive must contain exactly the reviewed fixture plus generated files.
        for item in archive.infolist():
            path = Path(item.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe bundle path")
        source_paths = {
            item.filename for item in archive.infolist() if item.filename.startswith("project/")
        }
        if source_paths != {"project/" + path for path in expected_files}:
            raise ValueError("Export differs from reviewed fixture")
        for path, content in expected_files.items():
            if archive.read("project/" + path) != content.encode():
                raise ValueError("Export source differs from reviewed fixture")
        archive.extractall(directory)
    environment = directory / "venv"
    command([sys.executable, "-m", "venv", str(environment)], directory)
    python = str(environment / "bin/python")
    command(
        [
            python,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "--require-hashes",
            "-r",
            "requirements.lock.txt",
        ],
        directory,
    )
    output = command([python, "experiment.py"], directory / "project")
    if output != "4.0":
        raise ValueError("Unexpected reviewed fixture output")
    return {
        "status": "passed",
        "stdout": output,
        "python": command([python, "--version"], directory),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reproduce", action="store_true")
    parser.add_argument("--ai", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = LogicClient(args.url, os.environ["NISHTYAK_SERVICE_TOKEN"], "resolution-acceptance")
    results = {}
    for name, files in FIXTURES.items():
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for path, content in files.items():
                archive.writestr(path, content)
        project = client.submit(stream.getvalue())
        identifier = project["project_id"]
        try:
            job = client.wait(project["job_id"], timeout=90)
            if job["status"] != "succeeded":
                raise RuntimeError("Fixture analysis failed")
            preparation = client.preparation(identifier)
            plan = preparation["plan"]
            if (
                plan["run_spec"]["target"] != "experiment.py"
                or plan["run_spec_origin"] != "inferred"
            ):
                raise ValueError("Unguarded fixture was not inferred automatically")
            lock = client.resolve(identifier, "3.11", "linux-x86_64")
            if client.lock(identifier) != lock:
                raise ValueError("Stored lock differs from resolved lock")
            bundle = client.bundle(identifier, lock_id=lock["id"])
            (args.output_dir / f"{name}-bundle.zip").write_bytes(bundle)
            result = {
                "status": "passed",
                "schema_version": job["analysis"]["schema_version"],
                "automatic_launch": plan["run_spec"],
                "origin": plan["run_spec_origin"],
                "lock": lock,
                "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
                "graph": client.graph(identifier),
            }
            if args.ai and name == "local":
                result["ai"] = {
                    feature: client.project_advice(identifier, feature, share_source=True)
                    for feature in ("entrypoint", "readme")
                }
                if any(value["status"] != "ok" for value in result["ai"].values()):
                    raise ValueError("Live project advisor did not complete")
            if args.reproduce:
                with tempfile.TemporaryDirectory(prefix="known-fixture-") as directory:
                    result["reproduction"] = reproduce(bundle, Path(directory), files)
            results[name] = result
        finally:
            client.delete(identifier)
    document = {"version": "0.3.0", "status": "passed", "projects": results}
    (args.output_dir / "resolution-verification.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "projects": list(results),
                "ai": args.ai,
                "reproduced": args.reproduce,
            }
        )
    )


if __name__ == "__main__":
    main()
