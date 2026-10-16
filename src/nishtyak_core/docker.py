from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from packaging.requirements import Requirement
from packaging.version import Version

from .layout import LayoutDetector, ProjectLauncher
from .models import GeneratedFile, PackagePlan


class DockerfileGenerator:
    """Opt-in template for simple pip projects; never builds or runs an image."""

    def generate(self, plan: PackagePlan) -> GeneratedFile:
        if plan.state != "ready_to_verify":
            raise ValueError("Docker generation requires a package ready for verification")
        if plan.python_version is None or plan.run_spec is None:
            raise ValueError(
                "Docker generation requires a selected Python version and launch target"
            )
        version = Version(plan.python_version)
        if version.major != 3 or len(version.release) < 2 or version.is_prerelease or version.local:
            raise ValueError("Select a stable Python 3 minor or patch version")
        if any(Requirement(value).url for value in plan.requirements):
            raise ValueError("Docker generation supports named pip dependencies only")
        root = plan.report.layout.root if plan.report.layout else "."
        conda_paths = {
            LayoutDetector.join(root, name) for name in ("environment.yml", "environment.yaml")
        }
        if any(file.path in conda_paths for file in plan.report.files):
            raise ValueError("Conda projects require a separate container profile")

        run = plan.run_spec
        cwd = PurePosixPath(run.working_directory)
        if (
            cwd.is_absolute()
            or ".." in cwd.parts
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", run.working_directory)
        ):
            raise ValueError("Docker generation requires a simple relative working directory")
        if run.kind not in {"script", "module", "callable"}:
            raise ValueError("Docker generation supports script, module or callable launch targets")

        helper = ProjectLauncher.required(plan)
        if helper:
            command = ["python", "/workspace/run_project.py"]
        else:
            command = ["python"]
            if run.kind == "module":
                command.append("-m")
            target = run.target
            if run.kind == "script" and target.startswith("-"):
                target = "./" + target
            command.extend([target, *run.args])
        workdir = PurePosixPath("/workspace/project") / cwd
        lines = [
            "# Unverified template: the image has not been built or run.",
            "# System libraries, GPU drivers and external services require author configuration.",
            f"FROM python:{version}-slim",
            "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1",
            "WORKDIR /workspace",
            "COPY requirements.txt ./requirements.txt",
            "RUN python -m pip install --no-cache-dir -r requirements.txt",
            "COPY project/ ./project/",
            *(["COPY run_project.py ./run_project.py"] if helper else []),
            f"WORKDIR {workdir}",
            "CMD " + json.dumps(command, ensure_ascii=False),
        ]
        return GeneratedFile("Dockerfile", ("\n".join(lines) + "\n").encode("utf-8"))
