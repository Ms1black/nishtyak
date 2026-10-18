from __future__ import annotations

import posixpath
import shlex

from packaging.requirements import InvalidRequirement, Requirement

from .environment_export import EnvironmentExportGenerator
from .installation import EnvironmentInstructions
from .launches import LaunchCandidate, LaunchDetector
from .layout import ProjectLauncher
from .messages import RussianFindingFormatter
from .models import Finding, GeneratedFile, GeneratedFiles, PackagePlan, ProjectSnapshot, RunSpec
from .security import SecretScanner


class DocumentRenderer:
    """Render deterministic documents using only the reviewed package plan."""

    def render(
        self,
        plan: PackagePlan,
        *,
        extra_files: tuple[GeneratedFile, ...] = (),
        snapshot: ProjectSnapshot | None = None,
        target: dict | None = None,
    ) -> GeneratedFiles:
        if plan.state == "blocked":
            raise ValueError("Resolve blocking findings before generating package files")
        files = (
            GeneratedFile("requirements.txt", self._requirements(plan).encode("utf-8")),
            *EnvironmentExportGenerator().generate(plan),
        )
        if ProjectLauncher.required(plan):
            files = (*files, ProjectLauncher().generate(plan))
        files = (*files, *extra_files)
        readme = self.readme(
            plan,
            generated_paths=tuple(file.path for file in files),
            snapshot=snapshot,
            target=target,
        )
        return GeneratedFiles((GeneratedFile("README.md", readme.encode("utf-8")), *files))

    @staticmethod
    def _requirements(plan: PackagePlan) -> str:
        lines = [
            "# Подготовлено по статическому анализу и сведениям автора.",
            "# Установка и запуск требуют проверки в отдельной среде.",
            "# Незафиксированная версия не восстанавливает исходную среду автора.",
            *plan.requirements,
        ]
        return "\n".join(lines) + "\n"

    def readme(
        self,
        plan: PackagePlan,
        *,
        target: dict | None = None,
        locked: bool = False,
        generated_paths: tuple[str, ...] = (),
        snapshot: ProjectSnapshot | None = None,
        locked_packages: tuple[str, ...] = (),
    ) -> str:
        target = (
            target
            or (plan.automatic_resolution or {}).get("target")
            or (
                {
                    "python_version": plan.report.options.target_python_version
                    or plan.report.options.python_version,
                    "platform": plan.report.options.target_platform,
                }
                if plan.report.options.target_platform
                else {}
            )
        )
        python_version = (
            target.get("python_version")
            or plan.python_version
            or plan.report.options.python_version
        )
        platform = target.get("platform")
        instructions = EnvironmentInstructions(
            plan,
            python_version,
            platform,
            locked=locked,
            generated_paths=generated_paths,
            snapshot=snapshot,
        )
        state = (plan.automation or {}).get("phase", plan.state)
        state_label = "Черновик с ограничениями" if state == "draft" else "Пакет для проверки"
        lines = [
            "# Как повторить эксперимент",
            "",
            f"**{state_label}.**",
            "",
            "Инструкция подготовлена по статическому анализу и сведениям автора. "
            "Код на сервере не выполнялся; результат нужно проверить в чистой среде.",
            "",
            "## Что делает проект",
            "",
            plan.description or "Назначение эксперимента не описано автором.",
        ]
        if plan.description_origin == "automatic":
            lines.extend(
                [
                    "",
                    "Описание составлено автоматически по отфильтрованным "
                    "фрагментам исходников; это не подтверждение автора или "
                    "результата запуска.",
                ]
            )
        lines.extend(["", "## Целевая среда", ""])
        if plan.python_version:
            if plan.report.options.target_python_version or plan.report.options.target_platform:
                lines.append(
                    f"Выбранный Python для целевой среды: `{plan.python_version}`. "
                    "Это выбор для нового набора зависимостей, а не подтверждение "
                    "исходной среды автора или успешного запуска."
                )
            elif plan.python_version_origin == "inferred":
                lines.append(
                    f"Предложенная версия Python: `{plan.python_version}`. "
                    "Это целевой профиль для нового набора зависимостей; исходная "
                    "версия Python автора неизвестна."
                )
            elif plan.decisions is not None and plan.decisions.python_version is None:
                lines.append(
                    f"Python из добровольного снимка среды автора: `{plan.python_version}`."
                )
            else:
                lines.append(f"Python, указанный автором: `{plan.python_version}`.")
        else:
            lines.append("Проверенная версия Python автора неизвестна.")
        if platform:
            lines.append(f"Целевая ОС и архитектура: `{platform}`.")
        if plan.report.python_requires:
            lines.append(
                f"Ограничение Python в метаданных проекта: `{plan.report.python_requires}`."
            )
        lines.extend(
            [
                f"Импорты разобраны для Python `{plan.report.options.python_version}`.",
                "",
                "## Установка",
                "",
                *instructions.installation(),
                "## Запуск",
                "",
            ]
        )
        if locked:
            environment = (
                f"Цель: Python {python_version}, {platform}. "
                "Точные версии и хеши записаны в requirements.lock.txt. "
                "Этот совместимый набор рассчитан на указанную платформу; "
                "он не восстанавливает историческую среду автора."
            )
            position = lines.index("## Установка")
            lines[position:position] = [environment, ""]
        if plan.run_spec:
            run = plan.run_spec
            if plan.run_spec_origin == "automatic":
                lines.extend(
                    [
                        "Команда выбрана моделью среди статически найденных "
                        "вариантов запуска по исходникам. Она не была "
                        "подтверждена автором или выполнена; могут потребоваться аргументы.",
                        "",
                    ]
                )
            elif plan.run_spec_origin == "inferred":
                lines.extend(
                    [
                        "Команда определена по структуре проекта и статическим "
                        "фактам. Она не подтверждена автором и не выполнялась; "
                        "могут потребоваться аргументы.",
                        "",
                    ]
                )
            elif plan.run_spec_origin == "author":
                lines.extend(["Конфигурация запуска указана автором.", ""])
            lines.extend(
                [
                    "Из корня распакованного архива с активированной выбранной средой:",
                    "",
                    "```powershell" if platform and platform.startswith("windows") else "```sh",
                ]
            )
            if ProjectLauncher.required(plan):
                lines.append("python run_project.py")
            else:
                windows = bool(platform and platform.startswith("windows"))
                lines.append("Set-Location -LiteralPath 'project'" if windows else "cd project")
                if run.working_directory != ".":
                    lines.append(
                        "Set-Location -LiteralPath '"
                        + run.working_directory.replace("'", "''")
                        + "'"
                        if windows
                        else "cd " + shlex.quote(run.working_directory)
                    )
                command = ["python"]
                if run.kind == "module":
                    command.append("-m")
                target = run.target
                if run.kind == "script" and target.startswith("-"):
                    target = "./" + target
                command.extend([target, *run.args])
                lines.append(
                    "python "
                    + " ".join("'" + value.replace("'", "''") + "'" for value in command[1:])
                    if windows
                    else shlex.join(command)
                )
            lines.extend(["```", ""])
            if ProjectLauncher.required(plan):
                lines.extend(
                    [
                        "`run_project.py` применяет выбранные папку, корни импортов, "
                        "точку входа и аргументы. Исходники используются напрямую, "
                        "без сборочных hooks проекта.",
                        "",
                    ]
                )
        else:
            assessment = LaunchDetector().detect(plan.report, plan.files)
            lines.extend(
                [
                    "Однозначная команда запуска не определена. "
                    "Ниже перечислены варианты, требующие выбора автора.",
                    "",
                ]
            )
            for candidate in assessment.candidates[:10]:
                lines.extend(self._candidate_commands(plan, candidate))
            if len(assessment.candidates) > 10:
                lines.extend(
                    ["У других файлов проекта могут быть собственные сценарии запуска.", ""]
                )

        lines.extend(instructions.docker())
        lines.extend(["## Зависимости", ""])
        requirements = locked_packages if locked else plan.requirements
        if requirements:
            lines.append(
                "Полный выбранный набор, включая транзитивные зависимости:"
                if locked
                else "Известные зависимости и ограничения версий:"
            )
            lines.extend("- `" + value.replace("`", "\\`") + "`" for value in requirements)
        else:
            lines.append("Внешние Python-зависимости не выявлены.")
        lines.extend(
            [
                "",
                "Локальные модули сохранены в `project/`; устанавливать их из PyPI не нужно. "
                "Модули стандартной библиотеки входят в выбранный Python.",
                "",
            ]
        )
        if plan.decisions and plan.decisions.selected_extras:
            lines.extend(
                [
                    "Включены дополнительные группы: "
                    + ", ".join(f"`{value}`" for value in plan.decisions.selected_extras)
                    + ".",
                    "",
                ]
            )
        lines.extend(
            [
                "## Входные данные",
                "",
                plan.input_description or self._input_description(plan),
                "",
                "## Ожидаемый результат",
                "",
                plan.expected_output
                or "Эталонный результат не предоставлен. Сравните вывод с запуском у автора.",
                "",
                "## Оставшиеся ограничения",
                "",
            ]
        )
        review_findings = self._review_findings(plan)
        review_findings = tuple(
            item
            for item in review_findings
            if item.code
            not in {
                "INPUT_PATH_UNVERIFIED",
                "EXTERNAL_INPUT_REFERENCE",
                "MISSING_DATA",
                "MISSING_INPUT_FILE",
                "RUN_INFERRED",
                "RUN_AUTOMATIC",
                "PYTHON_VERSION_UNKNOWN",
                "BUILD_DEPENDENCIES_SEPARATE",
            }
        )
        if review_findings:
            for finding in review_findings:
                message = RussianFindingFormatter().describe(finding, plan)
                lines.append(f"- [{finding.code}] {message}")
        else:
            lines.append(
                "Нерешённых замечаний статического анализа нет. Выполните проверку в чистой среде."
            )
        lines.extend(
            [
                "",
                "## Состав пакета",
                "",
                "Исходники и относительные пути сохранены в `project/`. "
                "Исходный README, если он был, остаётся там. "
                "Отчёт о целевой среде, зависимостях, данных и ограничениях приведён выше. "
                "Подробная машинная диагностика доступна в сервисе.",
                "",
                *self._file_tree(plan, locked=locked, generated_paths=generated_paths),
            ]
        )
        if plan.report.layout:
            layout = plan.report.layout
            lines.extend(
                [
                    f"Корень проекта внутри `project/`: `{layout.root}`.",
                    "Корни импортов внутри `project/`: "
                    + ", ".join(f"`{root}`" for root in layout.source_roots)
                    + ".",
                    "",
                ]
            )
        if plan.excluded_files:
            lines.extend(["Исключённые файлы:", ""])
            lines.extend(f"- {path}" for path in plan.excluded_files)
            lines.append("")
        lines.extend(
            [
                "## Проверка коллегой",
                "",
                "Создайте новое виртуальное окружение и выполните команды выше. "
                "Подготовьте перечисленные входные данные, "
                "сравните вывод с ожидаемым результатом автора. "
                "Зафиксируйте в сервисе или рядом с архивом версии Python и пакетов, "
                "ОС, команду, вывод и итог проверки.",
                "",
            ]
        )
        return SecretScanner().redact("\n".join(lines))

    @staticmethod
    def _review_findings(plan: PackagePlan) -> tuple[Finding, ...]:
        """Consolidate human-facing limits without discarding raw diagnostic evidence."""
        generic_data = {"DATA_DEPENDENCIES_UNVERIFIED", "DATA_NOT_CONFIRMED"}
        versions = {"VERSION_UNKNOWN", "VERSION_NOT_PINNED"}
        result: list[Finding] = []
        seen: set[object] = set()
        for finding in plan.findings:
            if finding.severity != "error":
                if finding.code in {"README_MISSING", "DATA_FILES_SUMMARY"}:
                    continue
                if finding.code in generic_data:
                    continue
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
    def _file_tree(
        plan: PackagePlan, *, locked: bool = False, generated_paths: tuple[str, ...] = ()
    ) -> list[str]:
        lines = ["```text", "README.md", "requirements.txt"]
        if locked:
            lines.append("requirements.lock.txt")
        if ProjectLauncher.required(plan):
            lines.append("run_project.py")
        lines.extend(path for path in generated_paths if path not in lines)
        lines.append("project/")
        for file in plan.files[:80]:
            lines.append("  " + file.path.replace("`", "\\`"))
        if len(plan.files) > 80:
            lines.append(f"  ... {len(plan.files) - 80} других файлов")
        lines.extend(["```", ""])
        return lines

    @staticmethod
    def _input_description(plan: PackagePlan) -> str:
        findings = [
            item
            for item in plan.findings
            if item.code
            in {
                "INPUT_PATH_UNVERIFIED",
                "EXTERNAL_INPUT_REFERENCE",
                "MISSING_DATA",
                "MISSING_INPUT_FILE",
            }
        ]
        if findings:
            formatter = RussianFindingFormatter()
            return "Перед запуском подготовьте данные:\n\n" + "\n".join(
                "- " + formatter.describe(item, plan) for item in findings
            )
        data = [
            file.path
            for file in plan.files
            if file.path.lower().endswith((".csv", ".tsv", ".parquet", ".xlsx", ".npy", ".npz"))
        ]
        if data:
            return "В архив включены возможные файлы данных: " + ", ".join(data[:20]) + "."
        return (
            "Отдельные входные файлы не выявлены. "
            "Внешние сервисы и вычисляемые пути не проверялись."
        )

    @staticmethod
    def _candidate_commands(plan: PackagePlan, candidate: LaunchCandidate) -> list[str]:
        run = candidate.run_spec
        lines = [f"Возможный запуск: {candidate.run_spec.target}", "", "```sh", "cd project"]
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
