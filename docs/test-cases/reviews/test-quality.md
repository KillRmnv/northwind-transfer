# Ревью качества тестов Transfer Core

Снимок рабочего дерева: **2026-11-19 ~02:23 MSK** (в дереве есть незакоммиченные
изменения, тесты и код активно правятся, поэтому номера строк и число тестов могут
сдвинуться после следующих правок).

Проверялись: `tests/**/*.py`, `tests/conftest.py`, `pyproject.toml`,
`docs/SPEC.md` (раздел «Автотесты»), `docs/Backend.md` (разделы 6 и 8) и
`docs/test-cases/**/SPEC.md`.

## Короткий вывод

Тесты **не пустые**: калькулятор проверяется на примерах, есть реальные проверки
резерва, fallback и часть вебхуков. Но зелёный прогон **не подтверждает Level 2/3**
в объёме заявленного:

- текущий сбор: **81 test** (`uv run pytest --collect-only -q`);
- изолированный прогон на отдельной БД `transfer_test_review`:
  **80 passed, 1 failed, 73.54 s**; красный — новый
  `test_foreign_payout_id_does_not_complete` (`assert 201 == 202`). Это либо
  дефект теста, либо реальный баг, но набор на снимке не зелёный;
- при совмещении с другим `pytest` на общей БД — массовые
  `relation ... does not exist`, `duplicate key ... pg_type_typname_nsp_index`
  и `assert fresh is not None`; `-n 2` до worker-БД давал 13 failed/10 errors;
- `DB_PROFILE=sqlite` для конкурентного теста: **1 failed** (async-generator
  `RuntimeError`), то есть заявленный «быстрый» профиль не покрыт;
- `uv run lint-imports`: **2 kept, 0 broken** (сам arch-тест реально запускается);
- `uv run ruff check tests` и `ruff format --check tests`: чисто.

Главные риски — не в количестве тестов, а в том, что **самые опасные места
(HTTP-клиент провайдера, неизвестный итог, сверка, лимиты, конкурентная
идемпотентность, chaos) либо не тестируются, либо проверяются так, что тест
останется зелёным при сломанном поведении**.

## Проблемы

| # | Severity | Проблема | Файл:строка | Чем грозит | Как исправить |
|---|----------|----------|-------------|------------|---------------|
| 1 | **P0** | Нет `testcontainers`: тесты ходят во внешний Postgres (`localhost:5433`/env, БД `transfer_test`), а не поднимают контейнер. `testcontainers` объявлен в dev-зависимостях, но нигде не импортируется. | `tests/conftest.py:19-31`, `tests/conftest.py:33-52`, `pyproject.toml:28` | `uv run pytest` в чистом окружении/CI без заранее поднятой БД падает; нет проверки `SHOW shared_buffers/work_mem/max_connections`; MySQL/sqlite-профили и реальные диалекты не проверяются; тесты не воспроизводимы. | Поднять `PostgresContainer`/`MySqlContainer` на `scope="session|module"`, получать DSN из контейнера, гарантировать `engine.dispose()`; параметризовать профиль БД; отдельный тест на `SHOW ...`. |
| 2 | **P1** | Изоляция неполная: worker-БД по `PYTEST_XDIST_WORKER` добавлена, но вне xdist все процессы используют `transfer_test_main`; два обычных `pytest` одновременно ломают друг друга, а `drop_all/create_all` выполняется в каждом тесте. | `tests/conftest.py:19-31`, `tests/conftest.py:61-73` | Параллельные CI-job или два локальных прогона дают `duplicate key ... pg_type...` и `assert fresh is not None`; данные перетираются; нет контейнерной изоляции. | Один контейнер/уникальная БД на процесс (`testcontainers` или `uuid` в имени), schema-per-test или транзакционный rollback; проверить `pytest -n auto` в CI. |
| 3 | **P0** | `ProviderClient` не тестируется по HTTP: все тесты подменяют `create_payout/get_payout/find_payout` monkeypatch-ем. Нет ни одного `respx`-теста на 201/422/503/5xx/409/transport error/404. | `tests/integration/test_transfers_api.py:43-69`, `app/services/providers.py:144-213`, `pyproject.toml:27` | Ошибки маппинга кодов провайдера (особенно 5xx/таймаут/409) не видны; в прод может уйти двойная выплата или неверный статус попытки. `respx` — мёртвая зависимость. | Добавить `respx`-тесты на каждый HTTP-код и на `httpx.TimeoutException`/`TransportError`; проверять `Accepted/Declined/UnknownResult`, `get_payout`/`find_payout` при 200/404/500. |
| 4 | **P0** | Неизвестный итог проверен только в одном сценарии, где `find` сразу находит запись. Нет теста `find=None → следующий провайдер`, нет проверки, что перед повторной отправкой сначала был `GET ?reference=`, нет проверки числа POST-ов. В `create_with_recovery` при `UnknownResult` код делает повторный `create_payout` до 4 раз. | `app/services/transfers.py:65-107`, `tests/integration/test_transfers_api.py:146-165` | Именно этот путь защищает от двойной выплаты; текущий тест останется зелёным, даже если `find` вызывается не первым или если создаются две выплаты. Chaos-инвариант не подтверждён. | Тесты: `find` возвращает `None` → следующий провайдер; `find` возвращает `PROCESSING/COMPLETED` → привязка без второго POST; счётчик `create_payout` и `find_payout`; проверка `payout_attempts` и ledger мока. |
| 5 | **P0** | `reconcile_once`, фоновая сверка, потерянный вебхук, рестарт между «отправили» и «записали», `chaos_run.py --transfers 300` не тестируются. `tests/load/` содержит только пустой `__init__.py`. | `app/services/reconcile.py:13-56`, `tests/load/__init__.py`, `scripts/chaos_run.py` | Level 3/chaos заявлен, но не проверен; зависшие резервы и двойные выплаты могут пройти в прод. | Unit-тесты `reconcile_once` с фейковым клиентом и состояниями PENDING/PROCESSING/COMPLETED/FAILED; интеграционный chaos-run в изолированном compose; проверка нуля двойных выплат и нуля зависших резервов. |
| 6 | **P1** | Подпись вебхука: тест принимает `401` **или** `422`, поэтому регрессия на `422` останется зелёной; отсутствие `X-Signature` не тестируется. Пустой секрет теперь проверяется (`test_empty_webhook_secret_fails_closed`, 503), но контракт `401` не зафиксирован строго. | `tests/integration/test_transfers_api.py:217-231`, `tests/integration/test_webhooks_and_limits.py:224-229`, `app/services/webhooks.py:19-31,45-50`, `app/core/exceptions.py:59-61` | Нарушение обязательного контракта `401` может вернуться незамеченным; подделанный/пропущенный вебхук не проверяется на отсутствие эффекта. | Убрать `in (401, 422)`: строго `401` + `application/problem+json`; добавить кейс «нет заголовка» и «валидная подпись, но ложный статус»; проверить, что перевод не изменился. |
| 7 | **P1** | Свежесть курсов и кэш почти не проверены: `live_rates` перезаписывает `updated_at` текущим временем, а `freeze_rates` полностью обходит `RatesClient`; нет проверки `min(quote_ttl_sec, 6ч)`, границы TTL, кэша, инвалидации, битого значения. | `tests/conftest.py:102-127`, `tests/integration/test_quotes_api.py:111-123` | Просроченный курс или отсутствие инвалидации кэша не будут замечены; тесты «свежести» на деле всегда свежие. | `respx` с явными `updated_at`; тесты на `age == limit`, `age > limit`, `ttl < 6h`, отсутствие пары, `rate=0`, повторный запрос и обновление. |
| 8 | **P1** | Конверт ошибок проверен только для части ошибок котировки. Нет проверок `error.message`, `details`, полей RFC 9457 (`type/title/detail/instance`), нет тестов на 404 перевода, 401 вебхука, 500/502, собственные коды `PROVIDER_UNAVAILABLE`/`TRANSFER_NOT_FOUND`, `VALIDATION_ERROR` для `POST /transfers`. | `tests/integration/test_quotes_api.py:78-91`, `tests/integration/test_transfers_api.py:303-323`, `app/api/exception_handlers.py:8-46` | Клиент может получить не-RFC-ответ или пустой `details`; автопроверка контракта §6 пройдёт не полностью. | Параметризовать все `DomainError` через API; проверять `Content-Type`, `status`, `error.code/message/details`, а также `type/title/detail/instance`; отдельные тесты на 500 и отсутствующий заголовок. |
| 9 | **P1** | Лимиты: проверены over-limit и лимит в третьей валюте (`test_daily_limit_in_third_currency`), но нет ровно-лимита, +1 minor unit, окна `[now−window, now)`, часового/7-дневного лимита, веса, `UNIQUE` `limit_definitions`. Ветка `convert_with_hints` в `limits.py` вызывается с 4 аргументами при сигнатуре из 6 и не тестируется. | `tests/integration/test_transfers_api.py:312-323`, `tests/integration/test_webhooks_and_limits.py:140-153`, `app/services/limits.py:129-132`, `app/services/limits.py:142-149` | Граница суточного лимита и взвешенные лимиты не проверены; ошибка в неиспользуемой ветке (`TypeError`) скрыта, пока нет `LimitDefinition`. | Добавить таблицу кейсов из `docs/test-cases/limits/SPEC.md`; unit-тесты `check_daily_limit`/`check_limit_definitions`; заполнять `limit_definitions` в фикстуре; проверить строгое `>` и валютную конвертацию. |
| 10 | **P1** | Идемпотентность проверяется только последовательными запросами. `test_double_submit_returns_one_transfer` фактически дублирует `test_idempotency_same_key_returns_same_transfer`; настоящей гонки с одинаковым ключом нет. | `tests/integration/test_transfers_api.py:168-214` | Два одновременных одинаковых запроса могут создать два перевода; текущий набор этого не увидит. | `asyncio.gather` с одинаковым ключом; проверка одного `transfer_id`, количества строк в БД, отсутствия второго резерва; отдельно — тест `UNIQUE(idempotency_key)` на уровне БД. |
| 11 | **P1** | Arch-правило «`api` не импортирует `repositories`/`sqlalchemy`/`asyncpg`» не проверяется. Реальный код его нарушает (`app/api/v1/quotes.py`, `app/api/v1/transfers.py`). | `tests/architecture/test_boundaries.py:15-38`, `app/api/v1/quotes.py:7,15-17`, `app/api/v1/transfers.py:4,11-14` | Слой `api` уже содержит запросы в репозитории/SQLAlchemy, но suite зелёный; границы слоёв декларативны. | Добавить import-linter `forbidden`-контракт `app.api -> app.repositories/sqlalchemy/asyncpg` или AST-тест; сделать положительный контроль (сломанная заготовка должна падать). |
| 12 | **P1** | Arch-тест проверяет только `"0 broken"` и не проверяет число/имена контрактов. Если удалить один контракт, тест останется зелёным. | `tests/architecture/test_boundaries.py:4-12` | Import-linter может перестать проверять правило, а тест этого не заметит. | Проверять `2 kept` и имена контрактов; запускать `lint-imports --contract ...`; добавить negative fixture/positive control. |
| 13 | **P1** | Вебхуки: тест дубля проверяет только баланс; даже при отключении дедупликации/`UNIQUE(event_id)` второй event увидит терминальный перевод и не спишет деньги — тест это не заметит. `test_late_processing...` проверяет только, что не-терминальный `PROCESSING` не меняет `COMPLETED`; `occurred_at` и `contradiction` не проверяются. `test_unknown_reference_logged_but_accepted` не проверяет ни лог, ни метрику. | `tests/integration/test_webhooks_and_limits.py:54-92`, `app/services/webhooks.py:57-71` | Дубли/порядок/неизвестные события могут быть сломаны незаметно; нет warn-лога и `unknown_webhooks_total`. | Проверять число строк `webhook_events`, `event_id`, историю; добавить `COMPLETED` vs `FAILED` с разными `occurred_at`, `caplog` и метрику для unknown; пачку событий на один перевод. |
| 14 | **P1** | Схема для тестов создаётся через `Base.metadata.create_all`, а не через Alembic; идемпотентность сидера, FK/UNIQUE, миграции не проверяются. | `tests/conftest.py:63-73`, `app/db/seed.py` | Миграция может разойтись с моделями, а тесты останутся зелёными; повторный сид может дублировать данные. | Fixture с `alembic upgrade head`; `downgrade`/повторный `upgrade`; сид дважды и проверка counts/constraints; тест FK (валюта вне lookup) и `UNIQUE` (idempotency, limit_definitions). |
| 15 | **P1** | Тест «пула» не проверяет пул: `engine` создаётся с `NullPool`, `api` подменяет `get_session` тестовым engine, а `test_parallel_requests_share_pool` делает 50 котировок и проверяет только статусы. `engine.pool.checkedout()` нигде не вызывается. | `tests/conftest.py:58`, `tests/conftest.py:76-94`, `tests/integration/test_webhooks_and_limits.py:95-113` | Настройки `pool_size`/`overflow`/`timeout` и отсутствие `TimeoutError` не проверены; имя теста вводит в заблуждение. | Использовать engine приложения с настроенным пулом; под нагрузкой проверять `checkedout() <= pool_size + max_overflow`, `pool_timeout`, отсутствие `TimeoutError`; 50–100 переводов, а не только котировок. |
| 16 | **P1** | Нет негативных тестов `POST /transfers`: без `Idempotency-Key`, невалидный UUID `quote_id`, неизвестный `client_id`, отсутствующий `recipient`, `GET /transfers/{id}` 404, `GET /balance` неизвестного клиента. | `tests/integration/test_transfers_api.py` (только happy/бизнес-ошибки), `app/api/v1/transfers.py:56-70` | Ручки могут отдавать 422/500 без Problem Details или принимать мусор; раздел 6 покрыт не полностью. | Параметризованные негативные тесты на все ветки; проверять код, `details` и отсутствие записей/резервов после отказа. |
| 17 | **P1** | Тесты медленные и зависят от реального времени: три теста тратят по ~12.4 с на `wait_for_settlement`, `resolve_unknown` спит 1.5 с; таймаут берётся из `.env` (`api_sync_timeout_sec=12`). | `tests/integration/test_webhooks_and_limits.py:40-83`, `tests/integration/test_transfers_api.py:146-165,253-289`, `app/services/transfers.py:92-107,421-424` | Прогон ~81 теста ~74 с, в CI будет минуты; изменение `.env` меняет поведение тестов; возможны флаки на границе таймаута. | Зафиксировать timeout в тестовой settings (0.05–0.2 с), подменить `asyncio.sleep`/использовать fake clock; `resolve_unknown` вынести в параметр; пометить `slow` и запускать отдельно. |
| 18 | **P1** | Нет герметичной тестовой конфигурации: `app_settings.provider_webhook_secret` читается из `.env`; тесты подписывают им же. `api_sync_timeout_sec`, `mock_provider_url` тоже из `.env`. | `tests/integration/test_transfers_api.py:247,283`, `tests/conftest.py:1-127` (нет fixture с тестовыми `Settings`), `app/core/settings.py:11-31` | В CI без `.env` секрет пуст → подпись не проверяется, тест «плохой подписи» падает или меняет смысл; таймауты/URL зависят от машины. | Fixture с `monkeypatch.setenv`/`Settings(...)` и override `app_settings`; всегда задавать секрет, URL и таймауты в тестах; добавить тест на пустой секрет. |
| 19 | **P1** | Нет тестов OpenAPI-контракта: `GET /openapi.json`, наличие путей, snake_case-полей, схем ошибок, отключения docs в prod. | `docs/test-cases/openapi-contract/SPEC.md`, `app/main.py:54-57`, `app/schemas/*.py` | Пустой/устаревший OpenAPI и отсутствие `Field(description/examples)` не ловятся; контракт как артефакт не защищён. | Тест `openapi.json`: все пути/дубли, свойства, `ErrorResponse`, `200/201/400/404/409/422`, `prod` без `/docs` и `/openapi.json`; добавить `Field(description=...)`. |
| 20 | **P1** | Логи проверяются только на уровне функций `redact_*`, а не на реальном кодовом пути. Нет маскирования счёта/имени/IP, нет проверки, что тело вебхука не логируется, нет `caplog` для unknown webhook. | `tests/unit/test_logging_redaction.py:23-90`, `tests/integration/test_webhooks_and_limits.py:86-92` | PII/секреты могут утечь в HTTP-логи; `unknown_webhooks_total` и warn-лог не проверены. | Перехватывать реальный handler/`caplog` на `POST /transfers`, `/webhooks`, ошибке провайдера; проверять маску последних 4 цифр, IP, timestamp/UUID; assert `provider_webhook_secret` отсутствует. |
| 21 | **P2** | Пути `data/` и `examples/` относительные (`Path("data")`), тесты запускаются только из корня репозитория. | `tests/conftest.py:70,123`, `tests/unit/test_quote_calculator.py:14,112` | Запуск `pytest` из другого каталога/IDE ломается; CI с нестандартным `rootdir` тоже. | Вычислять `REPO_ROOT = Path(__file__).parents[2]`; фикстура `data_dir`/`examples_dir`. |
| 22 | **P2** | Дубли и мёртвые проверки: `test_double_submit` дублирует `test_idempotency_same_key`; `test_unknown_reference_accepted` дублирует `test_unknown_reference_logged_but_accepted`; форматирование денег тестируется трижды (`test_minor_units_formatting`, `test_target_currency_zero_decimals_format`, `test_money_formats_three_decimals`). В `test_transfer_fallback` список `calls` заполняется, но не проверяется. | `tests/integration/test_transfers_api.py:98-121,168-214,234-250`, `tests/integration/test_webhooks_and_limits.py:86-92`, `tests/unit/test_quote_calculator.py:218-224`, `tests/unit/test_provider_selection.py:126-130`, `tests/unit/test_mappers.py:51-52` | Дубли увеличивают время и создают ложное ощущение покрытия; неиспользуемые данные скрывают отсутствие проверки. | Оставить по одному тесту на инвариант; `calls` заменить на assert порядка провайдеров; объединить проверки money в один параметризованный тест. |
| 23 | **P2** | Soft assertions не покрывают важные поля: `check_result` не проверяет `expires_at`, `breakdown`-шаги, полный набор полей; `test_create_and_read_quote` проверяет только `isinstance(breakdown, list) and breakdown`. | `tests/unit/test_quote_calculator.py:67-96`, `tests/integration/test_quotes_api.py:43,24-51` | Ошибка в `breakdown`/`expires_at` не видна, хотя SPEC требует проверки шагов расчёта через `pytest-check`. | Проверять каждый шаг breakdown и `expires_at` как soft assertions; эталонные значения брать из examples/ожидаемого контракта. |
| 24 | **P2** | Unit-тесты границ/скоринга слабые: `test_amount_limits` использует 500/500000 вместо min−ε/min/max/max+ε; `test_filter_limits`/`filter_availability` без равенства границам; `test_scoring_prefers_margin_then_success` проверяет только порядок, а не вклад margin/latency/success; `test_margin_floor` только 50% против 0.5%. | `tests/unit/test_quote_calculator.py:152-176`, `tests/unit/test_provider_selection.py:48-123` | Граничные ошибки (`>` vs `>=`, конвертация min/max, веса скоринга) не ловятся. | Таблицы BVA; отдельные тесты на `min`, `max`, `min−1`, `max+1`; assert score/веса и tie-break; проверить `limit_min == converted`. |
| 25 | **P2** | `test_health` использует sync `TestClient` без `with` (lifespan не запускается), не проверяет content-type и startup/reconcile; `test_configure_logging` меняет root logger и не восстанавливает его. | `tests/unit/test_health.py:1-10`, `tests/unit/test_logging_redaction.py:86-90` | Startup-ошибки и порядок тестов (глобальный logging) не проверяются; deprecation warning не превращается в ошибку. | `with TestClient(app) as client`, проверить startup/health; сохранять/восстанавливать handlers в fixture; `filterwarnings = ["error"]` для своих предупреждений. |
| 26 | **P2** | Нет трассируемости «кейс SPEC → тест» и нет coverage-конфига/порога. В `docs/test-cases/README.md` заявлено ~115 кейсов, в pytest — десятки тестов; соответствие нигде не зафиксировано. | `docs/test-cases/README.md`, `pyproject.toml:36-39` | Нельзя доказать полноту; легко потерять кейс при рефакторинге и не заметить. | Добавить `pytest-cov`, порог по `app/services`/`core`; в docstring теста указывать ID кейса (`limits-3`, `webhooks-4`) или вести mapping-файл. |
| 27 | **P2** | Новый `test_quote_edge_cases.py` проверяет только `result.target_amount >= requested` для TARGET; тест не поймает завышенную комиссию/неверный `source_amount`/`breakdown`. `test_target_roundtrip_for_all_directions_matches_examples` на деле проверяет только `RU-TH-BANK`, то есть дублирует старый roundtrip; имя обещает все направления. | `tests/unit/test_quote_edge_cases.py:45-84` | Свойство «не меньше» слишком слабое; регрессия в расчёте комиссии или в 0/3-decimal валютах пройдёт. | Для каждого направления проверять точные `source_amount`, `fee`, `amount_to_convert`, `target_amount` и шаги `breakdown`; параметризовать все `direction_id` из `tariffs.json`. |
| 28 | **P1** | На снимке набор красный: `test_foreign_payout_id_does_not_complete` ожидает `202`, получает `201` (и далее `PROCESSING` вместо `COMPLETED`). Это либо ошибка ожиданий теста, либо реальный дефект обработки чужого `payout_id`. | `tests/integration/test_webhooks_and_limits.py:178-225` | CI не зелёный; причина не отделена от тестовой/продуктовой, значит нельзя доверять остальным выводам этого сценария. | Разделить тест: сначала детерминированно получить `202` (например, `get=PROCESSING` и явно оборвать ожидание), затем отдельно проверить, что чужой `payout_id` не завершает перевод; зафиксировать ожидаемый статус и историю. |

### Отдельно про assert и monkeypatch

- Явных `assert True`, `assert response` или пустых проверок нет. Но есть
  **слабые assert-ы**, которые сохраняют зелёный цвет при сломанном смысле:
  `assert response.status_code in (201, 202)`, `assert isinstance(breakdown, list)`,
  `assert len(history) >= 2`, `assert response.status_code in (401, 422)`.
- Явного `monkeypatch`, который вообще ничего не патчит, нет. Есть патчи
  **неиспользуемых путей**: `find=no_find` в тестах без `UnknownResult`
  (`test_transfers_api.py:110,173,189,205,313`), `calls` в
  `test_transfer_fallback` заполняется, но не проверяется; `now` в
  `load_rates`/`test_stale_rate_unavailable` принимается, но не используется.
  Это скрывает, что соответствующие ветки не исполняются.

## Ложно-зелёные тесты

Ниже — тесты, которые могут остаться зелёными при реально сломанном поведении.
Это не «плохие тесты целиком», а места, где проверка не доказывает заявленное.

1. **`test_webhook_bad_signature_rejected`** (`tests/integration/test_transfers_api.py:217-231`) —
   принимает `401` **или** `422`, поэтому не фиксирует обязательный `401`:
   если код снова начнёт отдавать `422`, тест останется зелёным. Кейс
   «заголовок отсутствует» всё ещё не проверен (пустой секрет уже проверяется
   отдельно на 503).
2. **`test_parallel_requests_share_pool`** (`tests/integration/test_webhooks_and_limits.py:95-113`) —
   в названии «pool», но `engine` создан с `NullPool`, сессия подменена, `checkedout()`
   не вызывается; 50 котировок не создают нагрузку на балансы/провайдера.
3. **`test_double_submit_returns_one_transfer`** (`tests/integration/test_transfers_api.py:200-214`) —
   два запроса идут **последовательно**, гонки нет; тест дублирует
   `test_idempotency_same_key_returns_same_transfer`.
4. **`test_unknown_reference_logged_but_accepted`** и
   **`test_webhook_unknown_reference_accepted`** (`tests/integration/test_webhooks_and_limits.py:86-92`,
   `tests/integration/test_transfers_api.py:234-250`) — проверяют только `200`.
   Слова «logged»/«metric» в названии не подтверждены: `caplog` и метрика не проверяются,
   в `handle_webhook` для unknown-reference нет логгера.
5. **`test_late_processing_does_not_override_terminal`** (`tests/integration/test_webhooks_and_limits.py:70-83`) —
   отправляет `PROCESSING` после `COMPLETED`; код игнорирует любой не-терминальный статус,
   поэтому тест пройдёт и без логики `occurred_at`. Нет `COMPLETED` vs `FAILED` с разными
   `occurred_at` и проверки истории/метрики.
6. **`test_duplicate_event_is_idempotent`** (`tests/integration/test_webhooks_and_limits.py:54-67`) —
   проверяет только баланс. Даже если отключить дедупликацию/`UNIQUE(event_id)`,
   второй event увидит терминальный перевод и не спишет деньги; тест этого не заметит,
   потому что не проверяет число строк `webhook_events` и историю.
7. **`test_unknown_result_adopts_existing_payout`** (`tests/integration/test_transfers_api.py:146-165`) —
   `find` сразу возвращает `PROCESSING`; тест не доказывает, что `find` был **первым**
   вызовом, не проверяет отсутствие повторного POST и число попыток. При retry-логике
   тест может остаться зелёным, даже если выплата создана дважды.
8. **`test_layered_architecture_contracts`** (`tests/architecture/test_boundaries.py:4-12`) —
   проверяет только `"0 broken"`, а не `2 kept`/имена контрактов. Удаление контракта
   не обязательно сделает тест красным. Нет положительного контроля.
9. **`test_services_do_not_import_fastapi` + отсутствие проверки правила `api`**
   (`tests/architecture/test_boundaries.py:15-38`) — тест ищет только строки в
   `app/services`; `app/api/v1/quotes.py` уже импортирует `AsyncSession` и репозитории,
   но arch-тесты зелёные, потому что правило `api -> repositories/sqlalchemy` не реализовано.
10. **`test_error_codes_mapping`** (`tests/unit/test_quote_calculator.py:179-197`) —
    проверяет только атрибуты классов (`code`, `issubclass`), но не то, что исключение
    реально поднимается из кода и превращается в Problem Details с нужным статусом.
11. **`test_target_source_roundtrip`** (`tests/unit/test_quote_calculator.py:138-149`) —
    проверяет самосогласованность прямого и обратного расчёта. Систематическая ошибка
    в `calculate_target` (например, неверное округление вверх) может сохранить
    roundtrip и пройти; нет привязки к независимому эталону.
12. **`test_transfer_all_decline_releases_reserve`** и **`test_daily_limit_exceeded`**
    (`tests/integration/test_transfers_api.py:124-143,312-323`) — допускают
    `status_code in (201, 202)` для первого запроса; ослабляют контракт
    «терминальный/нетерминальный ответ» и не проверяют отсутствие второго перевода/резерва.
13. **`test_versioned_and_root_paths_match`** (`tests/integration/test_quotes_api.py:54-64`) —
    проверяет только `POST /quotes` и один `GET` версии; нет сверки ответов, нет
    `/transfers`, `/clients/.../balance`, `/webhooks/...` и корневых дублей.
14. **`test_examples` / `check_result`** (`tests/unit/test_quote_calculator.py:67-135`) —
    не проверяют `expires_at`, шаги `breakdown`, полный набор полей; границы
    min/max не проверяются, только 500 и 500000.
15. **`test_no_comments_in_application_code`** (`tests/architecture/test_boundaries.py:41-50`) —
    ловит только строки, начинающиеся с `#`; inline-комментарии (`x = 1  # ...`)
    проходят. Это не проверка правила D15.
16. **`test_configure_logging_installs_redacting_handler`**
    (`tests/unit/test_logging_redaction.py:86-90`) — проверяет наличие фильтра
    у `root.handlers[0]`, но не реальный вывод приложения; глобальный root-логгер
    не восстанавливается, что может влиять на последующие тесты.
17. **`test_target_never_underpays_*` / `test_target_roundtrip_for_all_directions_matches_examples`**
    (`tests/unit/test_quote_edge_cases.py:45-84`) — первые три проверяют только
    `target_amount >= requested`, то есть допускают завышение/неверную комиссию;
    последний тест с «all directions» фактически проверяет только `RU-TH-BANK`.

## Чего критически не хватает

- **testcontainers**: реальный PostgreSQL (и MySQL) с lifecycle контейнера,
  `SHOW shared_buffers/work_mem/max_connections`, `engine.dispose()`.
- **xdist-изоляция**: схема/БД на воркер; проверка `pytest -n auto` в CI.
- **HTTP-клиент провайдера**: `respx` на 201/422/503/500/409/404, timeout,
  transport error, невалидный JSON.
- **Неизвестный итог**: `find=None`, `find=PROCESSING`, `find=COMPLETED`,
  visibility lag, stale read, отсутствие повторного POST, привязка к существующей
  выплате; проверка ledger мока.
- **Сверка и chaos**: `reconcile_once`, `lost_webhook`, рестарт между отправкой
  и записью, single-runner, `FOR UPDATE SKIP LOCKED`, `chaos_run.py --transfers 300`,
  ноль двойных выплат и зависших резервов.
- **Лимиты**: ровно-лимит, +1 minor unit, окна, `HOURLY`, `WINDOW_N`, веса,
  валюта лимита ≠ валюта баланса, `UNIQUE(limit_definitions)`.
- **Конкурентность**: 50–100 переводов, одинаковый `Idempotency-Key` параллельно,
  два клиента, инвариант `available + reserved + spent = start`, `READ COMMITTED`,
  неподтверждённые вставки/обновления, `engine.pool.checkedout()`.
- **Негативные пути**: все коды §6 через API, Problem Details, `details`,
  отсутствующие/пустые поля, не-JSON, 404 перевода, неизвестный клиент,
  401 вебхука, 500/502.
- **Схема и данные**: Alembic upgrade/downgrade, идемпотентный сид, FK/UNIQUE,
  регистр/пробелы валют, `rub`/` eur `.
- **Контракт**: OpenAPI (`/openapi.json`, snake_case, схемы ошибок, prod-docs off),
  `expires_at`, все поля `POST /quotes`, `breakdown`.
- **Свежесть/кэш**: `min(ttl, 6h)`, граница, инвалидация, битое значение,
  промах не кэшируется.
- **Наблюдаемость**: реальные логи с `caplog`, маска счёта/IP, отсутствие секретов,
  метрики `transfers.*`, `rates.*`, `unknown_webhooks_total`.
- **Скорость/CI**: тестовая settings с короткими таймаутами, fake clock,
  маркеры `slow`/`chaos`, coverage-порог.

## Оценка покрытия по уровням

Оценка экспертная (без coverage-инструмента), по отношению к заявленным
сценариям `docs/test-cases/*/SPEC.md`, а не по числу строк.

| Уровень | Что реально закрыто | Оценка |
|---|---|---|
| **Level 1** — расчёт и контракт котировки | 7 позитивных examples, roundtrip SOURCE/TARGET, formatting minor units, базовые коды ошибок через API, stale/missing rate, invalid/zero/NaN rate и stale-direct (новый `test_quote_edge_cases.py`), `GET /quotes/{id}`. Нет BVA min/max, точных TARGET-эталонов на 1 minor unit, overflow, `expires_at`, полного Problem Details, freshness-границ/кэша, currency lookup, OpenAPI. | **~50–60 %** |
| **Level 2** — исполнение, провайдеры, идемпотентность, резерв | happy-path перевода, decline-fallback, all-decline reserve release, unknown-adopt (без доказательства порядка), последовательная идемпотентность, один конкурентный тест на 5 запросов. Нет HTTP-клиента/5xx, настоящей гонки ключа, pool, limits, нескольких клиентов, полного инварианта. | **~35–40 %** |
| **Level 3** — надёжность | дубль вебхука (слабо), late-processing (слабо), unknown-reference (без лога/метрики), пустой секрет (503), foreign payout id и async-fallback, базовая проверка подписи (тест всё ещё принимает 422). Полностью отсутствуют reconcile, lost webhook, рестарт, chaos, веса/window-лимиты, single-runner, 50–100 конкурентных переводов. | **~15–20 %** |
| **Level 4** — бонус | только unit-функции redaction; нет реальных логов, метрик, OpenAPI, нагрузочного сценария, конфигурации провайдеров без редеплоя. | **~5–10 %** |

### Матрица по сценариям

| Сценарий | Состояние |
|---|---|
| `money-rounding` | ⚠️ частично: examples и формат, нет границ/overflow/HALF_UP-краёв |
| `currency-lookup` | ❌ почти нет: только неизвестная валюта, нет регистра/пробелов/сида/FK |
| `quote-contract` | ⚠️ частично: happy-path и часть ошибок, нет `expires_at`, breakdown, BVA, non-JSON |
| `error-codes` | ⚠️ все коды по разу, но не все ветки и не полный конверт |
| `provider-selection` | ⚠️ только чистые функции; нет respx/5xx/unknown/fallback-порядка |
| `idempotency` | ⚠️ последовательно; нет гонки/во время/после/БД UNIQUE |
| `balances-concurrency` | ⚠️ один тест на 5 запросов; нет 50–100, двух клиентов, isolation |
| `webhooks` | ⚠️ дубль/late/unknown, пустой секрет, foreign payout, async-fallback; нет contradiction, строгого 401, логов/метрик, пачки |
| `reconciliation` | ❌ нет тестов |
| `rate-freshness` | ⚠️ stale/missing/invalid + stale-direct; кэш, TTL-границы и HTTP не проверены |
| `limits` | ❌/⚠️ over-limit и третья валюта; нет границ/весов/window, ветка `limit_definitions` не работает и не тестируется |
| `arch-boundaries` | ⚠️ import-linter запускается, но правил мало, нет positive control |
| `logging-disclosure` | ⚠️ только unit-функции; реальные пути/PII/IP/счёт не проверены |
| `openapi-contract` | ❌ нет тестов |
| `load` / chaos | ❌ `tests/load` пуст, `chaos_run.py` не запускается |

## Что проверить в первую очередь (приоритет исправлений)

1. Сделать тестовую БД изолированной: testcontainers + worker-схемы, затем прогнать
   `pytest -n auto` в CI.
2. Закрыть `ProviderClient` respx-тестами и сценарии неизвестного итога
   (`find=None` → следующий провайдер; `find=state` → без второго POST).
3. Добавить reconcile/chaos/lost-webhook тесты — без них Level 3 не подтверждён.
4. Исправить контракт вебхука: строго `401`, missing signature, unknown reference
   с логом и метрикой, `occurred_at`/contradiction.
5. Довести лимиты, конкурентную идемпотентность и 50–100 конкурентных переводов
   с проверкой инварианта баланса.
6. Добить Level 1-контракт: BVA min/max, `TARGET`-округление, `expires_at`,
   полный Problem Details, currency lookup, OpenAPI.
