from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from packaging.version import Version

from .exporting import BundleWriter
from .models import AnalysisOptions, RunSpec, UserDecisions
from .service import ReproducibilityService
from .snapshot import SnapshotBuilder


class CommandLine:
    def run(self, argv: list[str] | None = None) -> int:
        parser = argparse.ArgumentParser(prog="nishtyak")
        commands = parser.add_subparsers(dest="command", required=True)
        analyze = commands.add_parser("analyze", help="Analyze a project without executing it")
        analyze.add_argument("project", type=Path)
        analyze.add_argument("--source-root", action="append")
        analyze.add_argument("--project-root")
        analyze.add_argument("--author-environment", type=Path)
        analyze.add_argument("--python", dest="python_version")
        advise = commands.add_parser("advise", help="Request optional advice from DeepSeek")
        advise.add_argument("project", type=Path)
        advise.add_argument("--source-root", action="append")
        advise.add_argument("--project-root")
        advise.add_argument(
            "--feature", choices=("explain", "readme", "packages"), default="explain"
        )
        commands.add_parser(
            "environment", help="Collect explicitly selected local environment metadata"
        )
        conda = commands.add_parser("conda", help="Inspect a Conda environment declaration")
        conda.add_argument("project", type=Path)
        build = commands.add_parser("build", help="Prepare a reproducibility bundle")
        build.add_argument("project", type=Path)
        build.add_argument("--entry")
        build.add_argument("--output", type=Path, required=True)
        build.add_argument("--python", dest="python_version")
        build.add_argument("--description", default="")
        build.add_argument("--data", default="")
        build.add_argument("--expected", default="")
        build.add_argument("--source-root", action="append")
        build.add_argument("--project-root")
        build.add_argument("--extra", action="append", default=[])
        build.add_argument(
            "--decisions", type=Path, help="Reviewed decisions JSON from the service"
        )
        build.add_argument("--author-environment", type=Path)
        build.add_argument("--cwd", default=".")
        launch_kind = build.add_mutually_exclusive_group()
        launch_kind.add_argument("--module", action="store_true")
        launch_kind.add_argument("--callable", action="store_true")
        build.add_argument(
            "--docker",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="Include a Dockerfile (automatic for supported projects by default)",
        )
        build.add_argument(
            "--lock", action="store_true", help="Resolve wheel-only versions for an explicit target"
        )
        build.add_argument(
            "--target-platform",
            choices=(
                "linux-x86_64",
                "linux-aarch64",
                "macos-arm64",
                "macos-x86_64",
                "windows-x86_64",
            ),
        )
        build.add_argument("--exclude", action="append", default=[])
        build.add_argument("--package", action="append", default=[], metavar="MODULE=REQUIREMENT")
        arguments = parser.parse_args(argv)
        try:
            if arguments.command == "environment":
                from .environment import AuthorEnvironmentInspector

                environment = AuthorEnvironmentInspector().collect()
                print(json.dumps(environment.to_dict(), ensure_ascii=False, indent=2))
                return 0
            snapshot = SnapshotBuilder().from_directory(arguments.project)
            if arguments.command == "conda":
                try:
                    from .conda import CondaEnvironmentReader
                except ModuleNotFoundError as error:
                    if error.name != "yaml":
                        raise
                    raise ValueError(
                        "Install nishtyak-core[conda] to inspect Conda files"
                    ) from error

                environment = CondaEnvironmentReader().read(snapshot)
                print(json.dumps(environment.to_dict(), ensure_ascii=False, indent=2))
                return 0 if environment.complete else 1
            service = ReproducibilityService()
            options = AnalysisOptions(
                source_roots=tuple(getattr(arguments, "source_root", None) or (".",)),
                project_root=getattr(arguments, "project_root", None),
            )
            author_environment = None
            environment_file = getattr(arguments, "author_environment", None)
            if environment_file is not None:
                from .environment import AuthorEnvironmentSnapshot

                if environment_file.stat().st_size > 2_000_000:
                    raise ValueError("Author environment snapshot exceeds the size limit")
                author_environment = AuthorEnvironmentSnapshot.from_dict(
                    json.loads(environment_file.read_text())
                )
            selected_python = getattr(arguments, "python_version", None)
            if selected_python is None and author_environment is not None:
                selected_python = author_environment.python_version
            if selected_python is not None:
                version = Version(selected_python)
                if version.major != 3 or len(version.release) < 2:
                    raise ValueError("Choose a Python 3 minor or patch version")
                options = replace(options, python_version=f"{version.major}.{version.minor}")
            report = service.analyze(snapshot, options, author_environment)
            if arguments.command == "analyze":
                print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
                return 0 if report.status == "complete" else 1
            if arguments.command == "advise":
                from .advisors import AdvisoryService, DeepSeekClient

                result = AdvisoryService(DeepSeekClient.from_env()).advise(
                    report, arguments.feature
                )
                print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
                return 0 if result.status == "ok" else 1
            packages = {}
            if not arguments.entry and (
                arguments.module or arguments.callable or arguments.cwd != "."
            ):
                raise ValueError("Launch kind and working directory flags require --entry")
            for value in arguments.package:
                module, separator, requirement = value.partition("=")
                if not separator or not module or not requirement:
                    raise ValueError("Package overrides use MODULE=REQUIREMENT")
                packages[module] = requirement
            decisions = UserDecisions(
                analysis_id=report.analysis_id,
                packages=packages,
                excluded_files=tuple(arguments.exclude),
                run_spec=RunSpec(
                    arguments.entry,
                    kind="callable"
                    if arguments.callable
                    else "module"
                    if arguments.module
                    else "script",
                    working_directory=arguments.cwd,
                )
                if arguments.entry
                else None,
                python_version=arguments.python_version,
                description=arguments.description,
                input_description=arguments.data,
                expected_output=arguments.expected,
                data_confirmed=bool(arguments.data),
                selected_extras=tuple(arguments.extra),
            )
            if arguments.decisions is not None:
                from .decisions import DecisionsCodec

                if arguments.decisions.stat().st_size > 262144:
                    raise ValueError("Decisions document exceeds 256 KiB")
                decisions = DecisionsCodec().decode(json.loads(arguments.decisions.read_text()))
            plan = service.prepare(report, decisions)
            generated = service.render(
                plan,
                include_dockerfile=arguments.docker,
                snapshot=snapshot,
                target_platform=arguments.target_platform,
            )
            if arguments.lock:
                from .locking import LockExporter, ResolutionTarget, VersionResolver

                if plan.python_version is None:
                    raise ValueError("Supply --python or an author environment before locking")
                if arguments.target_platform is None:
                    raise ValueError("Supply --target-platform for the dependency lock")
                target = ResolutionTarget(plan.python_version, arguments.target_platform)
                lock = VersionResolver().resolve(plan, target)
                generated = LockExporter().attach(generated, plan, lock, snapshot=snapshot)
            elif arguments.target_platform is not None:
                raise ValueError("--target-platform requires --lock")
            artifact = BundleWriter().write(
                snapshot,
                plan,
                generated,
                arguments.output,
            )
            print(json.dumps(asdict(artifact), ensure_ascii=False, indent=2))
            return 0
        except (ValueError, OSError) as error:
            print(f"nishtyak: {error}", file=sys.stderr)
            return 2


def main() -> None:
    raise SystemExit(CommandLine().run())
