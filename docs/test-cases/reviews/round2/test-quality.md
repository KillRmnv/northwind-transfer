# Качество тестов и полнота coverage — второй раунд (Transfer Core)

**Снимок:** 2026-11-19 ~12:00–12:07 MSK. Код и тесты в рамках этого ревью **не менялись**.
Дерево в момент анализа активно правилось другими агентами: во время чтения появились
`tests/integration/test_limits_definitions.py` и правки в `app/services/{transfers,reconcile,limits,providers}.py`
и `app/repositories/transfers.py`. Поэтому номера строк и число тестов ниже — снимок на
момент чтения, а не финальное состояние дерева. Считал только тесты, прочитанные мной;
`test_limits_definitions.py` упомянут на уровне факта появления, детали — в `../round2-limits.md`.

Отчёт **не повторяет** `../test-quality.md` (первый раунд): внутри только новые факты,
новый статус старых P0 и то, что осталось дырой после правок.

## Что реально прогонялось

| Проверка | Результат |
|---|---|
| `uv run pytest --collect-only -q` | **104 tests** (было 81 → +23) |
| `uv run pytest -n 2 -q` | **104 passed, 2 warnings, 91.64 s** — xdist внутри одного запуска работает |
| Два независимых `uv run pytest -q <один тест>` одновременно | **оба RED**: `DeadlockDetectedError` на `DROP TABLE payout_attempts` (10.3 / 11.2 с) |
| Два `uv run pytest -n 2 -q <один тест>` одновременно | **1 passed / 1 ERROR**: `IntegrityError` при `CREATE TABLE currencies` |
| `DB_PROFILE=sqlite uv run pytest -q tests/integration/test_quotes_api.py::test_create_and_read_quote` | 1 passed, 0.53 с — точечный дымок проходит, но профиль не параметризован |
| `grep -rn` по тестам | `testcontainers`, `SHOW shared_buffers/work_mem/max_connections`, `checkedout()`, `caplog`, `openapi` — **0 совпадений** |

Важная оговорка: последовательный прогон с `--durations`, который я пытался сделать для
замеров, был испорчен сторонней активностью (в это же время другие агенты гоняли `pytest`
и правили код) — **13 failed, 9 errors**. Эти цифры не используются как доказательство
регрессии; полезны только результаты двух изолированных экспериментов выше, где я сам
контролировал обе стороны. `pytest -n 2` зелёный прогон зафиксирован до начала чужих правок.

## Таблица проблем второго раунда

| Severity | Проблема | Файл:строка | Чем грозит | Фикс |
|---|---|---|---|---|
| **P0** | `patch_provider()` теперь патчит **и `probe_payout`**: фейк просто вызывает фейковый `find` и возвращает `("found"/"missing", state)`. Реальный `ProviderClient.probe_payout` ни в одном integration-тесте не исполняется; фейк никогда не возвращает `"error"`, поэтому ветка `resolve_unknown` «error → продолжить зондировать» недостижима. | `tests/integration/test_transfers_api.py:43-56`; `app/services/transfers.py:50-58,96-111` | Ни один тест не доказывает, что перед повторным POST реально улетает `GET ?reference=`. В `test_unknown_result_adopts_existing_payout` нет счётчиков/порядка вызовов: если код сначала сделает второй POST, а потом probe, тест всё равно увидит `COMPLETED` и останется зелёным. P0 первого раунда №4 **не закрыт**, а лишь замаскирован. | Убрать патч `probe_payout`; поднять `respx` на уровне сервиса и проверять: ровно 1 POST, `GET /…/payouts?reference=` **до** любого повторного POST, порядок, аргумент `reference`, состояние `payout_attempts`; отдельно `find=None → следующий провайдер` и `probe="error"` с проверкой, что второго POST нет. |
| **P0** | `testcontainers` по-прежнему объявлен в dev-зависимостях, но не импортируется ни одним тестом. БД — внешний Postgres `localhost:5433`, креды хардкодом в конфиге. | `pyproject.toml:28`; `tests/conftest.py:19-53`; `tests/integration/test_migrations.py:12-19` | `uv run pytest` в чистом CI без заранее поднятой БД падает. Нет проверки `SHOW shared_buffers/work_mem/max_connections`, нет MySQL/sqlite-профилей в матрице, нет гарантированного `engine.dispose()` на уровне контейнера. P0 первого раунда №1 **не закрыт**. | Сессионная фикстура `PostgresContainer` (+ параметризованный `MySqlContainer`), DSN из контейнера, `SHOW ...` как отдельный тест, `DB_PROFILE` в параметризации. |
| **P1** | Изоляция БД сделана **только по имени xdist-воркера** (`transfer_test_{PYTEST_XDIST_WORKER}`), а не по процессу. Вне `-n` все используют `transfer_test_main`; два `-n 2` — одни и те же `gw0/gw1`. Миграционный тест использует фиксированное имя `transfer_test_migrations`. | `tests/conftest.py:25,47-51`; `tests/integration/test_migrations.py:9` | Два параллельных CI-job / два локальных прогона ломают друг друга (показано экспериментами выше: deadlock на `DROP TABLE`, `IntegrityError` на `CREATE TABLE`). P1 первого раунда №2 закрыт **наполовину**: внутри одного `-n 2` работает, между процессами — нет. | Суффикс БД/схемы из `os.getpid()` + uuid или отдельный контейнер на прогон; для миграций — уникальное имя на процесс; проверить два одновременно запущенных `-n 2` в CI. |
| **P1** | Сверка представлена ровно одним счастливым путём: `get_payout → COMPLETED → completed`. | `tests/integration/test_webhooks_and_limits.py:293-323`; `app/services/reconcile.py:55-92` | Не покрыты `PENDING → run_attempts`, `state is None → find_payout`, `still_open`, короткое замыкание на терминальном `fresh`, `FAILED → run_attempts`, лимит `list_open_transfers`, стартап-сверка, single-runner/`FOR UPDATE SKIP LOCKED`. P0 первого раунда №5 закрыт **на ~20 %**. | Таблица кейсов на каждую ветвь `_reconcile_one` + тест на startup и на двух concurrent runner-ов; отдельно lost-webhook как интеграционный тест. |
| **P1** | Нагрузочные тесты есть, но не проверяют ни пул, ни двойные выплаты: 100 котировок — только статус и `elapsed < 60`; 50 переводов — **50 разных ключей и 50 разных `account`**, проверяется только баланс. | `tests/load/test_load.py:7-85` | `engine.pool.checkedout()`, `pool_timeout`, `TimeoutError`, число `payout_attempts`/уникальных `payout_id` не проверяются. Баг с созданием двух выплат на один перевод при корректном списании пройдёт. `chaos_run.py --transfers 300` не запускается. P1 №15 и P0 №5 остаются открытыми. | Замерить `checkedout() <= pool_size + max_overflow`, проверить отсутствие `TimeoutError`; добавить счётчик POST/attempts и уникальность `payout_id`; отдельный chaos-прогон с инвариантом «ноль двойных выплат, ноль зависших резервов». |
| **P1** | Вебхук-дубль и late-processing остаются ложно-зелёными: `test_duplicate_event_is_idempotent` проверяет только баланс; `test_late_processing...` шлёт не-терминальный `PROCESSING`, который код игнорирует независимо от `occurred_at`. | `tests/integration/test_webhooks_and_limits.py:54-83`; `app/services/webhooks.py:84-100` | Если убрать дедупликацию/`UNIQUE(event_id)`, второй event увидит терминальный перевод и вернёт 200 без списания — тест зелёный. Монотонность `occurred_at` не проверена вообще: нужна пара `COMPLETED` vs `FAILED` с разными метками. P1 первого раунда №13 **не закрыт**. | Assert числа строк `webhook_events`, `event_id`, истории; пара терминальных событий с `occurred_at` в обе стороны + проверка финального статуса. |
| **P1** | Поддельная подпись по-прежнему принимает `401` **или** `422`. Новый `test_missing_signature_returns_401` закрыл только absent-header. | `tests/integration/test_transfers_api.py:223-237` (vs `tests/integration/test_webhooks_and_limits.py:264-270`) | Регрессия на `422` для forged-подписи останется незамеченной; `application/problem+json` на вебхук-ошибках не проверяется. | Убрать `in (401, 422)`, зафиксировать 401 + content-type + `error.code`. |
| **P1** | Arch-тест теперь проверяет `Contracts: 2 kept` (это хорошо), но правило `app.api → app.repositories/sqlalchemy/asyncpg` так и не добавлено; нет positive control и имён контрактов. AST-проверки — наивный поиск подстроки. | `tests/architecture/test_boundaries.py:6-44`; `pyproject.toml:57-71` | Удаление/переименование контракта ловится, но само нарушение слоёв в `app/api/v1/*.py` по-прежнему не ловится. P1 №11 первого раунда не закрыт, №12 закрыт частично. | Добавить третий `forbidden`-контракт, проверять имена, negative fixture (сломанная заготовка должна падать). |
| **P2** | Миграционный тест: фиксированное имя БД, проверяется только множество таблиц + 9 имён индексов + 2 имени constraint. Нет `downgrade`/повторного `upgrade`, типов колонок/numeric scale, поведения FK, `Path("data")` относительный. | `tests/integration/test_migrations.py:9,58-142` | Расхождение миграции с моделью по колонкам/типам не ловится; повторный прогон миграции может столкнуться с параллельным (см. P1 выше). Идемпотентность сида проверена хорошо (`first == second == 10`, providers == 7). | `downgrade base && upgrade head`, инспекция `information_schema.columns`, вставка валюты вне справочника → FK error, `REPO_ROOT` в путях. |
| **P2** | `respx`-тесты клиента закрыли статусы, но не тело: нет 201 с битым JSON, `get_payout` при 500/transport error, `probe_payout` при transport error. Тест 409 шлёт пустое тело и попадает в общий `ValueError`-fallback, а не в специальную ветку 409. | `tests/unit/test_provider_client.py:88-93,108-165`; `app/services/providers.py:184-190` | Специфичная причина `idempotency conflict at provider` не проверяется; невалидный ответ 201 упадёт `KeyError`/`JSONDecodeError` в проде без теста. | Параметризовать невалидные тела, добавить 409 с валидным JSON, transport error для `probe_payout`, 200 на create. |
| **P2** | Soft assertions по-прежнему не покрывают `breakdown`-шаги и `expires_at` (`check_result` — 9 полей; в API-тесте `isinstance(breakdown, list) and breakdown`). | `tests/unit/test_quote_calculator.py:67-96`; `tests/integration/test_quotes_api.py:43` | Ошибка в шагах расчёта или в TTL останется зелёной. P2 первого раунда №23 не закрыт. | Пошаговые soft-assert'ы breakdown с эталоном из `examples/`, явная проверка `expires_at`. |
| **P2** | Мёртвый код и дубли: `test_double_submit_returns_one_transfer` остался последовательным клоном `test_idempotency_same_key_returns_same_transfer` (при этом настоящая гонка теперь есть в другом файле); `_ = providers_module` в load-тесте; кросс-импорт хелперов из другого тест-модуля. | `tests/integration/test_transfers_api.py:174-220`; `tests/load/test_load.py:35-41,85` | Ложное ощущение покрытия, лишнее время, хрупкая связь модулей. | Удалить дубль и no-op, вынести хелперов в `tests/helpers.py`. |
| **P2** | По-прежнему нет герметичной конфигурации, маркеров `slow`/`chaos`, coverage-порога, относительные пути `Path("data")`/`Path("examples")`, `caplog`/OpenAPI-тестов. | `tests/conftest.py:75,128`; `pyproject.toml:36-39`; grep по тестам — 0 совпадений | Таймауты из `.env` (12 с) задают поведение тестов; прогон из другого каталога ломается; observable- и контрактные инварианты не защищены. | Фикстура `Settings` с короткими таймаутами, `REPO_ROOT`, `pytest-cov` + порог, маркеры, `caplog` и `GET /openapi.json`. |

## Что реально закрыто с первого раунда

| Пункт первого раунда | Статус | Обоснование |
|---|---|---|
| №3 — HTTP-клиент провайдера, `respx` | **ЗАКРЫТ на уровне клиента** | 14 тестов: 201/422/500/504/409/transport/timeout, `probe` 404/503/200, `get` 404/parse, заголовок `Idempotency-Key` и формат currency. Но не закрыт на уровне сервиса — см. P0 выше. |
| №12 — arch проверяет `2 kept` | **ЗАКРЫТ** | `test_boundaries.py:17-18`. |
| №10 — конкурентная идемпотентность | **ЗАКРЫТ в базовом виде** | `test_concurrent_same_key_one_transfer` (`test_webhooks_and_limits.py:273-290`): `asyncio.gather`×5 с одним ключом, один `transfer_id`, баланс 490000. Нет проверки числа строк в БД/резервов. |
| №6 — отсутствующая подпись → 401 | **ЗАКРЫТ для absent-header** | `test_missing_signature_returns_401` (`test_webhooks_and_limits.py:264-270`), строго 401 + `UNAUTHORIZED`. Для forged подписи — нет. |
| №14 — Alembic/сид | **ЗАКРЫТ частично** | 4 теста: upgrade head, множество таблиц == `Base.metadata`, 9 индексов, UNIQUE-имена, идемпотентный сид (10 валют, 7 провайдеров). |
| №5 — reconcile/lost webhook | **ЧАСТИЧНО (~1 путь из ~7)** | Один `reconcile_once → COMPLETED`. |
| №2 — xdist-изоляция | **ЧАСТИЧНО** | Внутри одного `-n 2` работает (104 passed); между процессами — нет. |
| №1 — testcontainers | **НЕ ЗАКРЫТ** | Импортов нет. |
| №4 — неизвестный итог | **НЕ ЗАКРЫТ** | См. P0: `probe_payout` замокан, нет счётчиков и порядка. |
| №11 — правило `api → repositories` | **НЕ ЗАКРЫТ** | В `pyproject.toml` по-прежнему 2 контракта. |
| №15/#17/#18/#19/#20/#21/#23/#26 | **НЕ ЗАКРЫТЫ** | Пул, тайминги, hermetic settings, OpenAPI, caplog, пути, breakdown, coverage — без изменений. |

Отдельно: в ходе ревью появился `tests/integration/test_limits_definitions.py` (HOURLY,
WINDOW_N, вес, отрицательный вес, null-window, category-scope) — это реальный сдвиг по
лимитам, но он не отменяет отсутствие границы «ровно лимит / +1 minor unit». Детали не
здесь, а в `../round2-limits.md`.

## Новые ложно-зелёные тесты

1. **`test_unknown_result_adopts_existing_payout`** (`test_transfers_api.py:152-172`) — главный.
   Фейк `create` всегда возвращает `UnknownResult`, фейк `find` сразу отдаёт `PROCESSING`,
   а подменённый `probe_payout` превращает это в `("found", state)` с первого вызова.
   Нет ни счётчика `create_payout`, ни проверки, что `GET ?reference=` был **первым**, ни
   проверки `payout_attempts`. Тест останется зелёным при двойном POST до зондирования —
   то есть при именно том баге, от которого защищает правило неизвестного итога.

2. **Все тесты, использующие `patch_provider(..., find=...)`** (`test_transfers_api.py:43-56`,
   `test_webhooks_and_limits.py`, `tests/load/test_load.py:58-63`) — `probe_payout` подменён
   глобально. Фейк не умеет возвращать `"error"`, поэтому вся отказоустойчивость
   `resolve_unknown` (повторные пробы при 5xx/transport) в интеграциях не существует.
   Единичные respx-тесты `probe_payout` это не компенсируют: они проверяют метод, а не
   то, что сервис его вызывает с нужным `reference`.

3. **`test_duplicate_event_is_idempotent`** (`test_webhooks_and_limits.py:54-67`) — см. P1 выше:
   проверка баланса не отличает дедупликацию от «второй event попал в терминальный перевод».

4. **`test_late_processing_does_not_override_terminal`** (`test_webhooks_and_limits.py:70-83`) —
   шлёт `PROCESSING` после `COMPLETED`; код игнорирует любой не-терминальный статус ещё до
   сравнения `occurred_at` (`webhooks.py:99` работает только во внутренней ветке не-терминального
   события). Тест зелёный и без какой-либо монотонности.

5. **`test_hundred_concurrent_quotes` / `test_fifty_concurrent_transfers_keep_invariant`**
   (`tests/load/test_load.py:7-85`) — «нагрузка» без нагрузки на пул и без проверки
   количества выплат: 100 котировок и 50 переводов с уникальными ключами, только статусы
   и один баланс. Имя `keep_invariant` обещает инвариант `available + reserved + spent`,
   а проверяются два поля из трёх.

6. **`test_webhook_bad_signature_rejected`** (`test_transfers_api.py:223-237`) — `in (401, 422)`
   по-прежнему допускает регрессию на 422.

7. **`test_migration_creates_every_model_table`** (`test_migrations.py:58-75`) — сильный тест,
   но с оговоркой: он сравнивает множества имён, а не структуру; если в миграции и модели
   одновременно поменять тип колонки, тест останется зелёным.

## Приоритет закрытия

1. **P0 — снять маску с `probe_payout`**: убрать подмену в `patch_provider`, перевести
   сценарий неизвестного итога на `respx` на уровне сервиса, добавить счётчики POST/GET и
   проверку порядка. Без этого весь Level 2/3 по двойной выплате — голословный.
2. **P0 — testcontainers** + `SHOW shared_buffers/work_mem/max_connections`; заодно решит
   проблему из п.3.
3. **P1 — изоляция между процессами** (pid/uuid в имени БД, уникальное имя для миграций),
   проверка двух одновременно запущенных `pytest -n 2` в CI.
4. **P1 — reconcile-матрица** (все ветви `_reconcile_one`, startup, single-runner) и
   исправление webhook-дубля/монотонности с проверкой строк `webhook_events`.
5. **P1 — load**: `checkedout()`, отсутствие `TimeoutError`, счётчики выплат, chaos-run.
6. **P2 — остальное**: strict 401 для forged, третье arch-правило + positive control,
   `breakdown`/`expires_at`, 409-with-body, hermetic settings, caplog/OpenAPI, coverage-порог.

## Обновлённая оценка покрытия Level 1/2/3

Экспертная оценка относительно сценариев `docs/test-cases/*/SPEC.md`, не по числу строк.

| Уровень | Было (1-й раунд) | Стало | Что изменилось |
|---|---|---|---|
| **Level 1** — расчёт, курсы, контракт котировки | ~50–60 % | **~55–60 %** | Почти без изменений: прибавились точечные invalid/zero/NaN-rate и stale-direct (уже учитывались), respx-тесты курса на уровне клиента отсутствуют. По-прежнему нет BVA min/max, точных TARGET-эталонов, `expires_at`, шагов `breakdown`, TTL-границ, кэша, OpenAPI. |
| **Level 2** — провайдеры, unknown result, идемпотентность, резерв | ~35–40 % | **~50–55 %** | Существенный сдвиг: 14 respx-тестов `ProviderClient`, настоящая гонка с одним ключом, 50 параллельных переводов с проверкой баланса, Alembic/сид. Но ключевой инвариант «GET до повторного POST / не более одного payout» **не подтверждён** из-за подмены `probe_payout`; пул не проверяется. |
| **Level 3** — надёжность, вебхуки, сверка, лимиты, chaos | ~15–20 % | **~25–30 %** | Появились один путь `reconcile_once`, строгий 401 для отсутствующей подписи, HOURLY/WINDOW_N/веса лимитов. По-прежнему нет: полной матрицы сверки, contradiction/`occurred_at` для терминальных событий, логов и метрик unknown webhook, chaos-run, 300 переводов, single-runner, двойной выплаты как проверяемого инварианта. |
| **Level 4** — бонус | ~5–10 % | **~5–10 %** | Без изменений: `caplog`, OpenAPI, реальные HTTP-логи, метрики, конфигурация без редеплоя — 0 тестов. |

### Матрица по сценариям (только изменения)

| Сценарий | Статус в 1-м раунде | Сейчас |
|---|---|---|
| `provider-selection` | ⚠️ только чистые функции, respx мёртв | **✅ на уровне клиента** (14 respx-тестов) / **⚠️ на уровне сервиса** (probe замокан) |
| `idempotency` | ⚠️ последовательно | **⚠️/✅** реальная гонка 5×один ключ есть; нет проверки числа строк/резервов в БД |
| `balances-concurrency` | ⚠️ 5 запросов | **⚠️** 50 переводов, но только баланс; нет `checkedout()` и счётчика выплат |
| `reconciliation` | ❌ | **⚠️** один путь `COMPLETED` |
| `limits` | ❌/⚠️ | **⚠️** + HOURLY/WINDOW_N/вес (новый файл); нет ровно-лимита и +1 minor unit |
| `arch-boundaries` | ⚠️ | **⚠️** `2 kept` проверяется, правило `api → repositories` отсутствует |
| `load` / chaos | ❌ пусто | **⚠️** тесты есть, но не проверяют пул и не запускают chaos |
| `testcontainers` | ❌ | **❌ без изменений** |
| `webhooks` / `logging-disclosure` / `openapi-contract` | ⚠️/❌ | **без изменений** (дубль и late по-прежнему ложно-зелёные) |

**Итог:** число тестов выросло с 81 до 104, `pytest -n 2` внутри одного запуска действительно
работает. Но два новых P0-дырных места — подмена `probe_payout` (маскирует случай двойной
выплаты) и отсутствие testcontainers — означают, что зелёный прогон по-прежнему **не является
доказательством** Level 2/3 в заявленном объёме.
