from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from packaging.requirements import Requirement
from packaging.version import Version

from .layout import LayoutDetector, ProjectLauncher
from .models import GeneratedFile, PackagePlan


class DockerfileGenerator:
    """Linux template for supported pip projects; never builds or runs an image."""

    def skip_reason(self, plan: PackagePlan, *, target_platform: str | None = None) -> str | None:
        try:
            self._validate(plan, target_platform=target_platform)
        except ValueError as error:
            return str(error)
        return None

    @staticmethod
    def _target_platform(plan: PackagePlan) -> str | None:
        target = (plan.automatic_resolution or {}).get("target") or {}
        return plan.report.options.target_platform or target.get("platform")

    def _validate(self, plan: PackagePlan, *, target_platform: str | None = None) -> None:
        if plan.state != "ready_to_verify":
            raise ValueError("Dockerfile не добавлен: пакет ещё содержит нерешённые вопросы.")
        if plan.python_version is None or plan.run_spec is None:
            raise ValueError(
                "Dockerfile не добавлен: не определены версия Python или команда запуска."
            )
        platform = target_platform or self._target_platform(plan)
        if platform and not platform.startswith("linux-"):
            raise ValueError("Dockerfile не добавлен: выбранная среда не Linux.")
        version = Version(plan.python_version)
        if version.major != 3 or len(version.release) < 2 or version.is_prerelease or version.local:
            raise ValueError("Dockerfile требует стабильную версию Python 3 с номером minor.")
        if any(Requirement(value).url for value in plan.requirements):
            raise ValueError("Dockerfile не добавлен: прямые URL зависимостей не поддерживаются.")
        root = plan.report.layout.root if plan.report.layout else "."
        conda_paths = {
            LayoutDetector.join(root, name) for name in ("environment.yml", "environment.yaml")
        }
        if any(file.path in conda_paths for file in plan.files):
            raise ValueError(
                "Dockerfile не добавлен: для environment.yml нужен отдельный профиль Conda."
            )

        run = plan.run_spec
        cwd = PurePosixPath(run.working_directory)
        if (
            cwd.is_absolute()
            or ".." in cwd.parts
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", run.working_directory)
        ):
            raise ValueError(
                "Dockerfile не добавлен: рабочий каталог должен быть простым относительным путём."
            )
        if run.kind not in {"script", "module", "callable"}:
            raise ValueError("Dockerfile не добавлен: этот способ запуска не поддерживается.")

    def generate(self, plan: PackagePlan, *, target_platform: str | None = None) -> GeneratedFile:
        self._validate(plan, target_platform=target_platform)
        version = Version(plan.python_version)
        run = plan.run_spec
        cwd = PurePosixPath(run.working_directory)
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
        platform = {"linux-x86_64": "linux/amd64", "linux-aarch64": "linux/arm64"}.get(
            target_platform or self._target_platform(plan)
        )
        base = f"FROM --platform={platform} python" if platform else "FROM python"
        lines = [
            "# Unverified template: the image has not been built or run.",
            "# System libraries, GPU drivers and external services require author configuration.",
            f"{base}:{version}-slim",
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
