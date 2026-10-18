from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

import yaml
from packaging.requirements import InvalidRequirement, Requirement

from .models import Finding, ProjectSnapshot
from .redaction import TextRedactor


@dataclass(frozen=True)
class CondaEnvironment:
    path: str | None
    name: str | None = None
    channels: tuple[str, ...] = ()
    python: str | None = None
    conda_dependencies: tuple[str, ...] = ()
    pip_dependencies: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    complete: bool = True

    def to_dict(self) -> dict[str, Any]:
        return TextRedactor().redact_value(asdict(self))


class CondaEnvironmentReader:
    NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
    SPEC = re.compile(r"(?:[A-Za-z0-9_.-]+::)?[A-Za-z0-9_.-]+[A-Za-z0-9_.<>=!~*+, -]*\Z")

    def __init__(self, max_bytes: int = 1_000_000) -> None:
        self.max_bytes = max_bytes

    def read(self, snapshot: ProjectSnapshot) -> CondaEnvironment:
        files = [snapshot.get(name) for name in ("environment.yml", "environment.yaml")]
        files = [source for source in files if source is not None]
        if not files:
            return CondaEnvironment(None)
        source = files[0]
        findings = []

        def problem(code: str, message: str) -> None:
            findings.append(Finding(code, message, source.path))

        if len(files) > 1:
            problem(
                "CONDA_FILES_AMBIGUOUS", "Multiple Conda manifests exist; first file was inspected."
            )
        if len(source.content) > self.max_bytes:
            problem("CONDA_TOO_LARGE", "Conda manifest exceeds the parsing limit.")
            return CondaEnvironment(source.path, findings=tuple(findings), complete=False)
        try:
            text = source.content.decode("utf-8-sig")
            depth = 0
            for count, event in enumerate(yaml.parse(text), 1):
                if isinstance(event, yaml.AliasEvent):
                    raise ValueError("YAML aliases are not supported")
                if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                    depth += 1
                elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                    depth -= 1
                if depth > 25 or count > 20_000:
                    raise ValueError("YAML structure exceeds limits")
            data = yaml.safe_load(text)
        except (yaml.YAMLError, ValueError, UnicodeError, RecursionError):
            problem("CONDA_PARSE_ERROR", "Conda YAML could not be parsed within safe limits.")
            return CondaEnvironment(source.path, findings=tuple(findings), complete=False)
        if not isinstance(data, dict):
            problem("CONDA_SCHEMA_INVALID", "Conda manifest must contain a mapping.")
            return CondaEnvironment(source.path, findings=tuple(findings), complete=False)
        name = data.get("name")
        if name is not None and (not isinstance(name, str) or not self.NAME.fullmatch(name)):
            name = None
            problem("CONDA_NAME_INVALID", "Environment name requires manual review.")
        channels = []
        raw_channels = data.get("channels", [])
        if not isinstance(raw_channels, list):
            problem("CONDA_SCHEMA_INVALID", "Conda channels must be an array.")
            raw_channels = []
        for channel in raw_channels:
            if isinstance(channel, str) and self.NAME.fullmatch(channel):
                channels.append(channel)
            else:
                problem(
                    "CONDA_CHANNEL_UNSUPPORTED", "Channel URL or nonstandard channel was omitted."
                )
        conda_dependencies = []
        pip_dependencies = []
        python_specs = []
        raw_dependencies = data.get("dependencies", [])
        if not isinstance(raw_dependencies, list):
            problem("CONDA_SCHEMA_INVALID", "Conda dependencies must be an array.")
            raw_dependencies = []
        for dependency in raw_dependencies:
            if isinstance(dependency, str):
                if not self.SPEC.fullmatch(dependency):
                    problem("CONDA_SPEC_UNSUPPORTED", "Conda dependency requires manual review.")
                    continue
                conda_dependencies.append(dependency)
                if re.match(r"^(?:[A-Za-z0-9_.-]+::)?python(?:[ =<>!~]|$)", dependency):
                    python_specs.append(dependency)
            elif isinstance(dependency, dict) and set(dependency) == {"pip"}:
                values = dependency["pip"]
                if not isinstance(values, list):
                    problem("CONDA_SCHEMA_INVALID", "Pip dependencies must be an array.")
                    continue
                for value in values:
                    try:
                        if not isinstance(value, str):
                            raise InvalidRequirement("Invalid value")
                        requirement = Requirement(value)
                        if requirement.url:
                            raise InvalidRequirement("Direct URL")
                    except InvalidRequirement:
                        problem(
                            "CONDA_PIP_UNSUPPORTED", "Pip dependency was omitted for manual review."
                        )
                    else:
                        pip_dependencies.append(str(requirement))
            else:
                problem("CONDA_SCHEMA_INVALID", "Unsupported dependency structure was omitted.")
        if len(set(python_specs)) > 1:
            problem("CONDA_PYTHON_CONFLICT", "Multiple Python constraints need reconciliation.")
        if "prefix" in data:
            findings.append(
                Finding(
                    "CONDA_PREFIX_IGNORED",
                    "Machine-specific environment prefix was omitted.",
                    source.path,
                    severity="info",
                )
            )
        return CondaEnvironment(
            source.path,
            name,
            tuple(dict.fromkeys(channels)),
            python_specs[0] if len(set(python_specs)) == 1 else None,
            tuple(dict.fromkeys(conda_dependencies)),
            tuple(dict.fromkeys(pip_dependencies)),
            tuple({finding.id: finding for finding in findings}.values()),
            not any(finding.severity != "info" for finding in findings),
        )
