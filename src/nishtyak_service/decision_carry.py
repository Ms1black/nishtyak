from __future__ import annotations

from dataclasses import asdict

from nishtyak_core.decisions import DecisionsCodec
from nishtyak_core.models import AnalysisReport
from nishtyak_core.planning import DecisionError, PackagePlanner


class SourceDecisionCarry:
    """Retain compatible author choices when the same project receives new files."""

    @staticmethod
    def pending_choices(saved: dict, *, source_changed: bool = False) -> dict:
        while "_source_carry" in saved or "_analysis_carry" in saved:
            if "_source_carry" in saved:
                source_changed = True
                saved = saved["_source_carry"]
            else:
                saved = saved["_analysis_carry"]
        return {**saved, "data_confirmed": False} if source_changed else saved

    def restore(self, report: AnalysisReport, saved: dict) -> dict:
        result = {"analysis_id": report.analysis_id}
        modules = {item.module for item in report.dependencies}
        files = {item.path for item in report.files}
        declarations = {item.id for item in report.declarations}
        for field in ("description", "input_description", "expected_output", "python_version"):
            if saved.get(field) is not None:
                self._retain(report, result, field, saved[field])
        for field, allowed in (
            ("packages", modules),
            ("declaration_actions", declarations),
        ):
            for key, value in saved.get(field, {}).items():
                if key in allowed:
                    mapping = {**result.get(field, {}), key: value}
                    self._retain(report, result, field, mapping)
        for field, allowed in (
            ("excluded_modules", modules),
            ("excluded_files", files),
            ("selected_extras", set(report.optional_groups)),
        ):
            self._retain(
                report, result, field, [item for item in saved.get(field, []) if item in allowed]
            )
        for key, value in saved.get("versions", {}).items():
            self._retain(report, result, "versions", {**result.get("versions", {}), key: value})
        run = saved.get("run_spec")
        if isinstance(run, dict):
            target = run.get("target", "")
            cwd = run.get("working_directory", ".")
            path = target if cwd == "." else cwd + "/" + target
            if run.get("kind", "script") != "script" or path in files:
                self._retain(report, result, "run_spec", run)
        return asdict(DecisionsCodec().decode(result))

    @staticmethod
    def _retain(report, result, field, value):
        candidate = {**result, field: value}
        try:
            decisions = DecisionsCodec().decode(candidate)
            plan = PackagePlanner().prepare(report, decisions)
            if field == "run_spec" and any(
                item.severity == "error" and item.code.startswith("RUN_") for item in plan.findings
            ):
                return
        except (DecisionError, ValueError, TypeError):
            return
        result[field] = value
