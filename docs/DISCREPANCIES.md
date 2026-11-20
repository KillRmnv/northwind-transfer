# DISCREPANCIES — расхождения реализации с ТЗ

Источники требований: `docs/Backend.md` (ТЗ, далее «ТЗ»), `docs/SPEC.md` (технические правила, далее «SPEC»), `docs/DECISIONS.md` (D…), `docs/ASSUMPTIONS.md` (A…), `docs/LIMITS.md` (L…).

Этот файл описывает состояние кода на коммит `e08b273` (аудит от 2026-11-19). Предыдущая версия утверждала, что все пункты R-01…R-32 закрыты. При повторной проверке по коду и тестам часть статусов не подтвердилась: пункты переоткрыты или помечены ⚠️, найдены новые (R-33…R-46). Каждый статус подкреплён ссылкой на код или на тест.

Легенда: ✅ подтверждено кодом (и тестом, где указано); ⚠️ реализовано частично или с осознанным отклонением (причина — в строке); ❌ не выполнено, пункт открыт.

## Методика и границы проверки

- Прочитано: весь `app/`, `mock_provider/`, `scripts/`, `alembic/`, `tests/` (список тестов сверен с утверждениями), `docker-compose.yml`, `Dockerfile`, `.env.example`, `pyproject.toml`, `.gitignore`, `.dockerignore`, `data/`, все `docs/*.md`, `README.md`.
- Вендорный код и данные (`data/`, `examples/`, `scripts/chaos_run.py`, `mock_provider/`) совпадают со стартовым архивом `transfer-core-starter (2).zip` побайтно (`examples/` возвращены через `git checkout`, см. R-46); `data/`, `mock_provider/` и `scripts/chaos_run.py` не менялись.
- Полный прогон `uv run pytest` выполнен изнутри compose-сети (хост не видит `localhost:5433`, см. README): результат — в разделе «Итог прогона».
- Пробы аудита по R-33, R-35, R-36, R-39 переписаны в постоянные регрессионные тесты (`test_young_pending_attempt_blocks_next_provider`, `test_persistent_5xx_reaches_terminal_failed`, `test_needs_review_counts_toward_daily_limit`, `test_webhook_failed_moves_to_next_provider`); временный файл проб удалён.
- `scripts/chaos_run.py --transfers 300 --seed 7 --concurrency 10` в режиме `MOCK_MODE=chaos` запущен дважды. Первый прогон по ошибке шёл на моке в `normal` (мок пересоздан `depends_on` без `--no-deps`), его результат не считается. Зачётный прогон — `339F3EC7`: `RESULT: OK`, 271 `COMPLETED`, 28 `FAILED`, 52 двойных сабмита, 0 по всем счётчикам. Chaos не прерывает процесс и не моделирует отмену по таймауту, поэтому R-33 и R-34 он не проверяет (в прогоне нет `NEEDS_REVIEW` и «фантомных» попыток).
- Независимая сверка после прогона (`/tmp/opencode/xcheck.py`, вне репозитория): переводы и попытки сверены с ledger мока, история переходов сверена с `EDGES` и с текущим статусом, балансы — по всем клиентам (`spent`, `reserved`, сохранение суммы относительно `clients.json`). Нарушений баланса и выплат нет. Найдены расхождения истории переходов — R-43, R-44.
- Номера строк — на коммит `e08b273`.

## Итог аудита

Все пункты P0–P3 закрыты кодом, тестами и доками (проверено полным прогоном и chaos, см. «Итог прогона»). Открытых пунктов нет; единственное осознанное отклонение — R-23 (веса скоринга, надёжность доминирует).

История аудита сохранена ниже попунктно: R-01 переоткрывался из-за R-33/R-34, R-35 решён вариантом A, R-36 был регрессией от R-07, R-43/R-44 найдены независимой сверкой истории, R-45 закрыт отдельным `scripts/chaos_audit.py` без правок вендорного `chaos_run.py`, R-46 — возвратом `examples/` и численным сравнением в тестах.

## Что соответствует ТЗ (проверено)

- Пути API с префиксом `/api/v1` и без него (`app/main.py:115-120`); формат ошибок RFC 9457 с вложенным `error` (`app/api/exception_handlers.py`).
- Деньги строками, `Decimal`, `NUMERIC(20, 8)` (`app/models/tables.py`); float не используется для сумм.
- Расчёт SOURCE/TARGET: примеры `examples/` и согласованность режимов (`tests/unit/test_quote_calculator.py:111-153`).
- Коды ошибок по ТЗ §6: все есть в `app/core/exceptions.py`, маппинг на HTTP-статусы описан в README.
- Блокировка клиента `FOR UPDATE` перед резервом; повторная проверка ключа после блокировки (`app/services/transfers.py:195-202`).
- Атомарная финализация: `UPDATE … WHERE funds_settled = false AND status NOT IN (terminal)` (`app/services/transfer_machine.py:138-154`).
- HMAC-подпись вебхуков (`app/services/webhooks.py:28-32`, `:43-47`), дедуп по `event_id`, монотонность по `occurred_at`.
- Архитектурные тесты: слои import-linter (`pyproject.toml`), запрет `try/except` в `services/` и `api/` (`tests/architecture/test_boundaries.py:47`), запрет `fastapi` в сервисах.
- `docker-compose` с тюнингом PostgreSQL и `shm_size: 1gb`, `.env.example`, Alembic, идемпотентный сидер (`app/db/seed.py`).
- Схема: CHECK на `available/reserved/spent >= 0` (`app/models/tables.py:126-130`, миграция 0005); `transfer_status_history`; `breakdown`; `spent`.

## Приоритеты

- **P0** — деньги и инварианты (двойная выплата, возврат при живой выплате, зависший резерв). Обязательно до сдачи.
- **P1** — явное требование ТЗ/SPEC не выполнено или регрессия.
- **P2** — частичное расхождение, документация не совпадает с кодом, решение не защищено.
- **P3** — гигиена, упаковка, мелочи.

| Приоритет | Открытые пункты |
|---|---|
| P0 | нет открытых |
| P1 | нет открытых |
| P2 | R-23 (осознанное отклонение, см. строку) |
| P3 | нет открытых |

## Статус выполнения

| Пункт | Приоритет | Статус | Что проверено / комментарий |
|---|---|---|---|
| R-01 | P0 | ✅ | Закрыт через R-33 (блок молодых, `_young_unconfirmed`), R-34 (`_settle_expired` с пробами) и R-18 (advisory lock + `_touch` round-robin). |
| R-02 | P1 | ✅ | Повторная проверка ключа после `lock_client` (`transfers.py:198-202`). Тесты: `test_concurrent_duplicate_transfers_single_row`, `test_concurrent_same_key_one_transfer`. |
| R-03 | P0 | ✅ | `create_with_recovery` возвращает `None` при «missing» → `NEEDS_REVIEW` (`transfers.py:123`, `:453-463`), а не следующий провайдер. Окно проб ≈5–11 с короче visibility-lag мока (3–15 с), поэтому итог — `NEEDS_REVIEW` и сверка. Pending-вариант той же проблемы — R-33. |
| R-04 | P1 | ✅ | Общий дедлайн `run_with_timeout` (`app/api/v1/transfers.py:100-107`, `api_sync_timeout_sec = 12`) плюс дедлайн в `wait_for_settlement` (`transfers.py:481-489`). Проба при постоянном 5xx: ответ за 5.1 с, таймаут не сработал (см. R-35). |
| R-05 | P1 | ✅ | Таблица маппинга в README (`README.md:116-124`) совпадает с `providers.py:170-189`. Трактовка 5xx — вариант A (D32, README): `missing` в окне видимости = «не создано». |
| R-06 | P1 | ✅ | `transfer_status_history` пишется в `record_transition` (`repositories/transfers.py:38-57`) при каждом переходе машины; `GET /transfers/{id}` отдаёт историю из таблицы (`api/v1/transfers.py:27-31`). |
| R-07 | P1 | ✅ | `NEEDS_REVIEW` в справочнике (`seed.py:29`), в `EDGES` (`transfer_machine.py`), виден в API; учтён в лимитах (R-36). |
| R-08 | P2 | ✅ | `warn` + `webhook_unknown_payout_total` для неизвестного `payout_id` при известном `reference` (нет записи у провайдера или чужой `reference`); остальные счётчики как раньше. In-memory метрики без экспорта — осознанно (D12). |
| R-09 | P2 | ✅ | Порядок «секрет → подпись → провайдер» (`webhooks.py:43-52`); A15 соответствует. |
| R-10 | P2 | ✅ | README: все коды и таблица маппинга кодов мока (`README.md:96-124`). `ProviderUnavailableError` удалён из `core/exceptions.py`. |
| R-11 | P2 | ✅ | Колонка `quotes.breakdown` (`models/tables.py:145`, миграция 0005), сохраняется в `save_quote` (`repositories/quotes.py:29`), возвращается в `quote_row_to_response` (`api/mappers.py:51`). |
| R-12 | P2 | ✅ | `balances.spent`, меняется в `consume_reserve` (`repositories/balances.py:66-72`). Инвариант проверяется: `test_balance_invariant_holds_after_completed_and_failed`. |
| R-13 | P2 | ✅ | Все правила округления — в `app/core/money.py` (`round_money`, `ceil_to_minor`, `quantize_rate`, `format_rate`); `quotes.py` и `mappers.py` только вызывают их. Хвоста не осталось. |
| R-14 | P2 | ✅ | Курс в расчёте, `breakdown` и ответе совпадает: `quantize_rate` с 8 знаками (R-37); один путь форматирования `format_rate` из `money.py`. |
| R-15 | P2 | ✅ | Кэш `RatesClient` на 60 с (`services/rates.py:10`, `:37-49`); D4 соответствует коду. |
| R-16 | P2 | ✅ | `UNIQUE(client_id, idempotency_key)` (`models/tables.py:175`, миграция 0005); поиск по паре (`repositories/transfers.py:79-85`). Тест: `test_same_key_different_clients_creates_two_transfers`. |
| R-17 | P2 | ✅ | `respond_with_outcome` (`api/v1/transfers.py:51-58`): `COMPLETED` → 201 + `Location`, `FAILED` → 200, иначе 202. Соответствует D5. |
| R-18 | P2 | ⚠️ | `SKIP LOCKED` есть в выборке (`repositories/transfers.py:164-166`), но `reconcile_loop` (`app/main.py:37-38`) закрывает сессию сразу после выборки, и блокировка снимается до обработки. Single-runner через advisory lock (`try_reconcile_lock`, ключ в D32; guard в `reconcile_loop` и `reconcile_once`, пропуск на не-postgres). Очередь без голодания: `_touch` обновляет `updated_at` после каждого разбора (`ORDER BY updated_at` = round-robin). Тест `test_reconcile_lock_single_runner`. |
| R-19 | P2 | ✅ | `check_daily_limit` и `check_limit_definitions` бросают `RATE_UNAVAILABLE`, а не пропускают проверку (`services/limits.py:65-68`, `:76-78`, `:143-146`, `:161-164`). Тесты: `test_unconvertible_limit_currency_fails_closed`, KZT-лимит в `test_limits_definitions.py:190-201`. |
| R-20 | P2 | ⚠️ | D31 приведена к коду: ребро `PENDING → COMPLETED`, акторы `client`/`service`/`provider:<id>`/`reconcile`, переход `PROCESSING → COMPLETED` через сверку. Прямой `PENDING → COMPLETED` без строки `PROCESSING` — валиден (см. R-44). |
| R-21 | P2 | ✅ | `CANCELLED` удалён из кода, сида и `EDGES` (`transfer_machine.py:15-21`, `seed.py:29`). Упоминание в `scripts/chaos_run.py:16` — вендорный код. |
| R-22 | P2 | ✅ | `filter_fresh_rates` применяется к отбору провайдеров (`transfers.py:193-194`, `:270-272`) и к расчёту котировки (`quotes.py:23-31`). |
| R-23 | P2 | ⚠️ | Веса — `Decimal` (`providers.py:17-19`), формула и обоснование в D21. Надёжность доминирует, маржа весит ≈0.5–1.5 балла на сотни. Решение осознанное, не баг; явная сортировка по марже не введена. |
| R-24 | P2 | ✅ | База маржи — `source_amount` (A14), код `providers.py:121`. |
| R-25 | P2 | ✅ | Допуск −60 с, `min(ttl, 6ч)` — A3; неизвестная валюта → `VALIDATION_ERROR` — A13; лимиты в сумме отправителя и `RATE_UNAVAILABLE` при неконвертируемости — A14. |
| R-26 | P2 | ⚠️ | Исправлено: A8 и A10 без `[OPEN]`, A11 без опечатки, LIMITS L1/L2 обновлены. Готово: дубль D24 удалён; ссылки на «Java-референс» убраны из D2/D9/D11 и README (провенанс кейсов в `docs/test-cases/*/SPEC.md` оставлен — это не заявление о наличии референса в архиве). |
| R-27 | P2 | ⚠️ | Тесты есть: `SHOW …` (`test_migrations.py`), `TRANSFER_NOT_FOUND`, 503 вебхука с проверкой отсутствия записи события и неизменности перевода. testcontainers не используются — отклонение задокументировано в D14 и LIMITS L3. |
| R-28 | P2 | ✅ | `description`/`examples` на полях DTO (`app/schemas/*.py`), `responses` на роутах (`app/api/v1/*.py`). Отказ от 401/503 у `POST /transfers` — осознанный (роут их не возвращает). |
| R-29 | P2 | ✅ | `CHECK` в модели (`models/tables.py:126-130`) и в миграции 0005. |
| R-30 | P3 | ⚠️ | В git нет артефактов: `git ls-files` не содержит `var/`, `.env`, `*.zip`. `.gitignore` и `.dockerignore` покрывают `var/`, `*.zip`. Готово: `scripts/build_archive.sh` собирает архив из чистого дерева (`git archive` + проверка на артефакты/секреты); `.env` и зипы не отслеживаются git и в архив не попадают. |
| R-31 | P3 | ⚠️ | `float` убран из бизнес-логики (`limit_sec: int`, веса `Decimal`). Остаётся в настройках и в `ProviderClient(timeout_sec: float)` — граница с httpx и pydantic-settings; задокументировано в D13. |
| R-32 | P3 | ✅ | Удалены: `create_transfer` и `set_status` в репозитории, `ServerError`, параметр `scenario`, `ProviderUnavailableError`. Побочный эффект на поведение `scope` → R-38. |
| R-33 | P0 | ✅ | `_young_unconfirmed` в `run_attempts`: молодая попытка без `payout_id` → `NEEDS_REVIEW`, перебора нет. Тест `test_young_pending_attempt_blocks_next_provider` (`calls == []`, попыток 1). |
| R-34 | P0 | ✅ | `_settle_expired`: сначала `_probe_pending`, терминал только по подтверждённому итогу; неподтверждённые → `NEEDS_REVIEW` с сохранением резерва. Старый тест заменён парой `test_expired_quote_with_confirmed_payout_completes` / `test_expired_quote_with_young_pending_keeps_reserve_for_review`. |
| R-35 | P1 | ✅ | Вариант A (D32): `create_with_recovery → None` = «запись не создана» → попытка `FAILED (PROVIDER_UNREACHABLE)`, сразу следующий провайдер; финал — терминальный `FAILED` (или `COMPLETED`). Тест `test_persistent_5xx_reaches_terminal_failed` (`200 FAILED`, все попытки `PROVIDER_UNREACHABLE`). |
| R-36 | P1 | ✅ | `NEEDS_REVIEW` добавлен в `sum_day_source`/`sum_window_source`. Тест `test_needs_review_counts_toward_daily_limit` (200k + 200k при лимите 300k → `409 DAILY_LIMIT_EXCEEDED`). |
| R-37 | P2 | ✅ | `quantize_rate` по умолчанию 8 знаков, `format_rate` в `money.py`; расчёт, `breakdown` и ответы — 8 знаков. `examples/` возвращены к стартовому состоянию (R-46), точность строк ответов проверяют `test_quotes_api`/`test_mappers`. |
| R-38 | P2 | ✅ | Неизвестный `scope` → `ValidationError` (fail-closed) при расчёте; LIMITS L3 обновлён (только `client`/`direction`). |
| R-39 | P2 | ✅ | `RETRY` из live-ветки продолжает перебор провайдеров (свежие `attempts` после сеттлмента). Тест `test_webhook_failed_moves_to_next_provider` (2 провайдера, `COMPLETED`). |
| R-40 | P2 | ✅ | `stored == False` → `200` без обработки. Покрыто тестом конкурентного дубля (`test_duplicate_webhook_event_single_row` — одна строка на два запроса). |
| R-41 | P2 | ✅ | D29 перечисляет все `try` репозиториев/сидера; разбор `reference` переехал в `core/parse.py::try_parse_uuid`; arch-тест разрешает только `try/finally` без `except`. |
| R-42 | P3 | ✅ | README: запуск с хоста через `DB_HOST=localhost DB_PORT=5433`; `DB_PORT_HOST=5433` добавлен в `.env.example`. |
| R-43 | P2 | ✅ | `start(now_utc())` в момент подтверждения провайдером (оба пути в `run_attempts`, финал `wait_for_settlement` уже был). Тест `test_processing_transition_uses_confirmation_time` (`at - created_at >= 1с` при задержке провайдера 1.5с). |
| R-44 | P2 | ✅ | `from_status` читается из свежего чтения строки под `FOR UPDATE` (`_previous_locked` в `start`/`needs_review`/`_settle`/`repair`). Прямой `PENDING → COMPLETED` — валиден по `EDGES`, в D31 отражён. Тест `test_history_from_status_uses_locked_row` (устаревший объект в памяти → `from == PROCESSING`). |
| R-45 | P3 | ✅ | Новый `scripts/chaos_audit.py` (вендорный `chaos_run.py` не тронут): зависшие `NEEDS_REVIEW` старше grace и `FAILED` с живой выплатой `PROCESSING` в ledger; exit 1 при нарушениях. |
| R-46 | P1 | ✅ | `examples/` возвращены к стартовому состоянию (`git checkout -- examples`); `test_examples` сравнивает курс численно (`Decimal`), точность строк (`0.42000000`) проверяют `test_quotes_api`/`test_mappers`. Отклонение от иллюстрации ТЗ (§5, `0.4200`) зафиксировано в D2/A1/A3: суммы идентичны, отличается только число знаков. |
| R-47 | P2 | ✅ | `X-Scenario` мока (ТЗ §7) сервис не отправлял; сценарии `ok`/`decline`/`error_5xx`/`slow` проверялись только фейками. Добавлен тестовый шов `ProviderClient(scenario)` + `tests/integration/test_mock_contract.py` против живого мока (D34).

---

## P0

### R-01. Неразрешённые попытки: зависшие переводы и резерв (переоткрыт, частично)

- **Закрыто:** пробы `PROCESSING`-попыток без `payout_id` (`app/services/transfers.py:292-308`) и сверка через `reconcile_once` (`reconcile.py:55-65`) действительно вызываются. Попытка старше 60 с помечается `FAILED PROVIDER_UNREACHABLE`, и перевод уходит дальше.
- **Открыто:** два пути, которые делают эту защиту неполной, описаны в R-33 и R-34.
- **Открыто:** очередь сверки. `list_open_transfers` сортирует по `updated_at` (`repositories/transfers.py:161`), а застрявший перевод не меняет `updated_at`, поэтому всегда первый. При >200 застрявших новые переводы не сверяются.
- **Исправить:** закрыть R-33 и R-34; для очереди — сортировать по `last_reconciled_at` (или обновлять `updated_at` при каждой сверке) и вести round-robin.
- **Тест:** падение между POST и записью попытки; `reconcile_once` должен довести перевод до терминала без возврата при живой выплате.

### R-02, R-03 — закрыты (см. таблицу).

### R-33. Молодая неподтверждённая попытка не блокирует переход к следующему провайдеру

- **Требование:** SPEC «Правило неизвестного итога» (следующий провайдер только если записи нет); ТЗ §8 L3 («процесс упал между «отправили» и «записали»»).
- **Код:** `app/services/transfers.py`:
  - `:292-308` — для `PROCESSING`-попытки без `payout_id` выполняется проба; при `missing` и возрасте < 60 с попытка не меняется;
  - `:356-357` — `tried = {a.provider_id for a in attempts}`, попытка с неподтверждённым итогом входит в `tried`, но дальше идёт цикл по `remaining` (остальные провайдеры);
  - `:358-387` — для следующего провайдера создаётся новая попытка и вызывается `create_payout`.
- **Риск:** если выплата у первого провайдера уже создана, но видна с лагом (мок: 3–15 с), сверка может перевести перевод ко второму провайдеру. Итог — две выплаты.
- **Когда возникает:** процесс упал или запрос отменён по `run_with_timeout` (12 с, R-04) между `record_attempt` и записью ответа провайдера; затем `reconcile` запускает `run_attempts` раньше, чем через 60 с.
- **Проверка:** проба `test_probe_young_pending_attempt_moves_to_next_provider` (временный файл, удалён). Первый провайдер завис в `create_payout`, проба вернула `missing`, `run_attempts` вызвал `prov_charlie`. Результат: `calls=['prov_alpha', 'prov_charlie']`, статус `COMPLETED`. Ожидалось `calls == 1`. Проба упала.
- **Исправить:** для молодой неподтверждённой попытки не переходить к другому провайдеру. Перевести перевод в `NEEDS_REVIEW` и ждать пробы до истечения окна (`max visibility-lag` + запас, с учётом ретраев мока). Перейти дальше только после `FAILED` в пробе или по истечении окна.
- **Тест:** см. выше; дополнительно — падение в середине `create_with_recovery`.

### R-34. Истечение котировки возвращает резерв без проверки неподтверждённой выплаты

- **Требование:** SPEC «Правило неизвестного итога»; ТЗ §8 L3 («ни одного зависшего резерва»); D10 (сверка переживает ложь чтений).
- **Код:** `app/services/transfers.py:281-288`. Если котировка истекла и среди попыток нет `payout_id` (`live` пуст), `run_attempts` вызывает `TransferStateMachine.fail()`: статус `FAILED`, резерв возвращается клиенту. Пробы неподтверждённых попыток (`:292-308`) стоят после этой ветки и не выполняются.
- **Риск:** перевод с попыткой `PROCESSING` без `payout_id` или `NEEDS_REVIEW` отменяется и возвращает деньги, а у провайдера выплата может существовать. Клиент получает деньги дважды.
- **Тест, закрепляющий поведение:** `tests/integration/test_race_guards.py:164-190` (`test_expired_quote_without_live_payout_fails_and_releases`). Тест создаёт перевод с `UnknownResult` (попытка остаётся `PROCESSING` без `payout_id`), истекает котировку и ожидает `FAILED` и `reserved == 0`, не спрашивая провайдера. Тест фиксирует небезопасное поведение; его нужно переписать.
- **Исправить:** истечение котировки ограничивает только создание перевода и новые попытки (когда попыток ещё не было). Если есть неподтверждённая попытка, сначала пробы; терминал — только по подтверждённому итогу. Истечение не должно быть основанием для возврата резерва при живой выплате.
- **Тест:** неподтверждённая попытка + истёкшая котировка + проба возвращает `found` (`COMPLETED`) → перевод `COMPLETED`, резерв списан; `missing` до окна → `NEEDS_REVIEW`, резерв не возвращён.

---

## P1

### R-35. Сценарий `error_5xx` не даёт терминального итога в синхронном пути

- **Требование:** ТЗ §8 L2 («в сценариях `ok` / `decline` / `error_5xx` / `slow` перевод доходит до итогового статуса»); D7 («5xx без созданной записи — следующий провайдер»).
- **Код:** `app/services/providers.py:181-184` — любой 5xx кроме 503 → `UnknownResult`. `transfers.py:77-123` — после повторов и проб с `missing` возвращается `None` → `NEEDS_REVIEW` (`:453-463`). В синхронном пути следующий провайдер не пробуется. Дальше перевод продвигается только фоновой сверкой, по одному провайдеру за цикл (`reconcile_interval_sec = 60` и возраст попытки ≥ 60 с).
- **Итог:** при `MOCK_SCENARIO=error_5xx` на всех провайдерах синхронный ответ — `202 NEEDS_REVIEW` (или `PROCESSING`), финал — через ≈ N × (60 с + пробы), где N — число провайдеров направления. Мок в этом сценарии не создаёт выплату вообще, поэтому пробы говорят правду, и ожидание 60 с на каждом шаге не нужно.
- **Проверка:** проба `test_probe_persistent_5xx_sync_outcome`. Все провайдеры отвечают 5xx, проба вернула `missing`. Результат: `202`, статус `NEEDS_REVIEW`, ответ за 5.1 с. Ожидался терминальный статус (`COMPLETED` или `FAILED`). Проба упала.
- **Исправить:** решить политику 5xx явно. Вариант A: `5xx` без `payout_id` после проб с `missing` в окне видимости — «точно не создано», переход к следующему провайдеру сразу (как D7). Вариант B: оставить `NEEDS_REVIEW`, но в синхронном пути довести до терминала (пробы до окна видимости, затем следующий провайдер). Записать выбор в D7 и README. Для реального провайдера вариант A небезопасен без подтверждения; решение должно учитывать R-33.

### R-36. `NEEDS_REVIEW` не учитывается в дневном и оконном лимитах

- **Требование:** ТЗ §8 L3 («суточный лимит клиента»); D11 (агрегация по переводам за окно); D9 (резерв — это деньги клиента).
- **Код:** `app/repositories/transfers.py:191` (`sum_day_source`) и `:217` (`sum_window_source`) фильтруют по `status IN ('PENDING', 'PROCESSING', 'COMPLETED')`. `NEEDS_REVIEW` в список не входит, хотя резерв по нему стоит.
- **Регрессия:** появилась при введении `NEEDS_REVIEW` (R-07). Тест не покрывает.
- **Проверка:** проба `test_probe_needs_review_counts_toward_daily_limit`. Первый перевод 200 000 RUB стал `NEEDS_REVIEW` (резерв стоит). Второй перевод на 200 000 RUB при дневном лимите 300 000 RUB вернул `201 COMPLETED`. Ожидался `409 DAILY_LIMIT_EXCEEDED`. Проба упала.
- **Исправить:** добавить `NEEDS_REVIEW` в оба списка. Тест по образцу пробы.

### R-46. Стартовые `examples/` изменены, точность курса расходится с примером ТЗ

- **Требование:** ТЗ §6 (пример ответа `"rate": "0.4200"`), ТЗ §5 (`rates.json` — 4 знака), ТЗ §9 («ожидаемые значения сверяйте с `examples/`»); D20 («`data/`, `examples/` взяты из стартового набора без изменения логики»).
- **Факт (рабочее дерево, не закоммичено):** в семи файлах `examples/` курс изменён: `"0.4200"` → `"0.42000000"`, `"1.9000"` → `"1.90000000"`, `"113.7500"` → `"113.75000000"`, `"300.0000"` → `"300.00000000"`. `tests/unit/test_quote_calculator.py` сравнивает `f"{result.rate:.8f}"` вместо `.4f`. `data/` не менялись.
- **Риск:**
  1. Автопроверка по примерам ТЗ сравнивает строку курса с `0.4200`. Новый формат ломает её, даже если сумма верна.
  2. Ожидания стартового набора переписаны под реализацию. Проверка перестаёт быть независимой, а D20 это запрещает.
- **Конфликт решений:** R-37 требует точности курса, ТЗ показывает 4 знака. Варианты:
  - A. Считать с 8 знаками, в ответе показывать 4 (как в ТЗ). Ответ тогда не воспроизводит расчёт с точностью до 8 знаков; A3 надо переписать.
  - B. Считать и показывать 8 знаков, стартовые `examples/` вернуть. Тогда строка курса в ответе не совпадает с примером ТЗ, это надо явно описать как отклонение.
  - C. Оставить 4 знака и задокументировать потерю (R-37, пример 36.408 против 36.444 KWD).
- **Исправить:** если изменение намеренное, решение записать в D2, A1 и A3 и согласовать с ТЗ. В любом случае вернуть `examples/` к стартовому состоянию (`git checkout -- examples`) и выбрать вариант A, B или C.
- **Проверка:** `diff -r examples <стартовый архив>/examples` даёт пустой вывод; тест на соответствие строки курса выбранному варианту.

---

## P2

### R-08. Вебхук с известным `reference` и неизвестным `payout_id` — без предупреждения (P2, частично)

- **Требование:** SPEC «Правила вебхуков»: неизвестный `payout_id`/`reference` — `200 + warn-лог + метрика`.
- **Код:** `app/services/webhooks.py`:
  - `:82-89` — неизвестный `reference`: `warn` и счётчик (закрыто);
  - `:99-110` — `payout_id` принадлежит другому переводу: `warn` и счётчик (закрыто);
  - при известном `reference` и `payout_id`, которого нет ни в одной попытке, `find_attempt_by_payout` возвращает `None` (`:99`), предупреждения нет. Дальше вызывается `get_payout` (`:111-114`), и при совпадении `reference` у провайдера перевод может завершиться по выплате, которой нет в попытках (`:119-127`). Деньги при этом корректны, но учёт расходится.
- **Метрики:** `core/metrics.py` хранит счётчики в памяти процесса, экспорта нет (D12 запрещает `/metrics`).
- **Исправить:** `warn` и счётчик при `attempt is None`; привязать `payout_id` к попытке без `payout_id`, если она одна в `PROCESSING`, иначе пометить как аномалию. Решение по экспорту метрик записать в LIMITS или D12.
- **Тест:** вебхук с известным `reference` и чужим `payout_id` — `warn` в логе, перевод не завершается без привязки.

### R-18. `SKIP LOCKED` в сверке не даёт single-runner, очередь голодает (P2)

- **Код:** `app/repositories/transfers.py:155-167` — `with_for_update(skip_locked=True)` при не-sqlite; `app/main.py:37-38` закрывает сессию сразу после выборки, блокировка снимается до обработки. `app/services/reconcile.py:17-18` — обёртка без блокировки.
- **Что защищает:** `run_attempts` берёт `FOR UPDATE` по переводу (`transfers.py:273`) и `transfer_lock` внутри процесса. Двойная сверка не даёт двойной выплаты (финализация атомарна, см. R-34 для риска другого рода), но нагрузка и гонки растут (R-43, R-44).
- **Очередь:** `ORDER BY updated_at` (`repositories/transfers.py:161`). Застрявший перевод при сверке не меняет `updated_at`, поэтому всегда первый. При >200 открытых переводах новые не сверяются.
- **Исправить:** держать блокировку на время обработки партии либо хранить `next_reconcile_at` с `SKIP LOCKED` и обновлять его при каждой попытке; round-robin по переводам.
- **Тест:** 250 открытых переводов, из них 200 застрявших — новый перевод должен попасть в сверку за один проход.

### R-20. Схема переходов в D31 не совпадает с `EDGES` и историей (P2)

- **Код и документ:** `EDGES` (`app/services/transfer_machine.py:15-21`) содержит `PENDING → COMPLETED`; D31 (`docs/DECISIONS.md:273-292`) этого ребра не показывает. D31 показывает `PROCESSING → COMPLETED` только как «provider / webhook», а сверка переводит через `reconcile` и `service` (см. R-44).
- **Акторы:** на схеме `sync-path`, в истории и API — `client`, `service`, `provider:<id>`, `reconcile` (`app/schemas/transfers.py:72-75`). Названия разные.
- **Данные:** `PENDING → COMPLETED` встречается 9 раз (R-44).
- **Исправить:** синхронизировать схему с `EDGES`, акторы — с историей. Пояснить, что `PENDING → COMPLETED` — переход вебхука или сверки, опередивший запись `start()`.

### R-26. Документация: остатки после правок (P2, частично)

- Абзац D24 (`docs/DECISIONS.md:196-200`) дословно повторяет абзац D23 (`:192-194`). Удалить дубль.
- Ссылки на Java-референс: в `docs/ASSUMPTIONS.md:39` («как Java `domain-rules.md`»). В текущем `DECISIONS.md` такой ссылки не найдено, но ссылка на `docs/test-cases/` в `README.md:132` указывает на сценарии «из Java-референса». Референса в архиве нет: либо убрать ссылку, либо приложить.
- `A10` без `[OPEN]`, `A11` без опечатки, LIMITS L1/L2 обновлены — исправлено.
- **Исправить:** удалить дубль, выровнять формулировки в `ASSUMPTIONS.md:39` и `README.md:132`.

### R-27. Тесты не покрывают часть кодов и сценариев (P2, частично)

- Есть: `SHOW shared_buffers/work_mem/max_connections` (`tests/integration/test_migrations.py:194-201`), `TRANSFER_NOT_FOUND` (`test_transfers_api.py:417`), 503 вебхука (`test_webhooks_and_limits.py:224-229`, проверяется только статус, не код).
- Нет: `create_payout` с 503 (`providers.py:181-182`) и код `PROVIDER_UNAVAILABLE` в попытке. Проверка 5xx есть только для `probe_payout` (`test_provider_client.py:120`) и для `create_payout` с 500/504 (`:55`, `:65`).
- testcontainers не используются: отклонение задокументировано в D14 и LIMITS L3.
- **Исправить:** тест на `create_payout` с 503 (`failure_code == PROVIDER_UNAVAILABLE`); проверка `error.code == SERVICE_UNAVAILABLE` вместо одного статуса.

### R-37. Точность курса: четыре знака теряют до 0.1% на обратных курсах

- **Требование:** ТЗ §8 L1 («один и тот же вход → один и тот же расчёт»); корректность денежных расчётов (ТЗ §12).
- **Код:** `app/services/quotes.py:80`, `:87`, `:102` — курс квантуется до 4 знаков через `quantize_rate` (`app/core/money.py:10-12`). Для `RUB→KWD` берётся обратный курс `1/270 = 0.0037`, точное значение `0.00370370…`.
- **Пример:** `10 000 RUB → KWD` (комиссия 1.6% = 160 RUB, к конвертации 9 840 RUB): получатель `36.408 KWD` вместо `36.444 KWD` (потеря 0.10%). Для курсов вида `1/300` потеря ≈1%.
- **Что корректно:** курс в расчёте и в ответе совпадает, клиент воспроизводит расчёт по ответу (A3). Потеря — следствие решения, а не рассогласования.
- **Исправить:** хранить и считать курс с 8 знаками (`NUMERIC(20, 8)` уже есть), в ответе показывать тот же курс с 8 знаками (или 4 знака для прямых курсов и 8 для обратных). Обновить A3 и `format_rate`. Либо явно задокументировать потерю как решение с обоснованием.

### R-38. `scope=category` стал клиентским лимитом; LIMITS L3 утверждает обратное

- **Код:** `app/services/limits.py:112-114` пропускает только `scope == "direction"` с другим направлением. Ветка `scope == "category"` удалена в R-32, поэтому правило с `scope=category` применяется ко всем переводам клиента.
- **Документация:** `docs/LIMITS.md:24` утверждает: «правило с таким scope молча пропускается».
- **Исправить:** либо отклонять неизвестный `scope` при записи (CHECK или валидация сида и миграции), либо явно пропускать с логом, либо применять как клиентский лимит и поправить LIMITS L3. Тест на неизвестный scope.

### R-39. Асинхронный `FAILED` из вебхука не переводит перевод к следующему провайдеру сразу

- **Требование:** D26 («асинхронный FAILED … следующий провайдер пробуется сразу, без ожидания прохода сверки»).
- **Код:** `app/services/webhooks.py:128-133` вызывает `run_attempts`. В `run_attempts` при живой попытке (`transfers.py:309-322`) выполняется `return await wait_for_settlement(...)`, который при `FAILED` возвращает `"RETRY"` (`:405-407`). Этот `RETRY` возвращается из `run_attempts` и вебхуком игнорируется. Цикл по провайдерам в этой ветке не запускается.
- **Итог:** перевод остаётся `PROCESSING`, пока сверка не сделает следующий проход (до 60 с). Синхронный путь (`transfers.py:405-407`, `:450-452`) этой проблемы не имеет: там `continue`.
- **Проверка:** проба `test_probe_webhook_failed_moves_to_next_provider`. Синхронная попытка ушла в `PROCESSING`, затем вебхук `FAILED`. Результат: вызван один провайдер (`calls=['prov_alpha']`), перевод `PROCESSING`. Ожидалось `calls == 2`. Проба упала.
- **Исправить:** в live-ветке `run_attempts` при `RETRY` продолжить перебор провайдеров (вынести перебор в функцию, которую вызывают и синхронный путь, и live-ветка).

### R-40. Гонка дубля вебхука: результат `store_webhook_event` игнорируется

- **Требование:** SPEC («UNIQUE(event_id) — дубль → 200 без эффекта»).
- **Код:** `app/services/webhooks.py:59-60` проверяет дубль через `session.get`, затем `:61-70` вызывает `store_webhook_event` и не смотрит на результат. `repositories/transfers.py:316-345` при `IntegrityError` возвращает `False`. Вызывающий код всё равно вызывает `_process_event`.
- **Итог:** при двух параллельных доставках одного `event_id` оба запроса обрабатывают событие. Переходы защищены атомарным `UPDATE`, поэтому денег это не сдвигает, но правило «дубль без эффекта» не выполнено.
- **Исправить:** если `store_webhook_event` вернул `False`, вернуть `200` без обработки.

### R-41. D29 не описывает все `try/except` вне сервисов и API

- **Требование:** D29 («переводит и рендерит их только advice-слой; валидные `try` — только на инфраструктурных границах»).
- **Код:** `try/except` есть в `repositories/transfers.py` (`:72-74` — `ValueError` при разборе `reference`, `:97-125` — гонка идемпотентности, `:239-278` — `record_attempt`, `:329-345` — `store_webhook_event`), в `repositories/balances.py:41-56` (`ensure_balance`), в `app/db/seed.py:125-130`. Разбор `reference` — не граница по D29.
- **Исправить:** перечислить эти места в D29 или перенести разбор `reference` в `core/parse.py`. Гонки репозитория оставить с явным описанием (как `create_transfer_or_existing`).

### R-43. Время перехода в `PROCESSING` — момент создания перевода

- **Требование:** ТЗ §6 (`GET /transfers/{id}` — «история переходов … кто, когда»); D6 (`transfer_status_history` — источник истории).
- **Код:** синхронный путь `run_attempts` вызывает `TransferStateMachine.start(now, actor)` (`app/services/transfers.py:392`), а `now` — это момент начала запроса, переданный из роута (`app/api/v1/transfers.py:82`, `:103`). Поэтому переход в `PROCESSING` записывается с `at` начала запроса.
- **Данные (chaos `339F3EC7`):** `PENDING → PROCESSING` — 833 записи, все с `at = transfers.created_at` (сдвиг 0.00 с). Провайдер подтверждает выплату через 3–4 с (строки `PROCESSING → COMPLETED provider:*`, сдвиг ≈3–4 с). История говорит, что перевод был в обработке сразу, а не после ответа провайдера.
- **Исправить:** `start` получает `now_utc()` в момент подтверждения провайдером (или в момент фактической записи статуса). Тест: `at` перехода `PROCESSING` больше `created_at` при задержке провайдера.

### R-44. Разрывы цепочки истории: `from_status` не совпадает с предыдущим статусом

- **Требование:** D6 («каждый переход пишется в `transfer_status_history` (from → to, актор, время)»); D31 (схема переходов).
- **Данные (chaos `339F3EC7`, 899 переводов в БД, все переходы сверены с `EDGES` и текущим статусом):**
  - 3 перевода: `PENDING → PROCESSING (client)`, затем `PENDING → COMPLETED (service)`. `from_status` последнего перехода — `PENDING`, хотя перед ним статус был `PROCESSING`.
  - 6 переводов: `PENDING → COMPLETED (provider:*)` без строки `PROCESSING` (alpha 2, foxtrot 3, charlie 1). Перевод ушёл из `PENDING` в `COMPLETED`, пока синхронный путь ещё не записал `start()`.
- **Что проверено:** все переходы допустимы по `EDGES`; последний `to_status` совпадает с текущим статусом; баланс и `reserved` сходятся по клиентам. Деньги не затронуты: `_settle` меняет статус через `UPDATE … WHERE funds_settled = false AND status NOT IN (terminal)`, а `start()` при уже закрытых деньгах ничего не пишет.
- **Что не установлено:** точная причина `from = PENDING` в 3 случаях. Изолированно ORM-объект обновляется корректно: `get_transfer(lock=True)` перечитывает строку (проверено в одной сессии). Вероятный сценарий — гонка между `start()` синхронного пути (не закоммичен) и вебхуком или сверкой без межсессионной блокировки перевода. Нужна воспроизводимая проба.
- **Исправить:** (1) записывать `from_status` из свежего чтения строки под `FOR UPDATE`, а не из памяти; (2) гонку вебхука/сверки и синхронного `start()` закрыть блокировкой перевода на уровне БД (сейчас `transfer_lock` только внутри процесса); (3) решить, пишется ли пропущенный `PROCESSING` при прямом переходе `PENDING → COMPLETED`, и отразить это в D31 (связано с R-20).

---

## P3

### R-45. `chaos_run.py` пропускает часть состояний

- `scripts/chaos_run.py:292-293` (`compare_with_ledgers`): нарушение `failed_but_paid` ищет только `COMPLETED` в ledger. `FAILED`-перевод с живой выплатой `PROCESSING` не попадает в отчёт, хотя провайдер может выплатить позже.
- `scripts/chaos_run.py:224-253` (`classify`, `poll_until_settled`): `NEEDS_REVIEW` относится к «устоявшимся» и не попадает в `stuck_transfers`, в итоговом отчёте его нет.
- Прогон `339F3EC7` этих состояний не содержал (0 `NEEDS_REVIEW`, 0 «фантомных» попыток), поэтому счётчики чистые. Но проверка неполная.
- Скрипт вендорный (`D20`), правки в нём искажают проверку. Вариант: отдельный `scripts/chaos_audit.py` с расширенными проверками, без изменения `chaos_run.py`.

### R-13 (хвост). Форматирование курса вне `money.py`

- `app/api/mappers.py:35`, `:49` и `app/services/quotes.py:299-300` форматируют курс через `f"{rate:.4f}"` (округление по умолчанию в Python — `ROUND_HALF_EVEN`). Курс уже квантован `ROUND_HALF_UP` в `money.py`, поэтому на результат это не влияет, но второй путь округления стоит убрать. Вынести в `money.py` (`format_rate`).

### R-30 (упаковка). Артефакты на диске и архив

- Незакоммиченные артефакты на диске: `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.import_linter_cache/`, `.env`, `payment-stack-starter.zip`, `transfer-core-starter (2).zip`. Не должны попасть в архив (ТЗ §11).
- Исправить: собрать архив из чистого дерева (`git archive` или явный список), `.env` не класть, проверить на секреты.

### R-31 (float на границах). Настройки и HTTP-клиент

- `app/core/settings.py:24`, `:30`, `:31` — таймауты как `float`; `providers.py:137` — `ProviderClient(timeout_sec: float)`. Это граница с httpx и pydantic-settings, не бизнес-логика. Оставить и задокументировать (D13) либо перевести в `timedelta`.

### R-42. Локальный запуск и `.env.example`

- `Settings.db_host` по умолчанию `db`, `.env.example` тоже `DB_HOST=db`. README («Локальная разработка») запускает `uv run alembic upgrade head` с хоста без правки `DB_HOST`, и это не подключится.
- `docker-compose.yml:32` читает `DB_PORT_HOST`, в `.env.example` этой переменной нет (ТЗ: `.env.example` — полный шаблон).
- Исправить: в README указать `DB_HOST=localhost DB_PORT=5433` для запуска с хоста; добавить `DB_PORT_HOST` в `.env.example`.

---

## Документация (сквозные пункты)

Закрыты: дубль D24 удалён, D31 приведена к коду (R-20), ссылки на «Java-референс» убраны из D2/D9/D11 и README (R-26).

## Точки, где ТЗ допускает свободу, но решение должно быть защищено

Не баги, а то, что проверяющий попросит объяснить на защите:

- Синхронный режим с отдачей `202` и таймаут 12 с (D5, A5). Ответ при 30 с — `PROCESSING`, деньги в резерве. Защищается, но R-33/R-35 влияют на то, что происходит после таймаута.
- `422` для `IDEMPOTENCY_CONFLICT`, а не `409` (D8).
- Отказ от запасного провайдера ниже `min_margin_percent` (A6, D7).
- `RATE_UNAVAILABLE` при протухшем прямом курсе, а не подмена кросс-курсом (D25).
- Восемь знаков курса (R-37/R-46): решение в D2/A1/A3/D32 — суммы идентичны примеру ТЗ, отличается только число знаков в строке ответа; стартовые `examples/` не менялись.

## Итог прогона

- `uv run pytest` в compose-сети (полный набор): **171 passed**, 1 warning (deprecation `starlette.testclient`). Пробы аудита — постоянные тесты (`test_young_pending_attempt_blocks_next_provider`, `test_persistent_5xx_reaches_terminal_failed`, `test_needs_review_counts_toward_daily_limit`, `test_webhook_failed_moves_to_next_provider`, `test_expired_quote_with_confirmed_payout_completes`, `test_expired_quote_with_young_pending_keeps_reserve_for_review`, `test_processing_transition_uses_confirmation_time`, `test_history_from_status_uses_locked_row`, `test_reconcile_lock_single_runner`, `test_unknown_limit_scope_rejected_at_write`, `test_direction_scoped_limits_coexist_per_direction`) плюс `tests/integration/test_mock_contract.py`: `ok`/`decline`/`error_5xx`/`slow` против живого мока через `X-Scenario` (R-47, D34).
- Chaos после закрытия пунктов (образ с фиксами, dev-БД пересоздана перед каждым прогоном, мок в `chaos`, `--concurrency 10`): seed 7 — **300, `RESULT: OK`** (282 `COMPLETED`, 18 `FAILED`, 52 дубля); seed 11 — **300, `RESULT: OK`** (285/15, 38 дублей); seed 42 — **300, `RESULT: OK`** (285/15, 42 дубля). Везде 0 нарушений; `scripts/chaos_audit.py` — 0 зависших `NEEDS_REVIEW`, 0 `FAILED` с живой выплатой, exit 0.

## Закрытие R-33…R-46

- R-33: `_young_unconfirmed` — молодая попытка без `payout_id` блокирует перебор, перевод в `NEEDS_REVIEW`.
- R-34: `_settle_expired` — сначала `_probe_pending`, терминал только по подтверждённому итогу.
- R-35: вариант A — `create_with_recovery → None` означает «запись не создана»: `FAILED (PROVIDER_UNREACHABLE)` + следующий провайдер сразу (D7, D32, A17).
- R-36: `NEEDS_REVIEW` в `sum_day_source`/`sum_window_source`.
- R-37: 8 знаков (`quantize_rate`, `format_rate` в `money.py`); R-13-хвост закрыт тем же.
- R-38: неизвестный `scope` — `ValidationError` при расчёте + `CHECK (ck_limit_definitions_scope)` при записи (миграция 0006).
- R-39: `RETRY` из live-ветки продолжает перебор.
- R-40: `stored == False` → `200` без обработки.
- R-41: D29 перечисляет все `try`; разбор `reference` — в `core/parse.py`; arch-тест — только `try/except`.
- R-42: README + `.env.example` (`DB_PORT_HOST`).
- R-43: `start(now_utc())` в момент подтверждения.
- R-44: `_previous_locked` (свежее чтение под `FOR UPDATE`) в `start`/`needs_review`/`_settle`/`repair`; прямой `PENDING → COMPLETED` — валиден, отражён в D31.
- R-45: `scripts/chaos_audit.py` без правок вендорного `chaos_run.py`.
- R-46: `examples/` — стартовые (`git checkout`); сравнение курса в `test_examples` — численное; строки ответов — в `test_quotes_api`/`test_mappers`.
- R-01: закрыт суммой R-33 + R-34 + R-18. R-18: advisory lock + `_touch` round-robin.
- R-08/R-14/R-20/R-26/R-27/R-30/R-31: см. таблицу (warn+метрика; 8 знаков; D31; без дублей и Java-ссылок; 503-тест; `build_archive.sh`; float в D13).

## Воспроизведение проверок

- **Тесты** (хост не видит порты compose, поэтому в compose-сети):
  `docker compose run --rm -e DB_HOST=db -e DB_PORT=5432 --entrypoint sh api -c "cd /app && uv sync --frozen && uv run --frozen pytest -q"`
- **Пробы:** временный файл в `tests/integration/`, отдельная БД `transfer_probe` (`docker exec transfer-core-db-1 psql -U transfer -d transfer -c "CREATE DATABASE transfer_probe"`), запуск с `-e TEST_DATABASE_URL=postgresql+asyncpg://transfer:transfer@db:5432/transfer_probe -v "$PWD/tests:/app/tests"`.
- **Chaos:** `MOCK_MODE=chaos MOCK_SEED=7 docker compose up -d --no-deps mock-provider`, затем `docker compose run --rm --no-deps --entrypoint sh api -c "cd /app && python scripts/chaos_run.py --transfers 300 --api http://api:8080 --mock http://mock-provider:9000 --data data --settle-timeout 200 --ledger-grace 30 --seed 7 --concurrency 10"`. `--no-deps` обязателен: без него `depends_on` пересоздаёт мок с `MOCK_MODE=normal`, и это уже случилось один раз.
- **Независимая сверка:** `/tmp/opencode/xcheck.py` и `/tmp/opencode/stale.py` лежат вне репозитория и не воспроизводимы для другого человека. Предлагаю перенести их в `scripts/audit_consistency.py` и закоммитить (связано с R-45).

## Состояние окружения

- Мок — в compose-стеке (`mock-provider`); chaos-прогоны — в режиме `MOCK_MODE=chaos`.
- Dev-БД `transfer` — песочница chaos (пересоздаётся drop/create + миграции + сид перед сериями); тестовые БД создаются тестами на воркер (`transfer_test_*`, `transfer_fork_*`).
