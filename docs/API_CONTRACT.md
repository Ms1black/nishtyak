# Внутренний API сервиса логики

Маршруты реализованы в `nishtyak_service.api`. Машинный контракт: [openapi.yaml](openapi.yaml); он выгружается из работающего FastAPI-приложения и проверяется тестом на расхождение. Серверный запуск и изоляция: [SERVER_RUNTIME.md](SERVER_RUNTIME.md).

## Граница ответственности

Сервис логики принимает ZIP, хранит проекты и отчеты, ведет очередь, запускает статический анализ, проверяет владельца и TTL, строит план и ZIP, обращается к DeepSeek. Загруженный код, команды и build hooks не исполняются.

Второй backend аутентифицирует пользователя и определяет его постоянный `owner_id`, затем вызывает этот API по внутренней сети. Логин, пользовательская сессия, cookie, CSRF и браузерный API принадлежат второму backend. Сервис логики не предназначен для прямых запросов из браузера; CORS выключен.

Каждый запрос к `/v1/*` содержит:

```text
Authorization: Bearer <NISHTYAK_SERVICE_TOKEN>
X-Owner-ID: <идентификатор пользователя из доверенного backend>
```

Секрет сервиса: 32–512 печатных ASCII-символов, хранится на серверах и не передается фронтенду. `X-Owner-ID` соответствует `[A-Za-z0-9_.:-]{1,128}`; подходят UUID. Backend получает владельца из своей аутентификации и перезаписывает этот заголовок, не берет его из пользовательского JSON или произвольного входного заголовка. Сервис проверяет владельца каждого проекта и job. Чужой, неизвестный, удаленный или истекший ресурс дает одинаковый `404 RESOURCE_NOT_FOUND`.

## Автоматическая подготовка и разрешение версий (API 1.2)

После анализа `GET /v1/projects/{id}/preparation` возвращает план из сохраненных решений (или автоматических значений) и `launch`: кандидаты, рекомендация, основание. Одиночному научному скрипту не требуется `main.py`, функция main или main guard. Явный выбор пользователя имеет приоритет; `plan.run_spec_origin` различает `author`, `inferred`, `unknown`. Несколько равноправных команд остаются вариантами. `GET /v1/projects/{id}/graph` возвращает статические локальные связи, строки и ID свидетельств. Модули проекта при этом не импортируются.

`POST /plan` допускает пустое тело; `/bundle` принимает `{}`. В обоих случаях используются сохраненные решения либо новый `UserDecisions(analysis_id)`. Неизвестные версии/описание/данные/команда могут оставить `draft`, который разрешено скачать. Это не мешает дальнейшим уточнениям. `blocked` по-прежнему запрещает экспорт.

Разрешение версий выполняется отдельным явным запросом:

```json
POST /v1/projects/{id}/resolve
{
  "target": {"python_version": "3.11", "platform": "linux-x86_64"},
  "decisions": null
}
```

`decisions` необязательны. Поддержаны `linux-x86_64`, `linux-aarch64`, `macos-arm64`, `macos-x86_64`, `windows-x86_64`. Ответ `DependencyLock`: ID, привязка к анализу/требованиям/цели, точные прямые и транзитивные версии, SHA-256 хеши, происхождение. Сервис хранит последний lock проекта; `GET /lock` возвращает его после рестарта. Повторный анализ удаляет lock. Изменение требований делает прежний lock несовместимым с экспортом.

Скачать пакет с этим набором: `POST /bundle` с `{"lock_id":"<64 hex>"}`. В ZIP добавляются `requirements.lock.txt`, `dependency-lock.json` и инструкция установки с `--require-hashes --only-binary=:all:`. Исходный `requirements.txt` сохраняет прямые требования. Lock фиксирует новое согласованное разрешение под указанную платформу/Python; он не доказывает запуск и не восстанавливает неизвестную среду автора. Неопределенности импорта сохраняются в плане. URL/Git/local/editable и пакеты без подходящих wheels в этом режиме не собираются.

uv получает только нормализованные имена и PEP 508 ограничения, без исходного ZIP, setup.py, build hooks и настроек проекта. Таймаут 45 секунд; лимит 2 вызова на владельца и 20 на сервис в минуту. Запрос держит общий слот; SDK устанавливает сетевой timeout минимум 60 секунд. Коды ошибок включают `TARGET_INVALID`, `TARGET_PYTHON_MISMATCH`, `TARGET_PROFILE_MISMATCH`, `TARGET_PATCH_REQUIRED`, `RESOLUTION_CONFLICT`, `RESOLUTION_TIMEOUT`, `INDEX_UNAVAILABLE`, `LOCK_STALE`, `LOCK_NOT_FOUND`. Несовместимые ограничения не исправляются молча. Если целевой Python отличается от `analysis.options.python_version`, сначала повторите анализ сохраненного ZIP с соответствующим профилем; это важно для модулей вроде tomllib, появившихся только в новых Python.

## DeepSeek с чтением проекта

Старая `/advice` работает по метаданным. Новая `POST /project-advice` принимает `{"feature":"entrypoint","share_source":true,"description":""}`. `feature`: `explain`, `readme`, `packages`, `entrypoint`. `share_source:true` — явное разрешение передать внешнему провайдеру ограниченные очищенные фрагменты; без него 422. До этого действия фронтенд объясняет передачу кода. Ключ остается на сервере. Без ключа/модели приходит честное `status: unavailable`; основной сценарий работает самостоятельно.

Агент имеет четыре виртуальных инструмента: дерево, ограниченные фрагменты исходников, факты анализа, кандидаты запуска. Доступа к ОС, shell и произвольным адресам у инструментов нет. Максимум 4 раунда, 6 вызовов, 24 KiB результатов инструментов, 15 секунд логического и 20 секунд жесткого времени. Из контекста исключаются данные и файлы с обнаруженными секретами; остальное редактируется до передачи. Это эвристическая фильтрация, а не гарантия обнаружить любой произвольный секрет.

Ответ schema2 содержит `suggestions`, `unknowns`, расход токенов и счетчики. Предложение ссылается на реальные свидетельства; точка входа выбирается только из проверенных статических кандидатов. Предложения требуют подтверждения и не меняют зависимости или решения. README-режим возвращает описательный текст; команды и версии формируются детерминированно.

SDK: `preparation(id)`, `graph(id)`, `plan(id)`, `resolve(id,"3.11","linux-x86_64")`, `lock(id)`, `bundle(id,lock_id=lock["id"])`, `project_advice(id,"readme",share_source=True)`.

## Последовательность интеграции

| Шаг | Метод и путь | Вход | Ответ |
| --- | --- | --- | --- |
| Загрузить | `POST /v1/projects` | multipart: один `file`, необязательные JSON-строки `options` и `author_environment` | `202 SubmittedProjectDTO`: `project_id`, `job_id`, `status` |
| Дождаться анализа | `GET /v1/jobs/{job_id}` | ID job | `200 JobDTO`: статус, время, отчет или ошибка задачи |
| Получить отчет | `GET /v1/projects/{project_id}` | ID проекта | `200 ProjectReportDTO`: `project_id`, `analysis` |
| Повторить анализ | `POST /v1/projects/{project_id}/analysis` | `{"options": {...}, "author_environment": {...}}`; среда необязательна | `202`, новый `job_id`, тот же ZIP и срок хранения |
| Восстановить решения | `GET /v1/projects/{project_id}/decisions` | ID проекта | `{analysis_id, revision, decisions, updated_at}` |
| Сохранить решения | `PUT /v1/projects/{project_id}/decisions` | `{"expected_revision": 0, "decisions": {...}}` | Документ с увеличенной ревизией |
| Проверить решения | `POST /v1/projects/{project_id}/plan` | `DecisionsDTO` JSON | `200 PackagePlan`, включая `state` и `findings` |
| Скачать результат | `POST /v1/projects/{project_id}/bundle` | `{"decisions": {...}, "include_dockerfile": false}` | `200 application/zip`, фиксированное имя `reproducible-project.zip` |
| Получить совет | `POST /v1/projects/{project_id}/advice` | `{"feature": "explain", "description": "..."}` | `200 AdvisoryResult` |
| Удалить | `DELETE /v1/projects/{project_id}` | ID проекта | `204` без тела |

`project_id` и `job_id` — отдельные серверные ID из 32 строчных hex-символов. `analysis_id` — 20 hex-символов из полученного отчета. Клиент отправляет решения, сервис использует сохраненный отчет и ZIP. Клиентские `AnalysisReport`, `PackagePlan`, пути сервера или списки файлов как доверенные данные не принимаются.

Жизненный цикл job: `queued → running → succeeded | failed | timed_out | resource_limited`. Опрос достаточно начинать с интервала 2 секунды; успешные ответы не устанавливают `Location` или `Retry-After`. `created_at` и `expires_at` — Unix time в секундах. `JobDTO.analysis` и `JobDTO.error` могут быть `null`: при успехе возвращается отчет, при ошибке задачи — `{code, message}`. Ошибка принятого анализа доступна через `GET job` с HTTP 200. Невалидный непустой ZIP поэтому может получить 202, а затем `failed / ARCHIVE_INVALID`.

`GET project` до завершения дает `409 ANALYSIS_NOT_READY`, после неуспешного анализа — `409 ANALYSIS_UNAVAILABLE`. Операции plan/bundle/advice требуют успешного анализа; до него дают `409 PROJECT_NOT_READY`. `partial` внутри отчета — успешный анализ с предупреждениями, отдельного состояния job для него нет. План имеет `blocked`, `draft` или `ready_to_verify`; `blocked` запрещает экспорт, черновик можно скачать. `ready_to_verify` не означает, что пользовательский проект исполнялся или проверен запуском.

TTL по умолчанию — 3600 секунд с создания проекта. После удаления или TTL проект и его job сразу недоступны: ответ 404, состояния `expired` в API нет. Фоновая очистка отменяет обработку и удаляет данные; неудачная очистка повторяется. Повторный DELETE также дает 404. Поздний результат worker не восстанавливает доступ. Прерванные рестартом running-задачи завершаются ошибкой `WORKER_RESTARTED` после очистки worker; queued-задачи сохраняются.

Пример загрузки из доверенного backend или терминала оператора:

```bash
LOGIC_URL=http://127.0.0.1:8010
OWNER_ID=2bafebca-3bf4-4af6-95ad-58a91fc8eda9
: "${NISHTYAK_SERVICE_TOKEN:?Set the service token in the environment}"
curl --fail-with-body "$LOGIC_URL/v1/projects" \
  -H "Authorization: Bearer $NISHTYAK_SERVICE_TOKEN" \
  -H "X-Owner-ID: $OWNER_ID" \
  -F 'file=@project.zip;type=application/zip' \
  -F 'options={"source_roots":["."],"max_files":1000}'
```

Для следующих запросов сохраняются оба заголовка. Имена загруженных файлов игнорируются; пути и ID назначает сервер.

## Решения и дополнительные функции

Пример запроса `/plan` для проекта с `main.py`; замените `analysis_id` значением из полученного `analysis.analysis_id`:

```json
{
  "analysis_id": "00000000000000000000",
  "run_spec": {"kind": "script", "target": "main.py", "args": [], "working_directory": "."},
  "python_version": "3.11",
  "data_confirmed": true,
  "input_description": "Какие входные данные нужны",
  "expected_output": "Как проверить результат"
}
```

Обязателен только `analysis_id`. Для черновика `run_spec` может быть `null`. Поля `packages` задают отображение импортируемый модуль → подтвержденное PEP 508 требование; `versions` — модуль/дистрибутив → версия или ограничение. Например, `packages={"yaml":"PyYAML>=6"}`, `versions={"pandas":"==2.2.0"}`. Есть `excluded_modules`, `excluded_files`, `description`, `input_description`, `expected_output`, `data_confirmed`. Все неизвестные поля отклоняются; строки, коллекции и типы ограничены схемой. Числа и строки не приводятся автоматически к boolean.

Для `/bundle` тот же объект передается в поле `decisions`. При другом `analysis_id` ответ `409 STALE_ANALYSIS`. Сервис пересчитывает план и проверяет fingerprint сохраненного ZIP, не экспортирует присланный клиентом план.

В `/advice` доступны `feature`: `explain`, `readme`, `packages`. Ключ и модель задаются на сервере: `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`. При отключенном или недоступном провайдере возвращается `AdvisoryResult.status=unavailable`; экспорт остается доступен. Предложения требуют подтверждения и не меняют зависимости автоматически. Conda остается отдельной функцией Python/CLI. Снимок среды автора поддержан загрузкой и повторным анализом через HTTP.

## Структура проекта и подготовка

Отчет версии 2 содержит `layout`: выбранный `root`, фактические архивные `source_roots`, `root_candidates` и `ambiguous`. В `options.project_root` можно указать каталог внутри ZIP; `null` включает однозначный выбор. Настраиваемые `options.source_roots` относительны выбранному корню; при стандартном `["."]` каталог `src` обнаруживается автоматически. Неоднозначный проект остается `partial`: выбрать корень и вызвать повторный анализ.

Пути `files`, `entrypoints` для скриптов, `excluded_files` и `run_spec.working_directory` сохраняют координаты исходного ZIP. `run_spec.target` для скрипта относителен рабочей папке, для `module` — dotted module, для `callable` — `module:attribute`. Например, ZIP с `repo-main/src/tool` использует `working_directory="repo-main"`, `kind="callable"`, `target="tool:main"`. Экспортированный `run_project.py` настраивает эти пути только при явном запуске получателем; код и build hooks не запускаются сервисом.

`declarations` — отдельные исходные требования с устойчивыми `id`, файлом, строкой (если доступна), группой и PEP 508 строкой. `optional_groups` перечисляет extras, включая пустые. Решения выбирают `selected_extras` и `declaration_actions={"<id>": {"action":"keep|replace|exclude", "requirement":"..."}}`. `requirement` допустим только для `replace`. При замене сохраняются исходные markers/extras, если автор их не указал. Исключение декларации отличается от `excluded_modules`: оставшийся импорт требует явного отображения пакета либо исключения импорта. Сервис пересчитывает конфликты по эффективным требованиям, сохраняя исходные факты в `plan.report`.

Снимок среды собирается **на машине автора**: `nishtyak environment > author-environment.json`. JSON содержит `python_version`, `platform`, `distributions=[{name,version,modules}]`, без переменных окружения. Передается строковым полем `author_environment` при загрузке (до 256 KiB) или объектом при повторном анализе. Report возвращает `author_environment_id`, `author_python_version`, `author_platform`. Это сведения об установленных пакетах, не доказательство запуска; существующие ограничения не перезаписываются молча.

## Сохранение и повторное открытие

После успешного анализа GET decisions дает `revision=0, decisions=null`. PUT принимает `expected_revision` и полный `DecisionsDTO`. Сервис проверяет решения, очищает описания от секретов и атомарно сохраняет их. Повторный GET восстанавливает документ. План остается производным: POST plan/bundle получает восстановленные решения. Несовпавшая ревизия дает `409 DECISIONS_REVISION_CONFLICT`: перечитать документ и показать изменения, не повторять запись вслепую.

Повторный анализ не продлевает TTL и не требует нового ZIP. Он недоступен при активном worker/операции, создает новый `job_id`, очищает текущие решения и увеличивает ревизию; история решений в этой версии не хранится. Интерфейс должен объяснить сброс перед изменением параметров анализа. Если новая среда не передана, используется сохраненный снимок автора. После завершения получить новый отчет и сформировать решения с его `analysis_id`; старые решения дают 409. Удаление и TTL распространяются на ZIP, отчет, среду и решения.

## Клиент для второго backend

В wheel входит `nishtyak_client.LogicClient`, только стандартная библиотека Python. Его блокирующие вызовы переносить в thread pool асинхронного backend. Пример полного сценария: [service_client.py](../examples/service_client.py).

```python
from nishtyak_client import LogicClient

client = LogicClient(internal_url, service_token, authenticated_user_id)
project = client.submit(zip_bytes, options, author_environment)
job = client.wait(project["job_id"])
report = job["analysis"]
document = client.decisions(project["project_id"])
# UI формирует choices из report; analysis_id должен совпадать.
saved = client.save_decisions(project["project_id"], choices, document["revision"])
plan = client.plan(project["project_id"], saved["decisions"])
package = client.bundle(project["project_id"], saved["decisions"])
```

Клиент передает код/поле/HTTP-статус через `LogicClientError`, не повторяет мутации автоматически и запрещает redirects, чтобы служебный ключ не ушел на другой адрес. В браузер отправляются только пользовательские данные и ZIP через второй backend. Сам frontend пока разрабатывается отдельно; его фактическое подключение не заявляется проверенным.

## Проверки состояния

| Путь | Заголовки | Поведение |
| --- | --- | --- |
| `GET /healthz` | Не нужны | `200 {"status":"ok"}` — HTTP-процесс работает |
| `GET /readyz` | Только Bearer | `200 {"status":"ready"}` или `503 {"status":"unavailable"}` по heartbeat диспетчера |
| `GET /openapi.json` | Только Bearer | Актуальная OpenAPI-схема |

Интерактивные `/docs` и `/redoc` отключены. Readiness не заменяет проверку полного сценария на целевой VM. Ответ 503 readiness имеет `Retry-After: 2`.

## Ошибки и лимиты

Ошибки ручек приложения имеют конверт `{code, message, field?}`, без исходного пользовательского ввода, серверных путей и traceback. Имена моделей, поля и ограничения доступны в OpenAPI. Коды результата worker находятся в `JobDTO.error`; их не следует смешивать с ошибкой самого HTTP-запроса. HTTP-сервер или proxy могут отклонить запрос до приложения, поэтому backend также обрабатывает сетевые ошибки и ответы без JSON.

| HTTP | Примеры кодов | Действие backend |
| --- | --- | --- |
| 400 | `OWNER_REQUIRED`, `INVALID_LENGTH`, `INVALID_MULTIPART`, `ARCHIVE_INVALID` | Исправить запрос |
| 401 | `UNAUTHORIZED` | Проверить серверный ключ; пользователю ключ не показывать |
| 404 | `RESOURCE_NOT_FOUND` | Ресурс недоступен этому владельцу |
| 408 | `BODY_TIMEOUT` | Загрузка не завершена за 30 секунд |
| 409 | `ANALYSIS_NOT_READY`, `ANALYSIS_UNAVAILABLE`, `PROJECT_NOT_READY`, `PROJECT_BUSY`, `STALE_ANALYSIS`, `ANALYSIS_STALE`, `DECISIONS_REVISION_CONFLICT`, `PLAN_BLOCKED` | Проверить состояние или решения |
| 413 | `BODY_TOO_LARGE`, `ARCHIVE_TOO_LARGE`, `OPTIONS_TOO_LARGE`, `BUNDLE_LIMIT` | Уменьшить данные |
| 422 | `INVALID_REQUEST`, `INVALID_UPLOAD`, `INVALID_OPTIONS`, `DECISIONS_INVALID`, `EXPORT_INVALID` | Исправить поля по схеме |
| 429 | `HTTP_BUSY`, `RATE_LIMIT`, `OWNER_JOB_LIMIT`, `OWNER_STORAGE_LIMIT` | Повторить с задержкой, учитывать квоты владельца |
| 500 | `INTERNAL_ERROR`, `REPORT_UNAVAILABLE`, `STORAGE_UNAVAILABLE` | Зафиксировать код и обратиться к оператору |
| 503 | `QUEUE_FULL`, `WORKER_BUSY`, `STORAGE_UNAVAILABLE` | Повторить с задержкой |
| 507 | `STORAGE_LIMIT` | Общая квота хранилища исчерпана |

Ответы 429 и 503 с конвертом ошибки содержат `Retry-After: 2`. Rate limit использует фиксированные окна: возможны повторные 429 после этой задержки, поэтому backend увеличивает интервал повторов. Примеры кодов в таблице не являются закрытым enum.

Начальные пределы задаются `Settings`: ZIP ≤10 MiB; options ≤64 KiB; JSON запрос ≤256 KiB; 8 активных HTTP-запросов; 2 одновременно выполняемых операции; 32 queued-задачи; 2 активные job одного владельца. Plan/bundle/advice используют те же глобальные слоты, а на одном проекте одновременно идет одна такая операция. Режим запуска и квоты принадлежат оператору, клиент не повышает их через options.

Хранилище резервируется атомарно: ≤2 GiB всего, ≤256 MiB на владельца. Резерв одного проекта равен `archive_bytes + 2 × max_report_bytes + max_bundle_bytes + 1 MiB`: при стандартных лимитах это размер ZIP +97 MiB. Это бюджет будущих отчетов и экспорта, а не только текущий размер ZIP; свободное место на диске VM не отменяет квоту. Отчет ограничен 8 MiB, ZIP результата — 80 MiB. После удаления резерв освобождается, когда физическая очистка завершена.

Лимиты на владельца за минуту: загрузка 5, чтение job/project суммарно 120, plan 30, bundle 10, advice 5, удаление 30. Для включенного AI дополнительно действуют 10 запросов на владельца и 100 на сервис в сутки; это лимит запросов, не токенов. Сервис не определяет пользовательский IP: ограничения публичного трафика и IP остаются на втором backend/proxy.

Worker проверяет ZIP без распаковки на диск: ≤1000 записей, ≤50 000 000 байт распакованных данных, ≤10 MiB на файл, коэффициент сжатия ≤200; запрещены небезопасные пути, дубли, симлинки, шифрование и специальные файлы. Эти проверки происходят после постановки в очередь. Контейнерный профиль и его проверка описаны отдельно в серверной спецификации.
