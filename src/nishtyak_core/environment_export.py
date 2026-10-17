from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from .models import GeneratedFile, PackagePlan

if TYPE_CHECKING:
    from .locking import DependencyLock


class EnvironmentExportGenerator:
    """Describe the selected runtime profile without building or installing the project."""

    def generate(
        self, plan: PackagePlan, *, lock: DependencyLock | None = None
    ) -> tuple[GeneratedFile, ...]:
        # Imports stay local because locking also uses the document renderer.
        from .locking import (
            DependencyLock,
            _active,
            _validate_coverage,
            _validate_target,
            _validated_requirements,
            resolution_input_id,
        )

        if plan.state == "blocked":
            raise ValueError("Resolve blocking findings before exporting environment formats")
        omitted = 0
        if lock is not None:
            requirements = _validated_requirements(plan.requirements)
        else:
            known = []
            omitted = max(0, len(plan.requirements) - 500)
            for raw in plan.requirements[:500]:
                try:
                    known.extend(_validated_requirements((raw,)))
                except ValueError:
                    omitted += 1
            requirements = tuple(sorted(set(known)))
        python = plan.python_version
        platform = plan.report.options.target_platform
        if lock is not None:
            lock = DependencyLock.from_dict(lock.to_dict())
            if lock.analysis_id != plan.analysis_id or lock.input_id != resolution_input_id(
                plan, lock.target
            ):
                raise ValueError("Dependency lock does not match the current package plan")
            _validate_target(plan, lock.target)
            _validate_coverage(requirements, lock.packages, lock.target)
            pins = {
                canonicalize_name(item.name): next(iter(item.specifier)).version
                for raw in lock.packages
                if _active(item := Requirement(raw), lock.target)
            }
            direct = []
            for raw in requirements:
                requirement = Requirement(raw)
                if not _active(requirement, lock.target):
                    continue
                requirement.specifier = SpecifierSet(
                    "==" + pins[canonicalize_name(requirement.name)]
                )
                direct.append(str(requirement))
            requirements = tuple(sorted(set(direct)))
            python, platform = lock.target.python_version, lock.target.platform
        if python is not None and not re.fullmatch(r"3\.[0-9]{1,2}(?:\.[0-9]{1,3})?", python):
            raise ValueError("A concrete stable Python version is required for this profile")
        return (
            GeneratedFile(
                "pyproject.toml",
                self._pyproject(
                    plan, requirements, python, platform, locked=lock is not None, omitted=omitted
                ).encode(),
            ),
            GeneratedFile(
                "environment.yml",
                self._conda(
                    requirements, python, platform, locked=lock is not None, omitted=omitted
                ).encode(),
            ),
        )

    @staticmethod
    def _string(value: str) -> str:
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")

    def _pyproject(self, plan, requirements, python, platform, *, locked, omitted):
        lines = [
            "# Сгенерированный профиль воспроизводимости, не исходные метаданные автора.",
            "# Имя и версия ниже относятся только к этому служебному профилю.",
            "# Файл не настраивает сборку: запускайте исходники по README.",
            "# Транзитивные версии и хеши находятся в requirements.lock.txt, если он создан.",
            "[project]",
            'name = "reproducibility-profile"',
            'version = "0"',
            'description = "Generated runtime profile; original environment not restored"',
        ]
        constraint = plan.report.python_requires
        if python:
            selected = "==" + python + (".*" if len(python.split(".")) == 2 else "")
            constraint = str(SpecifierSet(",".join(filter(None, (constraint, selected)))))
        if constraint:
            lines.append("requires-python = " + self._string(str(SpecifierSet(constraint))))
        lines.extend(
            ["dependencies = [", *("  " + self._string(r) + "," for r in requirements), "]"]
        )
        lines.extend(
            [
                "",
                "[tool.nishtyak-reproducibility]",
                "generated = true",
                "author-environment-restored = false",
                "execution-verified = false",
                "dependency-mode = " + self._string("locked" if locked else "draft"),
                "package-state = " + self._string(plan.state),
                "omitted-requirement-count = " + str(omitted),
                'source-directory = "project"',
            ]
        )
        if python:
            lines.append("target-python = " + self._string(python))
        else:
            lines.append(
                "# Точная целевая версия Python неизвестна; профиль анализа её не доказывает."
            )
        if platform:
            lines.append("target-platform = " + self._string(platform))
        if locked:
            lines.append('lock-file = "requirements.lock.txt"')
        if omitted:
            lines.append(
                "# Часть объявлений зависимостей не перенесена: URL, локальные ссылки, "
                "неподдерживаемый синтаксис или превышение лимита. Исходники сохранены в project/."
            )
        lines.extend(["", "[tool.uv]", "package = false"])
        return "\n".join(lines) + "\n"

    def _conda(self, requirements, python, platform, *, locked, omitted):
        lines = [
            "# Сгенерированная обёртка Conda для pip-профиля; это не Conda-lock среды автора.",
            "# Python, pip, каналы и транзитивные пакеты Conda не покрыты проверкой PyPI/OSV.",
            "# Перед созданием Conda-среды нужна отдельная проверка этого базового слоя.",
            "# Сервис не создавал среду и не запускал проект.",
        ]
        if platform:
            lines.append("# Целевая платформа pip-профиля: " + self._string(platform))
        if locked:
            lines.extend(
                [
                    "# Храните YAML рядом с requirements.lock.txt в доступном для записи каталоге.",
                    "# Относительный путь к lock-файлу разрешается из каталога этого YAML.",
                    "# Перед использованием перейдите в корень распакованного ZIP.",
                ]
            )
        else:
            lines.append(
                "# Черновик: pip-зависимости не устанавливаются без проверенного lock-файла."
            )
            if not python:
                lines.append("# Версия Python неизвестна; выберите её перед созданием среды.")
            lines.extend("# Известная Python-зависимость: " + self._string(r) for r in requirements)
            if omitted:
                lines.append(
                    "# Часть объявлений зависимостей не перенесена; "
                    "проверьте исходные файлы в project/."
                )
        lines.append('name: "reproducibility-profile"')
        if python:
            lines.extend(["dependencies:", "  - " + self._string("python=" + python), '  - "pip"'])
            if locked:
                lines.extend(
                    [
                        "  - pip:",
                        '      - "--only-binary=:all:"',
                        '      - "--require-hashes"',
                        '      - "-r requirements.lock.txt"',
                    ]
                )
        else:
            lines.append("dependencies: []")
        return "\n".join(lines) + "\n"
