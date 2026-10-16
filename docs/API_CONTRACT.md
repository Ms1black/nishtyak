# Контракт API и подключение ядра

Это контракт HTTP-адаптера для второго разработчика. В ветке реализованы Python-библиотека и CLI; описанные HTTP-маршруты предстоит подключить. Формальные запросы и ответы находятся в [openapi.yaml](openapi.yaml), примеры моделей — в `examples/contracts/`.

## Хранение и доверенные объекты

Сервер связывает `project_id` с `ProjectSnapshot` и `AnalysisReport`. Клиент получает сериализованный отчет и отправляет только решения пользователя. Сервер не принимает клиентские AnalysisReport, PackagePlan, список файлов или хеши как доверенные.

В каждом новом анализе получается `analysis_id`. Он меняется при изменении файлов, параметров анализа и снимка среды. Решения содержат этот ID; устаревшее решение требует повторного получения отчета.

## Маршруты

| Метод и путь | Вход | Результат |
| --- | --- | --- |
| `POST /api/projects` | multipart: `file` ZIP, необязательный JSON-текст `options` | 201, `{project_id, analysis}` |
| `GET /api/projects/{project_id}` | ID серверного проекта | 200, `{project_id, analysis}` |
| `POST /api/projects/{project_id}/plan` | UserDecisions JSON | 200, PackagePlan JSON |
| `POST /api/projects/{project_id}/bundle` | `{decisions, include_dockerfile}` | 200, `application/zip` |
| `POST /api/projects/{project_id}/advice` | `{feature, description}` | 200, AdvisoryResult JSON |

`partial` — нормальный результат анализа с предупреждениями. Подготовка возвращает blocked/draft/ready_to_verify вместе с findings. Черновик допускает скачивание; blocked не генерируется. Недоступность AI выражается статусом дополнительной функции и не ломает экспорт.

## Вызовы ядра

```python
from pathlib import Path

from nishtyak_core import ReproducibilityService, RunSpec, SnapshotBuilder, UserDecisions
from nishtyak_core.exporting import BundleWriter

service = ReproducibilityService()
snapshot = SnapshotBuilder().from_directory(extracted_project_directory)
report = service.analyze(snapshot)

# project_id -> snapshot/report хранится на сервере.
# DTO отдельно проверяет типы, размеры, разрешенные поля и неизвестные параметры.
data = validated_decisions
run = RunSpec(
    target=data["run_spec"]["target"],
    kind=data["run_spec"].get("kind", "script"),
    args=tuple(data["run_spec"].get("args", [])),
    working_directory=data["run_spec"].get("working_directory", "."),
)
decisions = UserDecisions(
    analysis_id=data["analysis_id"],
    packages=data.get("packages", {}),
    versions=data.get("versions", {}),
    excluded_modules=tuple(data.get("excluded_modules", [])),
    excluded_files=tuple(data.get("excluded_files", [])),
    run_spec=run,
    python_version=data.get("python_version"),
    description=data.get("description", ""),
    input_description=data.get("input_description", ""),
    expected_output=data.get("expected_output", ""),
    data_confirmed=data.get("data_confirmed", False),
)
plan = service.prepare(report, decisions)
generated = service.render(plan, include_dockerfile=False)
artifact = BundleWriter().write(snapshot, plan, generated, Path(server_output_path))
```

Для `/plan` достаточно вернуть `plan.to_dict()`. Для `/bundle` сервер заново вызывает prepare с хранимым отчетом и принятыми решениями; он не берет план из JSON клиента. Возвращается ZIP с `Content-Disposition: attachment` и именем файла. Локальный `artifact.path` клиенту не раскрывается.

`run_spec` может быть null при подготовке черновика. В этом случае DTO передает `run_spec=None`; показанный пример соответствует выбранному запуску. Все коллекции преобразуются явно. Dataclass не заменяет валидацию HTTP-запроса.

## Поля решений

| Поле | Значение |
| --- | --- |
| `analysis_id` | Обязательный ID текущего серверного отчета |
| `packages` | Импортируемый модуль → подтвержденное PEP 508 требование |
| `versions` | Модуль или имя дистрибутива → версия/ограничение |
| `excluded_modules` | Явно исключенные из внешних зависимостей модули |
| `excluded_files` | Относительные пути исключаемых файлов |
| `run_spec` | script/module, target, args, рабочая папка |
| `python_version` | Версия Python, выбранная автором; не профиль анализатора |
| `description` | Назначение эксперимента |
| `input_description` | Что требуется для запуска |
| `expected_output` | Какой результат проверить |
| `data_confirmed` | Автор подтвердил комплектность/доступность данных |

Пример: `packages={"yaml": "PyYAML>=6"}`. `versions={"pandas": "==2.2.0"}` задает спецификатор с сохранением имеющегося marker. Разные markers не объединяются в одно требование только по имени.

## Дополнительные функции

```python
from nishtyak_core.advisors import AdvisoryService, DeepSeekClient

advisor = AdvisoryService(DeepSeekClient.from_env())
result = advisor.advise(report, feature="explain", description="Описание автора")
payload = result.to_dict()
```

Ключ и модель находятся только на сервере: `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`. `feature` — explain/readme/packages. Предложения всегда требуют подтверждения и становятся обычными UserDecisions. Список suggestions или текст ответа не исполняется и не используется как готовый requirements.

Снимок среды автора импортируется через `AuthorEnvironmentSnapshot.from_dict()` и передается в `service.analyze(..., author_environment=...)`. Он также хранится серверно. Сбор локальной среды запускается явно у автора через CLI `nishtyak environment`; среда API-сервера не подменяет среду проекта.

Conda читается через `CondaEnvironmentReader.read(snapshot)` как отдельный профиль. Для первой интеграции достаточно показать этот результат и предупреждение о ручном согласовании; автоматического pip-пакета из Conda нет.

## Ошибки

Предлагаемый конверт: `{code, message, findings?}`. Сообщения очищаются и не раскрывают пути сервера, исходники, ключи или traceback.

| HTTP | Случай |
| --- | --- |
| 400 | Некорректный архив или JSON-текст options |
| 404 | Неизвестный/истекший project_id |
| 409 | Устаревший analysis_id; запрос экспорта blocked-плана |
| 413 | Превышены серверные лимиты загрузки/распаковки |
| 415 | Неподдерживаемый формат загрузки |
| 422 | Некорректный DTO, решение, запуск или неподдерживаемая конфигурация генерации |
| 500 | Неожиданная ошибка сервера с общим сообщением |

Исключения ядра пока преимущественно ValueError. Для устойчивой классификации ошибок нужен адаптер с явными проверками либо типизированные исключения; производственный код не должен разбирать текст сообщения.

## Задачи HTTP-адаптера

- Проверить ZIP-пути, дубли имен, симлинки, число файлов, распакованный размер и коэффициент сжатия до создания снимка.
- Хранить снимок/отчет на сервере, назначать непредсказуемый ID, срок хранения и изолированную папку.
- Ограничить входные options серверными пределами. У SnapshotBuilder по умолчанию 1000 файлов и 50 MB; у анализатора 1 MB на исходник.
- Делать операции с тяжелыми файлами/синхронным AI-клиентом вне event loop.
- Передавать фронтенду только безопасные сериализаторы `to_dict()`, файлы ZIP и статусы.
- Отдавать ZIP строго по серверному PackagePlan, не по присланному списку файлов.
- Не выполнять загруженный код, shell-команды и build hooks.
