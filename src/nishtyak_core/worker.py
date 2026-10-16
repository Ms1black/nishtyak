from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

from .archives import ArchiveError, ArchiveReader
from .jobs import JobPolicy
from .models import AnalysisOptions
from .report_codec import AnalysisReportCodec, InvalidWorkerReport, bounded_json
from .service import ReproducibilityService


class StaticAnalysisWorker:
    def run(
        self,
        archive_path: str,
        output_path: str,
        options_path: Path | None = None,
        policy: JobPolicy | None = None,
        options_json: str | None = None,
    ) -> int:
        policy = policy or JobPolicy()
        try:
            self._set_limits(policy)
        except (OSError, ValueError, ImportError):
            return self._write(output_path, policy, "failed", "LIMITS_UNAVAILABLE")

        try:
            options = AnalysisOptions()
            if options_path is not None and options_json is not None:
                return self._write(output_path, policy, "failed", "OPTIONS_INVALID")
            if options_json is not None:
                if len(options_json.encode("utf-8")) > 64 * 1024:
                    return self._write(output_path, policy, "failed", "OPTIONS_INVALID")
                options = AnalysisReportCodec().decode_options(json.loads(options_json))
            elif options_path is not None:
                with options_path.open("rb") as stream:
                    raw = stream.read(64 * 1024 + 1)
                if len(raw) > 64 * 1024:
                    return self._write(output_path, policy, "failed", "OPTIONS_INVALID")
                options = AnalysisReportCodec().decode_options(json.loads(raw))
        except (OSError, TypeError, ValueError, RecursionError):
            return self._write(output_path, policy, "failed", "OPTIONS_INVALID")

        try:
            reader = ArchiveReader()
            if archive_path == "-":
                content = sys.stdin.buffer.read(reader.policy.max_archive_bytes + 1)
                if len(content) > reader.policy.max_archive_bytes:
                    return self._write(output_path, policy, "failed", "ARCHIVE_LIMIT")
                snapshot = reader.read(io.BytesIO(content))
            else:
                snapshot = reader.from_path(archive_path)
            report = ReproducibilityService().analyze(snapshot, options)
            return self._write(output_path, policy, "succeeded", report=report)
        except ArchiveError as error:
            return self._write(output_path, policy, "failed", error.code)
        except MemoryError:
            return self._write(output_path, policy, "resource_limited", "MEMORY_LIMIT")
        except OSError:
            return self._write(output_path, policy, "failed", "ARCHIVE_UNAVAILABLE")
        except (ValueError, TypeError, RecursionError):
            return self._write(output_path, policy, "failed", "ANALYSIS_INVALID")
        except Exception:
            return self._write(output_path, policy, "failed", "ANALYSIS_FAILED")

    @staticmethod
    def _set_limits(policy: JobPolicy) -> None:
        if os.name != "posix":
            raise OSError("POSIX limits required")
        import resource

        limits = [
            (resource.RLIMIT_CPU, policy.cpu_time_seconds),
            (resource.RLIMIT_FSIZE, policy.max_result_bytes),
            (resource.RLIMIT_NOFILE, policy.max_open_files),
            (resource.RLIMIT_CORE, 0),
        ]
        if sys.platform.startswith("linux"):
            limits.append((resource.RLIMIT_AS, policy.memory_bytes))
        for kind, configured in limits:
            _, inherited_hard = resource.getrlimit(kind)
            limit = (
                configured
                if inherited_hard == resource.RLIM_INFINITY
                else min(configured, inherited_hard)
            )
            resource.setrlimit(kind, (limit, limit))

    @staticmethod
    def _write(output_path: str, policy: JobPolicy, status: str, code=None, report=None) -> int:
        try:
            document = {
                "status": status,
                "code": code,
                "report": AnalysisReportCodec().encode(report) if report is not None else None,
            }
            try:
                output = bounded_json(document, policy.max_result_bytes)
            except InvalidWorkerReport:
                output = bounded_json(
                    {"status": "resource_limited", "code": "RESULT_LIMIT", "report": None},
                    policy.max_result_bytes,
                )
            if output_path == "-":
                sys.stdout.buffer.write(output)
                sys.stdout.buffer.flush()
            else:
                with Path(output_path).open("xb") as stream:
                    stream.write(output)
            return 0
        except (OSError, ValueError, MemoryError):
            return 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--output", required=True)
    options_group = parser.add_mutually_exclusive_group()
    options_group.add_argument("--options", type=Path)
    options_group.add_argument("--options-json")
    parser.add_argument("--memory-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--cpu-seconds", type=int, default=15)
    parser.add_argument("--max-report-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--max-open-files", type=int, default=64)
    options = parser.parse_args()
    try:
        policy = JobPolicy(
            memory_bytes=options.memory_bytes,
            cpu_time_seconds=options.cpu_seconds,
            max_result_bytes=options.max_report_bytes,
            max_open_files=options.max_open_files,
        )
    except ValueError:
        parser.error("Worker limits must be positive integers")
    raise SystemExit(
        StaticAnalysisWorker().run(
            options.archive, options.output, options.options, policy, options.options_json
        )
    )


if __name__ == "__main__":
    main()
