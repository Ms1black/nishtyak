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

Публичная точка входа — `ReproducibilityService`: `analyze` → `prepare` → `render`. ZIP собирает отдельный `BundleWriter`. В этой ветке реализованы библиотека и CLI. HTTP-контракт подготовлен для разработчика API.

- [Архитектура и ограничения](docs/CORE_ARCHITECTURE.md)
- [Ручки API и подключение ядра](docs/API_CONTRACT.md)
- [OpenAPI контракт](docs/openapi.yaml)
- [Статус разработки и следующие задачи](docs/DEVELOPMENT_PLAN.md)
- [Серверный запуск и изоляция задач](docs/SERVER_RUNTIME.md)
- [Примеры JSON-контрактов](examples/contracts/)
- [Результаты проверки двух учебных проектов](examples/verification.json)
- [Источники технических решений](docs/research.json)

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

Текущий результат: 231 тестов проходят. Два собственных примера повторены в отдельных чистых venv с результатом `mean=4.00`; wheel установлен и проверен отдельно. ZIP-анализ проверен в отдельном процессе и через stdin/stdout worker. Docker-конфигурация и shell-синтаксис проверены; сборка образа, ограничения на Linux и реальные запросы DeepSeek пока не проверялись. Загруженный пользовательский код ядром не выполняется.
