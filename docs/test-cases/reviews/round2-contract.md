# Ревью-раунд 2: контракт API (Backend §6–9, OpenAPI)

Снимок: `HEAD 1baaa9b` (2026-11-19 02:44 MSK). На момент старта рабочее дерево было чистым;
во время ревью параллельные сессии добавили `docs/test-cases/reviews/round2-limits.md`,
`docs/test-cases/reviews/round2/` и правку `app/services/limits.py` — они в этот отчёт не
входят. Исходный контейнер `api` был собран из более старого коммита (в нём, в частности,
не было `UnauthorizedError` и вебхук без подписи отдавал 422), поэтому сервис пересобран из
`HEAD` (`docker compose up -d --build api`). Проверки — на `http://localhost:8080`,
PostgreSQL 16, `ENV=dev`, `mock-provider` в режиме `ok` (сценарии `slow`/`decline`
включались через admin-ручку мока и затем снимались). Код и тесты ревьюером не изменялись;
в dev-БД остались тестовые котировки/переводы. Ссылки `файл:строка` — по снимку `1baaa9b`;
параллельные правки других сессий могут сдвинуть номера строк.

Метод: чтение `docs/Backend.md` §5–9, `docs/SPEC.md`, `app/api/**`, `app/schemas/**`,
`app/api/exception_handlers.py`, `examples/*.json`, `README.md`; живой прогон curl/httpx:
`/docs`, `/openapi.json`, `POST/GET /quotes`, `POST/GET /transfers`, баланс, вебхуки,
сценарии `slow` (202) и `decline` (терминальный FAILED), истёкшая котировка, длинный
`Idempotency-Key`, неизвестный статус вебхука, 404/405.

Severity: **P1** — нарушение обязательного контракта §6 / SPEC, 5xx на контрактном вводе,
неверные данные в ответе; **P2** — неполный/неверный OpenAPI, формат ошибок вне домена,
семантика 4xx; **P3** — точность, косметика, лишние/недостающие описания.

Пересечения с раундом 1: F2, F6, F18 (`money-quotes.md`), TR-IDEM-10 (`transfers-idempotency.md`),
W-09, W-13, W-20 (`webhooks-reconcile.md`). Ниже они отмечены как «повторно подтверждено»:
на снимке `1baaa9b` они не исправлены.

## 1. Сводная таблица

| ID | Sev | Пункт ТЗ / SPEC | Ожидание | Факт | Файл:строка | Тест |
|---|---|---|---|---|---|---|
| C-01 | P1 | SPEC: ошибки RFC 9457; openapi-contract #3 | Все ответы-ошибки в OpenAPI объявлены как `application/problem+json` со схемой RFC 9457 и `error.code` | OpenAPI объявляет `application/json` для 400/404/409/410/422; фактические ответы — `application/problem+json`. Для 422 пути/баланса в OpenAPI остаётся стандартный `HTTPValidationError` (`application/json`), хотя на проводе `VALIDATION_ERROR` | `app/api/v1/quotes.py:37-46`; `app/api/v1/transfers.py:42-49`; `app/api/exception_handlers.py:23`, `:45` | `test_openapi_contract.py::test_error_responses_are_problem_json` |
| C-02 | P1 | SPEC OpenAPI: `responses={200, 201, 400, 404, 409, 422}`; Backend §6 | OpenAPI для `POST /transfers` документирует реальные 201/202 (со схемой тела), `Idempotency-Key` обязателен; 200 отсутствует | В OpenAPI есть фантомный `200 Successful Response`, а 201/202 — с пустым `content` (схемы `TransferResponse` нет). `Idempotency-Key` объявлен `required: false`, хотя без него API отвечает 422 | `app/api/v1/transfers.py:38-50`, `:55` | `test_openapi_contract.py::test_transfer_status_codes_and_header` |
| C-03 | P2 | Backend §6; SPEC REST: `POST` → 201 + `Location` | 201 обязательно с `Location`; для 202 `Location` желателен для поллинга; 202 не используется для уже терминального результата | 201 (COMPLETED) содержит `Location: /transfers/{id}`. 202 (`slow`, 12.4 с) содержит `Location: null`. Терминальный `FAILED` после `decline` тоже отдаётся как 202 без `Location` | `app/api/v1/transfers.py:79-84` | `test_transfers_api.py::test_202_has_location_and_terminal_failed_is_201` |
| C-04 | P1 | Backend §6 `GET /transfers/{id}`: попытки «кто, когда, с каким результатом»; SPEC webhooks #7 | Если перевод терминальный, попытки отражают тот же результат | Перевод `COMPLETED` по вебхуку: `GET /transfers/{id}` отдаёт `status=COMPLETED`, но `attempts[0].status=PROCESSING` (остаётся навсегда). В истории при этом есть `COMPLETED` от провайдера | `app/services/webhooks.py:110-115`; `app/services/finalize.py:26-35`; `app/api/v1/transfers.py:25-27`, `:104-105` | `test_transfers_api.py::test_webhook_completed_updates_attempt` |
| C-05 | P2 | Backend §6: `GET /quotes/{id}` возвращает котировку «в том же формате»; quote-contract #2 | POST и GET отдают одинаковые `breakdown`, суммы, rate | POST возвращает 4 шага `breakdown`; GET всегда возвращает `breakdown: []` | `app/api/mappers.py:35-46`, `:44` | `test_quotes_api.py::test_get_quote_equals_post` |
| C-06 | P1 | Backend §6: `QUOTE_EXPIRED`; quote-contract #12 | Котировка с `expires_at` в прошлом при `GET /quotes/{id}` → 410 `QUOTE_EXPIRED` | 200 с полным телом котировки; TTL проверяется только при `POST /transfers` (там 410 корректный) | `app/api/v1/quotes.py:108-113` | `test_quotes_api.py::test_get_expired_quote_410` |
| C-07 | P1 | Backend §6: `AMOUNT_ABOVE_MAX` — сумма вне лимитов направления; §9 | Для TARGET фактически списываемая сумма (`source_amount`) не выше `max` направления | `RU-TH-BANK`, TARGET `124200.00` THB → 201, `source_amount=300217.55` RUB (> 300000); TARGET `126000.00` → 201, `source_amount=304568.53` RUB. Лимит проверяется по `target/rate` до комиссии | `app/api/v1/quotes.py:86-88`; `app/services/quotes.py:262-280`; `app/services/transfers.py:180-223` | `test_quotes_api.py::test_target_source_respects_max` |
| C-08 | P1 | Backend §6: `Idempotency-Key` — «строка от клиента»; SPEC: 4xx вместо 5xx на некорректном вводе | Ключ длиннее колонки → 422 `VALIDATION_ERROR` (или явно документированный предел), без 5xx | `Idempotency-Key` из 200 символов → 500 `INTERNAL_ERROR`; в логах `value too long for type character varying(128)` | `app/api/v1/transfers.py:55`; `app/models/tables.py:145` | `test_transfers_api.py::test_idempotency_key_too_long_is_4xx` |
| C-09 | P2 | SPEC webhooks #4/#5; Backend §7 | Неизвестный/некорректный статус события не должен давать 500; событие либо принимается по правилам, либо отвергается 4xx | `status="BOGUS"` с валидной подписью → 500; FK `webhook_events.status → attempt_statuses` не пропускает значение | `app/schemas/webhooks.py:10`; `app/repositories/transfers.py:222-233`; `app/models/tables.py:175` | `test_webhooks_api.py::test_unknown_event_status_is_4xx` |
| C-10 | P2 | SPEC webhooks #5; A7/D10 | Неизвестный `payout_id`/`reference` → 200 + warn-лог + `unknown_webhooks_total` | 200 есть, но в коде нет `logging` и метрик; в логах живой доставки только access-log «request completed», warn отсутствует | `app/services/webhooks.py:70-83`; `docs/ASSUMPTIONS.md:29-31`; `docs/DECISIONS.md` D10 | `test_webhooks_api.py::test_unknown_reference_logs_and_metric` |
| C-11 | P2 | SPEC/error-codes #1: любой 4xx/5xx — problem+json; A11 | Неизвестный путь и метод отдают RFC 9457 с `error.code` | `GET /nope` → 404 `application/json` `{"detail":"Not Found"}`; `GET /transfers` → 405 `application/json` `{"detail":"Method Not Allowed"}` | `app/main.py:78-80` (нет обработчика `HTTPException`) | `test_error_envelope.py::test_404_405_problem_json` |
| C-12 | P2 | SPEC OpenAPI: `summary`, `description`, `Field(description=..., examples=[...])` для каждого DTO и полный перечень кодов | У каждого поля и операции есть описание/пример; перечень кодов соответствует проводу | У операций нет `description`; у большинства полей request/response нет ни `description`, ни `examples`; `POST /webhooks` не имеет `requestBody`; у `POST /quotes` документированы недостижимые 404/410; у вебхука нет 401/503; у баланса нет 404 | `app/schemas/quotes.py:7-37`; `app/schemas/transfers.py:6-54`; `app/schemas/balances.py:4-16`; `app/schemas/webhooks.py:4-11`; `app/api/v1/webhooks.py:12-19`; `app/api/v1/quotes.py:43-45` | `test_openapi_contract.py::test_dto_annotations_and_response_codes` |
| C-13 | P2 | SPEC OpenAPI: схемы DTO | `TransferResponse.source_amount`/`target_amount` — `Money` (`amount`, `currency`) | В OpenAPI это `object` с `additionalProperties: true`; на проводе — `Money` | `app/schemas/transfers.py:48-49` | `test_openapi_contract.py::test_transfer_money_schema` |
| C-14 | P2 | Backend §6: `recipient: {name, account, bank_code}` | Обязательные поля получателя валидируются; неполный recipient не создаёт выплату | `name` и `bank_code` имеют `default=""`; перевод с `recipient={"account":"123"}` проходит и завершается COMPLETED | `app/schemas/transfers.py:6-11` | `test_transfers_api.py::test_recipient_required_fields` |
| C-15 | P3 | Backend §6; README «ошибки» | Неизвестный `client_id` — 404/отдельный код, 422 только для некорректного формата | `GET /clients/nope/balance` и `POST /transfers` с неизвестным клиентом → 422 `VALIDATION_ERROR`; код не описан в README | `app/api/v1/transfers.py:116-118`; `app/services/transfers.py:177-179` | `test_transfers_api.py::test_unknown_client_contract` |
| C-16 | P3 | Backend §6: история — «когда» | Время результата попытки — момент смены статуса; история не дублирует один переход | `build_history` берёт `attempt.created_at` (время старта), а не `updated_at`; у sync-перевода PENDING и первое COMPLETED провайдера имеют одинаковый timestamp, затем COMPLETED провайдера дублируется событием вебхука | `app/api/v1/transfers.py:25-27`; `app/models/tables.py:165-166`; `app/repositories/transfers.py:174-187` | `test_transfers_api.py::test_history_attempt_timestamps_and_dedup` |
| C-17 | P3 | SPEC REST: `Location` | При запросе к `/api/v1/...` `Location` указывает на ресурс в том же API-пространстве | `POST /api/v1/quotes` → `Location: /quotes/{id}`; `POST /api/v1/transfers` → `Location: /transfers/{id}` (жёстко зашитый корневой путь) | `app/api/v1/quotes.py:95`; `app/api/v1/transfers.py:81` | `test_quotes_api.py::test_location_matches_requested_prefix` |
| C-18 | P3 | Backend §6: деньги — строка с числом знаков minor units | Принимаются только ASCII-цифры и ровно `minor_units` знаков | `amount="١٢٣٤.٥٦"` и `"１２００.００"` → 201, нормализуются в `1234.56`/`1200.00`; `str.isdigit()` пропускает не-ASCII | `app/core/money.py:20-28` | `test_quotes_api.py::test_money_ascii_only` |
| C-19 | P3 | SPEC: версионирование API | Версия API видна в OpenAPI | `info.version = "0.1.0"` при наличии `/api/v1`; `servers` не заданы | `app/main.py:75-77` | `test_openapi_contract.py::test_info_version` |
| C-20 | P3 | SPEC OpenAPI: перечень кодов | В OpenAPI только достижимые коды | `POST /quotes` документирует 404 `QUOTE_NOT_FOUND` и 410 `QUOTE_EXPIRED`, которые этот метод не возвращает; `POST /transfers` не документирует 500/502/503 | `app/api/v1/quotes.py:43-45`; `app/api/v1/transfers.py:42-49` | `test_openapi_contract.py::test_only_reachable_codes` |

## 2. Подтверждённые расхождения

### C-01. OpenAPI врёт про Content-Type ошибок (P1)

`GET /openapi.json` для `POST /quotes`:

```json
"400": {
  "description": "UNSUPPORTED_DIRECTION, AMOUNT_BELOW_MIN, AMOUNT_ABOVE_MAX",
  "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}
}
```

Живой ответ `POST /quotes` с `amount=500.00`:

```http
HTTP/1.1 400 Bad Request
content-type: application/problem+json
```

То же для 401 вебхука, 409/410/422 переводов. Схема `ErrorResponse` сама по себе содержит
`type/title/status/detail/instance/error`, то есть RFC-поля есть, но объявленный media type
неверный. Отдельно: `GET /quotes/not-a-uuid` на проводе даёт `422 VALIDATION_ERROR`
(`application/problem+json`), а OpenAPI для этого кода оставляет стандартный
`HTTPValidationError` / `application/json`.

Следствие: сгенерированный клиент и автотест, читающие OpenAPI, будут ждать JSON-конверт
`{"detail": [...]}` вместо фактического `{"error": {...}}`.

### C-02. `POST /transfers`: фантомный 200 и пустые 201/202 (P1)

В OpenAPI у операции одновременно:

- `200 Successful Response` с `TransferResponse` — этот код API не возвращает;
- `201`/`202` с `description`, но без `content` — схема тела не привязана;
- `Idempotency-Key` с `required: false`.

Живой прогон: 201 при COMPLETED и 202 при PROCESSING/FAILED, оба с `TransferResponse`.
Отсутствие обязательности заголовка в контракте особенно опасно: сгенерированный клиент
может не отправить ключ, и получит 422 `VALIDATION_ERROR` уже в runtime.

### C-03. 202 без `Location`, терминальный FAILED тоже 202 (P2)

Slow-сценарий (mock admin `slow`, `MOCK_SLOW_SEC=30`):

```text
POST /transfers → 202 за 12.4 с, Location: None, status: PROCESSING
```

Сценарий `decline` для всех провайдеров RU-TH-BANK:

```text
POST /transfers → 202, Location: None, status: FAILED
attempts: [prov_alpha FAILED, prov_bravo FAILED]
```

Контракт допускает 202 для асинхронного исполнения, но не требует отдавать 202 уже
терминальный FAILED; и в обоих случаях созданный ресурс существует, поэтому `Location`
нужен для поллинга. Сейчас 201+`Location` видит только счастливый путь COMPLETED.

### C-04. После вебхука попытка навсегда остаётся PROCESSING (P1)

Воспроизведение: перевод создан в slow-сценарии (202), затем mock завершил выплату и
прислал подписанный `COMPLETED`-вебхук. `GET /transfers/{id}`:

```json
{
  "status": "COMPLETED",
  "attempts": [{"provider_id": "prov_alpha", "payout_id": "po_...", "status": "PROCESSING"}],
  "history": [
    {"status": "PENDING", "actor": "client"},
    {"status": "PROCESSING", "actor": "provider:prov_alpha"},
    {"status": "COMPLETED", "actor": "provider:prov_alpha"},
    {"status": "COMPLETED", "actor": "service"}
  ]
}
```

`complete_transfer` меняет только `transfers.status` и резерв; `PayoutAttempt` не обновляется
(`update_attempt` вызывается в polling/reconcile, но не в webhook-ветке). Это прямое
нарушение §6: попытки должны показывать результат. В тесте
`test_webhook_completes_transfer` проверяется только `got["status"]`, поэтому дефект зелёный.

### C-05. `GET /quotes/{id}` теряет `breakdown` (P2, повтор F18)

Живой POST: `breakdown` из 4 шагов; GET той же котировки: `breakdown: []`.
§6 требует «котировку в том же формате», quote-contract #2 — наличие `breakdown` в ответе.
`quote_row_to_response` жёстко подставляет пустой список. Тест
`test_create_and_read_quote` после GET проверяет только `quote_id`.

### C-06. `GET /quotes/{id}` не отдаёт 410 для истёкшей котировки (P1, повтор F6)

Котировка создана, `expires_at` переведён в прошлое SQL-обновлением:

```text
GET /quotes/{id} → 200 application/json (полное тело)
POST /transfers  → 410 QUOTE_EXPIRED
```

§6 перечисляет `QUOTE_EXPIRED` как обязательный код; quote-contract #12 ожидает 410 и на
GET. Сейчас GET является единственным способом «проверить цену» и возвращает протухшую
котировку как валидную.

### C-07. TARGET обходит верхний лимит направления (P1, повтор F2)

`RU-TH-BANK`, лимит `max=300000.00 RUB`, rate `0.4200`:

| TARGET, THB | HTTP | source_amount, RUB | max |
|---|---|---|---|
| 124068.63 | 201 | 299900.00 | 300000.00 |
| 124200.00 | 201 | **300217.55** | 300000.00 |
| 126000.00 | 201 | **304568.53** | 300000.00 |
| 300000.00 | 400 `AMOUNT_ABOVE_MAX` | — | — |

`check_limits` получает `target/rate` (сумму до комиссии), а фактическое списание
`source_amount = to_convert + fee` больше. При создании перевода повторной проверки max нет.
Для клиента это «сумма вне лимитов направления», для сервиса — превышение лимита на 1.5%.

### C-08. Длинный `Idempotency-Key` даёт 500 (P1, повтор TR-IDEM-10)

Заголовок из 200 символов:

```http
HTTP/1.1 500 Internal Server Error
content-type: application/problem+json
{"error":{"code":"INTERNAL_ERROR",...}}
```

Причина — `VARCHAR(128)` и отсутствие валидации длины в `Header`. §6 не ограничивает длину
ключа; корректный ответ — 422 `VALIDATION_ERROR` (или документированный предел), но не 5xx.

### C-09. Неизвестный статус вебхука даёт 500 (P2, повтор W-09)

Подписанное событие со `status="BOGUS"` и неизвестным reference:

```text
POST /webhooks/prov_alpha → 500 INTERNAL_ERROR
log: ForeignKeyViolationError ... Key (status)=(BOGUS) is not present in attempt_statuses
```

Схема принимает любую строку (`max_length=24`), а БД — только `PROCESSING/COMPLETED/FAILED`.
Провайдер может прислать новый/опечатанный статус; это не должно ронять обработчик в 500.
Ожидание: 200 без эффекта либо 422 `VALIDATION_ERROR` с записью в лог.

### C-10. Неизвестный вебхук: 200 без warn-лога и метрики (P2, повтор W-13)

Событие по неизвестному `reference` возвращает `200 {"status":"ok"}` (это правильно), но:
`app/services/webhooks.py` не импортирует `logging` и не инкрементирует метрику; в логах
живой доставки нет ни `warn`, ни `unknown_webhooks_total`. A7/D10 и webhooks #5 требуют
ровно `200 + warn-лог + metric`, чтобы не потерять такие события при разборе инцидентов.

### C-11. 404/405 вне problem+json (P2)

```text
GET /nope        → 404 application/json {"detail":"Not Found"}
GET /transfers   → 405 application/json {"detail":"Method Not Allowed"}
```

В `main.py` зарегистрированы `DomainError`, `RequestValidationError` и `Exception`, но не
`starlette.exceptions.HTTPException`. SPEC/A11/error-codes #1 требуют единый конверт для
любого 4xx/5xx. Доменные ошибки при этом корректны: 400/401/404/409/410/422 — problem+json.

### C-12. OpenAPI не размечен (P2)

Фактический `GET /openapi.json`:

- у всех операций `description` отсутствует (только короткий `summary`);
- `QuoteRequest`: ни одного `description`/`examples` у 7 полей;
- `TransferRequest`, `Recipient`, `BalanceResponse`, `CurrencyBalance`, `Attempt`,
  `HistoryEntry`: описаний нет; примеры есть только у `quote_id`, `direction_id`, `rate`,
  `Money.amount/currency`, `ErrorDetail.code/message`;
- у `POST /webhooks/{provider_id}` нет `requestBody`, поэтому Swagger не может отправить
  событие; нет 401 (подпись) и 503 (пустой секрет);
- `POST /quotes` содержит недостижимые 404/410;
- у `GET /clients/{id}/balance` нет 404/422-схемы домена.

SPEC требует `summary`, `description`, `Field(..., description=..., examples=[...])` для
каждого поля DTO и полный перечень кодов; это не выполнено.

### C-13. `TransferResponse` деньги описаны как произвольный object (P2)

В OpenAPI:

```json
"source_amount": {"type": "object", "additionalProperties": true},
"target_amount": {"type": "object", "additionalProperties": true}
```

На проводе — `{"amount": "1100.00", "currency": "RUB"}`. Схема должна ссылаться на `Money`,
иначе кодогенерация теряет типы и валидацию.

### C-14. `recipient.name` и `bank_code` фактически необязательны (P2)

Запрос `recipient={"account":"123"}` (без имени и банка) → 201 COMPLETED, выплата создана.
В §6 recipient показан с тремя полями; пустое имя получателя — риск для реального payout.
Если это осознанное допущение, оно должно быть в ASSUMPTIONS; сейчас его там нет.

### C-15…C-20. Мелкие расхождения

- **C-15.** Неизвестный клиент в балансе и переводе → 422 `VALIDATION_ERROR`
  (`app/api/v1/transfers.py:116-118`, `app/services/transfers.py:177-179`). Это не
  «некорректный формат запроса»; README такого кода/кейса не описывает.
- **C-16.** История попыток использует `created_at`, а не `updated_at`: у sync-перевода
  событие провайдера `COMPLETED` имеет тот же timestamp, что `PENDING`, хотя статус сменился
  позже; затем тот же `COMPLETED` приходит второй раз из вебхука
  (`app/api/v1/transfers.py:26-27`; `app/models/tables.py:165-166`).
- **C-17.** `Location` для `/api/v1` указывает на корневой `/quotes/...`/`/transfers/...`
  (`app/api/v1/quotes.py:95`; `app/api/v1/transfers.py:81`); корневые дубли есть, поэтому
  ссылка рабочая, но версия в ней теряется.
- **C-18.** `amount_string_is_valid` использует `str.isdigit()` и принимает арабо-индийские
  и полноширинные цифры: `"١٢٣٤.٥٦"` → 201, `"１２００.００"` → 201.
  (`app/core/money.py:24`).
- **C-19.** `info.version="0.1.0"` при URL `/api/v1`; `servers` не заданы (`app/main.py:75-77`).
- **C-20.** В OpenAPI `POST /quotes` документирует 404/410, которых не бывает; у
  `POST /transfers` нет 500/502/503, хотя доменные 5xx возможны
  (`app/api/v1/quotes.py:43-45`, `app/api/v1/transfers.py:42-49`).

## 3. Что соответствует контракту (проверено вживую)

- Все пути §6 есть и в корне, и под `/api/v1`: `/quotes`, `/quotes/{id}`, `/transfers`,
  `/transfers/{id}`, `/clients/{id}/balance`, `/webhooks/{provider_id}`
  (`/openapi.json`, `app/main.py:107-112`).
- `/docs` и `/openapi.json` доступны в `ENV=dev`; при `ENV=prod` оба `None` — совпадает с
  D12/openapi-contract #5 (`app/main.py:73-74`; проверено импортом приложения).
- `POST /quotes` → 201 + `Location: /quotes/{id}`; все обязательные поля ответа на месте.
- `POST /transfers` → 201 при COMPLETED + `Location`; 202 при slow/PROCESSING; тело содержит
  `transfer_id`, `status`, `attempts`, `history`.
- Ошибки домена: `application/problem+json`, поля RFC 9457 + вложенный
  `error: {code, message, details}`; 422 Pydantic конвертируется в `VALIDATION_ERROR` с
  `details` по полям (`body.amount`, `body.amount_type`, `path.quote_id`).
- Обязательные коды §6 воспроизводятся: `VALIDATION_ERROR`, `UNSUPPORTED_DIRECTION`,
  `AMOUNT_BELOW_MIN`, `AMOUNT_ABOVE_MAX`, `RATE_UNAVAILABLE`, `QUOTE_NOT_FOUND`,
  `QUOTE_EXPIRED`, `INSUFFICIENT_FUNDS`, `DAILY_LIMIT_EXCEEDED`; плюс
  `IDEMPOTENCY_CONFLICT`.
- Идемпотентность: тот же ключ + то же тело → тот же `transfer_id`; другое тело → 422
  `IDEMPOTENCY_CONFLICT`; отсутствие ключа → 422 `VALIDATION_ERROR`.
- `GET /clients/{id}/balance` отдаёт `available` и `reserved` по каждой валюте (включая KWD
  с 3 знаками и RUB с 2); chaos_run эти ключи читает.
- `POST /webhooks/{provider_id}`: без подписи/с неверной подписью → 401; дубль `event_id` →
  200; неизвестный payout/reference → 200; не-терминальный статус не перетирает терминал.
- Все 10 `examples/*.json` проходят (201/400 и точные суммы), таблица §9:
  10000 RUB → fee 150, convert 9850, target 4137; 2000 RUB → fee 50; TARGET 4137 → source
  10000; 500 RUB → `AMOUNT_BELOW_MIN`.
- Деньги во всех проверенных ответах — строки с minor_units: JPY `"18658"`, KWD `"50.000"`,
  VND `"2069800"`, RUB `"1100.00"`; `rate` — строка `"0.4200"`.
- Slow-сценарий укладывается в SPEC: синхронное ожидание ~12.4 с, затем 202 с текущим
  `PROCESSING`, а не FAILED; перевод позже завершается вебхуком/сверкой.

## 4. Не покрыто

### 4.1. Тестами

Полный `pytest` в этом раунде не прогонялся: выводы о покрытии сделаны по чтению тестов,
живые проверки — отдельно (раздел 3). Это не заменяет прогон `uv run pytest`.

- **OpenAPI как артефакт:** ни один тест не читает `/openapi.json`; нет проверок
  problem+json в схемах, обязательности `Idempotency-Key`, 201/202-контента, Location,
  описаний и примеров. `docs/test-cases/openapi-contract/SPEC.md` не реализован.
- **`Location`:** проверяется только вхождение `quote_id` в заголовок для `POST /quotes`
  (`tests/integration/test_quotes_api.py:31-32`); для `/transfers` и для 202 — нет.
- **`GET /quotes/{id}`:** нет сравнения POST/GET целиком (проверяется только `quote_id`,
  `tests/integration/test_quotes_api.py:47-51`) и нет теста истёкшей котировки на GET.
- **Попытки после вебхука:** `test_webhook_completes_transfer` проверяет только статус
  перевода, не `attempts` (`tests/integration/test_transfers_api.py:283-287`).
- **Ошибки вне домена:** нет теста 404/405 в problem+json; `test_missing_signature_returns_401`
  корректный, но `test_webhook_bad_signature_rejected` всё ещё принимает `(401, 422)`
  (`tests/integration/test_transfers_api.py:223-237`), поэтому регрессия 422 не покраснеет.
- **Вебхуки:** `test_unknown_reference_logged_but_accepted` не проверяет ни warn-лог, ни
  метрику (`tests/integration/test_webhooks_and_limits.py:86-92`); нет теста неизвестного
  статуса события.
- **Ввод:** нет теста длинного `Idempotency-Key`, не-ASCII цифр, неполного `recipient`,
  неизвестного клиента.
- **TARGET-лимит:** нет теста `source_amount <= max` и ответа `AMOUNT_ABOVE_MAX` на
  `RU-TH-BANK` TARGET 124200/126000 (round1 F2 не закрыт).
- **Описания/схемы:** нет проверки, что каждое поле DTO имеет `description/examples`, а
  `TransferResponse.source_amount/target_amount` — `Money`.

### 4.2. Живой проверкой

- MySQL и SQLite профили (проверялся только PostgreSQL 16).
- `error_5xx` провайдера и полный fallback-цикл 5xx (живьём проверены `slow` и `decline`;
  5xx/unknown-result покрыты только unit/integration-тестами).
- Суточный лимит и `RATE_UNAVAILABLE` живьём (воспроизводились в интеграционных тестах,
  не в этом прогоне).
- Повтор ключа во время PROCESSING и гонка двух запросов (есть интеграционные тесты,
  живьём не гонял).
- `POST /webhooks` с валидной подписью и невалидным `occurred_at` (после 500 на статусе
  соединение сбрасывалось; отдельного прогона нет).
- `/docs` в `ENV=prod` проверен импортом приложения, не HTTP-запросом к прод-контейнеру.
- `GET /transfers` как список: эндпоинта нет, возвращается 405. §6 список не требует;
  если он нужен для «истории», это отдельное расширение контракта.

## 5. Порядок исправлений (предложение)

1. **C-04, C-07, C-08** — неверные данные/лимит/500 на контрактном вводе.
2. **C-01, C-02, C-03, C-06** — обязательные статусы, `Location`, OpenAPI error-content и
   истёкшая котировка.
3. **C-05, C-09, C-10, C-11** — breakdown, неизвестный статус вебхука, warn/метрика,
   единый problem+json для 404/405.
4. **C-12…C-20** — полнота OpenAPI, `Money` в TransferResponse, обязательность recipient,
   семантика 422/404, timestamp истории, ASCII-деньги.
