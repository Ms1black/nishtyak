from __future__ import annotations

import json
import posixpath
import shlex

from packaging.requirements import InvalidRequirement, Requirement

from .launches import LaunchCandidate, LaunchDetector
from .layout import ProjectLauncher
from .models import Finding, GeneratedFile, GeneratedFiles, PackagePlan, RunSpec
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
        if ProjectLauncher.required(plan):
            files = (*files, ProjectLauncher().generate(plan))
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
            "This guide records static analysis and any supplied author decisions. "
            "The uploaded code was not executed. Successful reproduction has not been verified.",
            "",
            "## Experiment",
            "",
            plan.description or "The author has not described the experiment.",
        ]
        if plan.description_origin == "automatic":
            lines.extend(
                [
                    "",
                    "The project description was generated automatically from filtered source "
                    "excerpts. It describes the code; it is not an author confirmation or "
                    "an execution result.",
                ]
            )
        lines.extend(["", "## Python", ""])
        if plan.python_version:
            if plan.decisions is not None and plan.decisions.python_version is None:
                lines.append(
                    f"Observed author-environment Python version: `{plan.python_version}`."
                )
            else:
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
                "`requirements.txt` retains declared constraints and any supplied decisions. "
                "Unpinned packages are installable suggestions; their original tested versions "
                "cannot be recovered from imports alone.",
                "",
                "## Launch",
                "",
            ]
        )
        if plan.run_spec:
            run = plan.run_spec
            if plan.run_spec_origin == "automatic":
                lines.extend(
                    [
                        "The assistant selected the following command from existing static "
                        "launch candidates after reading project evidence. It has not been "
                        "confirmed by the author or executed; arguments may still be required.",
                        "",
                    ]
                )
            elif plan.run_spec_origin == "inferred":
                lines.extend(
                    [
                        "The following command was inferred from project structure and static "
                        "facts. It has not been confirmed by the author or executed; "
                        "arguments may still be required.",
                        "",
                    ]
                )
            elif plan.run_spec_origin == "author":
                lines.extend(["The author supplied this launch configuration.", ""])
            lines.extend(
                [
                    "From the extracted package root with the environment activated:",
                    "",
                    "```sh",
                ]
            )
            if ProjectLauncher.required(plan):
                lines.append("python run_project.py")
            else:
                lines.append("cd project")
                if run.working_directory != ".":
                    lines.append("cd " + shlex.quote(run.working_directory))
                command = ["python"]
                if run.kind == "module":
                    command.append("-m")
                target = run.target
                if run.kind == "script" and target.startswith("-"):
                    target = "./" + target
                command.extend([target, *run.args])
                lines.append(shlex.join(command))
            lines.extend(["```", ""])
            if ProjectLauncher.required(plan):
                lines.extend(
                    [
                        "`run_project.py` applies the recorded working directory, source roots, "
                        "entry point and arguments. It uses the extracted files directly; "
                        "no project build hooks are needed.",
                        "",
                    ]
                )
        else:
            assessment = LaunchDetector().detect(plan.report, plan.files)
            lines.extend(
                [
                    "A unique launch command could not be established statically. "
                    "The dependency and source package remains available for review.",
                    "",
                ]
            )
            for candidate in assessment.candidates[:10]:
                lines.extend(self._candidate_commands(plan, candidate))
            if len(assessment.candidates) > 10:
                lines.extend(["Additional source files may have their own launch scenarios.", ""])

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
        review_findings = self._review_findings(plan)
        if review_findings:
            for finding in review_findings:
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
                *self._file_tree(plan),
            ]
        )
        if plan.report.layout:
            layout = plan.report.layout
            lines.extend(
                [
                    f"Selected project root inside `project/`: `{layout.root}`.",
                    "Import roots inside `project/`: "
                    + ", ".join(f"`{root}`" for root in layout.source_roots)
                    + ".",
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

    @staticmethod
    def _review_findings(plan: PackagePlan) -> tuple[Finding, ...]:
        """Consolidate human-facing limits without discarding raw diagnostic evidence."""
        generic_data = {"DATA_DEPENDENCIES_UNVERIFIED", "DATA_NOT_CONFIRMED"}
        detailed_data = {
            "INPUT_PATH_UNVERIFIED",
            "EXTERNAL_INPUT_REFERENCE",
            "MISSING_DATA",
            "MISSING_INPUT_FILE",
        }
        versions = {"VERSION_UNKNOWN", "VERSION_NOT_PINNED"}
        has_data_details = any(item.code in detailed_data for item in plan.findings)
        result: list[Finding] = []
        seen: set[object] = set()
        for finding in plan.findings:
            if finding.severity != "error":
                if finding.code in {"README_MISSING", "DATA_FILES_SUMMARY"}:
                    continue
                if finding.code in generic_data:
                    if has_data_details:
                        continue
                    finding = Finding(
                        "DATA_UNVERIFIED",
                        "Completeness of required input data and configuration is not confirmed.",
                    )
                elif finding.code in versions:
                    names = []
                    for raw in plan.requirements:
                        try:
                            requirement = Requirement(raw)
                        except InvalidRequirement:
                            continue
                        if not any(
                            spec.operator in {"==", "==="} and "*" not in spec.version
                            for spec in requirement.specifier
                        ):
                            names.append(requirement.name)
                    packages = ": " + ", ".join(sorted(set(names))) if names else ""
                    finding = Finding(
                        "VERSIONS_UNVERIFIED",
                        "Exact tested library versions are not supplied"
                        + packages
                        + ". Known constraints remain in requirements.txt.",
                    )
            key = (finding.code, finding.message, finding.path, finding.line, finding.severity)
            if key not in seen:
                seen.add(key)
                result.append(finding)
        return tuple(result)

    @staticmethod
    def _file_tree(plan: PackagePlan) -> list[str]:
        lines = ["```text", "README.md", "requirements.txt", "analysis-report.json"]
        if ProjectLauncher.required(plan):
            lines.append("run_project.py")
        lines.append("project/")
        for file in plan.files[:80]:
            lines.append("  " + file.path.replace("`", "\\`"))
        if len(plan.files) > 80:
            lines.append(f"  ... {len(plan.files) - 80} additional files")
        lines.extend(["```", ""])
        return lines

    @staticmethod
    def _candidate_commands(plan: PackagePlan, candidate: LaunchCandidate) -> list[str]:
        run = candidate.run_spec
        lines = [f"Candidate: {candidate.reason}", "", "```sh", "cd project"]
        if run.working_directory != ".":
            lines.append("cd " + shlex.quote(run.working_directory))
        roots = (
            plan.report.layout.source_roots
            if plan.report.layout
            else plan.report.options.source_roots
        )
        relative_roots = [posixpath.relpath(root, run.working_directory) for root in roots]
        if run.kind == "script" and set(relative_roots) == {posixpath.dirname(run.target) or "."}:
            target = "./" + run.target if run.target.startswith("-") else run.target
            command = ["python", target]
        else:
            code = DocumentRenderer._candidate_python(run, relative_roots)
            command = ["python", "-c", code]
        lines.extend([shlex.join(command), "```", ""])
        return lines

    @staticmethod
    def _candidate_python(run: RunSpec, roots: list[str]) -> str:
        statements = [
            "import importlib, runpy, sys",
            f"sys.path[:0] = {roots!r}",
            f"sys.argv = [{run.target!r}]",
        ]
        if run.kind == "callable":
            module, attribute = run.target.split(":", 1)
            statements.append(f"entry = importlib.import_module({module!r})")
            for part in attribute.split("."):
                statements.append(f"entry = getattr(entry, {part!r})")
            statements.append("raise SystemExit(entry())")
        elif run.kind == "module":
            statements.append(
                f"runpy.run_module({run.target!r}, run_name='__main__', alter_sys=True)"
            )
        else:
            parent = posixpath.dirname(run.target) or "."
            statements.extend(
                [
                    f"sys.path.insert(0, {parent!r})",
                    f"runpy.run_path({run.target!r}, run_name='__main__')",
                ]
            )
        return "; ".join(statements)
