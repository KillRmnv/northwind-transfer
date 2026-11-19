# Ревью-раунд 2: новые тесты

Снимок: **HEAD `1baaa9b`** («test: respx provider client, migrations, reconcile, load,
concurrency»), Python 3.12.14, pytest 8.4.2, Postgres на `localhost:5433`.
Ревью только читает и запускает; код не менялся.

> Во время проверки в рабочем дереве параллельно работал другой агент и правил
> `tests/conftest.py`, `tests/load/test_load.py`, `app/services/*`. Все ссылки на
> строки ниже — по **HEAD** (`git show HEAD:<file>`), а не по текущему дереву.

## Итог

Новые тесты закрывают реальные дыры прошлого ревью (respx, миграции, сверка,
конкурентность), но часть из них зелёная по неверной причине, а два сценария
ломают стандартный параллельный прогон. Главное:

* **P0**: `test_migrations.py` использует фиксированное имя БД и под `-n 4`
  падает (проверено: 3 ошибки из 4).
* **P1**: `lint-imports` ищется только по `PATH` — при `python -m pytest` arch-тест
  красный (проверено), а вебхук-тесты зависят от незакоммиченного `.env`
  (проверено: с пустым секретом `401`-тест получает `503`).
* **P1**: три «сильных» новых теста не ловят мутацию в том guard-е, который
  заявлен в имени: `409` в `create_payout`, владение `payout_id` в вебхуке,
  retry `create_with_recovery`.
* Проверка «пула» по-прежнему не проверяет пул, хотя это прямое требование
  `docs/SPEC.md:97` (`engine.pool.checkedout()`, отсутствие `TimeoutError`).

## Таблица

| # | Severity | Проблема | Файл:строка | Чем грозит | Как исправить |
|---|----------|----------|-------------|------------|---------------|
| 1 | **P0** | Миграционный тест использует одну БД `transfer_test_migrations` на все воркеры; `drop if exists` + `create` из разных воркеров гоняются | `tests/integration/test_migrations.py:9,25-26` | Проверено: `pytest tests/integration/test_migrations.py -n 4` → `1 passed, 3 errors`, `DuplicateDatabaseError: Key (datname)=(transfer_test_migrations) already exists`. Стандартный CI-прогон `-n auto` красный, причём нестабильно (то 3 ошибки, то другие 3). | `DB_NAME = f"transfer_test_migrations_{os.environ.get('PYTEST_XDIST_WORKER','main')}_{os.getpid()}"`; либо schema-per-worker; удаление БД — только своей. |
| 2 | **P1** | Вне xdist все процессы используют `transfer_test_main`; `drop_all/create_all` в каждом тесте | `tests/conftest.py:19-31,68-78` | Проверено: два параллельных pytest → `DeadlockDetectedError` на `DROP TABLE payout_attempts`, затем `relation "currencies" does not exist` и каскад фейлов; после падения остаётся backend в `idle in transaction` с `SELECT payout_attempts…`, который блокирует `DROP TABLE` и отравляет все последующие прогоны (пока не убить сессию). | Уникальная БД на процесс (`pid`/`uuid`), `SET lock_timeout/statement_timeout`, при старте terminate чужих backend’ов, либо `testcontainers`. |
| 3 | **P1** | Arch-тест ищет `lint-imports` только через `shutil.which` | `tests/architecture/test_boundaries.py:9-12` | Проверено: `.venv/bin/python -m pytest tests/architecture` → `assert executable is not None` (failed); зелёный только при активированном venv/`uv run`, т.е. зависит от способа запуска, а не от кода. | Не полагаться на PATH: `[sys.executable, "-c", "from importlinter.cli import lint_imports_command; lint_imports_command()"]` или entry point из `importlib.metadata`. |
| 4 | **P1** | Вебхук-тесты читают `PROVIDER_WEBHOOK_SECRET` из незакоммиченного `.env`; тестовой `Settings`-фикстуры нет | `tests/conftest.py:1-16`; `tests/integration/test_webhooks_and_limits.py:264-270` | Проверено: `PROVIDER_WEBHOOK_SECRET= pytest … -k "missing_signature or unknown_reference"` → `assert 503 == 401` и `503` вместо `{"status":"ok"}`. В чистом клоне/CI (`.env` не в git, есть только `.env.example`) весь вебхук-блок краснеет; локально — зелёный на dev-секрете. | Фикстура `settings` с `monkeypatch` (секрет, `api_sync_timeout_sec`, `mock_provider_url`); отдельно проверять `401` (секрет задан) и `503` (секрет пуст). |
| 5 | **P1** | `test_create_payout_409_is_unknown` не доходит до 409-ветки | `tests/unit/test_provider_client.py:88-92`; `app/services/providers.py:184-189` | Проверено вручную: `409` с пустым телом → `UnknownResult(reason="http 409")` (срабатывает `except ValueError` выше), а `"idempotency conflict at provider"` возвращается только при JSON-теле. Удаление строк 188-189 — тест всё равно зелёный. Смысл «конфликт идемпотентности у провайдера» не проверен. | Мокать `409` с JSON-телом и проверять `reason == "idempotency conflict at provider"`; отдельный кейс — `409` без тела. |
| 6 | **P1** | `test_foreign_payout_id_does_not_complete` не проверяет владение `payout_id` | `tests/integration/test_webhooks_and_limits.py:178-221`; `app/services/webhooks.py:101-103` | `payout_id="po-foreign"` не принадлежит ни одной попытке → `find_attempt_by_payout` = `None`, и перевод не завершается только из-за несовпадения `state.reference` (`webhooks.py:108`). Удаление ownership-guard’а — тест зелёный. Плюс оба перевода получают один и тот же `payout_id="po-a"`, чего в реальности не бывает. | Разные `payout_id` для двух переводов; отправлять вебхук по первому переводу с `payout_id` второго и с `reference`, совпадающим с первым. |
| 7 | **P1** | Путь retry/`create_with_recovery` не покрыт вообще | `app/services/transfers.py:96-111`; `tests/integration/test_transfers_api.py:51-55` | Именно этот цикл защищает от двойной выплаты: `UnknownResult → GET ?reference= → POST` до 3 раз. Единственный тест (`test_unknown_result_adopts_existing_payout`) проверяет только «нашли сразу». Мок `patch_provider` схлопывает `probe` в `"found"/"missing"`, поэтому ветка `outcome == "error" → continue` невидима. Мутация `range(3)` → `range(0)` или удаление второго POST — зелено. | Unit-тест с фейковым клиентом: последовательность вызовов `POST→GET→POST` и счётчики; в интеграции — счётчик `create_payout`; исход `error` оставить отличимым от `missing`. |
| 8 | **P1** | `test_parallel_requests_share_pool` не проверяет пул | `tests/integration/test_webhooks_and_limits.py:95-113`; требование — `docs/SPEC.md:97` | 50 котировок и только статусы: пройдёт и с `NullPool`, и с пулом на 1 соединение. `engine.pool.checkedout()`, `max_overflow`, `pool_timeout`, отсутствие `TimeoutError` не проверяются, хотя это явно в ТЗ. | Пробрасывать app-engine в тест, под нагрузкой (50–100 переводов) проверять `checkedout() <= pool_size+max_overflow`, отсутствие pool `TimeoutError`; имя теста оставить осмысленным. |
| 9 | **P2** | Профиль `sqlite` заявлен, но сломан: xdist делит один файл, конкурентность падает | `tests/conftest.py:19-31,56-65` | Проверено: `DB_PROFILE=sqlite … -n 2` → `table currencies already exists` (общий `var/test.sqlite3`); последовательно `test_concurrent_same_key/no_double_reserve` → `RuntimeError: athrow(): asynchronous generator is already running`. Профиль, который README предлагает как «быстрый», не зелёный. | Файл на воркер (`test_<worker>.sqlite3`) и/или честно объявить sqlite неподдерживаемым; починить `for_update`/жизненный цикл сессий для sqlite. |
| 10 | **P2** | `test_seed_is_idempotent` не проверяет главное свойство сида — балансы | `tests/integration/test_migrations.py:111-142`; `app/db/seed.py:132-142` | Мутация «при повторном сиде выставлять `available` из JSON» не меняет counts → тест зелёный, а в проде рестарт вернёт уже потраченные деньги. Дубли `maintenance_windows` (у `prov_alpha` их 30) тоже не ловятся: считаются только currencies/providers. | После первого сида создать перевод/списать баланс, пересеять и проверить `available/reserved` без изменений; отдельно `count(maintenance_windows)` до/после. |
| 11 | **P2** | Миграция сверяется только по именам таблиц | `tests/integration/test_migrations.py:58-74` | Удалённая/переименованная колонка, неверный тип/nullable/FK — зелено. Дрейф «модели ↔ миграция» не виден. | `alembic.autogenerate.compare_metadata(connection, Base.metadata)` и `assert not diff`; либо сравнение `information_schema.columns`. |
| 12 | **P2** | Тест «event uniqueness» тавтологичен, uniqueness — по подстроке имени | `tests/integration/test_migrations.py:97-108` | `any("idempotency_key" in name)` не доказывает `UNIQUE(idempotency_key)`; `webhook_events_pkey` — это PK по `event_id`, он есть всегда, т.е. вторая проверка пустая. | `pg_get_constraintdef`/`pg_index` + `indisunique`, проверять точный список колонок; негативный кейс — вторая вставка должна нарушать constraint. |
| 13 | **P2** | Список индексов — копия миграции, а не сравнение с моделями | `tests/integration/test_migrations.py:77-94` | `required <= indexes`: лишние индексы и дрейф с моделями не ловятся; новый индекс в модели не даёт сигнала. | Сравнивать `Base.metadata.tables[...].indexes` с `pg_indexes` (exact set), негативный кейс на пропавший индекс. |
| 14 | **P2** | `test_duplicate_event_is_idempotent` не наблюдает «второй без эффекта» | `tests/integration/test_webhooks_and_limits.py:54-67` | Проверяется только баланс; после COMPLETED перевод уже терминален, поэтому второй доставке всё равно, работает ли дедуп по `event_id`. Дедуп по `(reference,status)` или лишняя запись в историю — зелено. | Проверять `count(webhook_events where reference=…) == 1`, ровно одну терминальную запись в истории; второе событие с другим `event_id` и тем же содержимым не должно добавлять историю. |
| 15 | **P2** | Кейс 3 из `docs/test-cases/webhooks/SPEC.md` («два не-терминала в беспорядке») не покрыт | `tests/integration/test_webhooks_and_limits.py:70-83`; `app/services/webhooks.py:97-100` | Тест покрывает кейс 2 (терминал не перетирается) и до монотонного guard’а по `occurred_at` не доходит. Удаление строк 97-100 — зелено; а именно они держат «побеждает больший `occurred_at`». | Перевод в PROCESSING: сначала событие PROCESSING с `occurred_at` T2, затем COMPLETED с T1<T2 → статус остаётся PROCESSING, строка события не добавлена. |
| 16 | **P2** | `test_no_double_reserve_under_concurrency` неоднозначен: 409 приходят из-за денег, а не лимита | `tests/integration/test_webhooks_and_limits.py:116-137` | `c_002`: 50 000 / 20 000 = ровно 2, а дневной лимит 100 000 допускает все 5. Код ошибки не проверяется: регресс, при котором 409 приходит по другой причине, зелёный; число вызовов провайдера не считается. | Сделать лимит связывающим (например, `c_001`: 500 000 и лимит 300 000, 5×100 000), проверять `INSUFFICIENT_FUNDS`/`DAILY_LIMIT_EXCEEDED` и счётчик `create_payout`. |
| 17 | **P2** | `test_concurrent_same_key_one_transfer` не увидит двойную выплату | `tests/integration/test_webhooks_and_limits.py:273-290` | Разрешены `202`, проверяется только баланс; лишний POST провайдеру или вторая строка `payout_attempts` не видны, а это и есть главный инвариант. | Требовать `1×201` (остальные — тот же ответ), счётчик `create_payout == 1`, одну попытку и одну запись истории. |
| 18 | **P2** | В respx-тестах `probe` не проверяется query-параметр | `tests/unit/test_provider_client.py:108-142` | `respx.get(f"{BASE}/prov_alpha/payouts")` матчит любой query. Удаление `params={"reference": reference}` (`providers.py:211-214`) — все три теста зелёные, хотя сверка тогда ищет не то. | `route.calls[0].request.url.params["reference"] == "ref-1"`. |
| 19 | **P2** | Тест тела запроса проверяет только заголовок и валюту | `tests/unit/test_provider_client.py:95-105` | `amount`, `reference`, `recipient`, `callback_url` и `X-Scenario` (`providers.py:155-157`) не проверяются — их удаление/переименование зелено. | Сравнить весь JSON-body и заголовок сценария. |
| 20 | **P2** | Непокрытые/незащищённые HTTP-пути провайдера | `app/services/providers.py:177-189` | `503 → Declined(PROVIDER_UNAVAILABLE)` не покрыт; `422` с не-JSON телом кидает необработанный `JSONDecodeError` (проверено вручную: `RAISED JSONDecodeError`), т.е. HTML-страница ошибки провайдера роняет перевод вместо `UnknownResult`; то же для `200` с битым JSON в `get_payout`/`probe`. | Обернуть `.json()` в try/except → `UnknownResult`/`None`; добавить respx-кейсы `503`, `400`, `422` c html, `200` c html, `get_payout 500`. |
| 21 | **P2** | Тест сверки идёт не тем путём, что прод, и слабо считает статистику | `tests/integration/test_webhooks_and_limits.py:293-323`; `app/main.py:28-43` | В проде вызывается `reconcile_transfer` на каждый перевод в своей сессии; `reconcile_once` используется только тестом (`grep`: других вызовов нет), поэтому commit/видимость и семантика цикла не проверены. `stats["completed"] >= 1` пройдёт и при обработке другого перевода; повторный проход (идемпотентность) не проверяется. | Тестировать `reconcile_transfer` (и цикл с 2–3 переводами), утверждать `checked==1, completed==1`, второй проход — `completed==0`, `reserved` не изменился. |
| 22 | **P2** | Зависимость от реального времени и `.env`-таймаута | `tests/integration/test_webhooks_and_limits.py:178-221`; `app/services/transfers.py:465-468`; `tests/load/test_load.py:29` | Файл идёт 74,5 с; foreign-тест — 2×`api_sync_timeout_sec` (24 с при 12), потому что `get=PROCESSING` ждёт полный таймаут; `elapsed < 60` ничего не гейтит (load-тесты прошли за 4,5 с суммарно). Смена `.env` меняет статусы/длительность. | В фикстуре выставить `api_sync_timeout_sec=0.2–0.5`, подменить `asyncio.sleep`/часы; порог сравнивать с конфигом и/или заменить на percentile-гейт. |
| 23 | **P2** | Доступность провайдеров зависит от календаря | `data/providers.json` (`prov_echo`: 2026-11-04…2027-01-04; `prov_alpha`: 22:00–22:30 до 2026-12-04); тесты используют `now_utc()` | После 2027-01-04 `prov_echo` снова станет кандидатом, а в 22:05 UTC `prov_alpha` исчезает → ожидаемые количества/суммы/выбор провайдера поедут. Фикстура `now` есть, но новыми тестами не используется. | Прокидывать фиксированный `now` в создание котировки/перевода (или сделать окна относительными к тестовым часам). |
| 24 | **P3** | Тавтологичный тест типов | `tests/unit/test_provider_client.py:168-172` | `not isinstance(Accepted(...), Declined)` истинно для любых двух dataclass’ов; `pytest.raises(TypeError): UnknownResult()` проверяет сигнатуру dataclass, а не поведение приложения. | Заменить на диспетчеризацию в `run_attempts`/`create_with_recovery`: для каждого типа исхода — ожидаемая ветка и запись попытки. |
| 25 | **P3** | Soft-assert, после которого значение используется без защиты | `tests/integration/test_webhooks_and_limits.py:309-318`; `48-51`; `tests/unit/test_provider_client.py:36-39` | Если мягкая проверка упала (например, `202` не пришёл), следующая строка падает `KeyError`/`AttributeError` и маскирует настоящую причину. Смешаны `with check` и жёсткие assert без правила. | Предусловия (код ответа/тип) — жёстко; мягкие проверки — только для независимых величин, которые дальше не используются. |
| 26 | **P3** | Мёртвый код и связка load ↔ integration | `tests/load/test_load.py:43,35,85`; `36-41` | `quote = None`, `_ = providers_module`, импорт хелперов из `tests.integration.test_transfers_api`: правка интеграционного модуля ломает load, а числа 50/100 жёстко завязаны на сид. | Хелперы — в общий `tests/helpers.py`; убрать мёртвый код; числа выводить из данных. |
| 27 | **P3** | `api`-фикстура чистит все override’ы | `tests/conftest.py:99` | `dependency_overrides.clear()` убирает и чужие override’ы (например, будущую session-scoped подмену) → тихая потеря подмены. | `app.dependency_overrides.pop(get_session, None)`. |
| 28 | **P3** | `alembic` в subprocess без `timeout` и `cwd` | `tests/integration/test_migrations.py:37-42` | Недоступная/зависшая БД вешает весь прогон; запуск pytest не из корня даёт непонятную ошибку (плюс `Path("data")` в conftest). | `timeout=60`, `cwd=<repo root>`, выводить `stderr` при падении. |
| 29 | **P3** | Новый пул на каждый тест + бюджет соединений | `tests/conftest.py:56-65` | При `-n 4` пик 4×(20+10)=120 > `PG_MAX_CONNECTIONS=100` → риск `too many clients`; создание схемы и пула на каждый тест — главная причина 74 с на файл. | Session-scoped engine + транзакция/schema на тест. |

## Ложно-зелёные (что мутировать, чтобы осталось зелёным)

| Тест | Мутация, которую он НЕ ловит | Почему |
|------|------------------------------|--------|
| `test_create_payout_409_is_unknown` | удалить `if response.status_code == 409` (`providers.py:188-189`) | пустое тело → `ValueError` → `"http 409"`; тип всё равно `UnknownResult` |
| `test_foreign_payout_id_does_not_complete` | удалить ownership-guard (`webhooks.py:101-103`) | `po-foreign` не найден в попытках, спасает проверка `reference` |
| `test_late_processing_does_not_override_terminal` | удалить монотонный guard (`webhooks.py:97-100`) | перевод уже терминален, срабатывает ветка `TERMINAL` |
| `test_duplicate_event_is_idempotent` | дедуп по `(reference,status)` вместо `event_id`; лишняя запись истории | проверяется только баланс, а он защищён `_claim` и терминальностью |
| `test_parallel_requests_share_pool` | вернуть `NullPool` / `pool_size=1` | проверяются только коды 201 |
| `test_no_double_reserve_under_concurrency` | сломать проверку дневного лимита | 409 объясняются нехваткой средств; код ошибки не проверяется |
| `test_concurrent_same_key_one_transfer` | лишний POST провайдеру на проигравшем запросе | `202` разрешён, вызовы провайдера не считаются |
| `test_probe_payout_*` (3 теста) | убрать `params={"reference": …}` | respx матчит любой query |
| `test_create_payout_sends_header_and_currency_format` | убрать `recipient`/`callback_url`/`X-Scenario` из запроса | проверяются только `Idempotency-Key` и `"currency":"THB"` |
| `test_migration_enforces_idempotency_and_event_uniqueness` | заменить UNIQUE на обычный индекс с тем же именем | проверяется подстрока имени + `pkey`, который есть всегда |
| `test_migration_creates_expected_indexes` | добавить лишний/неверный индекс, разойтись с моделью | `required <= indexes` — только подмножество |
| `test_seed_is_idempotent` | при повторном сиде перезаписывать `balances.available` | counts не меняются, балансы не проверяются |
| `test_unknown_outcome_types_are_distinct` | ничего (тест проверяет Python) | тавтология |

Отдельно: `test_reconcile_completes_lost_webhook` не ложно-зелёный (терминал
действительно наступает), но `stats["completed"] >= 1` и вызов `reconcile_once`
вместо продового `reconcile_transfer` дают слабое доказательство.

## Моки, подогнанные под реализацию

* `complete_getter()`/`fake_get` возвращают `reference=""`
  (`tests/integration/test_transfers_api.py:66-71`, `test_webhooks_and_limits.py:43-46`).
  Настоящий провайдер возвращает реальный `reference`; из-за `""` проверка
  `state.reference != event.reference` (`webhooks.py:108`) во всех вебхук-тестах
  отключена. Регресс в обработке `reference` не виден.
* `accept_factory("po-a")`/`accept_factory("po-wh")` возвращают **один и тот же**
  `payout_id` на все переводы и всех провайдеров. Реальная модель
  (`find_attempt_by_payout` по `(provider_id, payout_id)`) на таких данных
  неоднозначна — см. п. 6.
* `patch_provider(..., find=no_find)` подменяет `probe_payout` и превращает
  `None → "missing"` (`test_transfers_api.py:51-55`). Исход `"error"` (транспортная
  ошибка/5xx), ради которого и появился `probe_payout`, в интеграционных тестах
  не воспроизводится → ветка `continue` в `resolve_unknown` мертва.
* `processing_get` игнорирует `provider_id`/`payout_id` и всегда отдаёт PROCESSING
  с `reference=""` — мутация, не проверяющая, что опрашивается нужная выплата,
  пройдёт.
* `event(...)` всегда ставит `payout_id="po_wh"`, а `deliver()` подписывает
  тем же секретом из `app_settings`: позитивный путь подписи проверяется,
  негативный (кривая/отсутствующая подпись) — только в двух тестах, причём
  старый `test_webhook_bad_signature_rejected` допускает `(401, 422)`, т.е.
  `422` на плохую подпись тоже «зелёный».
* `respx`-маршруты заданы без проверки тела/query (см. п. 18–19), т.е. мок
  принимает любой запрос к нужному URL.

## Чего не хватает

1. **Retry/unknown-result**: последовательность `POST→GET ?reference=→POST`,
   число POST’ов, `probe="error"` (retry) vs `"missing"` (следующий провайдер),
   исчерпание 3 попыток, «нашли после второй попытки».
2. **Сверка**: `reconcile_transfer`/цикл из `main.py` (не `reconcile_once`),
   два воркера сверки, `PENDING` с отправленной, но незаписанной выплатой,
   второй проход без повторного списания, `FAILED`-ветка сверки.
3. **Вебхуки**: `event.status == "FAILED"` (`webhooks.py:116-119`) не покрыт
   ни одним тестом; дедуп с другим `event_id`; пачка событий на один перевод
   (кейс 9 спецификации); `contradiction` COMPLETED vs FAILED (кейс 4);
   `200 + warn + метрика` для неизвестного `payout_id` (кейс 5) — метрика/лог
   не проверяются.
4. **Провайдер**: `503→Declined`, `400` с кодом, `422`/`200` с не-JSON,
   `get_payout` 500, `find_payout` поверх HTTP, `X-Scenario`.
5. **Негативные API-кейсы** (были в прошлом ревью, п. 16): нет
   `Idempotency-Key` → 422, невалидный UUID `quote_id`, неизвестный `client_id`,
   `GET /transfers/{id}` → 404, баланс неизвестного клиента, отсутствующий
   `recipient`.
6. **Migrations**: колонки/типы/nullable/FK, `compare_metadata`,
   `downgrade base` → `upgrade head`, повторный `upgrade`, что
   `alembic_version` действительно на голове.
7. **Seed**: неизменность балансов/лимитов при повторном сиде, дубли окон
   обслуживания, идемпотентность при частично заполненной БД.
8. **Пул и нагрузка**: `checkedout()`, `max_overflow`, `pool_timeout`,
   отсутствие pool-`TimeoutError`; chaos-инвариант «ноль двойных выплат по
   ledger мока, ноль зависших резервов» (`chaos_run.py` тестами не запускается;
   `tests/load` проверяет только коды и один баланс).
9. **Герметичность**: `.env`-независимая `Settings`-фикстура, уникальная БД
   на процесс, `testcontainers` (зависимость объявлена, но не используется),
   фиксированный `now` вместо календаря `providers.json`.
10. **Трассируемость**: в новых тестах нет ID кейсов из
    `docs/test-cases/*/SPEC.md` (в README заявлено ~115 кейсов) — по имени
    теста нельзя однозначно сказать, какой кейс он закрывает.

## Что проверено эмпирически

Все команды — из корня репозитория, БД `localhost:5433`.

| Команда | Результат |
|---------|-----------|
| `pytest tests/unit/test_provider_client.py` | 14 passed |
| `pytest tests/architecture` | 3 passed, **1 failed** (`lint-imports` не в PATH) |
| `PATH=.venv/bin:$PATH pytest tests/architecture` | 4 passed |
| `pytest tests/integration/test_migrations.py` | 4 passed, 6,65 s |
| `pytest tests/integration/test_migrations.py -n 4` | **1 passed, 3 errors** (`DuplicateDatabaseError`) |
| `pytest tests/integration/test_webhooks_and_limits.py` | 13 passed, 74,51 s |
| `PROVIDER_WEBHOOK_SECRET= pytest … -k "missing_signature or unknown_reference"` | **2 failed** (`503` вместо `401`/`200`) |
| `pytest tests/load` | 2 passed, 4,53 s |
| `pytest tests/integration -n 4` | 39 passed, 1 failed, 2 errors (коллизия БД миграций) |
| `DB_PROFILE=sqlite … -n 2` | 1 passed, **1 error** (`table currencies already exists`) |
| `DB_PROFILE=sqlite` два конкурентных теста | **2 failed** (`asynchronous generator is already running`) |
| Два параллельных pytest на общей `transfer_test_main` | deadlock на `DROP TABLE payout_attempts`, каскад `relation … does not exist`, остаётся `idle in transaction` |
| `lint-imports` вручную | `Contracts: 2 kept, 0 broken` — проверка числа контрактов настоящая |

## Что сделано хорошо

* `respx` реально проверяет `ProviderClient` по HTTP: 201/422/500/504/transport
  error/timeout/409 и `probe` 404/503/found — раньше это был полностью
  замоканный путь.
* `probe_payout` 404 vs error отделён от `find_payout`, и это закреплено тестами.
* Миграционный тест действительно поднимает схему через `alembic upgrade head`
  в отдельной БД и сверяет набор таблиц с `Base.metadata` (для имён таблиц —
  корректное направление проверки).
* Worker-БД по `PYTEST_XDIST_WORKER` работает: `pytest tests/integration -n 4`
  даёт 39 passed (кроме миграций).
* `test_missing_signature_returns_401` и `test_empty_webhook_secret_fails_closed`
  различают «нет подписи» и «нет секрета» — при заданном секрете это верная
  пара проверок.
* `test_concurrent_same_key_one_transfer`, `test_no_double_reserve…` и
  `test_fifty_concurrent_transfers_keep_invariant` действительно проверяют
  инвариант баланса (`available/reserved`) под `asyncio.gather`, а не только
  коды ответов.
* В `test_daily_limit_in_third_currency` лимит `c_003` задан в USD, поэтому
  тест реально требует конвертации RUB→USD: без неё первый перевод получил бы
  409.
