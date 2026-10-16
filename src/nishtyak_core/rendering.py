from __future__ import annotations

import json
import shlex

from .models import GeneratedFile, GeneratedFiles, PackagePlan
from .security import SecretScanner


class DocumentRenderer:
    """Render deterministic documents using only the reviewed package plan."""

    def render(self, plan: PackagePlan) -> GeneratedFiles:
        if plan.state == "blocked":
            raise ValueError("Resolve blocking findings before generating package files")
        scanner = SecretScanner()
        files = (
            GeneratedFile("README.md", scanner.redact(self._readme(plan)).encode("utf-8")),
            GeneratedFile("requirements.txt", self._requirements(plan).encode("utf-8")),
            GeneratedFile(
                "analysis-report.json",
                (
                    json.dumps(
                        scanner.redact_value(plan.to_dict()),
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            ),
        )
        return GeneratedFiles(files)

    @staticmethod
    def _requirements(plan: PackagePlan) -> str:
        lines = [
            "# Generated from static analysis and author decisions.",
            f"# Package state: {plan.state}. Installation and execution have not been verified.",
            "# An unpinned dependency does not identify its tested version.",
            *plan.requirements,
        ]
        return "\n".join(lines) + "\n"

    def _readme(self, plan: PackagePlan) -> str:
        lines = [
            "# Project reproduction guide",
            "",
            f"Package state: **{plan.state}**.",
            "",
            "This guide was generated from static analysis and author decisions. "
            "The uploaded code was not executed. Successful reproduction has not been verified.",
            "",
            "## Experiment",
            "",
            plan.description or "The author has not described the experiment.",
            "",
            "## Python",
            "",
        ]
        if plan.python_version:
            lines.append(f"Author-selected Python version: `{plan.python_version}`.")
        else:
            lines.append("The author has not supplied a tested Python version.")
        if plan.report.python_requires:
            lines.append(f"Declared Python constraint: `{plan.report.python_requires}`.")
        lines.extend(
            [
                f"Imports were classified for Python `{plan.report.options.python_version}`.",
                "",
                "## Installation",
                "",
                "From the extracted package root, create an environment and install requirements. "
                "Commands below use a POSIX shell; on Windows activate `.venv\\Scripts\\activate`.",
                "",
                "```sh",
                "python -m venv .venv",
                ". .venv/bin/activate",
                "python -m pip install -r requirements.txt",
                "```",
                "",
                "## Launch",
                "",
            ]
        )
        if plan.run_spec:
            run = plan.run_spec
            lines.extend(
                [
                    "From the extracted package root with the environment activated:",
                    "",
                    "```sh",
                    "cd project",
                ]
            )
            if run.working_directory != ".":
                lines.append("cd " + shlex.quote(run.working_directory))
            command = ["python"]
            if run.kind == "module":
                command.append("-m")
            target = run.target
            if run.kind == "script" and target.startswith("-"):
                target = "./" + target
            command.extend([target, *run.args])
            lines.extend([shlex.join(command), "```", ""])
        else:
            lines.extend(
                [
                    "A launch target has not been selected. "
                    "Ask the author to provide it before running the experiment.",
                    "",
                ]
            )

        lines.extend(
            [
                "## Input data",
                "",
                plan.input_description or "The author has not described the input data.",
                "",
                "## Expected result",
                "",
                plan.expected_output or "The author has not supplied an expected result.",
                "",
                "## Review before reproduction",
                "",
            ]
        )
        if plan.findings:
            for finding in plan.findings:
                location = ""
                if finding.path:
                    location = f" ({finding.path}"
                    if finding.line is not None:
                        location += f":{finding.line}"
                    location += ")"
                message = " ".join(finding.message.splitlines())
                lines.append(f"- [{finding.code}]{location} {message}")
        else:
            lines.append(
                "No open static-analysis findings. A clean-environment run is still required."
            )
        lines.extend(
            [
                "",
                "## Package contents",
                "",
                "Original files and relative paths are retained under `project/`. "
                "An existing project README remains there. "
                "`analysis-report.json` records the analyzed facts and this package plan.",
                "",
            ]
        )
        if plan.excluded_files:
            lines.extend(["Excluded files:", ""])
            lines.extend(f"- {path}" for path in plan.excluded_files)
            lines.append("")
        lines.extend(
            [
                "## Verification",
                "",
                "On a clean machine or environment, follow this guide, "
                "provide the stated input data, "
                "and compare the result with the author's expected result. "
                "Report the actual Python and package versions used.",
                "",
            ]
        )
        return "\n".join(lines)
