# Стартовые наборы: что внутри и что взято

## transfer-core-starter — наш набор

Взят целиком, структура сохранена, изменён только способ запуска.

| Что | Куда легло | Изменения |
| --- | --- | --- |
| `data/*.json` | `data/` | без изменений |
| `examples/*.json` | `examples/` | без изменений |
| `mock-provider/app/*.py` | `mock_provider/` | без изменений кода, запуск через `uv run python -m mock_provider.main` вместо `pip install -r` |
| `scripts/chaos_run.py` | `scripts/chaos_run.py` | без изменений, только `+x` |
| `docker-compose.yml` | — | заменён нашим, переменные и тома сохранены |

`mock-provider` — aiohttp, требует `aiohttp` в зависимостях. `DATA_DIR=/data`, том `./data:/data:ro` — как в стартовом compose. Код мок-провайдера считается вендорным: под наш линт (ruff) не подпадает, комментарии в нём не трогаем.

## payment-stack-starter — не наш набор

Другой стартовый набор (платёжная платформа: Vault, RabbitMQ, worker, reporting, nginx, prometheus, миграции `00x_*.sql`, свой mock-provider на HTTPS). К Transfer Core отношения не имеет, в проект не переносится. Полезное на будущее: идея идемпотентных SQL-миграций и `runtime.env`-подхода; к нашим решениям не применяем.

## Что важно из данных

- `currencies.json`: 10 валют, `minor_units` различается — JPY и VND `0`, KWD `3`, остальные `2`. Форматирование сумм обязано идти от `minor_units`, а не от фиксированных двух знаков.
- `tariffs.json`: 11 направлений. `fixed` и `min` бывают с разной точностью (`1.000`, `0.500`, `0`, `5000`), а `charged_in` бывает `TARGET` (RU-VN-BANK, KW-JP-BANK) — комиссия в валюте получателя требует пересчёта.
- `providers.json`: 7 провайдеров, у каждого своя валюта (USD, EUR, THB, JPY, KWD) и свои лимиты в ней. `prov_delta` выключен (`enabled=false`), у `prov_echo` есть окно обслуживания — оба случая обязаны отсекаться фильтром.
- `rates.json`: 17 пар. Нет ни одной пары с KZT — направление KZ-TH-BANK необслуживаемо (`RATE_UNAVAILABLE`). RUB→PHP и RUB→AED протухли (октябрь против ноября) — проверка свежести обязательна. RUB→KWD напрямую нет, но есть RUB→EUR→KWD — кросс-курс нужен.
- `clients.json`: 13 клиентов. У `c_003` баланс в RUB, а суточный лимит в USD — лимит надо конвертировать. `c_007` с балансом 1000 RUB ниже минимальной суммы направления RU-TH-BANK с комиссией — `INSUFFICIENT_FUNDS`.
- `examples/`: 10 кейсов, включая `TARGET`-режим, минимальную комиссию, `percent+fixed`, нулевые знаки (JPY) и три знака (KWD), комиссию в валюте получателя.
- `scripts/chaos_run.py`: чистый stdlib, ходит в наш API, сверяет `available + reserved` с ledger мок-провайдера, понимает статусы `NEEDS_REVIEW` и родственные.
