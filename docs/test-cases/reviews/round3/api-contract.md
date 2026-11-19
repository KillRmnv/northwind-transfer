# Раунд 3: API-контракт, схемы, мапперы, OpenAPI, совместимость путей

Зона: контракт Backend.md §6–7 (эталон), SPEC (архитектура/OpenAPI/REST), реализация
`app/api/v1/*.py`, `app/api/mappers.py`, `app/schemas/*`, `app/main.py`,
`app/services/webhooks.py`, `app/services/transfers.py`, тесты
`tests/integration/test_quotes_api.py`, таблица кодов `README.md`.
Код и тесты НЕ менялись.

Прочитано из раунда 2 (не дублируется): `money-quotes.md` (N1–N9: TARGET/min-комиссия,
stale-inverse, 3-hop, `expires_at` POST↔GET naive-tz N6, `calculate_source` без валидации,
SPEC#7-переполнение, мёртвый return), `transfers-limits.md` (канонизация ключа,
PHANTOM, re-score, MINUTES-fail-open, кросс-валютные лимиты, гонки), `webhooks-reconcile.md`
(R2W-01…R2W-16: регистр event_id, tie-break `occurred_at`, audit-trail, path-traversal
payout_id, пустой payout_id→вторая выплата), `infra-security.md` (R2-01 длинный ключ→500,
маскирование секретов, admin-ручки мока, Content-Type 404/405), `providers-rates.md`
(R2-1…R2-10: unknown-итог, 409-конфликт, `/rates` недоступен), `test-quality.md`
(ложно-зелёные тесты, breakdown-coverage, отсутствие OpenAPI-тестов).
Ниже — только новое.

Severity: **P0** — неверные деньги/статус по контракту; **P1** — нарушение объявленного
контракта/кода ошибки/OpenAPI; **P2** — неоднозначность, дрейф формата, недокументированное
поведение.

## Таблица (только новое)

| Sev | Кейс | Вход | Ожидание по Backend.md | Факт | Файл:строка | Тест |
|-----|------|------|------------------------|------|-------------|------|
| P1 | C1. `GET /quotes/{id}` отдаёт 410, которого нет в OpenAPI | GET протухшей котировки | `responses` роута перечисляет все коды (SPEC: полный перечень) | `responses={200,404}` (`quotes.py:102-105`), а код бросает `QuoteExpiredError` → 410 (`quotes.py:111-112`). Зеркально POST заявляет 404 `QUOTE_NOT_FOUND` (`quotes.py:46`), недостижимый при создании | `app/api/v1/quotes.py:40-49,98-112` | OpenAPI-assert: 410 у `GET /quotes/{id}`; отсутствие 404 у `POST /quotes` |
| P1 | C2. `POST /transfers` документирует дефолт 200, runtime отдаёт 201/202 без моделей | POST перевод, провайдер `ok` / `slow` | SPEC: `POST → 201 + Location`; Backend §6: `202` + статус — оба с телом перевода | У роута нет `status_code` → OpenAPI-дефолт **200**; `respond_with_outcome` мутирует код в 201/202 (`transfers.py:52-57,60-72`), а записи 201/202 — голые `{"description"}` без `model` | `app/api/v1/transfers.py:52-72` | OpenAPI-assert: нет дефолта 200; 201/202 ссылаются на `TransferResponse` |
| P1 | C3. `TransferResponse.source/target_amount` — голый `dict` | `GET /transfers/{id}` | Деньги — строка по minor units; SPEC: `Field(description, examples)` на поле DTO | `source_amount: dict`, `target_amount: dict` (`schemas/transfers.py:50-51`) — без ключей/примеров в схеме; маппер кладёт `Money.model_dump()` (`mappers.py:61-66`), но OpenAPI этого не видит. У котировок `Money` с `examples` | `app/schemas/transfers.py:43-56`; `app/api/mappers.py:61-66` | OpenAPI-assert на `amount/currency` в ответе перевода либо смена типа на `Money` |
| P1 | C4. `GET /quotes` не «в том же формате»: `breakdown` всегда `[]` | POST → GET того же id | Backend §6: «Возвращает котировку в том же формате» | POST возвращает шаги (`mappers.py:30`), GET — `breakdown=[]` (`mappers.py:44`). Клиент после перезагрузки видит пустой расчёт. (В раунде 2 фиксировалось лишь отсутствие asserts — само расхождение POST↔GET новое) | `app/api/mappers.py:15-46` | `assert get.breakdown == post.breakdown` (или зафиксировать «GET без breakdown») |
| P2 | C5. `expires_at` — `+00:00`, пример контракта — `Z` | Любой `POST /quotes` | Backend §6: `"2026-11-02T09:15:00Z"` | Pydantic сериализует aware-datetime как `+00:00`. Строковое сравнение по примеру ТЗ падает. (Отличие от N6 раунда 2: там naive-vs-aware между POST и GET; здесь суффикс формата на обоих) | `app/schemas/quotes.py:37`; `app/api/v1/quotes.py:92-95` | Assert суффикса `expires_at` (`Z` либо документированный `+00:00`) в POST и GET |
| P1 | C6. `payout_method` не нормализуется, страны/валюты — да | `payout_method="bank_account"`, валюты строчными | `UNSUPPORTED_DIRECTION` — «нет направления/способа» | Валюты/страны `.upper()` (`quotes.py:54-55,63-66`), а `payout_method` уходит сырым в `find_direction` (`quotes.py:67`). `bank_account` → 400, `rub` → ок. `amount_type` (`Literal`) строго заглавный → `source` даёт 422. Три соседних поля — три политики регистра | `app/api/v1/quotes.py:54-68`; `app/schemas/quotes.py:7-16` | Параметризация регистра трёх полей → единая политика |
| P1 | C7. «Нет клиента» — 422, «нет перевода» — 404 | `POST /transfers` с `client_id="c_nobody"`; `GET /clients/nobody/balance` | Контракт молчит; README заявляет `TRANSFER_NOT_FOUND → 404` | `lock_client is None → ValidationError` → **422** (`services/transfers.py:181-183`); баланс неизвестного клиента — тоже 422 (`api/v1/transfers.py:120`). Рядом `GET /transfers/{id}` — 404. Один класс «нет сущности» — два кода | `app/services/transfers.py:181-183`; `app/api/v1/transfers.py:118-123` | Неизвестный `client_id` → единый код (404 либо документированный 422) на обоих рутах |
| P1 | C8. История не отсортирована и смешивает словари статусов | Перевод с попытками + вебхуками; `GET /transfers/{id}` | Backend §6: «статус, история переходов, попытки: кто/когда/с каким результатом» | `build_history` клеит `PENDING` + попытки + события + финал **без сортировки** (`transfers.py:23-32`); события выплат лежат в одной ленте со статусами перевода; актёры `client/provider:x/service` не описаны. Попытки без `updated_at`/причины выбора | `app/api/v1/transfers.py:23-32`; `app/api/mappers.py:68-81` | Assert монотонности `history[].at` и разделение лент «перевод vs выплаты» |
| P2 | C9. `provider_id` верхнего уровня — эвристика «последний с payout_id» | Попытка без `payout_id` (NEEDS_REVIEW) поверх успешной | Поле не описано в Backend §6 | `next(... reversed(attempts) if a.payout_id ...)` (`mappers.py:55`): при пустом `payout_id` у свежей попытки ответ показывает **старого** провайдера. Правило не зафиксировано | `app/api/mappers.py:55-67` | Перевод с `NEEDS_REVIEW`-попыткой → assert `provider_id` (null либо документированная эвристика) |
| P2 | C10. Часовые пояса в `GET /transfers` и истории | Любой перевод на SQLite-профиле | Даты — aware UTC (как `now_utc()` при записи) | `transfer_to_response`/`build_history` отдают сырые ORM-даты (`mappers.py:75,80-83`); на SQLite `DateTime(timezone=True)` возвращается naive. N6 раунда 2 покрывал только котировки; трансферный рут `as_utc` не нормализован | `app/api/mappers.py:68-84`; `app/api/v1/transfers.py:23-32` | Assert TZ у `at/created_at/updated_at` на PG и SQLite |
| P1 | C11. Вебхук `FAILED` без `failure_code`: схема молча режет поле | Подписанный `POST /webhooks/{id}` с `failure_code` | Backend §7: событие из 5 полей; статус выплаты провайдера включает `failure_code` | `WebhookEvent` — только 5 полей, `strict=True`, без `forbid` (`schemas/webhooks.py:6-13`): лишнее `failure_code` **тихо игнорируется**. Диагностика отказа по вебхуку теряется | `app/schemas/webhooks.py:1-20`; `app/services/webhooks.py:46` | Подписанный event с `failure_code` → 422 (forbid) либо сохранение кода |
| P1 | C12. У вебхука/баланса нет `responses`/моделей в OpenAPI | `POST /webhooks/{id}`, `GET /clients/{id}/balance` | SPEC: `summary, description, Field(examples), responses={...}` со схемами RFC 9457 | Вебхук: только `summary`, без `description`/`response_model`/`responses` (`webhooks.py:12-23`); баланс: только `summary` (`transfers.py:110-114`). Ошибки 401/422/503 вебхука и 422 баланса в спеке отсутствуют | `app/api/v1/webhooks.py:12-23`; `app/api/v1/transfers.py:110-123` | OpenAPI-assert на `responses` обоих рутов |
| P2 | C13. Двойная регистрация рутов: `Location` всегда корневой | `POST /api/v1/quotes`, `POST /api/v1/transfers` | SPEC: дублировать корневые пути; README: «те же маршруты с префиксом» | Один и тот же router включён дважды (`main.py:110-115`) → дубли operationId в спеке; `Location` всегда `/quotes/{id}`, `/transfers/{id}` без префикса (`quotes.py:94`, `transfers.py:57`). Тест раунда 2 проверяет лишь подстроку id — префикс не ловит | `app/main.py:110-115`; `app/api/v1/quotes.py:94`; `app/api/v1/transfers.py:57` | Assert префикса `Location` под оба базовых пути; уникальность operationId |
| P2 | C14. В не-`dev` нет `/openapi.json` | `GET /openapi.json` при `ENV≠dev` | SPEC: «Разметить все эндпоинты… полный перечень кодов» | `openapi_url/docs_url = None` вне dev (`main.py:75-76`). Проверяющий, работающий по путям §6 в прод-профиле, спеку не получит | `app/main.py:74-79` | Тест доступности спеки вне dev либо документированное ограничение |
| P2 | C15. Баланс отдаёт только хранимые валюты, нули опущены | `GET /clients/c_001/balance` (клиент без строки THB) | Backend §6: «остаток и резерв по каждой валюте» | `get_balances` возвращает только строки из БД (`repositories/balances.py:15-23`); `balances_to_response` маппит их 1:1 (`mappers.py:87-100`). Валюты без строки отсутствуют, а не `"0.00"` | `app/repositories/balances.py:15-23`; `app/api/mappers.py:87-100` | Assert полного множества валют из `currencies.json` с нулями |

## Подтверждённые расхождения с контрактом

1. **C1**: `GET /quotes/{id}` → 410 без отражения в `responses`; `POST /quotes` заявляет недостижимый 404. Открытая спека врёт в обе стороны.
2. **C2**: `POST /transfers` живёт на runtime-кодах 201/202 при задокументированном дефолте 200; модели 201/202 не привязаны.
3. **C3**: суммы перевода — `dict` без схемы против `Money` у котировок; проверка «деньги строкой по minor units» на уровне OpenAPI невозможна.
4. **C4**: `GET /quotes` ≠ `POST /quotes` по `breakdown` — прямое нарушение «в том же формате».
5. **C8**: `history` несортирована, смешивает статусы перевода и выплат, актёры не описаны.
6. **C13**: дубли operationId + `Location` без префикса версии.

## Не покрыто тестами

- Ни одного OpenAPI-assert: C1, C2, C3, C12, C13, C14 (поиск по тестам: `openapi` — 0 совпадений, подтверждено в раунде 2 `test-quality.md`).
- `test_quotes_api.py:43` проверяет `breakdown` как «непустой список», `test_quotes_api.py:47-51` — лишь равенство `quote_id`; эквивалентность POST↔GET (C4), суффикс `Z` (C5), сортировка истории (C8) не проверяются.
- `test_versioned_and_root_paths_match` (`test_quotes_api.py:54-64`) сравнивает только статусы, не тела и не `Location`-префикс (C13).
- Регистр `payout_method`/`amount_type`/валют (C6), неизвестный клиент 422-vs-404 (C7), `provider_id`-эвристика (C9), naive-даты трансферного рута на SQLite (C10), `failure_code` в вебхуке (C11), нулевые валюты баланса (C15) — параметризации нет ни в unit-, ни в API-тестах.
- Таблица 9 кодов Backend §6 покрыта частично: `QUOTE_EXPIRED` на `POST /transfers` (410 через чужую котировку), `IDEMPOTENCY_CONFLICT`/`TRANSFER_NOT_FOUND`/`PROVIDER_UNAVAILABLE`/`SERVICE_UNAVAILABLE`/`UNAUTHORIZED` (README-коды) в `test_quotes_api.py` отсутствуют — это файловая зона данного ревью, остальные transfer/webhook-тесты здесь не переоцениваются.
