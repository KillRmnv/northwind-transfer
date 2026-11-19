# Transfer Core

Сервис международных переводов Northwind Transfer: расчёт котировки, фиксация цены, резерв средств, выплата через провайдера, обработка сбоев.

## Запуск с нуля

```sh
cp .env.example .env
docker compose up --build
```

Сервис слушает `http://localhost:8080`, мок-провайдер — `http://localhost:9000`, PostgreSQL — `localhost:5433`.
Миграции Alembic и идемпотентный сидер из `data/` выполняются автоматически при старте контейнера `api` (`scripts/entrypoint.sh`).

Проверка:

```sh
curl http://localhost:8080/health
curl http://localhost:8080/clients/c_001/balance
```

Swagger — `http://localhost:8080/docs` в `ENV=dev`; в других профилях отключён.

## Локальная разработка

```sh
uv sync --frozen
DB_HOST=localhost DB_PORT=5433 uv run alembic upgrade head
DB_HOST=localhost DB_PORT=5433 uv run python -m app.db.seed
DB_HOST=localhost DB_PORT=5433 uv run uvicorn app.main:app --port 8080
```

С хоста база доступна через опубликованный порт (`DB_PORT_HOST`, по умолчанию `5433`); значения по умолчанию в `.env.example` (`DB_HOST=db`) рассчитаны на запуск внутри compose-сети. Только `uv`: `pip`, `venv` и `requirements.txt` не используются. Python закреплён в `.python-version` (3.12).

## Тесты

```sh
uv run pytest
docker compose run --rm api uv run pytest
```
Тесты работают на реальном PostgreSQL в отдельной базе `transfer_test` (создаётся автоматически). Dev-база `transfer` тестами не затрагивается. Переопределить базу — `TEST_DATABASE_URL`. SQLite-профиль доступен для быстрых локальных прогонов, но не проверяет блокировки и конкурентность.

Нагрузка и хаос:

```sh
uv run python scripts/chaos_run.py --transfers 60 --api http://localhost:8080 \
  --mock http://localhost:9000 --data data --settle-timeout 200 --seed 7
```

Ожидаемый результат — `RESULT: OK`: ноль двойных выплат, зависших резервов и расхождений с ledger провайдера.

Если хост не видит опубликованные порты (`pg_isready -h localhost -p 5433` молчит при healthy-контейнерах) — обычно виноват VPN на хосте, режущий host→container трафик. Варианты: отключить VPN на время прогона либо запускать тесты изнутри compose-сети, где container→container ходит напрямую:

```sh
docker compose run --rm -e DB_HOST=db -e DB_PORT=5432 --entrypoint sh api \
  -c "cd /app && uv sync --frozen && uv run --frozen pytest -q"
```

## Профили БД

`DB_PROFILE`:

- `postgres` — основной (`postgresql+asyncpg`), используется в `docker compose up`;
- `mysql` — `mysql+aiomysql`;
- `sqlite` — `sqlite+aiosqlite`, локальный прогон.

Параметры пула (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`) и память PostgreSQL (`PG_SHARED_BUFFERS`, `PG_WORK_MEM`, `PG_MAINTENANCE_WORK_MEM`, `PG_EFFECTIVE_CACHE_SIZE`, `PG_MAX_CONNECTIONS`) задаются в `.env`; обоснование — `docs/DECISIONS.md` (D13).

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| `POST` | `/quotes` | Котировка (`SOURCE` / `TARGET`) |
| `GET` | `/quotes/{quote_id}` | Котировка по id |
| `POST` | `/transfers` | Перевод по котировке, заголовок `Idempotency-Key` |
| `GET` | `/transfers/{transfer_id}` | Статус, попытки, история |
| `GET` | `/clients/{client_id}/balance` | `available` и `reserved` по валютам |
| `POST` | `/webhooks/{provider_id}` | События провайдера, `X-Signature` (HMAC-SHA256) |

Те же маршруты доступны с префиксом `/api/v1`.

### Ошибки

Формат — RFC 9457 (`application/problem+json`) с вложенным `error`:

```json
{
  "type": "about:blank",
  "title": "AMOUNT_BELOW_MIN",
  "status": 400,
  "detail": "amount below min 1000.00 RUB",
  "error": {"code": "AMOUNT_BELOW_MIN", "message": "amount below min 1000.00 RUB", "details": {"min": "1000.00"}}
}
```

| Код | Статус | Когда |
| --- | --- | --- |
| `VALIDATION_ERROR` | 422 | некорректный запрос, неизвестная валюта, нет `Idempotency-Key` |
| `UNSUPPORTED_DIRECTION` | 400 | нет направления или способа выплаты |
| `AMOUNT_BELOW_MIN` / `AMOUNT_ABOVE_MAX` | 400 | сумма вне лимитов направления |
| `RATE_UNAVAILABLE` | 422 | нет курса или он старше TTL / 6 часов |
| `QUOTE_NOT_FOUND` | 404 | котировки нет |
| `QUOTE_EXPIRED` | 410 | истёк `quote_ttl_sec` |
| `INSUFFICIENT_FUNDS` | 409 | не хватает доступного баланса |
| `DAILY_LIMIT_EXCEEDED` | 409 | превышен суточный или оконный лимит |
| `IDEMPOTENCY_CONFLICT` | 422 | тот же ключ с другим телом |
| `TRANSFER_NOT_FOUND` | 404 | перевода нет |
| `CLIENT_NOT_FOUND` | 404 | клиента нет |
| `UNAUTHORIZED` | 401 | неверная или отсутствующая подпись вебхука `X-Signature` |
| `SERVICE_UNAVAILABLE` | 503 | секрет вебхуков не настроен, сервис не готов принимать события |
| `NOT_FOUND` | 404 | неизвестный путь |
| `METHOD_NOT_ALLOWED` | 405 | метод не поддерживается для пути |

Коды мок-провайдера не пробрасываются наружу: они маппятся во внутренние статусы попыток и видны в `GET /transfers/{id}`:

| Код мок-провайдера | HTTP | Статус попытки | Дальше |
| --- | --- | --- | --- |
| `RECIPIENT_ACCOUNT_INVALID` | 422 | `FAILED` | следующий провайдер |
| `AMOUNT_OUT_OF_LIMITS` | 422 | `FAILED` | следующий провайдер |
| `CURRENCY_NOT_SUPPORTED` | 422 | `FAILED` | следующий провайдер |
| `PROVIDER_DISABLED`, `PROVIDER_MAINTENANCE` | 503 | `FAILED` (`PROVIDER_UNAVAILABLE`) | следующий провайдер; такие провайдеры заранее отсекаются фильтром доступности |
| `INTERNAL_ERROR` | 500 | `FAILED` (`PROVIDER_UNREACHABLE`) | следующий провайдер сразу: повторные опросы `GET ?reference=` в пределах окна видимости записи не нашли — считается, что запись не создана; нечитаемый провайдер (ошибки проб) — `NEEDS_REVIEW` |
| `GATEWAY_TIMEOUT`, обрыв соединения, таймаут | 504 / нет ответа | `FAILED` (`PROVIDER_UNREACHABLE`), если опросы `GET ?reference=` в окне видимости ничего не нашли, иначе `NEEDS_REVIEW` | следующий провайдер или сверка — по итогу опросов |
| `IDEMPOTENCY_KEY_REUSED` | 409 | `PROCESSING` с пометкой `NEEDS_REVIEW` | сверка: `GET ?reference=`, затем следующий провайдер |

## Документация

- `docs/DECISIONS.md` — архитектура и обоснования.
- `docs/ASSUMPTIONS.md` — неоднозначности и как разрешены.
- `docs/LIMITS.md` — границы решения.
- `docs/starter-notes.md` — данные, мок-провайдер, хаос.
- `docs/test-cases/` — переносимые сценарии приёмочных проверок.
- `docs/er_diagram.puml` + `er_diagram.svg` — схема БД.

## Логи

Логи — JSON в stdout, с `request_id` (также в заголовке ответа `X-Request-ID`). Секреты маскируются: `Authorization`, `X-Signature`, `password`, `token`, `api_key`, `secret`, `cookie` и `Bearer …` превращаются в `*****`. Access-log uvicorn отключён, чтобы query string не утекал в обход маскировки.

```json
{"ts":"2026-11-18T22:41:47+0000","level":"INFO","logger":"transfer-core.http","message":"request completed","request_id":"c3417f1fdd95405d","method":"POST","path":"/quotes","status":201}
```
