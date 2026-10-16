# Контракт API и подключение ядра

Это согласованный асинхронный контракт HTTP-адаптера для второго разработчика. В ветке реализованы Python-библиотека, CLI и worker; HTTP-маршруты еще не реализованы. Формальные запросы и ответы находятся в [openapi.yaml](openapi.yaml), примеры моделей — в `examples/contracts/`. Изоляция worker и развертывание описаны в [SERVER_RUNTIME.md](SERVER_RUNTIME.md).

## Сессия и владение

Все маршруты требуют серверную сессию. Cookie `__Host-nishtyak_session` содержит случайный непрозрачный ID; атрибуты: `Secure; HttpOnly; SameSite=Strict; Path=/`, без Domain. Выдача сессии/логин остается за приложением и в эти маршруты не входит. Для POST и DELETE обязателен `X-CSRF-Token`, привязанный к сессии и выданный при загрузке интерфейса; сервер также проверяет Origin. Cookie и токены не передаются worker.

Сервер проверяет владельца каждого проекта и job до чтения данных или запуска действия. Чужой ID дает тот же `404 RESOURCE_NOT_FOUND`, что неизвестный; отсутствие/истечение сессии дает `401 SESSION_REQUIRED`. Непредсказуемый ID не заменяет проверку владельца. Политика доступа распространяется на ZIP, отчеты и AI-ответы.

## Хранение и доверенные объекты

Сервер связывает `project_id`, `job_id` и владельца с сохраненным ZIP, снимком и результатом анализа. Клиент получает отчет через публичный `report.to_dict()` и отправляет только решения пользователя. Приватный JSON worker нельзя пересылать напрямую. Сервер не принимает клиентские AnalysisReport, PackagePlan, список файлов или хеши как доверенные.

В каждом новом анализе получается `analysis_id`. Он меняется при изменении файлов, параметров анализа и снимка среды. Решения содержат этот ID; устаревшее решение требует повторного получения отчета.

## Маршруты

| Метод и путь | Вход | Результат |
| --- | --- | --- |
| `POST /api/projects` | multipart: `file` ZIP, необязательный JSON-текст `options` | 202, `{project_id, job_id, status}`; `Location: /api/jobs/{job_id}` |
| `GET /api/jobs/{job_id}` | ID своей задачи | 200, `{project_id, job_id, status, analysis?, error?}` |
| `GET /api/projects/{project_id}` | ID своего проекта | 200, `{project_id, analysis}` после успеха; до завершения 409 |
| `DELETE /api/projects/{project_id}` | ID своего проекта | 204, отзыв доступа, отмена работы и очистка |
| `POST /api/projects/{project_id}/plan` | UserDecisions JSON | 200, PackagePlan JSON |
| `POST /api/projects/{project_id}/bundle` | `{decisions, include_dockerfile}` | 200, `application/zip` |
| `POST /api/projects/{project_id}/advice` | `{feature, description}` | 200, AdvisoryResult JSON |

Upload возвращает статус queued или running. Job проходит `queued → running → succeeded/failed/timed_out/resource_limited → expired`; возможен `queued → failed` при ошибке диспетчера. В succeeded поле `analysis` обязательно; в failed/timed_out/resource_limited обязательно очищенное `error={code,message,findings?}`. В остальных состояниях этих полей нет. `Retry-After` на 202 и незавершенном GET job задает рекомендуемую задержку опроса в секундах, по умолчанию 2. Ошибка принятой задачи возвращается внутри GET job с HTTP 200, а не как ошибка нового HTTP-запроса.

GET project, plan, bundle и advice доступны только после succeeded. Для queued/running возвращается `409 ANALYSIS_NOT_READY`; для failed/timed_out/resource_limited — `409 ANALYSIS_UNAVAILABLE`. `partial` — нормальный успешный отчет с предупреждениями. Подготовка возвращает blocked/draft/ready_to_verify вместе с findings. Черновик допускает скачивание; blocked не генерируется. Недоступность AI выражается статусом дополнительной функции и не ломает экспорт.

DELETE сначала отзывает доступ и отменяет queued/running job. После 204 любые GET/POST и повторный DELETE проекта или его job дают 404; поздний результат worker не восстанавливает доступ. ZIP, отчеты, bundles и AI-данные удаляются; неудачная физическая очистка повторяется внутри сервера. TTL по умолчанию — 1 час. После TTL проект недоступен, а GET job может временно вернуть expired без содержимого, пока сохраняется запись о владельце; после удаления метаданных — 404.

## Локальный вызов ядра на доверенном примере

Следующий пример предназначен для локальных доверенных данных. Публичную загрузку сначала анализирует изолированный worker по [SERVER_RUNTIME.md](SERVER_RUNTIME.md). HTTP-адаптер использует сохраненный сервером отчет и тот же ZIP с проверкой fingerprint.

```python
from pathlib import Path

from nishtyak_core import ArchiveReader, ReproducibilityService, RunSpec, UserDecisions
from nishtyak_core.exporting import BundleWriter

service = ReproducibilityService()
snapshot = ArchiveReader().from_path(trusted_archive_path)
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

Конверт: `{code, message, findings?}`. Сообщения очищаются и не раскрывают пути сервера, исходники, ключи или traceback.

| HTTP | Случай |
| --- | --- |
| 400 | Некорректный JSON/options или архив, отклоненный до очереди |
| 401 | `SESSION_REQUIRED`: сессия отсутствует, недействительна или истекла |
| 403 | `REQUEST_FORBIDDEN`: неверный CSRF-токен или Origin |
| 404 | `RESOURCE_NOT_FOUND`: чужой, неизвестный, удаленный или полностью истекший ресурс |
| 409 | `ANALYSIS_NOT_READY`, `ANALYSIS_UNAVAILABLE`, `STALE_ANALYSIS`, `PLAN_BLOCKED` |
| 413 | `UPLOAD_LIMIT`: сжатая загрузка превышает серверный предел |
| 415 | Неподдерживаемый формат загрузки |
| 422 | Некорректный DTO, решение, запуск или неподдерживаемая конфигурация генерации |
| 429 | `QUEUE_FULL`, `RATE_LIMITED`, `STORAGE_QUOTA_EXCEEDED`, `ADVISORY_BUDGET_EXCEEDED`; обязательный `Retry-After` в секундах |
| 500 | Неожиданная ошибка сервера с общим сообщением |

Исключения ядра пока преимущественно ValueError. Для устойчивой классификации ошибок нужен адаптер с явными проверками либо типизированные исключения; производственный код не должен разбирать текст сообщения.

Лимиты распаковки проверяет worker после 202: `ARCHIVE_LIMIT` приходит в `error` задачи. Переполнение очереди, rate limiting, диск и бюджет AI единообразно дают 429; отдельный 503 для перегрузки не используется. При отказе upload клиент не получает частично созданный проект/job.

## Серверные квоты и обязанности адаптера

Начальный профиль: ZIP ≤10 MiB; распакованные данные ≤50 000 000 байт; ≤1000 записей; файл ≤10 MiB; коэффициент сжатия ≤200; исходник ≤1 000 000 байт; JSON options ≤64 KiB; отчет ≤8 MiB. Сервер отклоняет неизвестные поля и значения options сверх этих границ; клиент не повышает лимиты.

Для VM с 8 CPU и диском 120 GB: ≤2 running job на весь сервис, очередь ≤32 задач, у одного владельца ≤2 активных job (queued/running). Дополнительные квоты задаются конфигурацией: upload ≤5/мин на сессию и IP, polling ≤120/мин на сессию, хранилище ≤100 MiB на сессию и ≤2 GiB суммарно, TTL 1 час, отдельный лимит запросов/токенов AI. Резервирование квот и постановка в очередь атомарны; проверки выполняются до дорогостоящей обработки. Значения можно уменьшать на конкретном сервере; для изменения общей параллельности проверяют cgroups и ресурсы.

- Ограничить multipart на proxy/API и передать ZIP в ArchiveReader внутри изолированного worker: пути, дубли, симлинки, размер и коэффициент сжатия проверяются до создания снимка.
- Хранить снимок/отчет на сервере, назначать непредсказуемый ID, срок хранения и изолированную папку.
- Проверять владельца и состояние задачи на каждой ручке; не принимать пользовательский путь как серверный.
- Восстанавливать незавершенные задачи после рестарта, освобождать квоты после отмены/TTL, повторять неудачную очистку.
- Делать операции с тяжелыми файлами/синхронным AI-клиентом вне event loop.
- Передавать фронтенду только безопасные сериализаторы `to_dict()`, файлы ZIP и статусы.
- Отдавать ZIP строго по серверному PackagePlan, не по присланному списку файлов.
- Не выполнять загруженный код, shell-команды и build hooks.
