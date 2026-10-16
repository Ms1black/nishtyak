# Калибровка измерений с локальным модулем

Пример проекта без внешних библиотек: основной скрипт использует локальный
пакет `calibration`, а тот — стандартные `dataclasses` и `math`.
Данные синтетические, предназначены для проверки упаковки.

## Входные данные

`data/measurements.csv` содержит идентификатор `measurement_id` и исходное
число `raw_value`. Формула: `calibrated_value = raw_value * 0.5 - 1.0`.

## Запуск

Из корня проекта, Python 3.11 или новее:

```sh
python -m venv .venv
.venv/bin/python experiment.py
```

В Windows используйте `.venv\Scripts\python.exe` вместо `.venv/bin/python`.
Установка пакетов не нужна: `calibration` — исходники внутри проекта,
его не следует искать или устанавливать из PyPI.

## Результат

Программа печатает CSV и записывает `outputs/calibrated.csv`.
Эталон — `expected/calibrated.csv`:

```csv
measurement_id,calibrated_value
m1,4.000
m2,6.000
m3,8.000
```

Выполнение не требует сети. Это исходный проект для загрузки в упаковщик,
а не уже собранный сервисом пакет.
