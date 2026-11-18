# test-cases — индекс переносимых кейсов из Java-референса

Источник: `financial-transactions` (Gherkin `src/test/resources/features/*`,
JUnit: `TransactionIsolationTests`, `WeightedLimitPolicyTests`, `MetricsRecorderTest`,
`LoggingDisclosureTests`, `HttpLoggingDisclosureTests`, `OpenApiContractTests`,
`ArchitectureTest`). Здесь — только спеки; код `pytest` не пишется, тесты не запускаются.

## Сценарии

| Директория | Источник (Java) | P | Уровень |
|---|---|---|---|
| `money-rounding` | `conversion_boundaries` (HALF_UP, ставка не округлена, min rate, потолок), `ledger_policy` (rounding, ceiling), `client_api` (row vs response) | P0 | L1 |
| `currency-lookup` | `currency_contract` (7), `currency_sweep` (12), `client_api` (unsupported) | P0 | L1 |
| `quote-contract` | `wire_contract`, `request_validation` (11), `ledger_policy` (границы) | P0 | L1 |
| `error-codes` | `request_validation` + `client_api` (ProblemDetail), `currency_pairs` (404/400) | P0 | L1 |
| `provider-selection` | `rates_fallback` (8), `rate_fetch_coordination` (3), `client_api` (division, batch-once) | P0 | L2 |
| `idempotency` | техника `concurrency` + `currency_sweep` (идемпотентность), поведение мока `409` | P0 | L2–3 |
| `balances-concurrency` | `concurrency` (5), `limit_flag_scenario` (serialise), `client_api` (HTTP 20), `TransactionIsolationTests` (3) | P0 | L2–3 |
| `webhooks` | поведение мока (`webhooks.py`: дубли/порядок/unknown/forged), техника монотонности Java | P0 | L3 |
| `reconciliation` | `daily_rate_sweep`, `currency_sweep` (startup idempotent), `conversion_boundaries` (down≠loss) | P0 | L3 |
| `rate-freshness` | `rate_cache` (5) + `RateCacheDisabledTests`, `conversion_boundaries` (день расхода) | P1 (ядро P0) | L1–2 |
| `limits` | `limit_policies` (7), `WeightedLimitPolicyTests` (3), техника `limit_flag_scenario` | P1 | L3 |
| `arch-boundaries` | `ArchitectureTest` (10 правил, без JVM-специфики) | P1 | L1–4 |
| `logging-disclosure` | `LoggingDisclosureTests` (7), `HttpLoggingDisclosureTests` (2) | P1 | L4 |
| `openapi-contract` | `OpenApiContractTests` + урок `known-gaps` (имена провода) | P2 | L1–4 |
| `load` | `concurrency` (50), `MetricsRecorderTest` (12), техника `quota` (переопределённые числа) | P1 (chaos P0) | L3–4 |

Итого: **15 сценариев, ~115 кейсов**, из них **P0 ~70**.

## Оценка покрытия

- **Level 1**: `money-rounding`, `currency-lookup`, `quote-contract`, `error-codes`,
  `rate-freshness`, `openapi-contract` — закрывают расчёт, коды §6, свежесть, контракт.
- **Level 2**: + `provider-selection`, `idempotency`, `balances-concurrency` —
  исполнение, скоринг, резерв, инвариант `доступно + резерв + списано = старт`.
- **Level 3**: + `webhooks`, `reconciliation`, `limits`, `load/chaos` —
  дубли/беспорядок/неизвестные, сверка, суточный лимит, `chaos_run.py --transfers 300`
  без двойных выплат и зависших резервов.

## Сознательно не перенесено (и почему)

| Что в Java | Почему нет |
|---|---|
| `limit_flag_rule` truth table, `limit_flag_scenario`, `month_boundary` (месячный флаг, UTC-месяц, эпоха-фолбэк) | В нашем домене месячного флага нет; лимиты — скользящие окна (D11/A9). Портирована только техника (строгое `>`, ноль, заднее число) в `limits/` |
| `rate_cache` на Redis, `RateCacheDisabledTests` (флаг), Testcontainers Redis | Redis не является обязательной частью решения; кэш — TTL по `quote_ttl_sec` (D4). Портированы свойства (инвалидация сразу, битое выбрасывается, промах не кэшируется) в `rate-freshness/` |
| ShedLock (`@SchedulerLock`), `WeeklyCurrencyImport` вкл/выкл без рестарта | Сверка — single-runner `FOR UPDATE SKIP LOCKED` (D10); ручки вкл/выкл — конфиг, не тест поведения |
| Flyway-миграции, сид 150 кодов ISO | Alembic + сид `currencies.json` (10 кодов); утверждение «вклеен список ISO» заменено проверкой формы кода |
| `quota/rate_limit` (квота 429 + `Retry-After`) | Квоты запросов в ТЗ нет; переиспользуется только техника (переопределённые числа) в `load/` |
| Батч `POST …/batch`, `currency_pairs` трекинг-эндпоинт | В контракте §6 их нет; атомарность отказа покрыта в `error-codes/`, «один вызов на пару» — в `provider-selection/` |
| JVM-правила arch: MapStruct, CGLIB/final, Spring-аннотации | Заменены Python-эквивалентами в `arch-boundaries/` |
| `V4__amount_scale`, `@Digits(11,8)` как есть | Заменены split scale D2 (`NUMERIC(20,8)` + показ по `minor_units`) |
