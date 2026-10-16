from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import DeclarationDecision, RunSpec, UserDecisions
from .report_codec import bounded_json


class DecisionsCodec:
    """Read a portable decision document without importing the HTTP service."""

    def encode(self, decisions: UserDecisions) -> dict[str, Any]:
        value = asdict(decisions)
        bounded_json(value, 262144)
        return value

    def decode(self, value: Any) -> UserDecisions:
        if not isinstance(value, dict) or set(value) - set(UserDecisions.__dataclass_fields__):
            raise ValueError("Unknown decisions fields")
        bounded_json(value, 262144)
        if not isinstance(value.get("analysis_id"), str):
            raise ValueError("Decisions require an analysis ID")
        data = dict(value)
        for name in ("packages", "versions"):
            mapping = data.get(name, {})
            if (
                not isinstance(mapping, dict)
                or len(mapping) > 1000
                or not all(
                    isinstance(key, str)
                    and isinstance(item, str)
                    and len(key) <= 2048
                    and len(item) <= 2048
                    for key, item in mapping.items()
                )
            ):
                raise ValueError(f"Invalid {name} decisions")
        for name in ("excluded_modules", "excluded_files", "selected_extras"):
            items = data.get(name, ())
            if (
                not isinstance(items, (list, tuple))
                or len(items) > 1000
                or not all(isinstance(item, str) and len(item) <= 2048 for item in items)
            ):
                raise ValueError(f"Invalid {name} decisions")
            data[name] = tuple(items)
        for name in ("description", "input_description", "expected_output"):
            if not isinstance(data.get(name, ""), str) or len(data.get(name, "")) > 8192:
                raise ValueError(f"Invalid {name}")
        if type(data.get("data_confirmed", False)) is not bool:
            raise ValueError("Invalid data confirmation")
        if data.get("python_version") is not None and (
            not isinstance(data["python_version"], str) or len(data["python_version"]) > 16
        ):
            raise ValueError("Invalid Python version")
        run = data.get("run_spec")
        if run is not None:
            if not isinstance(run, dict) or set(run) - set(RunSpec.__dataclass_fields__):
                raise ValueError("Invalid launch fields")
            if not isinstance(run.get("target"), str) or len(run["target"]) > 1024:
                raise ValueError("Invalid launch target")
            if run.get("kind", "script") not in {"script", "module", "callable"}:
                raise ValueError("Invalid launch kind")
            if not isinstance(run.get("working_directory", "."), str):
                raise ValueError("Invalid working directory")
            args = run.get("args", ())
            if (
                not isinstance(args, (tuple, list))
                or len(args) > 64
                or not all(isinstance(arg, str) and len(arg) <= 1024 for arg in args)
            ):
                raise ValueError("Invalid launch arguments")
            data["run_spec"] = RunSpec(**{**run, "args": tuple(args)})
        actions = data.get("declaration_actions", {})
        if not isinstance(actions, dict) or len(actions) > 10000:
            raise ValueError("Invalid declaration actions")
        parsed = {}
        for identifier, action in actions.items():
            if not isinstance(identifier, str) or not isinstance(action, dict):
                raise ValueError("Invalid declaration action")
            if set(action) - {"action", "requirement"}:
                raise ValueError("Unknown declaration action fields")
            if action.get("action", "keep") not in {"keep", "replace", "exclude"}:
                raise ValueError("Invalid declaration action")
            requirement = action.get("requirement")
            if requirement is not None and (
                not isinstance(requirement, str) or len(requirement) > 2048
            ):
                raise ValueError("Invalid replacement requirement")
            parsed[identifier] = DeclarationDecision(**action)
        data["declaration_actions"] = parsed
        return UserDecisions(**data)
