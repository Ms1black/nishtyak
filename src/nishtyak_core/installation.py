from __future__ import annotations

import posixpath
import re
import shlex
import tomllib
from dataclasses import dataclass

from .layout import LayoutDetector
from .models import PackagePlan, ProjectSnapshot


@dataclass(frozen=True)
class ProjectManifest:
    path: str
    kind: str
    valid: bool | None = None
    installable: bool = False


class EnvironmentInstructions:
    """Describe retained manifests and generated artifacts without evaluating project code."""

    def __init__(
        self,
        plan: PackagePlan,
        python_version: str,
        platform: str | None,
        *,
        locked: bool = False,
        generated_paths: tuple[str, ...] = (),
        snapshot: ProjectSnapshot | None = None,
    ) -> None:
        self.plan = plan
        self.python_version = python_version
        self.platform = platform
        self.locked = locked
        self.generated_paths = generated_paths
        self.audited = (plan.automatic_resolution or {}).get("package_safety") is not None
        if snapshot is not None and snapshot.fingerprint != plan.fingerprint:
            raise ValueError("Documentation snapshot belongs to a different package plan")
        self.manifests = self._manifests(snapshot)

    def _manifests(self, snapshot: ProjectSnapshot | None) -> tuple[ProjectManifest, ...]:
        root = self.plan.report.layout.root if self.plan.report.layout else "."
        retained = {file.path: file for file in self.plan.files}
        result = []
        for name in ("pyproject.toml", "environment.yml", "environment.yaml", "Dockerfile"):
            path = LayoutDetector.join(root, name)
            if path not in retained:
                continue
            source = snapshot.get(path) if snapshot is not None else None
            if source is not None and source.info != retained[path]:
                raise ValueError("Documentation source does not match the retained file")
            valid = None
            installable = False
            if source is not None and name == "pyproject.toml":
                try:
                    if len(source.content) > self.plan.report.options.max_source_bytes:
                        raise ValueError("Manifest exceeds parsing limit")
                    data = tomllib.loads(source.content.decode("utf-8-sig"))
                    project = data.get("project", {})
                    build = data.get("build-system", {})
                    valid = isinstance(project, dict) and isinstance(build, dict)
                    if valid:
                        dynamic = project.get("dynamic", [])
                        backend = build.get("build-backend")
                        installable = bool(
                            (isinstance(backend, str) and backend.strip())
                            or (
                                isinstance(project.get("name"), str)
                                and (
                                    isinstance(project.get("version"), str)
                                    or (isinstance(dynamic, list) and "version" in dynamic)
                                )
                            )
                        )
                except (ValueError, UnicodeError, RecursionError, TypeError):
                    valid = False
            elif source is not None and name.startswith("environment."):
                try:
                    from .conda import CondaEnvironmentReader
                    from .models import SourceFile

                    normalized = ProjectSnapshot(
                        (SourceFile("environment.yml", source.content),), snapshot.fingerprint
                    )
                    valid = CondaEnvironmentReader().read(normalized).complete
                except ImportError:
                    valid = None
            kind = "conda" if name.startswith("environment.") else name
            result.append(ProjectManifest(path, kind, valid, installable))
        return tuple(result)

    def _quote(self, value: str) -> str:
        if self.platform and self.platform.startswith("windows"):
            return "'" + value.replace("'", "''") + "'"
        return shlex.quote(value)

    @property
    def fence(self) -> str:
        return "```powershell" if self.platform and self.platform.startswith("windows") else "```sh"

    def installation(self) -> list[str]:
        conda = next((item for item in self.manifests if item.kind == "conda"), None)
        lines = [
            "Откройте терминал в корне распакованного архива. "
            + (
                "Ниже описана отдельная pip-среда и границы проверки исходных конфигураций."
                if self.audited
                else "Выберите один способ подготовки среды; pip, Conda и Docker "
                "не нужно последовательно устанавливать в одно окружение."
            ),
            "",
        ]
        if conda is not None and self.audited:
            lines.extend(
                [
                    "### Conda — исходная конфигурация для проверки",
                    "",
                    f"Файл `project/{conda.path}` сохранён. Проверка PyPI-зависимостей по OSV "
                    "и созданный pip-lock не охватывают каналы Conda, полный набор пакетов "
                    "этой среды и её секцию pip. Перед созданием Conda-среды нужна отдельная "
                    "проверка исходного YAML и всех устанавливаемых зависимостей.",
                    "",
                    "Ниже приведён отдельный pip-вариант для найденных Python-зависимостей. "
                    "Он не заменяет исходную Conda-среду и её системные пакеты.",
                    "",
                ]
            )
        if conda is not None and conda.valid is not False and not self.audited:
            lines.extend(
                [
                    "### Conda — среда из конфигурации автора",
                    "",
                    f"В архиве сохранён `project/{conda.path}`. Установите Conda, "
                    "затем создайте отдельную среду из этого файла:",
                    "",
                    self.fence,
                    "conda env create --prefix ./.conda-env --file "
                    + self._quote("project/" + conda.path),
                    "conda activate ./.conda-env",
                    "```",
                    "",
                    "Conda-зависимости и секция pip остаются в исходном YAML. "
                    "Это конфигурация автора, а не автоматически проверенный Conda-lock: "
                    "каналы, доступность пакетов и совместимость ещё требуют проверки. "
                    "`requirements.txt` описывает только найденные Python-зависимости "
                    "и не заменяет системные пакеты Conda.",
                    "",
                ]
            )
            if self.locked:
                lines.extend(
                    [
                        "`requirements.lock.txt` относится к "
                        "отдельному pip-профилю целевой платформы. Он не является lock-файлом "
                        "этой Conda-среды; не устанавливайте их поверх неё автоматически.",
                        "",
                    ]
                )
        else:
            if conda is not None and not self.audited:
                lines.extend(
                    [
                        f"`project/{conda.path}` сохранён, но его структура или зависимости "
                        "требуют исправления. Ниже приведён ограниченный pip-вариант; "
                        "он не воспроизводит Conda-среду и её системные пакеты.",
                        "",
                    ]
                )
            lines.extend(["### pip — отдельное виртуальное окружение", "", *self._venv(), ""])
            lines.extend(
                [
                    "В `requirements.txt` сохранены известные ограничения. "
                    "Незафиксированные версии — черновик; исходные проверенные версии "
                    "невозможно восстановить только по импортам.",
                    "",
                ]
            )
            if self.locked:
                lines.extend(
                    [
                        "Точные версии и хеши пакетов записаны в `requirements.lock.txt`. "
                        "Устанавливайте его на указанной выше версии Python и платформе.",
                        "",
                    ]
                )
        lines.extend(self._generated_profiles())
        pyproject = next((item for item in self.manifests if item.kind == "pyproject.toml"), None)
        if pyproject is not None:
            lines.extend(
                ["### Проект из pyproject.toml", "", f"Файл: `project/{pyproject.path}`.", ""]
            )
            if pyproject.valid is False:
                lines.append(
                    "TOML не удалось корректно разобрать; команда установки пакета не сформирована."
                )
            elif pyproject.installable and self.audited:
                lines.append(
                    "Исходная конфигурация сохранена для отдельной проверки. "
                    "Проверка PyPI-зависимостей по OSV и созданный pip-lock не охватывают "
                    "build-system.requires, изолированную среду сборки и выполняемые "
                    "сборочным backend действия. Даже отключение runtime-зависимостей "
                    "не отключает установку сборочных пакетов. Команда установки самого "
                    "проекта не сформирована; используйте подготовленную pip-среду и "
                    "запуск исходников ниже. Если проект требует сборки, сначала отдельно "
                    "проверьте её зависимости и действия."
                )
            elif pyproject.installable:
                directory = posixpath.dirname("project/" + pyproject.path)
                extras = self.plan.decisions.selected_extras if self.plan.decisions else ()
                target = "./" + directory + ("[" + ",".join(extras) + "]" if extras else "")
                lines.extend(
                    [
                        "После подготовки выбранной среды можно установить сам проект "
                        "для его команд и метаданных. `--no-deps` сохраняет уже установленный "
                        "набор зависимостей; выбранные extras учтены при подготовке пакета.",
                        "",
                        self.fence,
                        "python -m pip install --no-deps " + self._quote(target),
                        "```",
                        "",
                        "Установка проекта может запустить его сборочный backend на вашем "
                        "компьютере. Сервис его не запускал и не проверял успешность сборки. "
                        "Для запуска исходников по инструкции ниже этот шаг обычно не требуется.",
                    ]
                )
            else:
                lines.append(
                    "Файл сохранён как исходная конфигурация. Устанавливаемый пакет "
                    "по доступным статическим сведениям не подтверждён; настройки инструментов "
                    "в pyproject.toml сами по себе не являются командой установки. "
                    "Используйте описанную выше среду для запуска исходников."
                )
            lines.append("")
        return lines

    def _generated_profiles(self) -> list[str]:
        pyproject = "pyproject.toml" in self.generated_paths
        conda = "environment.yml" in self.generated_paths
        if not pyproject and not conda:
            return []
        lines = ["### Другие форматы среды", ""]
        if pyproject:
            lines.extend(
                [
                    "Корневой `pyproject.toml` — новый профиль зависимостей. Его служебные "
                    "имя и версия не описывают исходный проект; устанавливаемую библиотеку "
                    "сервис не создаёт. Этот файл не заменяет lock-файл с хешами.",
                    "",
                ]
            )
        if conda:
            known_target = (
                self.locked
                and self.platform
                and re.fullmatch(r"3\.[0-9]+(?:\.[0-9]+)?", self.python_version)
            )
            if known_target:
                lines.extend(
                    [
                        "Корневой `environment.yml` — альтернатива venv: Conda создаёт "
                        "отдельную среду с Python, затем pip устанавливает зависимости из "
                        "`requirements.lock.txt` с проверкой хешей и без сборки исходных пакетов. "
                        "Установите Conda и выполните из локальной папки распакованного ZIP, "
                        "доступной для записи:",
                        "",
                        self.fence,
                        "conda env create --prefix ./.conda-env --file environment.yml",
                        "conda activate ./.conda-env",
                        "```",
                        "",
                        "Используйте указанную выше целевую платформу. Проверка PyPI-пакетов "
                        "не охватывает Python, pip, каналы и системные пакеты Conda; "
                        "этот файл не является полным Conda-lock. После pip не добавляйте "
                        "Conda-пакеты в эту среду: создайте новую при изменении состава.",
                        "",
                    ]
                )
            else:
                lines.extend(
                    [
                        "Корневой `environment.yml` — черновик без установки пакетов проекта. "
                        "Команда создания Conda-среды не сформирована: сначала нужны "
                        "известная целевая среда и проверенный lock-файл зависимостей.",
                        "",
                    ]
                )
            if any(item.kind == "conda" for item in self.manifests):
                lines.extend(
                    [
                        "Исходный YAML автора в `project/` сохранён отдельно. Сгенерированный "
                        "pip-профиль не воспроизводит его каналы и системные зависимости.",
                        "",
                    ]
                )
        return lines

    def _venv(self) -> list[str]:
        version = ".".join(self.python_version.split(".")[:2])
        install = "python -m pip install " + (
            "--only-binary=:all: --require-hashes -r requirements.lock.txt"
            if self.locked
            else "-r requirements.txt"
        )
        installation = [] if self.audited and not self.locked else [install]
        limitation = (
            [
                "",
                "`requirements.txt` остаётся черновиком: полный набор транзитивных "
                "зависимостей ещё не разрешён и не проверен по OSV. Среду можно создать, "
                "но команда установки не сформирована. Сначала нужны отдельный подбор "
                "версий и проверка всех пакетов; затем используйте проверенный lock-файл.",
            ]
            if self.audited and not self.locked
            else []
        )
        if self.platform and self.platform.startswith("windows"):
            return [
                "Windows, PowerShell:",
                "",
                "```powershell",
                f"py -{version} --version",
                f"py -{version} -m venv .venv",
                ".\\.venv\\Scripts\\Activate.ps1",
                *installation,
                "```",
                *limitation,
            ]
        commands = [
            "Linux:" if self.platform and self.platform.startswith("linux") else "Linux / macOS:",
            "",
            "```sh",
            f"python{version} --version",
            f"python{version} -m venv .venv",
            ". .venv/bin/activate",
            *installation,
            "```",
            *limitation,
        ]
        if self.platform is None:
            commands.extend(
                [
                    "",
                    "Альтернатива для Windows, PowerShell:",
                    "",
                    "```powershell",
                    f"py -{version} --version",
                    f"py -{version} -m venv .venv",
                    ".\\.venv\\Scripts\\Activate.ps1",
                    *installation,
                    "```",
                ]
            )
        return commands

    def docker(self) -> list[str]:
        if "Dockerfile" in self.generated_paths:
            platform = {"linux-x86_64": "linux/amd64", "linux-aarch64": "linux/arm64"}.get(
                self.platform
            )
            option = f" --platform {platform}" if platform else ""
            return [
                "## Docker — альтернативный запуск",
                "",
                "В корне ZIP добавлен `Dockerfile`. Установите Docker Engine или Docker Desktop "
                "с Linux-контейнерами. Выполните из корня распакованного архива:",
                "",
                "```sh",
                f"docker build{option} -t experiment .",
                f"docker run --rm{option} experiment",
                "```",
                "",
                "Точка в команде сборки — весь распакованный пакет: Dockerfile, зависимости, "
                "папка project/ и вспомогательный launcher, если он нужен. "
                "Локальная .venv внутри контейнера не используется.",
                "",
                "Это непроверенный шаблон: сервис не собирал образ и не запускал проект. "
                "Системные библиотеки, GPU, внешние сервисы и сохранение результатов на хост "
                "могут потребовать дополнительных настроек.",
                "",
            ]
        original = next((item for item in self.manifests if item.kind == "Dockerfile"), None)
        if original is not None:
            if self.audited:
                return [
                    "## Dockerfile автора",
                    "",
                    f"Исходный `project/{original.path}` сохранён без изменений. "
                    "Проверка PyPI-зависимостей по OSV и созданный pip-lock не охватывают "
                    "базовый образ, команды RUN и зависимости, устанавливаемые этим "
                    "Dockerfile. Перед сборкой или запуском нужна отдельная проверка "
                    "его содержимого и контекста COPY; команды не сформированы.",
                    "",
                ]
            directory = posixpath.dirname("project/" + original.path)
            return [
                "## Dockerfile автора",
                "",
                f"Исходный `project/{original.path}` сохранён без изменений. "
                "Предполагаемый контекст сборки — каталог с этим Dockerfile:",
                "",
                self.fence,
                "docker build -f "
                + self._quote("project/" + original.path)
                + " -t experiment-author "
                + self._quote(directory),
                "docker run --rm experiment-author",
                "```",
                "",
                "Проверьте контекст COPY, аргументы сборки и ENTRYPOINT исходного файла. "
                "Сервис не проверял сборку и не заменяет настройки автора автоматически.",
                "",
            ]
        from .docker import DockerfileGenerator

        reason = (
            "Dockerfile не добавлен: для environment.yml нужен отдельный профиль Conda."
            if any(item.kind == "conda" for item in self.manifests)
            else DockerfileGenerator().skip_reason(self.plan, target_platform=self.platform)
        )
        return ["## Docker", "", reason or "Генерация Dockerfile отключена для этого пакета.", ""]
