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
- [Источники технических решений](docs/research.json)

## Сервис логики

```sh
python -m pip install -e '.[service]'
export NISHTYAK_SERVICE_TOKEN=$(python -c 'import secrets; print(secrets.token_hex(32))')
nishtyak-service dev --port 8010
```

`dev` запускает HTTP и очередь на собственных тестовых проектах через subprocess. Серверный профиль: `deploy/service-compose.yaml`, отдельный диспетчер и offline-контейнер на анализ. Команды развертывания и проверки — в [серверной спецификации](docs/SERVER_RUNTIME.md), ручки для второго backend — в [API-контракте](docs/API_CONTRACT.md).

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

Текущий результат: 382 теста проходят. Проверены HTTP-сценарий загрузка → отчет → план → ZIP, запрет доступа другому владельцу, TTL/удаление, постоянная очередь, восстановление, отмена и отказоустойчивая очистка. Два собственных примера ранее повторены в чистых venv с результатом `mean=4.00`. На Linux VM собраны образы, проверены изоляция/cgroups/таймаут/очистка и HTTP-сценарий; очередь пережила перезапуск. Результаты: [service-verification.json](examples/service-verification.json). Платных запросов DeepSeek не было. Загруженный пользовательский код не выполняется.
