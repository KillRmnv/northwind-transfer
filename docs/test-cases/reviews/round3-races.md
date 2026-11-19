# Раунд 3 — гонки: что осталось после фиксов (машина состояний, release_reserve, funds_settled)

Дата: 2026-11-19. Код и тесты НЕ менялись (единственный новый файл — этот отчёт).
Рабочее дерево до начала уже содержало чужие незакоммиченные правки (`git status`:
`app/api/*`, `app/core/*`, `app/db/seed.py`, `app/main.py`, `app/models/tables.py`,
`app/services/providers.py`) — их не трогал,Diff не анализировал.

Прочитано: `app/services/transfers.py` (все 455 строк), `app/services/transfer_machine.py`,
`app/repositories/transfers.py`, `app/repositories/balances.py`, `app/services/webhooks.py`,
`app/services/reconcile.py`, `app/main.py`, `app/services/limits.py`,
`app/api/v1/transfers.py`, `app/models/tables.py`,
`tests/integration/test_webhooks_and_limits.py`, `tests/load/test_load.py`.

## Метод и стенд

- `localhost:5433` из хоста: TCP accept проходит мгновенно, но handshake asyncpg/psql
  стабильно висит до таймаута (проверено 3 раза, 15–60 с). По инструкции работал
  **изнутри compose-сети**: `docker exec transfer-core-api-1` → `db:5432` коннектится
  сразу, БД здорова (16 сессий, запросы текут).
- Параллельно в том же compose-проекте чужой прогон: `transfer-core-api-1` + mock,
  в логах PG массовые `chaos-*` INSERT-дубли. Чтобы не мешать, все repro шли в
  **изолированной БД `transfer_test_races`** (drop/create, свой сид), dev-БД `transfer`
  не трогал. Тайминги под шумом соседа не снимал — перф-строки помечены как статика.
- Repro-скрипты (`/tmp/repro_races.py`, `/tmp/repro_s4.py`) в репозиторий не коммитил,
  гонялись через `docker cp` + `docker exec -i transfer-core-api-1 ... python`.
  Моки провайдера/рейтов — патчингом `ProviderClient.*` и `RatesClient.fetch`
  по образцу `tests/integration/test_transfers_api.py:patch_provider`.

## Сводная таблица

| ID | Severity | Сценарий | Как воспроизвести | Ожидание | Факт | Файл:строка | Тест |
|----|----------|----------|-------------------|----------|------|-------------|------|
| RACE-01 | High | Параллельный дубль webhook с одним `event_id` | `/tmp/repro_s4.py`: барьер 2 одновременных POST `/webhooks/prov_alpha` с тем же `event_id`, 6 раундов, внутри compose-сети | Оба 200 (идемпотентность) | Один 200, второй `IntegrityError: duplicate key webhook_events_pkey` → 500. **6/6** | `app/services/webhooks.py:52-63`, `app/repositories/transfers.py:288-313` | `test_webhooks_and_limits.py:54` (`test_duplicate_event_is_idempotent` — последовательный, гонку не ловит) |
| RACE-02 | Medium | Конвой на строчных локах + сверка не догоняет 300 висящих | Статика + арифметика (ниже). Живой замер пропущен из-за чужого chaos-прогона | Пропускная способность деградирует плавно | (a) `lock_client` держится через rates-HTTP; (b) lock перевода держится через весь provider-цикл 12 с+; (в) `list_open` без `SKIP LOCKED`, лимит 200/проход, 8 слотов × ~12–30 с → проход по 200 ≈ 300+ с > интервала 60 с | `app/services/transfers.py:181-233`, `:244-322`; `app/main.py:29-60`; `app/repositories/transfers.py:156-163`; `app/services/reconcile.py:17-24`; `app/core/settings.py:30-31` | `tests/load/test_load.py:32` (10 шт, все COMPLETED-быстрые — конвоя не видит) |
| RACE-03 | Low | `repair("COMPLETED")` ставит статус без `consume_reserve` — резерв виснет | Статика: достижим только на drift-строках (`funds_settled=true` + не-терминал), которых атомарный `_settle` не производит | Ремонт чинит и деньги, и статус | Статус чинится, `reserved` остаётся навсегда (нет ни consume, ни release) | `app/services/transfer_machine.py:87-92`, `app/services/reconcile.py:81-89` | нет (покрыть нечем без seed-дрифта) |
| RACE-04 | Info | `create_transfer_or_existing`: `IntegrityError`, но winner не виден → `raise` | Статика: окно закрыто тем, что PG блокирует второй INSERT до коммита/аборта первого; deferred-ограничений нет | 500 без утечки (транзакция откатывается целиком, резерв внутри неё) | Подтверждено кодом; утечки нет, т.к. `reserve` (transfers.py:227) и INSERT в одной транзакции с одним `commit` (:233) | `app/repositories/transfers.py:82-88`, `app/services/transfers.py:227-233` | — |
| HOLD-01 | — | 5+ параллельных POST с одним ключом | `/tmp/repro_races.py` S1: `asyncio.gather` 5 POST, один ключ, PG | 1 transfer, проигравшие откатывают свой резерв | **Держит**: коды 5×201, `distinct_ids=1`, `available=490000.00 reserved=0.00` | `app/services/transfers.py:176-234` | `test_webhooks_and_limits.py:273` |
| HOLD-02 | — | Два разных ключа на один quote; «победитель упал между резервом и коммитом» | S2: 5 ключей × 20000 на `c_002` (баланс 50000) | Ровно 2 успеха, лимит/баланс держат; падение победителя = откат одной транзакции | **Держит**: `201,201,409,409,409`, `available=10000.00 reserved=0.00`, трансферов ровно 2 | те же + `app/repositories/balances.py:25-32` (`FOR UPDATE`) | `test_webhooks_and_limits.py:116` |
| HOLD-03 | — | Двойной `complete` (`wait_for_settlement` vs reconcile): держит ли `funds_settled` | S3: два параллельных `run_attempts` одной выплаты, затем оба видят COMPLETED | Один consume, одна строка COMPLETED | **Держит**: оба вернули `COMPLETED`, попыток 1 (`prov_alpha/COMPLETED`), `funds_settled=true`, `reserved=0`, `available` сошёлся до копейки | `app/services/transfer_machine.py:94-104` (атомарный `_settle`) | `test_webhooks_and_limits.py:403` |
| HOLD-04 | — | `can()` на устаревшем объекте; webhook COMPLETED во время `run_attempts` | S3 + код: входной `get_transfer(lock=True)` сериализует, дальше решают атомарные предикаты | Stale-`can()` даёт лишь холостой UPDATE (0 строк) | **Держит**: `set_status_if_unsettled` и `_settle` проверяют `funds_settled=false + not TERMINAL` в самом UPDATE; `repair`/`force_status` тоже с гардом | `app/services/transfer_machine.py:33-44`, `app/repositories/transfers.py:135-147`, `:123-131` | — |
| HOLD-05 | — | `release_reserve` проигравшего при уже завершённом переводе → «уход в плюс» | S1/S2 балансы точные | Резерв/релиз строго спарены, сериализованы локом баланса | **Держит**: релиз всегда парный своему резерву в той же сериализованной цепочке; `consume`/`release` вне пары — только через `_settle`-гард | `app/repositories/balances.py:45-66`, `app/services/transfer_machine.py:46-76` | `test_webhooks_and_limits.py:273,116` (балансные assert'ы) |
| HOLD-06 | — | `refresh()` после чужого коммита | Ревью всех `refresh()`: `transfer_machine.py:43,59,75`, `transfers.py:454` | Каждый `refresh` читает объект, записанный **той же** транзакцией; чужое перечитывается через `get_transfer(lock=True)` | **Не найдено**: кросс-воркерная несвежесть нейтрализуется rowcount-гардами, а не свежестью объекта | см. HOLD-04 | — |
| HOLD-07 | — | Unknown-исход → уход к следующему провайдеру с живой выплатой у предыдущего (дабл-пэйаут) | Код: `UnknownResult` → `NEEDS_REVIEW` + стоп цикла; `None` (missing) → следующий провайдер только после `probe=missing` | Дабл-пэйаут только если врёт probe мока/провайдера | **Держит по дизайну** (не тред-гонка) | `app/services/transfers.py:358-364`, `:394-404`; `create_with_recovery:67-118` | `test_async_failed_falls_back_without_reconcile` (`test_webhooks_and_limits.py:232`) |

## Детали находок

### RACE-01 (High). Параллельный дубль webhook → `IntegrityError` → 500
`handle_webhook` (`app/services/webhooks.py:52-63`): pre-check `session.get(WebhookEvent,
event_id)` → `store_webhook_event` (`app/repositories/transfers.py:298-313`:
снова `session.get` + `add` + `flush`, **без** обработки `IntegrityError`).
Два одновременных запроса с одним `event_id` оба проходят оба check'а до коммита
(изоляция READ COMMITTED ничего не видит) → второй `flush` падает
`UniqueViolationError(webhook_events_pkey)`. В отличие от трансферного пути
(`create_transfer_or_existing`, `transfers.py:82-88`, где `IntegrityError`
перехватывается и резолвится в winner), здесь исключение летит наверх.
Живой факт: с барьером (истинная одновременность) — **6/6**: один `code=200`,
второй `EXC IntegrityError`. Без барьера (микрозадержка) оба 200 — поэтому
существующий `test_duplicate_event_is_idempotent` (:54) гонку не ловит.
Денег не трогает (запись аудита одна, второй запрос падает до `_process_event`),
но ломает at-least-once контракт: провайдер будет ретраить → усиление шторма
именно под нагрузкой. Примечание: стора идёт **до** TERMINAL-раннего-возврата
(:75-79), поэтому дубли по давно завершённым переводам тоже падают (все 6 раундов
моего repro били в уже-COMPLETED перевод — и всё равно 500 у второго).
Направление чинки (код не меняю): `try/except IntegrityError` вокруг `flush`
в `store_webhook_event` по образцу `create_transfer_or_existing`, либо
`INSERT ... ON CONFLICT DO NOTHING`.

### RACE-02 (Medium). Конвой на локах: `lock_client` + lock перевода через сетевые вызовы, сверка от 300 висящих отстаёт навсегда
Три механизма складываются:
1. `create_transfer` (`app/services/transfers.py:181-233`): `lock_client`
   (`SELECT ... FOR UPDATE`, `repositories/transfers.py:29-36`) берётся **до**
   `live_rates_map` (HTTP, :190) и всех проверок — весь клиент сериализован
   и стоит на латентности rates. Баланс-лок (`get_balance(lock=True)` → `FOR UPDATE`,
   `balances.py:25-32`) аналогично.
2. `run_attempts` (`transfers.py:244-322`): `get_transfer(lock=True)` (:244), первый
   `commit` только на :322 — между ними `live_rates_map` (HTTP), скоринг и
   `create_payout` (HTTP, до `api_sync_timeout_sec=12` с + ретраи
   `create_with_recovery` до ~11 с слипов). Строка перевода заблокирована **десятки
   секунд**; webhook (`webhooks.py:99-102`) и reconcile (`reconcile.py:82-88`) на
   тот же перевод встают в очередь на тот же лок. Это корректно (сериализация),
   но 8 слотов семафора (`main.py:36`) массово паркуются.
3. `reconcile_loop` (`main.py:29-60`): `list_open_transfers` без лока вообще
   (`repositories/transfers.py:156-163` — ни `FOR UPDATE`, ни `SKIP LOCKED`),
   лимит прохода 200 (`reconcile.py:17`), дальше 8 воркеров. Арифметика отставания:
   200 висящих × ~12–30 с / 8 слотов ≈ 300+ с на проход при интервале 60 с
   (`settings.py:31`); при 300 висящих очередь растёт каждый цикл, свежие PENDING
   от API ждут. Плюс сверка и API `run_attempts` дублируют работу по одним строкам
   (второй сериализованно «джойнится» — корректно, но слот занят).
   Итог: не потеря денег, а лавинообразная деградация латентности/отставания
   именно под параллельной нагрузкой — то, что chaos-300 с быстрыми моками не видит.

### RACE-03 (Low). `repair("COMPLETED")` не трогает деньги
`reconcile.py:86-87`: если `complete()` вернул False — `repair("COMPLETED")`.
`repair` (`transfer_machine.py:87-92`) → `force_status` (`transfers.py:123-131`):
ставит статус без `funds_settled` и без `consume_reserve`/`release_reserve`.
Разбор достижимости: `complete()` при свежем не-терминальном локе возвращает False
⇔ `_settle` затронул 0 строк ⇔ `funds_settled` уже true (остальные предикаты
выполнены). Комбинация `settled=true + PROCESSING` атомарным `_settle` не
производится (он всегда пишет статус+флаг вместе, `:94-104`) — только legacy-дрифт
до фикса 7576498. Если такая строка существует и `reserved > 0`, после `repair`
получаем `COMPLETED + settled=true` с вечно висящим `reserved` (ни consume, ни
release уже недостижимы — `can()` с терминала всегда False). Денег не создаёт,
но навсегда морозит резерв. Узко, но это единственный найденный путь
«статус ушёл — деньги не осели».

## Топ-3 самых опасных

1. **RACE-01** — единственная **доказанная живой гонкой** (6/6) с видимым клиенту
   эффектом (500 на легитимный ретрай вебхука). Бьёт по надёжности ingest'а именно
   в шторм, лечится 5 строками по готовому образцу рядом.
2. **RACE-02** — архитектурный конвой: длинные держания строчных локов через HTTP
   + лимит/семафор сверки дают никогда-не-догоняющее отставание при сотнях висящих.
   Денег не теряет, но превращает параллельную нагрузку в стояние.
3. **RACE-03** — узкий, но единственный путь рассинхрона «статус vs деньги»
   (вечный `reserved`). Страхует только то, что drift-строк быть «не должно».

## Что проверено и держит (не чинить)

- Одинаковый ключ ×5: один `transfer_id`, балансы точные (живое S1).
- Разные ключи ×5 на предел баланса: ровно 2×201 + 3×409, `reserved=0` (живое S2).
- Краш победителя между резервом и коммитом невозможен как утечка: резерв+INSERT
  в одной транзакции (`transfers.py:227-233`), откат PenTest-чист.
- Двойной `complete` разных воркеров: один `consume` (атомарный `_settle`,
  живое S3: одна попытка, `funds_settled=true`).
- Stale `can()`: все переходы подтверждены rowcount-предикатами с гардами
  (`set_status_if_unsettled`, `_settle`, `force_status`).
- Webhook COMPLETED конкурентно с `run_attempts`: тот же атомарный settle;
  `_sync_attempt` подтягивает попытку (покрыто `test_webhook_completed_updates_attempt_status`).
- Конкурентные `run_attempts` одного перевода (API + webhook-FAILED + reconcile):
  сериализуются входным `get_transfer(lock=True)` (`transfers.py:244`), опоздавший
  видит свежую попытку и джойнится в `wait_for_settlement` — фан-аута попыток
  с дублем `attempt_no` живое S3 не показало (одна строка попытки).
