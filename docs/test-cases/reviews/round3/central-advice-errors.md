# Центральный advice и unchecked-исключения — третий раунд (Transfer Core)

Снимок: `be5a72a` (поверх `e52ba27` — «zero try/except», D29). Код и тесты в рамках ревью **не менялись**.
Зона — только рефактор advice: `app/core/exceptions.py`, `app/api/exception_handlers.py`, `app/main.py`,
`app/services/*.py`, `app/api/v1/*.py`. Дубли round2 (`round2/*.md`, `round2-*.md`) и round1 (INF-*) не повторяются —
старые пункты ниже упомянуты одной строкой как «проверено, статус».

Проверено исполнением (не только чтением): прямые вызовы `RatesClient.fetch` / `ProviderClient.create_payout`
с подменённым `get_json`, `TestClient` на `create_app()` без БД (`GET /nope`, `POST /health`, `GET /docs`,
невалидный UUID, искусственные `/boom` → `RuntimeError` и `/dom` → `ValidationError`), MRO-инспекция
`RequestValidationError` / `ResponseValidationError` / `StarletteHTTPException`, разбор
`FastAPI.build_middleware_stack` и `starlette._exception_handler`.

## Таблица новых кейсов

| Severity | Кейс | Вход | Ожидание | Факт | Файл:строка | Тест |
|---|---|---|---|---|---|---|
| P1 | Протухший/битый элемент `/rates` роняет котировку в 500 | `GET /rates` мока → `200` с entry без ключей / `rate: "abc"` / `updated_at: "not-a-date"` | `422 RATE_UNAVAILABLE` (`problem+json`), рефактор добавил проверку только `isinstance(entry, dict)` | `KeyError 'updated_at'` / `InvalidOperation` / `ValueError` → `500 INTERNAL_ERROR` (проверено исполнением, все три ветки) | `app/services/rates.py:27-32` | `test_rates_malformed_entry_is_rate_unavailable` |
| P2 | `201` без `payout_id` роняет перевод в 500 вместо unknown-result | Мок `POST /{provider}/payouts` → `201 {"status": "PROCESSING"}` без `payout_id` | Ветка «неизвестный итог» (D7): `GET ?reference=` и дальше по правилу, без 500 | `payload["payout_id"]` → `KeyError` → `500` (проверено исполнением). `isinstance`-гард рефактора закрыл только list-payload, прямой индекс остался | `app/services/providers.py:177` | `test_create_payout_201_without_payout_id_is_unknown` |
| P2 | Котировка с направлением вне справочника → 500 | `POST /transfers` по котировке, чей `direction_id` исчез из тарифов после выпуска | Доменная ошибка (`400 UNSUPPORTED_DIRECTION` или `404/410`), а не падение | `next(t for t in ...)` → `StopIteration` → `500` (в async-функции — `RuntimeError: coroutine raised StopIteration`) | `app/services/transfers.py:193,269` | `test_transfer_with_dropped_direction_is_domain_error` |
| P2 | Конкурентный дубль вебхука → 500 вместо 200-дедупа | Два параллельных `POST /webhooks/{p}` с одним `event_id` | Оба `200`, вторая запись без эффекта (SPEC D10: дубль — `200`, иначе шторм ретраев) | Check-then-insert без трансляции гонки: оба проходят `session.get` (`webhooks.py:52`), проигравший `flush` ловит `IntegrityError` → `500`. Граница репозитория (D25) переводит только гонку `Idempotency-Key`, вебхук-гонку — нет | `app/repositories/transfers.py:288-313`; `app/services/webhooks.py:52-64` | `test_concurrent_duplicate_webhook_is_200` |
| P3 | Валюта исчезла из справочника между quote и transfer → 500 | `minor_units` без `source_currency` (удалённая/незасиденная валюта) при `POST /transfers` | Доменная `422 VALIDATION_ERROR` | `minor_units[source_currency]` → `KeyError` → `500` | `app/services/transfers.py:203` | `test_transfer_with_unknown_balance_currency_is_422` |

## Подтверждённые баги

1. **P1. Битый элемент rates → 500 (строка таблицы №1).** Рефактор перевёл транспорт на `get_json`
   и добавил `RateUnavailableError` для `status is None` / `!= 200` / `not isinstance(payload, list)` /
   `not isinstance(entry, dict)` — но доступ к `entry["updated_at"]`, `entry["base"]`, `entry["quote"]`,
   `Decimal(str(entry["rate"]))`, `datetime.fromisoformat` остался незащищённым. Любой из трёх дефектов
   формы даёт недоменную ошибку сквозь advice в `500`. Правило свежести (`min(ttl, 6ч)`) здесь ни при чём —
   падает раньше него, на разборе.
2. **P2. `201` без `payout_id` → 500 (№2).** Строка-индекс предшествует рефактору, но пережила его:
   новый гард отличает list от dict, а dict без ключа — нет. По контракту D7 любой непонятный ответ
   после отправки — это unknown-result, а не 500.
3. **P2. `next(...)` без дефолта (№3).** Два места в `transfers.py`. Окно узкое (тарифы статичны),
   но это единственный `StopIteration`-путь в services после запрета `try/except` — arch-тест его не ловит
   (ищет только `try:`/`except`), перевести не во что кроме падения.
4. **P2. Гонка дубля вебхука (№4).** `store_webhook_event` делает check-then-insert + `flush` без
   `begin_nested()`/`IntegrityError`-трансляции — в отличие от `create_transfer_or_existing`
   (`repositories/transfers.py:60-88`), где гонка закрыта образцово (`from exc` сохранён).
   Последовательный дубль работает, конкурентный — 500, а 500 на вебхук провоцирует ретраи провайдера,
   которых D10 требует избегать именно кодом `200`.

## Проверено, багом не является (новое знание о рефакторе)

- **Двойной обработки нет.** MRO-lookup (`starlette._exception_handler`) находит `DomainError`-хендлер
  раньше `Exception`; живой проб: `/dom` → ровно один `422` от `domain_error_handler`.
- **500-ответ чистый.** Живой проб `/boom` (`RuntimeError` с маркером секрета): тело — фиксированное
  `INTERNAL_ERROR`, без traceback, без эха исключения. Утечки секретов наружу через advice нет.
- **`error.details` секретов не несёт.** Хендлеры валидации отдают `{loc: msg}` без `input` (как и в round2);
  `cannot fetch rates: http {status}` — только код, без URL (INF-39 закрыт, держится);
  `IdempotencyConflictError` эхом отдаёт сам ключ — ключ клиентский, не секрет.
- **Chain не теряется там, где он есть.** `parse.py:12,20` и `repositories/transfers.py:87` — `from exc`;
  middleware — bare `raise`. В services `raise` без `from` — активного исключения там нет, норма.
- **`CancelledError` не глотается** (`BaseException`, мимо `except Exception` и мимо хендлера `Exception`).
- **Механика `Exception`-хендлера во FastAPI.** `FastAPI.build_middleware_stack` изымает ключ `Exception`
  из `exception_handlers` и вешает его на внешний `ServerErrorMiddleware`. Следствие: необработанное
  исключение проходит `log_requests` по `except`-ветке (лог есть) и рендерится снаружи — это объясняет,
  почему 500 без `X-Request-ID` (см. ниже INF-38). На поведение `DomainError`/`RequestValidationError`
  не влияет: они обрабатываются внутренним `ExceptionMiddleware`, заголовок на месте (проверено).
- **Статусы кодов консистентны.** Все `DomainError.status` совпадают с таблицей `README.md:96-110`;
  `401` за подпись и `503` за пустой секрет соответствуют SPEC §services и D25. Но в таблице README
  нет строк `UNAUTHORIZED 401` / `SERVICE_UNAVAILABLE 503` — расхождение доки и кода, предшествует
  рефактору, строкой в таблицу не выношу.
- **Локализация.** Сообщения advice — EN, `summary` роутов — RU. Требований на язык нет (примеры SPEC — EN),
  находки нет.
- **Старые пункты живы, не дублирую:** `404/405` по-прежнему `application/json {"detail"}` мимо
  `problem+json` (R2-06, проверено: `/nope`, `/docs`, `POST /health`); `X-Request-ID` отсутствует именно
  на необработанном 500 (INF-38, проверено на `/boom`, механизм — см. выше); маскировка секретов
  в traceback логов (R2-02) рефактором не затронута.

## Не покрыто тестами (новое)

- Нет теста `RatesClient` на entry с отсутствующими ключами / нечисловым `rate` / мусорным `updated_at`
  (только happy-path и транспортные ошибки).
- Нет теста `create_payout` на `201` без `payout_id` (и вообще на `201` с невалидной формой тела).
- Нет теста на `StopIteration`-путь при расхождении quote и тарифов (направление котировки отсутствует в справочнике).
- Нет конкурентного теста дубля вебхука (`asyncio.gather` с одним `event_id` → оба `200`, одна строка).
- Нет теста, что `X-Request-ID` присутствует на ответах всех advice-хендлеров включая `Exception`-хендлер
  (сейчас на последнем его нет — INF-38).
- Нет теста единообразия advice для `ResponseValidationError` (дрейф маппера → сейчас 500 через внешний
  middleware без request-id; осознанно 500, но поведение никак не зафиксировано).
