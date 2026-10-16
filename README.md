# nishtyak

Python-ядро кейса 03 Обнинского хакатона 2026: статический анализ научного проекта и подготовка пакета для повторного запуска.

## Запуск

Требуется Python 3.11 или новее.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
nishtyak analyze tests/fixtures/local_project
nishtyak build tests/fixtures/local_project --entry main.py \
  --python 3.11 --data 'data.csv: значения 2, 4, 6' \
  --expected 'mean=4.00' --output /tmp/local-project.zip
```

На Windows окружение активируется через `.venv\Scripts\activate`. CLI выводит JSON. Для базовой библиотеки достаточно `pip install -e .`; чтение Conda требует дополнительной зависимости `.[conda]`, также входящей в `.[dev]`.

## Подключение

Публичная точка входа — `ReproducibilityService`: `analyze` → `prepare` → `render`. ZIP собирает отдельный `BundleWriter`. Реализованы библиотека, CLI и самостоятельный внутренний HTTP-сервис с очередью, хранением, ownership и TTL. Пользовательский backend обращается к нему по Bearer + X-Owner-ID.

- [Архитектура и ограничения](docs/CORE_ARCHITECTURE.md)
- [Ручки API и подключение ядра](docs/API_CONTRACT.md)
- [OpenAPI контракт](docs/openapi.yaml)
- [Статус разработки и следующие задачи](docs/DEVELOPMENT_PLAN.md)
- [Серверный запуск и изоляция задач](docs/SERVER_RUNTIME.md)
- [Примеры JSON-контрактов](examples/contracts/)
- [Результаты проверки двух учебных проектов](examples/verification.json)
- [Клиент полного сервисного сценария](examples/service_client.py)
- [Приемка автоматической подготовки, lock и живого AI для v0.3.0](examples/resolution-verification.json)
- [Приемка веб-интерфейса и браузерного шлюза на VM](examples/web-verification.json)
- [Приемка двух публичных проектов для v0.2.0](examples/workflow-verification.json)
- [Источники технических решений](docs/research.json)

## Сервис логики

```sh
python -m pip install -e '.[service]'
export NISHTYAK_SERVICE_TOKEN=$(python -c 'import secrets; print(secrets.token_hex(32))')
nishtyak-service dev --port 8010
```

`dev` запускает HTTP и очередь на собственных тестовых проектах через subprocess. Серверный профиль: `deploy/service-compose.yaml`, отдельный диспетчер и offline-контейнер на анализ. Команды развертывания и проверки — в [серверной спецификации](docs/SERVER_RUNTIME.md), ручки для второго backend — в [API-контракте](docs/API_CONTRACT.md).

## Веб-интерфейс

Фронтенд из ветки `web` объединен с сервисом: Vue 3, TypeScript, Vite, Pinia. Проекты используют HTTP: загрузка ZIP/`.py`, анализ, просмотр файлов, сохранение решений, план и скачивание настоящего пакета. Моки проектов удалены. Регистрация, команды и приглашения остаются отдельным участком второго разработчика.

Для локальной проверки установите `pip install -e '.[service,web,dev]'` и `npm ci`. Запустите три процесса с одинаковым серверным `NISHTYAK_SERVICE_TOKEN` у двух Python-процессов:

```sh
nishtyak-service dev --port 8010
nishtyak-web --port 8020
npm run dev
```

Откройте `http://127.0.0.1:5173`. Vite направляет `/api` в браузерный шлюз на 8020. Локальный режим явно обозначен в интерфейсе, не требует фиктивного входа и использует одну рабочую область. Шлюз принимает только loopback-соединения. Внутренний API и ключ DeepSeek не передаются браузеру; переменные `VITE_*` не должны содержать секретов. Моки авторизации/команд включаются отдельно и только явно: `VITE_USE_MOCK_API=true`.

`npm run build` проверяет типы и собирает `dist`; `npm test` проверяет сохранение и переключение проектов. Для сервера предусмотрен отдельный ограниченный контейнер `deploy/web-compose.yaml`: интерфейс и `/api` доступны на loopback-порту 8081. [Развертывание и подключение авторизации коллеги](docs/SERVER_RUNTIME.md), [контракт шлюза](docs/API_CONTRACT.md).

Все экраны исходного интерфейса доступны и в локальной проверке: профиль, меню пользователя, рабочая область, участники, приглашения и вход. Действия аккаунтов и команд, для которых backend ещё разрабатывается, обозначены в интерфейсе; они не заменены фиктивными успешными ответами.

Для совместной проверки через интернет предусмотрен `deploy/preview-compose.yaml`: тот же интерфейс и настоящие проектные операции в общей рабочей области, вход по отдельному коду проверки и подписанной защищенной cookie. Это отдельный доступ для демонстрации, а не готовая пользовательская регистрация. [Настройка публичной проверки](docs/SERVER_RUNTIME.md).

Настоящие исходные проекты доступны через кнопки на странице загрузки и в `public/examples`: `scientific-measurements.zip` (NumPy/pandas, локальный модуль, CSV) и `local-calibration.zip` (локальный пакет и CSV). Оба распакованы и запущены в чистых venv, вычисленные CSV совпали с эталонами. Состав, хеши и проверенная среда — в `public/examples/manifest.json`; исходники — в `examples/upload-projects/`.

## Дополнительные функции

```sh
nishtyak environment > /tmp/author-environment.json
nishtyak analyze /path/to/project --author-environment /tmp/author-environment.json
nishtyak conda /path/to/project
nishtyak build tests/fixtures/local_project --entry main.py --python 3.11 \
  --data 'data.csv' --docker --output /tmp/local-project-docker.zip
```

DeepSeek подключается через серверные переменные `DEEPSEEK_API_KEY` и `DEEPSEEK_MODEL`; необязательный `DEEPSEEK_BASE_URL` по умолчанию равен `https://api.deepseek.com`.

```sh
nishtyak advise /path/to/project --feature explain
```

Функции советника: `explain`, `readme`, `packages`. Предложения требуют подтверждения и не изменяют зависимости автоматически. Недоступность API не мешает основному сценарию.

## Проверки

```sh
pytest -q
ruff check src tests
ruff format --check src tests
```

Текущий результат: 681 Python-тест, 6 тестов фронта, проверка типов, production build, Ruff и форматирование проходят. Браузерный сценарий `.py`/ZIP, удаление, граф, lock, Dockerfile и живой DeepSeek проверены через собранный интерфейс на VM. Решения, lock и байты архива сохранились после рестарта шлюза. [Веб-приемка](examples/web-verification.json).

v0.3.0 развернут на VM: 38 проверок изоляции и 32 HTTP-проверки, совпадение OpenAPI и установленного кода. Два подготовленных проекта (NumPy/Pandas и локальный модуль) прошли автоматический выбор запуска, uv lock, экспорт и повторение в чистых Python 3.11 окружениях с результатом `4.0`. Живой DeepSeek проверен для запуска и README. Lock и решения пережили перезапуск, повторный анализ удаляет прежний lock. [Результаты приемки ядра](examples/resolution-verification.json). Загруженный пользовательский код сервис не выполняет.

v0.3.0 добавляет полный статический граф локальных импортов, stdlib выбранного Python, запуск без обязательного main, объяснимый черновик без лишних обязательных полей, wheel-only lock с хешами и DeepSeek с ограниченными инструментами чтения. Lock — новый согласованный набор под выбранные Python/OS, а не восстановленная среда автора. Контракт новых ручек и SDK описан в [API_CONTRACT.md](docs/API_CONTRACT.md).

Версия 0.2.0 добавляет выбор корня ZIP и `src`, декларации зависимостей/extras, запуск `module:function`, среду автора через HTTP и изолированный worker, сохранение решений с ревизией и повторный анализ исходного ZIP. Для второго backend в wheel входит `nishtyak_client.LogicClient`; он использует стандартную библиотеку Python. Порядок интеграции и поля ошибок — в [API_CONTRACT.md](docs/API_CONTRACT.md). Vue-интерфейс подключен через браузерный шлюз; пользовательская авторизация и команды остаются у второго backend.

Приемка v0.2.0: два публичных проекта прошли HTTP-путь, сохранение и восстановление решений, экспорт и повторение в чистых venv. Исходные результаты совпали; оба сгенерированных Dockerfile собраны и запущены без сети. Решения пережили перезапуск контейнера, повторный анализ и stale-защита проверены на VM. Доказательства: [workflow-verification.json](examples/workflow-verification.json).
