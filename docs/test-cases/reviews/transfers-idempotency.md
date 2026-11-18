# Ревью: переводы, идемпотентность, балансы, лимиты, конкурентность

Снимок рабочего дерева: **2026-11-19 ~02:17 MSK**, `HEAD c35b896` + незакоммиченные правки
(`app/services/transfers.py`, `webhooks.py`, `reconcile.py`, `providers.py`,
`quotes.py`, `tests/conftest.py`, `tests/integration/*`). Правки вносились параллельно с
ревью: часть найденного (B9, B10) была устранена в процессе, это отмечено отдельно.
`файл:строка` — по указанному снимку; после следующих правок номера могут сдвинуться.
Код и тесты в проекте ревьюером не изменялись.

Область: `app/services/transfers.py`, `app/services/finalize.py`, `app/services/limits.py`,
`app/repositories/balances.py`, `app/repositories/transfers.py`, `app/api/v1/transfers.py`,
`app/models/tables.py`, `tests/integration/test_transfers_api.py`,
`tests/integration/test_webhooks_and_limits.py`, решения D5/D6/D7/D8/D9/D11.

Метод: чтение кода + воспроизведение на реальном PostgreSQL 16 в отдельной БД
(`transfer_review*`, `Base.metadata.create_all` + реальный сидер `data/`) через
`httpx.ASGITransport` с подменённым HTTP-клиентом провайдера. Всё перечисленное в разделе
«Подтверждённые баги» (B1–B5) наблюдалось эмпирически на этом снимке,
а не только выведено из кода.

Пересечения с другими ревью (чтобы не дублировать): конкурентный дубль `Idempotency-Key` —
INF-14 в `infra-security.md` (в этой рабочей копии уже исправлен); потеря вебхука после
`commit` — W-10/W-13 в `webhooks-reconcile.md` (снимок до правки); общий уровень тестов — `test-quality.md`.

Severity: **P0** — деньги/лимиты не защищены или нарушен контракт идемпотентности с
зависшим резервом; **P1** — денежный риск, 5xx на штатном сценарии, обязательный по SPEC
сценарий без покрытия; **P2** — латентный дефект, контрактная неоднозначность, отсутствие
регрессии.

## Кейсы: идемпотентность

| ID | Severity | Кейс | Вход | Ожидание | Текущее поведение | Файл:строка | Предлагаемый тест |
|---|---|---|---|---|---|---|---|
| TR-IDEM-01 | **P0** | Повтор ключа после истечения quote | `c_001`, quote 10000 RUB (TTL 900 с), перевод `COMPLETED`; `now = created + 100000 с`; тот же ключ и тело | Тот же `transfer_id` и терминальный статус (D8), без обращения к провайдеру | `410 QUOTE_EXPIRED`: проверка `expires_at` стоит **до** поиска ключа. Для `PENDING`-перевода (сбой после commit) повтор невозможен, резерв висит до сверки — **подтверждено** (201 → 410) | `app/services/transfers.py:164-165`, `:176-182` | `test_replay_after_quote_expiry`: создать перевод, сдвинуть `app.api.v1.transfers.now_utc` за TTL, повторить → 200/201, тот же id, `create_payout` не вызван |
| TR-IDEM-02 | P2 | Два параллельных запроса с одним ключом | `asyncio.gather` двух одинаковых `POST /transfers`, один quote, один ключ | Оба 200/201 с одним `transfer_id`, один резерв, один payout (D8/D9) | **Исправлено в рабочей копии во время ревью**: `IntegrityError` → `rollback` → повторное чтение ключа; проверено gather 2× → 201/201 с одним id. Регрессионного теста нет; остаётся возможная двойная работа `run_attempts` у проигравшего | `app/services/transfers.py:221-235`; `app/models/tables.py:145` | `test_concurrent_same_key_returns_one_transfer`: gather 2–20 запросов, все 200/201 с одним id, одна строка `transfers`, `sum(create_payout)==1` |
| TR-IDEM-03 | P1 | Два разных ключа на один quote | Один quote 5000 RUB, ключи `k1`, `k2` | Второй запрос отвергнут (`409/422`) либо один перевод: quote — это блокировка курса на сумму | Два независимых перевода, `available` 490000 → 480000, два payout у провайдера — **подтверждено**. Ограничение только баланс/лимит; тест `test_no_double_reserve_under_concurrency` сам опирается на это поведение | `app/services/transfers.py:176-182`; `app/models/tables.py:135` (нет UNIQUE/маркера использования quote) | `test_two_keys_one_quote_rejected` (зафиксировать решение) + `create_payout` count == 1 |
| TR-IDEM-04 | P2 | Повтор во время выполнения | Перевод `PROCESSING` (`get_payout → PROCESSING`), `api_sync_timeout_sec=1`, повтор тем же ключом | 202 + тот же id и текущий статус, без новой выплаты (A4) | Повтор заново проходит `run_attempts`: снова читает курсы и опрашивает провайдера до 12 с; ошибки нет, но зависимость от курсов и задержка не проверены | `app/api/v1/transfers.py:73`; `app/services/transfers.py:254`, `:276-289` | `test_replay_while_processing`: таймаут 1 с, счётчики `create_payout`/`get_payout`, тот же id |
| TR-IDEM-05 | P2 | Повтор после `FAILED` | Терминальный `FAILED`, тот же ключ | Тот же id/`FAILED`, выплата не создаётся (D6) | Реализовано ранним выходом из `run_attempts`; теста нет | `app/services/transfers.py:249-250` | `test_replay_after_failed_returns_same_terminal` |
| TR-IDEM-06 | P1 | Новый ключ после `FAILED` на том же quote | Перевод `FAILED`, затем новый ключ + тот же `quote_id` | Повторная выплата по тому же курсу запрещена (или явно разрешена и задокументирована) | Создаётся второй перевод и второй payout — то же, что TR-IDEM-03, но это типовой клиентский retry после неудачи | `app/services/transfers.py:176-182`; `app/api/v1/transfers.py:64-84` | `test_new_key_after_failed_does_not_double_pay` |
| TR-IDEM-07 | P2 | Тот же ключ, другой recipient | Изменён `recipient.account` | 422 `IDEMPOTENCY_CONFLICT`, второй перевод не создан, баланс не изменён | 422 работает — **подтверждено**; существующий тест меняет только `quote_id` и не проверяет отсутствие второго перевода/движение баланса | `app/services/transfers.py:170-182`; `tests/integration/test_transfers_api.py:185-197` | Расширить `test_idempotency_different_body_conflicts`: recipient + client, `count(transfers)==1`, баланс неизменен |
| TR-IDEM-08 | P2 | Тот же ключ, другой клиент | `k` клиента `c_001` повторён с `c_002` | 422, перевод клиента B не создан | 422 `IDEMPOTENCY_CONFLICT` — **подтверждено**; теста нет | `app/services/transfers.py:178-182` | `test_same_key_other_client_conflicts` |
| TR-IDEM-09 | P2 | Namespace ключей | Клиент B использует ключ `k-1`, уже занятый клиентом A для другого тела | По D8 не определено: либо глобальный namespace (текущее), либо ключ скоупится клиентом | Ключ глобальный (`UNIQUE(idempotency_key)`), B получает 422 на свой перевод; поведение не зафиксировано ни тестом, ни решением | `app/models/tables.py:145`; `app/repositories/transfers.py:35-37` | `test_idempotency_key_scope`: зафиксировать решение и проверить (глобально → 422; по клиенту → 201) |
| TR-IDEM-10 | P2 | Ключ > 128 символов | `Idempotency-Key` из 200 символов | 422 `VALIDATION_ERROR` (контракт заголовка) | Валидируется только непустота; PostgreSQL отвергает `VARCHAR(128)` → `DataError` → 500 | `app/api/v1/transfers.py:57-58`; `app/models/tables.py:145` | `test_idempotency_key_too_long_422` |
| TR-IDEM-11 | P2 | Отсутствующий заголовок | Нет `Idempotency-Key` | 422 `VALIDATION_ERROR` | Реализовано; теста нет | `app/api/v1/transfers.py:57-58` | `test_missing_idempotency_key_422` |
| TR-IDEM-12 | P2 | Повтор `PENDING` при недоступных курсах | Перевод создан, `run_attempts` упал; `RatesClient.fetch` бросает; повтор тем же ключом | Идемпотентный повтор не зависит от `/rates`, возвращает текущий статус | `run_attempts` читает курсы **до** проверки существующих попыток → 500 на повторе | `app/services/transfers.py:254`, `:276-289` | `test_replay_when_rates_unavailable`: первый POST → 500, второй при живых курсах завершает, резерв не задвоен |
| TR-IDEM-13 | P2 | Сбой после commit `create_transfer` | `run_attempts` бросает исключение (курсы/провайдер) | Перевод `PENDING` + резерв; повтор тем же ключом завершает; резерв не теряется | Так и есть, но повтор упирается в TR-IDEM-01 после истечения quote; теста на весь сценарий нет | `app/services/transfers.py:220-235`; `app/api/v1/transfers.py:64-73` | `test_partial_failure_after_create_then_replay` (инвариант + один payout) |
| TR-IDEM-14 | P2 | Quote истёк между созданием и выплатой | Перевод создан за 1 с до `expires_at`, выплата идёт после | Решение: курс зафиксирован при создании (документировать) либо отказ | Повторной проверки `expires_at` в `run_attempts` нет — выплата проходит по старому курсу | `app/services/transfers.py:164` (только create), `:251-253` | `test_created_transfer_executes_after_quote_expiry` (зафиксировать правило) |

## Кейсы: балансы, резервы и конкурентность

| ID | Severity | Кейс | Вход | Ожидание | Текущее поведение | Файл:строка | Предлагаемый тест |
|---|---|---|---|---|---|---|---|
| TR-BAL-01 | P1 | 50–100 параллельных на один баланс | `c_002` (50000 RUB), 100 запросов по 1000–2000 RUB, разные ключи/quote | `available + reserved = start`, число успехов = `start // amount`, нет 500/`TimeoutError`, `pool.checkedout()` в бюджете (SPEC п.4) | Есть только 5 запросов; 50 — это `POST /quotes`, без проверки пула. Нагрузочного теста нет (`tests/load` пуст) | `tests/integration/test_webhooks_and_limits.py:116-138`, `:95-113`; `tests/load/` | `test_100_concurrent_transfers_single_balance`: gather 100, `engine.pool.checkedout() <= pool_size+max_overflow`, инвариант |
| TR-BAL-02 | P2 | Ровно доступная сумма | `c_007` = 1000.00 RUB, quote 1000.00 | 201 `COMPLETED`, `available=0`, `reserved=0` (проверка строгая `<`) | Так и должно быть; теста на границу нет (текущий тест использует 10000 против 1000) | `app/services/transfers.py:194`; `tests/integration/test_transfers_api.py:303-309` | `test_balance_exact_amount_allowed` |
| TR-BAL-03 | P2 | Нулевой баланс | Существующая строка `available=0` | 409 `INSUFFICIENT_FUNDS`, `available/reserved` не меняются | Так и есть; теста нет (нулевой строки в сиде нет) | `app/services/transfers.py:192-198` | `test_zero_balance_rejected` |
| TR-BAL-04 | P2 | Клиент без строки баланса | Клиент есть, строки `balances` нет | 409 `INSUFFICIENT_FUNDS`; строка не создаётся | `available=0` → 409; `ensure_balance` не вызывается, строка не появляется. Теста нет | `app/services/transfers.py:192-193`; `app/repositories/balances.py:34-42` | `test_client_without_balance_row` |
| TR-BAL-05 | P2 | Баланс в другой валюте | `c_005` (USD), направление RU-TH (RUB) | 409 | 409; теста нет | `app/services/transfers.py:192` | `test_balance_wrong_currency` |
| TR-BAL-06 | P2 | Неизвестный клиент | `client_id="nope"` | 422 `VALIDATION_ERROR` (и в `GET /balance` — 422, не 404) | Реализовано; теста нет | `app/services/transfers.py:166-168`; `app/api/v1/transfers.py:113-121` | `test_unknown_client_transfer_and_balance` |
| TR-BAL-07 | P2 | Двойная финализация sync + webhook | `COMPLETED`-вебхук и синхронный `wait_for_settlement` одновременно | Один `consume_reserve`, `reserved` не отрицателен | `_claim` (`UPDATE ... WHERE status NOT IN terminal`, `rowcount`) защищает; теста нет | `app/services/finalize.py:14-35` | `test_concurrent_complete_consumes_once` |
| TR-BAL-08 | P2 | FAILED-вебхук + сверка одновременно | Два `fail_transfer` по одному переводу | Один `release_reserve`, `available = start`, `reserved=0` | `_claim` защищает; в БД нет CHECK на неотрицательность `reserved/available` | `app/services/finalize.py:38-45`; `app/repositories/balances.py:60-66`; `app/models/tables.py:108-113` | `test_concurrent_fail_releases_once` + CHECK-тест |
| TR-BAL-09 | P2 | Инвариант после сценариев | Набор reserve/consume/release/refund | `available + reserved + списано = start` | Ни один тест не считает «списанное»; нет ledger-агрегата, инвариант проверяется глазами | `tests/integration/*` | Helper `assert_balance_invariant(client, start)` во всех денежных тестах |
| TR-BAL-10 | P2 | sqlite-профиль | `DB_PROFILE=sqlite`, конкурентные тесты | Конкурентные тесты должны скипаться: блокировок нет | `for_update` молча отключён для sqlite → `test_no_double_reserve_under_concurrency` на sqlite даст другую семантику (ложный зелёный/красный) | `app/repositories/balances.py:9-12`; `tests/conftest.py:19-30` | `pytest.mark.skipif` по профилю и явный тест «sqlite не гарантирует блокировки» |
| TR-BAL-11 | P2 | Параллельные переводы в разных валютах одного клиента | `c_006` (RUB+KWD), лимит в RUB | Лимит — общий на клиента, проверки сериализуются | Блокируется строка баланса конкретной валюты; два разных баланса → лимит проверяется параллельно. Для c_006 KWD-перевод вообще обходит лимит (см. TR-LIM-01) | `app/services/transfers.py:192-219`; `app/repositories/balances.py:25-31` | `test_limit_serialized_across_currencies` (нужен клиент с лимитом в THB и два направления) |

## Кейсы: лимиты

| ID | Severity | Кейс | Вход | Ожидание | Текущее поведение | Файл:строка | Предлагаемый тест |
|---|---|---|---|---|---|---|---|
| TR-LIM-01 | **P0** | Лимит в валюте вне пары quote | `c_003`: лимит 5000 USD, два перевода по 300000 RUB (RU-TH, rate RUB→THB) | Второй 409 `DAILY_LIMIT_EXCEEDED`: 600000 RUB ≈ 7200 USD | Оба 201: `to_limit_currency` умеет только «source↔target текущего quote», иначе `None`; `spent` по таким строкам не считается, `addition is None` → `return` (fail-open) — **подтверждено** | `app/services/limits.py:18-34`, `:53-65` | `test_daily_limit_other_currency_enforced` (c_003, 2×300000 → второй 409) |
| TR-LIM-02 | P1 | Лимит-определение с валютой без прямого курса | `c_001` + `LimitDefinition(scope=client, DAILY, amount=1e6, currency=KZT)` + RU-TH | Перевод создаётся или доменная 4xx; сервис не падает | `TypeError: convert_with_hints() missing 2 required positional arguments` → 500 — **подтверждено** (вызов с 4 аргументами вместо 6) | `app/services/limits.py:129-132`; сигнатура `:142-149` | `test_limit_definition_unknown_rate_is_not_500` |
| TR-LIM-03 | P1 | HOURLY | `LimitDefinition(HOURLY, 50000 RUB)`, два перевода по 30000 RUB за 10 минут | Второй 409 | Код есть, но нет ни теста, ни сида; окно `[now-1h, now)` не имеет верхней границы (строки с `created_at > now` считаются) | `app/services/limits.py:99-100`, `:107-112`; `app/repositories/transfers.py:115-140` | `test_hourly_limit_window` (+ «ровно час назад» / «час+1 с») |
| TR-LIM-04 | P1 | WINDOW_N 7/16 дней | `WINDOW_N`, `window_days=7` и `16`; перевод 8 дней назад и 6 дней назад | 8-дневный не считается, 6-дневный считается | Реализовано, тестов нет; `window_days=0` даёт пустое окно, `NULL` → `int(None or 0)` = 0 | `app/services/limits.py:103-104`; `app/repositories/transfers.py:115-140` | `test_window_7d_and_16d_boundaries` (patch `transfers_route.now_utc`) |
| TR-LIM-05 | P2 | Ровно на границе | `c_002` (limit 100000): 50000 + 50000, затем +1000 | Равенство разрешено, превышение — 409 (A9) | Сравнение строгое `>` (равенство проходит); теста нет — текущий тест берёт 2×200000 против 300000 | `app/services/limits.py:67`, `:135`; `tests/integration/test_transfers_api.py:312-323` | `test_daily_limit_exact_boundary` |
| TR-LIM-06 | P2 | Вес | `weight=2`, сумма 10000 | В окне учитывается 20000 | Реализовано, тестов нет; отрицательный/нулевой вес не валидируется (может увеличить остаток) | `app/services/limits.py:119`, `:135`; `app/models/tables.py:193` | `test_limit_weight_applied`, `test_negative_weight_rejected` |
| TR-LIM-07 | P2 | Scope `direction` | `LimitDefinition(scope=direction, direction_id="RU-TH-BANK", HOURLY)` | Считаются только переводы этого направления | Логика есть; нельзя создать два direction-правила на один период: `UNIQUE(client_id, scope, period_type)` не включает `direction_id`/`category` | `app/services/limits.py:97`, `:110`; `app/models/tables.py:196-198` | `test_direction_scope_counts_only_direction` + schema-тест на два правила |
| TR-LIM-08 | P2 | Scope `category` | `scope="category"` | Категория перевода | У `transfers` нет колонки `category`; правило `scope != "direction"` считается как client-wide → скрытое ужесточение всех переводов | `app/services/limits.py:97-112`; `app/models/tables.py:132-148`, `:188` | Зафиксировать решение; тест «category-правило не влияет на другие категории» |
| TR-LIM-09 | P2 | Выключенное правило | `is_active=false` | Не влияет | Реализовано; теста нет | `app/services/limits.py:87-89` | `test_inactive_limit_ignored` |
| TR-LIM-10 | P2 | Лимит в валюте = target quote | Клиент с лимитом THB, RU-TH | Конвертация по `quote.rate` | Ветка работает; теста нет (ни один сид-клиент не имеет лимит THB) | `app/services/limits.py:30-31` | `test_daily_limit_target_currency` |
| TR-LIM-11 | P2 | История переводов в разных валютах | Лимит THB; ранее были RUB- и KWD-переводы; сейчас RUB→THB | Каждая строка конвертируется своим курсом (`quote.rate` строки) | `sum_day_source` возвращает rate строки, но `check_daily_limit` его игнорирует и конвертирует **все** строки курсом текущего quote → сумма неверна | `app/services/limits.py:53-60`; `app/repositories/transfers.py:96-102` | `test_daily_limit_history_uses_each_row_rate` |
| TR-LIM-12 | P2 | UTC-граница суток | 23:59:59 и 00:00:01 следующего дня | Новое окно дня | `day_bounds` корректен; тестов нет; для теста нужно патчить `now_utc` и в `/quotes`, и в `/transfers` | `app/services/limits.py:13-15` | `test_utc_day_boundary` (frozen clock для обоих роутов) |
| TR-LIM-13 | P2 | `FAILED` и лимит | Перевод `FAILED` | Решение: освобождает лимит (деньги не ушли) — да | В агрегат входят только `PENDING/PROCESSING/COMPLETED`, `FAILED`/`CANCELLED` не считаются; теста/фиксации нет | `app/repositories/transfers.py:108`, `:134` | `test_failed_transfer_frees_daily_limit` |
| TR-LIM-14 | P2 | Код ошибки для HOURLY/WINDOW | Превышен HOURLY | Отдельный код или явно `DAILY_LIMIT_EXCEEDED` | Все виды лимитов бросают `DailyLimitExceededError`; в `details` только `limit` | `app/services/limits.py:135-139` | `test_limit_error_code_per_period` |
| TR-LIM-15 | P2 | Часовой лимит по умолчанию | Нет `LimitDefinition` у клиента | D11: «часовой — строкой HOURLY, дефолт daily/6» | Ни сида, ни автосоздания правила: по умолчанию часового лимита нет | `docs/DECISIONS.md` D11; `app/db/seed.py:123-145` | Зафиксировать решение и покрыть сид/тест |

## Кейсы: состояние перевода, частичные сбои, финализация

| ID | Severity | Кейс | Вход | Ожидание | Текущее поведение | Файл:строка | Предлагаемый тест |
|---|---|---|---|---|---|---|---|
| TR-ST-01 | P1 | Провайдер принял, затем settled `FAILED` | `create_payout → Accepted`, `get_payout → FAILED` | В том же запросе: fallback на следующего провайдера или `FAILED` + возврат резерва | 202 + статус `PROCESSING`, `reserved=4000.00`; следующий провайдер пробуется только при повторном `run_attempts` (вебхук/сверка) — **подтверждено** | `app/services/transfers.py:356-372`, `:458-465` | `test_settled_failed_falls_back_or_finalizes` |
| TR-ST-02 | P2 | Все провайдеры перебраны | Два провайдера, оба `FAILED` | `FAILED`, `reserved=0`, без дублей payout | На втором проходе `tried` исключает первого, второй падает → `fail_transfer`; теста нет | `app/services/transfers.py:299-300`, `:413-417` | `test_all_providers_failed_releases_reserve` |
| TR-ST-03 | P2 | Синхронный таймаут | `api_sync_timeout_sec=1`, `get_payout` всегда `PROCESSING` | 202 + `PROCESSING`, деньги в резерве (D5) | Так и есть; теста нет, значение таймаута в тестах не уменьшается (каждый такой тест ждал бы 12 с) | `app/services/transfers.py:433-465`; `app/core/settings.py:30` | `test_sync_timeout_returns_202` с `monkeypatch.setattr(app_settings, "api_sync_timeout_sec", 1)` |
| TR-ST-04 | P2 | `FAILED`-перевод отдаётся как 202 | Все провайдеры `Declined` | Контракт: итог известен → 201 или 200 с `Location` | `final_status != COMPLETED` → 202 без `Location`; тест принимает «201 или 202», контракт не зафиксирован | `app/api/v1/transfers.py:79-84`; `tests/integration/test_transfers_api.py:134-143` | `test_failed_transfer_http_contract` |
| TR-ST-05 | P2 | Отмена перевода | `POST /transfers/{id}/cancel` или `DELETE` | `CANCELLED`, резерв возвращён | Роута нет; `CANCELLED` есть в статусах/`TERMINAL`, но недостижим | `app/api/v1/transfers.py`; `app/models/tables.py:38-40`; `app/services/transfers.py:38` | `test_cancel_transfer` или зафиксировать отсутствие в контракте |
| TR-ST-06 | P2 | Чужая выплата в вебхуке | `event.reference` = перевод A, `payout_id` = выплата B того же провайдера | A не завершается; резерв A не списывается | В рабочей копии добавлены проверки `state.reference` и `find_attempt_by_payout`; остаётся случай, когда попытка ещё не записана (`attempt is None`) и `state.reference` пустой — тогда проверка не срабатывает. Детали: W-01 в `webhooks-reconcile.md` (до правки) | `app/services/webhooks.py:97-115`; `app/repositories/transfers.py:199-206` | `test_webhook_foreign_payout_not_applied` (в т.ч. `attempt=None`) |
| TR-ST-07 | P2 | `FAILED`-вебхук при живой выплате | Попытка `PROCESSING`, приходит `FAILED` | Второй payout не создаётся; событие не блокирует повторную доставку, пока состояние не подтверждено | `run_attempts` видит `live` и только опрашивает провайдера; если `get_payout → None`, событие всё равно сохраняется в конце → повторная доставка дедуплицируется, хотя payout мог уже завершиться (спасёт только сверка) | `app/services/webhooks.py:105-130`; `app/services/transfers.py:277-289` | `test_failed_webhook_does_not_create_second_payout` + `test_failed_webhook_provider_unavailable_is_retriable` |
| TR-ST-08 | P2 | Повтор провайдера после транзиентного отказа | Провайдер ответил `503` (маппится в `Declined`) | Транзиентный отказ не должен навсегда исключать провайдера | `tried` исключает провайдера после любой попытки, включая `PROVIDER_UNAVAILABLE`, на всех следующих проходах | `app/services/transfers.py:299-300`; `app/services/providers.py:180-181` | `test_transient_provider_failure_can_retry` (решение) |
| TR-ST-09 | P2 | Неизвестный итог без записи у провайдера | `create` всегда `UnknownResult`, `find` → `None` | Следующий провайдер, без второй выплаты; POST-ы и GET-ы в правильном порядке | Покрыт только случай «запись есть»; ветка «записи нет» стоит ~21 с из-за жёстких `sleep` (1.5/2.5 с), инъекции задержек нет | `app/services/transfers.py:66-108`; `tests/integration/test_transfers_api.py:146-165` | `test_unknown_without_record_falls_back`: счётчики/порядок вызовов, задержки через параметр |
| TR-ST-10 | P2 | Конкурентные `run_attempts` | Повтор ключа и сверка одновременно | Одна попытка на провайдера, без дублей строк | `attempt_no = len(attempts)+1` без блокировки/UNIQUE; возможны одинаковые `attempt_no` и дубли строк (payout защищён ключом провайдера) | `app/services/transfers.py:290-302`; `app/repositories/transfers.py:143-171`; `app/models/tables.py:151-166` | `test_concurrent_run_attempts_single_attempt_row` |
| TR-ST-11 | P2 | Резерв не может стать отрицательным | Баг финализации/ручная правка | БД/код запрещают `reserved < 0`, `available < 0` | Ни CHECK в схеме, ни проверки в `consume/release`: любая логическая ошибка даст отрицательный резерв | `app/models/tables.py:108-113`; `app/repositories/balances.py:52-66` | `test_balance_never_negative` + CHECK-констрейнты |

## Подтверждённые баги

### B1. Дневной лимит не проверяется, если его валюта не входит в пару quote (P0)

`to_limit_currency` (`app/services/limits.py:18-34`) конвертирует только `source→target`
и `target→source` текущего quote. Для `c_003` (лимит 5000 USD) на направлении RU-TH
(RUB→THB) и `spent`, и `addition` дают `None`; `check_daily_limit` при `addition is None`
делает `return` — проверка молча пропускается (`:61-65`). Воспроизведено: два перевода по
300000 RUB (600000 RUB ≈ 7200 USD при `RUB→USD=0.012`) оба `201`, лимит 5000 USD не
сработал. Это fail-open и прямое нарушение D11/Level 3.

Исправление: конвертировать через граф курсов (в проекте уже есть
`convert_amount` в `app/services/providers.py:33-62`), а при отсутствии пути — явная
доменная ошибка, а не `return`.

### B2. Идемпотентный повтор после истечения quote возвращает 410 (P0)

`create_transfer` сначала проверяет `quote.expires_at` (`app/services/transfers.py:164-165`)
и только потом ищет ключ (`:176-182`). Воспроизведено: первый POST — 201, повтор того же
ключа/тела после TTL — `410 QUOTE_EXPIRED`. Если первый запрос упал после commit
(`PENDING` + резерв), клиент не может довести перевод тем же ключом; остаётся только
сверка. Нарушает D8 («совпало — вернуть тот же transfer_id»).

Исправление: искать ключ до валидации quote/клиента (hash уже содержит `quote_id` и
`client_id`, так что возврат существующего безопасен), либо валидировать expiry только
для новых переводов.

### B3. Один quote обслуживает несколько переводов (P1)

Нет ни `UNIQUE(quote_id)`, ни маркера использования quote; ключ ищется только по
`idempotency_key`. Воспроизведено: два разных ключа на один quote → два перевода, два
резерва (`490000 → 480000`), два payout. Клиентский retry после `FAILED` с новым ключом
делает то же самое (двойная выплата по одному курсу). Тест
`test_no_double_reserve_under_concurrency` (`test_webhooks_and_limits.py:116-138`)
фактически закрепляет это поведение: 5 разных ключей на один quote.

Нужно решение: одноразовый quote (`UNIQUE(quote_id)`/`used_at`) либо явно разрешённый
многократный перевод и тогда отдельный лимит на quote.

### B4. Лимит-определение с «экзотической» валютой роняет создание перевода (P1)

В `check_limit_definitions` (`app/services/limits.py:129-132`) `convert_with_hints`
вызывается с 4 аргументами, а функция требует 6 (`:142-149`). Воспроизведено на
`LimitDefinition(currency=KZT)` при RU-TH: `TypeError` → 500. Недоступны лимиты в любой
валюте вне пары `source/target` текущего quote, для которой нет прямого курса `source→limit`.

Исправление: передать `quote_rate`/`quote_source` либо удалить мёртвую ветку и
использовать граф курсов; отсутствие курса — доменная ошибка, не 500.

### B5. `FAILED` на этапе settlement оставляет `PROCESSING` и резерв (P1)

В `wait_for_settlement` статус `FAILED` только прерывает цикл (`:458-459`), после чего
перевод безусловно переводится в `PROCESSING` (`:460-465`). Воспроизведено: accepted →
`get → FAILED` → ответ 202 `PROCESSING`, `reserved=4000.00`; следующий провайдер
пробуется только на следующем проходе (вебхук/сверка). При отключённой сверке (тесты,
`ENV=test`) перевод остаётся в этом состоянии навсегда.

Ожидание: либо сразу fallback по `remaining`, либо `fail_transfer` (возврат резерва) в
том же запросе.

### B6. Для истории лимита берётся курс текущего quote, а не курс строки (P2)

`sum_day_source` возвращает `quote.rate` каждой строки
(`app/repositories/transfers.py:96-102`), но `check_daily_limit` распаковывает rate в `_`
и конвертирует все строки курсом текущего запроса (`app/services/limits.py:53-60`).
Для клиента с лимитом в третьей валюте и историей в разных валютах сумма окна будет
неверной (в отличие от `check_limit_definitions`, где используется rate строки).
Проявляется на клиенте с лимитом THB и предыдущими переводами RUB/KWD (отдельного
воспроизведения не делалось: в сиде нет клиента с лимитом THB).

### B7. Схема не позволяет несколько direction/category-правил на период (P2)

`UNIQUE(client_id, scope, period_type)` (`app/models/tables.py:196-198`) не включает
`direction_id`/`category`. Вторая direction-правила с тем же периодом (например,
HOURLY для RU-TH и RU-JP) падает на `IntegrityError` при вставке; D11 описывает scope
«client+direction / категория», а `transfers.category` в схеме вообще нет.

### B8. `CANCELLED` недостижим (P2)

Статус есть в `transfer_statuses`, в `TERMINAL` (`app/services/transfers.py:38`,
`finalize.py:11`) и в БД, но ни одного роута/сервиса отмены нет
(`app/api/v1/transfers.py`). Контракт D6 упоминает `CANCELLED`; клиент отменить перевод
не может, резерв освобождается только по `FAILED`.

## Устранено в рабочей копии во время ревью

Ниже — дефекты, которые были воспроизведены на снимке, но устранены правками, пришедшими
во время ревью. Они остаются в этом отчёте, потому что регрессионных тестов на них нет.

### B9. Конкурентный дубль `Idempotency-Key` давал 500 (P1, устранено)

Было: `get_by_idempotency_key` не блокирует, `IntegrityError` на `UNIQUE` не обрабатывается
→ второй запрос получал 500. Сейчас `create_transfer` ловит `IntegrityError`, делает
`rollback`, перечитывает ключ и возвращает существующий перевод
(`app/services/transfers.py:221-235`). Проверено: gather двух одинаковых запросов → 201 и
201 с одним `transfer_id`. Нужен `test_concurrent_same_key_returns_one_transfer` (см.
TR-IDEM-02): фикс легко сломать, а тест `test_double_submit_returns_one_transfer`
последовательный.

### B10. `COMPLETED`-вебхук терялся при visibility lag (P1, устранено)

Было: событие коммитилось до проверки у провайдера, при `state != COMPLETED` обработка
прерывалась, повторная доставка того же `event_id` считалась дублем — перевод оставался
`PROCESSING` навсегда. Сейчас событие сохраняется только после успешной обработки
(`app/services/webhooks.py:97-130`), а при `state is None`/`!= COMPLETED` не сохраняется,
поэтому повторная доставка дообрабатывает. Проверено: первая доставка при
`get_payout → PROCESSING` не меняет статус, повтор того же `event_id` после появления
`COMPLETED` завершает перевод. Регрессии нет: оба «зелёных» теста подменяют `get_payout`
на `COMPLETED` **до** первой доставки (`test_webhooks_and_limits.py:56,74`;
`test_transfers_api.py:269`) и потому не воспроизводят lag. Детали — W-10/W-13 в
`webhooks-reconcile.md` (там снимок до этой правки).

## Не покрыто тестами

Ниже — то, что сейчас не проверяется, включая «ложное покрытие» и то, что мешает
написать тесты. Кейсы с конкретным входом/ожиданием — в таблицах выше.

### Идемпотентность

- [ ] Повтор ключа до выполнения, во время `PROCESSING` и после терминала как отдельные
  кейсы: сейчас есть только последовательный повтор после `COMPLETED`
  (`test_transfers_api.py:168-182`).
- [ ] Повтор после истечения quote (B2) — регрессии нет; существующий
  `test_expired_quote_rejected` использует **новый** ключ и не ловит порядок проверок.
- [ ] Параллельный дубль ключа (TR-IDEM-02): `test_double_submit_returns_one_transfer`
  (`test_transfers_api.py:200-214`) — последовательный вызов и побайтовый дубль
  предыдущего теста, конкурентности в нём нет.
- [ ] Два разных ключа на один quote (B3): тест либо фиксирует запрет, либо явно
  разрешает; сейчас поведение закреплено косвенно в
  `test_no_double_reserve_under_concurrency`.
- [ ] Тот же ключ + другой recipient / client / регистр UUID; отрицательные проверки
  (баланс, число строк `transfers`, число payout) в `test_idempotency_different_body_conflicts`
  отсутствуют.
- [ ] Namespace ключа (глобальный/по клиенту), длина > 128, отсутствующий/пустой заголовок,
  ключ из пробелов.
- [ ] Повтор после `FAILED` тем же ключом и новым ключом (B3/TR-IDEM-05/06).
- [ ] Сбой после `create_transfer` (500 из `run_attempts`) + повтор тем же ключом;
  проверка, что `create_payout` вызван один раз, а резерв не удвоен.
- [ ] Повтор `PENDING`/`PROCESSING` при недоступном `/rates` (TR-IDEM-12).
- [ ] Регрессия на конкурентный дубль ключа (B9/TR-IDEM-02): gather 2–20 запросов,
  один `transfer_id`, один payout; текущий `test_double_submit_returns_one_transfer`
  последовательный и фикс не защищает.
- [ ] Поведение при истечении quote между созданием и выплатой (TR-IDEM-14) — решение
  не зафиксировано ни в D-документе, ни тестом.

### Балансы и конкурентность

- [ ] 50–100 параллельных переводов на один баланс; сейчас максимум 5
  (`test_webhooks_and_limits.py:116-138`), а 50 запросов — это `/quotes`
  (`:95-113`), без `engine.pool.checkedout()` и без `TimeoutError`-проверок.
- [ ] Инвариант `available + reserved + списано = старт` нигде не проверяется; нет
  helper-а и нет учёта списанного (ledger).
- [ ] Границы: `available == source_amount`, нулевой баланс, клиент без строки баланса,
  баланс в другой валюте.
- [ ] Двойная финализация/двойной возврат: sync + webhook, два `FAILED`-события, сверка +
  вебхук. `_claim` корректен, но регрессии нет.
- [ ] Отрицательные `available`/`reserved` (CHECK в БД + тест).
- [ ] Параллельные переводы одного клиента в разных валютах (сериализация лимита).
- [ ] sqlite-профиль: конкурентные тесты должны скипаться, иначе они дают ложную
  уверенность (блокировки отключены в `for_update`).

### Лимиты

- [ ] Дневной лимит в валюте, отличной от валюты quote (B1) — главный непокрытый
  денежный кейс; `c_003` для него уже есть в сиде.
- [ ] `LimitDefinition` в валюте без прямого курса (B4) — 500.
- [ ] Ровно граница лимита (равенство разрешено), граница `max` направления.
- [ ] `HOURLY`, `WINDOW_N` 7/16, `weight`, `scope=direction`, `scope=category`,
  `is_active=false` — ни одного теста; `limit_definitions` не создаётся ни в одном
  тесте и не сидируется.
- [ ] Лимит в валюте `target` quote; история в валюте, отличной от валюты лимита.
- [ ] UTC-граница суток; `created_at` из будущего (часы) и его влияние на окно.
- [ ] `FAILED`/`CANCELLED` и лимит (освобождают окно), повтор ключа не удваивает
  использование лимита.
- [ ] Код ошибки для HOURLY/WINDOW (`DAILY_LIMIT_EXCEEDED` для всех) и `details`
  (нет текущего остатка/окна).
- [ ] Отсутствие часового лимита по умолчанию (D11 говорит про `daily/6`).

### Состояние, финализация, частичные сбои

- [ ] `FAILED` на settlement (B5): fallback/финализация в том же запросе.
- [ ] Синхронный таймаут (`api_sync_timeout_sec`), переопределённый в тесте на 1 с.
- [ ] Контракт `POST /transfers`: 201 + `Location` только для `COMPLETED`, 202 для
  `PROCESSING` **и** `FAILED`; сейчас это закреплено мягким `in (201, 202)`.
- [ ] Отмена (`CANCELLED`) — нет ни роута, ни теста.
- [ ] Вебхук при `visibility_lag`/`stale_read`: первая доставка `COMPLETED` не меняет
  статус, повторная с тем же `event_id` после появления `COMPLETED` завершает перевод
  (B10). Сейчас оба теста подменяют `get_payout` на `COMPLETED` до первой доставки, поэтому
  сценарий не воспроизводится.
- [ ] `FAILED`-вебхук при живой выплате; чужой `payout_id`; конкурентные `run_attempts`.
- [ ] Неизвестный итог без записи у провайдера; тест «запись есть» не проверяет порядок
  `GET`→`POST` и число POST-ов.
- [ ] Транзиентный отказ провайдера (`503` → `Declined`) навсегда исключает провайдера
  из `remaining` — поведение не зафиксировано.

### Инфраструктура тестов (мешает закрыть пробелы)

- [ ] Нет фикстуры «стоп-время» для обоих роутов: `now_utc` импортируется отдельно в
  `app/api/v1/quotes.py` и `app/api/v1/transfers.py`, а `now`-фикстура в `conftest.py`
  не используется. Для окон/границы суток/истечения quote нужен единый управляемый
  clock (patch обеих точек + `RateData.updated_at`).
- [ ] Нет фикстур для `LimitDefinition`/клиента с лимитом в THB/нулевым балансом/без
  строки баланса.
- [ ] Нет короткого `api_sync_timeout_sec` — тест таймаута иначе идёт 12 с; сейчас
  `wait_for_settlement` спит реальными `0.5 с` (`transfers.py:436`).
- [ ] Жёсткие задержки `resolve_unknown` (1.5/2.5 с, до ~21 с,
  `transfers.py:94,108`) не инжектируются → ветка «unknown без записи» практически
  нетестируема.
- [ ] Нет счётчиков вызовов провайдера и helper-а инварианта баланса — большинство
  проверок «нет второй выплаты» сейчас невозможно выразить.
- [ ] `test_parallel_requests_share_pool` проверяет только коды `/quotes`; имя обещает
  проверку пула, которой нет.
- [ ] `PROVIDER_WEBHOOK_SECRET` теперь обязателен (пустой → 503 на любой вебхук), но
  conftest его не задаёт: тесты вебхуков молча зависят от локального `.env`
  (`.env` в `.gitignore`). В чистом окружении они упадут по причине, не связанной с кодом.
