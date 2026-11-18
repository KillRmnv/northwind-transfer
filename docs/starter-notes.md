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

`mock-provider` — aiohttp, требует `aiohttp` в зависимостях. `DATA_DIR=/data`, том `./data:/data:ro` — как в стартовом compose. Код мок-провайдера считается вендорным: под наш линт (ruff) не подпадает, комментарии в нём не трогаем. Из стартового compose унаследованы дефолты `MOCK_SLOW_SEC=30`, `MOCK_SETTLE_SEC=2` (`MOCK_HANG_SEC=35`, `MOCK_PORT=9000` — см. `config.py`).

## Поведение мок-провайдера, важное для реализации

Сверено с кодом (`main.py`, `behavior.py`, `store.py`, `webhooks.py`, `reference_data.py`):

- Сценарные ручки для тестов: заголовок `X-Scenario` (`ok`/`decline`/`error_5xx`/`slow`) на `POST`, админ-эндпоинты `PUT/DELETE /{provider}/admin/scenario`, сверка — `GET /{provider}/admin/ledger`, поиск — `GET /{provider}/payouts?reference=`.
- Идемпотентность мока: тот же ключ + другое тело (`reference`/`amount`/`currency`) — `409 IDEMPOTENCY_KEY_REUSED`; тот же ключ + то же тело — повтор ответа.
- Допуск по лимитам ±10%: `within_limits` считает `min×0.9 … max×1.1`. Наш фильтр строже — это норма.
- Коды ошибок мока (маппятся в статусы попыток, не в наши коды напрямую): `RECIPIENT_ACCOUNT_INVALID` (422, запись `FAILED` создаётся), `CURRENCY_NOT_SUPPORTED`, `AMOUNT_OUT_OF_LIMITS` (422), `PROVIDER_DISABLED` / `PROVIDER_MAINTENANCE` (503), `INTERNAL_ERROR` (500), `GATEWAY_TIMEOUT` (504 — трактовать как неизвестный итог, см. D7).
- Chaos-обрывы: `DROP_LOST` (записи нет — можно к следующему), `DROP_CREATED` / `HANG_CREATED` / `ERROR_CREATED` (запись есть — сначала `GET ?reference=`).
- Ложь чтений: `visibility_lag` 3–15 с (свежая выплата — `404`), `stale_read` 1–3 с (`GET` врёт `PROCESSING` после settle) — поллинг обязан сходиться, одиночный `GET` итогом не считается.
- Вебхуки: обычная доставка всегда с корректным `X-Signature`; без/кривая подпись — только `forged` (~6%) с ложным статусом. Дубли 1–2 (30%), `late_processing` (20%), `contradiction` (4%, решается `occurred_at`), `lost_webhook` (12%), `unknown_event` (5%).
- Свежесть курсов: `STALE_AFTER = 6ч` — всё моложе подменяется на «почти сейчас», старше отдаётся как есть (поэтому RUB→PHP/AED протухшие).
- Суммы в запросах к моку валидируются строго по `minor_units` (`amount_is_valid`); кросс-конвертация — прямой, обратный и двухзвенный курс (`convert`).

## payment-stack-starter — не наш набор

Другой стартовый набор (платёжная платформа: Vault, RabbitMQ, worker, reporting, nginx, prometheus, миграции `00x_*.sql`, свой mock-provider на HTTPS). К Transfer Core отношения не имеет, в проект не переносится. Полезное на будущее: идея идемпотентных SQL-миграций и `runtime.env`-подхода; к нашим решениям не применяем.

## Что важно из данных

- `currencies.json`: 10 валют, `minor_units` различается — JPY и VND `0`, KWD `3`, остальные `2`. Форматирование сумм обязано идти от `minor_units`, а не от фиксированных двух знаков.
- `tariffs.json`: 11 направлений. `fixed` и `min` бывают с разной точностью (`1.000`, `0.500`, `0`, `5000`), а `charged_in` бывает `TARGET` (RU-VN-BANK, KW-JP-BANK) — комиссия в валюте получателя требует пересчёта.
- `providers.json`: 7 провайдеров, у каждого своя валюта (USD, EUR, THB, JPY, KWD) и свои лимиты в ней. `prov_delta` выключен (`enabled=false`), у `prov_echo` есть окно обслуживания — оба случая обязаны отсекаться фильтром.
- `rates.json`: 17 пар. Нет ни одной пары с KZT — направление KZ-TH-BANK необслуживаемо (`RATE_UNAVAILABLE`). RUB→PHP и RUB→AED протухли (октябрь против ноября) — проверка свежести обязательна. RUB→KWD напрямую нет, но есть RUB→EUR→KWD — кросс-курс нужен.
- `clients.json`: 13 клиентов. У `c_003` баланс в RUB, а суточный лимит в USD — лимит надо конвертировать. `c_007` с балансом 1000 RUB ниже минимальной суммы направления RU-TH-BANK с комиссией — `INSUFFICIENT_FUNDS`. `c_101–c_105` — нагрузочные (балансы 90M) для chaos-прогона.
- `examples/`: 10 кейсов, включая `TARGET`-режим, минимальную комиссию, `percent+fixed`, нулевые знаки (JPY) и три знака (KWD), комиссию в валюте получателя.
- `scripts/chaos_run.py`: чистый stdlib, ходит в наш API, сверяет `available + reserved` с ledger мок-провайдера, понимает статусы `NEEDS_REVIEW` и родственные.
