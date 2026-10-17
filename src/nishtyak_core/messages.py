from __future__ import annotations

from packaging.requirements import Requirement

from .models import Finding, PackagePlan


class RussianFindingFormatter:
    """Describe limits in Russian; retain complete diagnostics in the JSON report."""

    messages = {
        "SECRET_DETECTED": "Обнаружен секрет. Удалите его или исключите файл перед сборкой.",
        "SECRET_FILE_EXCLUDED": "Файл с секретом исключён. Проверьте запуск без него.",
        "NO_PROJECT_FILES": "В пакете не осталось исходных файлов.",
        "DEPENDENCY_CONFLICT": "Объявленные ограничения библиотек противоречат друг другу.",
        "DEPENDENCY_UNRESOLVED": "Не подтверждён устанавливаемый пакет для импорта",
        "DECLARATION_IMPORT_UNRESOLVED": "Не подтверждён поставщик импортируемого модуля",
        "DEPENDENCY_DECLARATIONS_MULTIPLE": "Найдены разные декларации одной зависимости.",
        "DYNAMIC_IMPORT": "Динамический импорт может требовать дополнительных библиотек.",
        "DYNAMIC_IMPORT_UNRESOLVED": "Динамический импорт не удалось определить статически.",
        "SOURCE_PARSE_ERROR": "Исходный файл не разобран. Проверьте синтаксис и версию Python.",
        "SYNTAX_ERROR": "Не удалось разобрать синтаксис исходного файла.",
        "LICENSE_MISSING": "Лицензия не указана. Уточните разрешённые способы использования кода.",
        "README_MISSING": "Исходный README отсутствует; этот файл подготовлен сервисом.",
        "RUN_TARGET_AMBIGUOUS": "Найдено несколько вариантов запуска. Нужен выбор автора.",
        "RUN_TARGET_UNCERTAIN": "Предполагаемый запуск требует проверки автора.",
        "RUN_TARGET_UNKNOWN": "Команду запуска определить не удалось.",
        "RUN_NOT_SELECTED": "Команда запуска не выбрана.",
        "RUN_TARGET_MISSING": "Файл запуска отсутствует в пакете.",
        "RUN_WORKING_DIRECTORY_MISSING": "Папка запуска не включена в пакет.",
        "LOCAL_MODULE_MISSING": "Не хватает локального модуля. Добавьте исходники в ZIP.",
        "LOCAL_MODULE_EXCLUDED": "Нужный локальный модуль исключён из пакета.",
        "LOCAL_IMPORT_NAME_UNRESOLVED": "Импортируемое имя не найдено в локальном модуле.",
        "LOCAL_IMPORT_CONTEXT_REQUIRED": "Локальный импорт зависит от папки и способа запуска.",
        "LOCAL_IMPORT_CONTEXT_AMBIGUOUS": "Неоднозначный контекст локального импорта.",
        "LOCAL_NAME_COLLISION": "Локальный модуль перекрывает одноимённый модуль среды.",
        "PYTHON_VERSION_UNKNOWN": "Версия Python исходной среды автора неизвестна.",
        "PYTHON_VERSION_CONFLICT": "Выбранный Python не соответствует требованиям проекта.",
        "PYTHON_PROFILE_MISMATCH": "Версия Python анализа отличается от целевой среды.",
        "MANIFEST_UNSUPPORTED": "Формат декларации зависимостей поддержан не полностью.",
        "TOOL_METADATA_UNSUPPORTED": "Дополнительные правила источников пакетов требуют проверки.",
        "REQUIREMENTS_UNSUPPORTED": "Некоторые строки зависимостей не поддержаны.",
        "UNSUPPORTED_REQUIREMENT": "Неподдерживаемая декларация зависимости.",
        "OPTIONAL_DEPENDENCIES_UNSELECTED": "Необязательные группы зависимостей не включены.",
        "PROJECT_ROOT_AMBIGUOUS": "В архиве несколько проектов; корень не определён однозначно.",
        "MISSING_DATA": "Не хватает входных данных.",
        "MISSING_INPUT_FILE": "Не найден входной файл.",
        "MODULE_EXCLUDED": "Внешний импорт исключён из зависимостей. Проверьте запуск без него.",
    }

    def describe(self, finding: Finding, plan: PackagePlan) -> str:
        if finding.code in {"INPUT_PATH_UNVERIFIED", "EXTERNAL_INPUT_REFERENCE"}:
            message = (
                finding.message
                if any("А" <= c <= "я" for c in finding.message)
                else ("Код ссылается на данные, наличие или доступность которых не подтверждены.")
            )
        elif finding.code in {"VERSION_UNKNOWN", "VERSION_NOT_PINNED", "VERSIONS_UNVERIFIED"}:
            names = sorted({Requirement(raw).name for raw in plan.requirements})
            message = "Версии исходной среды автора не подтверждены"
            message += ": " + ", ".join(names) + "." if names else "."
        else:
            message = self.messages.get(
                finding.code,
                "Требуется проверка условия «"
                + finding.code
                + "»: "
                + finding.message,
            )
        modules = sorted(
            {
                item.module
                for item in plan.report.dependencies
                if set(item.evidence_ids) & set(finding.evidence_ids)
            }
        )
        if modules and finding.code in {"DEPENDENCY_UNRESOLVED", "DECLARATION_IMPORT_UNRESOLVED"}:
            message += ": " + ", ".join(modules) + "."
        if finding.path:
            location = finding.path + (f":{finding.line}" if finding.line is not None else "")
            message += f" ({location})"
        return message
