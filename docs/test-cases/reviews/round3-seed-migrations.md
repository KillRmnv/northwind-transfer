# Раунд 3 — ревью сидера и миграций

Область: `app/db/seed.py`, `alembic/versions/*.py`, `alembic/env.py`,
`scripts/entrypoint.sh`, `data/*.json`, `app/models/tables.py`,
`tests/integration/test_migrations.py`, `docs/DECISIONS.md` (D16–D19).

Метод: статический разбор + живая проверка внутри compose-сети
(`docker compose run --rm -e DB_HOST=db -e DB_PORT=5432 ... api`),
scratch-БД `transfer_seedprobe3` / `transfer_raceprobe`.
Каждый факт ниже воспроизведён, а не выведен из чтения кода.

## Таблица кейсов

| # | Sev | Кейс | Вход | Ожидание | Факт | Файл:строка | Тест |
|---|-----|------|------|----------|------|--------------|------|
| S1 | P1 | Конкурентный двойной сид дублирует окна обслуживания | пустая БД, `alembic upgrade head`, два `seed()` параллельно (`asyncio.gather`, отдельные сессии) | тот же набор `(provider_id, from_at, to_at)`, без дублей | `maintenance_windows`: 31 → **61** (+30, окна `prov_alpha` задвоились). UNIQUE `(provider_id, from_at, to_at)` нет, `seed_provider_windows` делает delete-then-insert без блокировки | `app/db/seed.py:108-127`; `alembic/versions/0001_initial.py:93-106` | нет |
| S2 | P2 | Баланс из JSON не применяется при повторном сиде | существующий `balances(c_002,RUB)=50000`, вызов `seed_client_balances` с `{"RUB": "99999999.00"}` | определено: либо overwrite, либо задокументированный insert-only | молча игнорируется: осталось `50000.00000000`. Семантика нигде не зафиксирована (D16–D19, D28 молчат) | `app/db/seed.py:142-153` | нет |
| S3 | P2 | Операторская правка провайдера затирается ресидом | `UPDATE providers SET enabled=false WHERE provider_id='prov_bravo'`, затем `seed()` | определено: сид — источник истины или правки живут | `merge` перезаписал на JSON (`enabled` снова `true`); балансы при этом insert-only (S2) — две разные семантики в одном сиде без документации | `app/db/seed.py:130-139` | нет |
| S4 | P2 | Удаление направления/провайдера из JSON оставляет висячие связи | вставлена лишняя `provider_directions(prov_bravo, RU-KW-BANK)`, затем `seed()` | лишняя связь удалена (полная синхронизация) либо задокументировано merge-only | связь выжила: `5 → 5`. `seed_providers` только `merge`, delete нет; то же для направлений, удалённых из `tariffs.json` (код страны/метод останутся в справочниках) | `app/db/seed.py:130-139` | нет |
| S5 | P2 | `test_seed_is_idempotent` проверяет только счётчики справочников | два последовательных `seed()` | counts currencies/providers + counts окон + неизменность балансов после списания | тест считает только `currencies` (10) и `providers` (7). Мутация «баланс перезаписывается» и дубли окон (S1) его не роняют. Баланс `c_001` после `-1000` и ресида остался `499000` — это поведение тест не фиксирует | `tests/integration/test_migrations.py:111-142` | есть, слабый |
| S6 | P2 | Модели не содержат ни одного индекса из миграции | `alembic check` на свежей `upgrade head` БД | пустой diff | `FAILED: New upgrade operations detected` — автогенерация предлагает **удалить все 9 индексов** (`ix_quotes_direction`, `ix_transfers_client_created`, `ix_transfers_quote`, `ix_payout_attempts_*`, `ix_webhook_events_reference`, `ix_maintenance_windows_provider`, `ix_provider_directions_direction`, `ix_limit_definitions_client`). Следующая автогенерация-миграция испортит схему, если её примут не глядя | `app/models/tables.py:1-199` (нет `Index`); `alembic/versions/0001_initial.py:92-221` | частично (`test_migration_creates_expected_indexes` проверяет БД, не модели) |
| S7 | P3 | D19 обещает несуществующий индекс | D19: индексы `(client_id, created_at)` и `(client_id, category, created_at)` на `transfers` | оба индекса в миграции | `(client_id, category, created_at)` нет ни в миграции, ни в моделях; колонки `category` в `transfers` вообще нет (есть только в `limit_definitions`). D19 невыполним в текущем виде | `docs/DECISIONS.md:137`; `app/models/tables.py:132-149`; `alembic/versions/0001_initial.py:166-167` | нет |
| S8 | P2 | `parse_time` падает на битых датах без маппинга | `parse_time("not-a-date")`, `"2026-13-45T99:99:99Z"`, `""` | `ValueError` → 500/аварийный останов сида (так и есть, но осознанно?) | `ValueError: Invalid isoformat string` / `month must be in 1..12`. В сиде исключение до единственного `commit` в `seed()` — вся транзакция откатывается (атомарно, хорошо), но `entrypoint.sh` под `set -e` падает целиком. Одна битая строка окна роняет весь сид включая валюты/направления | `app/db/seed.py:33-37,168-174`; `scripts/entrypoint.sh:1-5` | нет |
| S9 | P3 | Пустой/отсутствующий `data/` роняет entrypoint | `seed()` с пустым каталогом | ясная ошибка конфигурации / пропуск сида | `FileNotFoundError: .../currencies.json`. `load_json` без обработки, `set -e` → контейнер в CrashLoop. Маунт `./data:/data:ro` + `DATA_DIR` по умолчанию `./data` — расхождение путей тоже никак не диагностируется | `app/db/seed.py:40-41`; `scripts/entrypoint.sh:1-5`; `docker-compose.yml:60,89` | нет |
| S10 | P3 | `MINUTES` живёт в коде и сиде, но не в SPEC/D11 | `LIMIT_PERIOD_TYPES` включает `MINUTES`; `limits.py` ветка `MINUTES` | единый перечень периодов везде | сид сеет 4 периода, `check_limit_definitions` поддерживает `MINUTES` (`app/services/limits.py:109`), D26 фиксирует добавление — а `docs/test-cases/limits/SPEC.md` и D11 перечисляют только `HOURLY, DAILY, WINDOW_N`. Дрейф документации, не кода | `app/db/seed.py:30`; `docs/test-cases/limits/SPEC.md:41`; `docs/DECISIONS.md:86,213` | нет |
| S11 | P3 | Гонка двух реплик в entrypoint | две реплики одновременно: `alembic upgrade head` + `seed` | advisory lock / leader election / идемпотентный сид при гонке | лока нет: `env.py` без advisory lock, сид без идемпотентности окон (S1). Проверка: два `upgrade head` по пустой БД одновременно — оба `rc=0` (гонку миграции воспроизвести не удалось, DDL сериализовался); гонка **сидов** воспроизведена (S1: 31→61). Комбинация «миграция ок + двойной сид» — реальный сценарий двух реплик | `scripts/entrypoint.sh:1-5`; `alembic/env.py:26-43` | нет |
| S12 | P3 | entrypoint без ожидания БД | `DB_HOST` недоступен при старте | retry/wait-for-db с таймаутом | `set -e`: первая же команда падает → перезапуск контейнера. В compose спасает `depends_on: service_healthy`, вне compose — CrashLoop вместо ожидания | `scripts/entrypoint.sh:1-5`; `docker-compose.yml:92-96` | нет |
| S13 | P4 | `build_direction`/`build_provider` vs JSON — полное покрытие | все ключи `tariffs.json` (19 путей) и `providers.json` (13 путей) | каждый ключ где-то читается | покрытие 1:1: `direction_id/source.*/target.*/payout_method/fee.*/amount_limits.*/min_margin_percent/quote_ttl_sec` и `provider_id/directions/currency/fee.*/limits.*/success_rate_24h/p95_latency_ms/enabled/maintenance_windows` — всё потребляется (`directions`/`maintenance_windows` — в `seed_providers`, остальное — в конструкторах) | `app/db/seed.py:65-105,130-139` | нет (держится честным словом) |
| S14 | P4 | Типы/UNIQUE/server_default миграции против моделей | `alembic check` (типы), прямые запросы (констрейнты) | полное соответствие | типы, FK, `UNIQUE (idempotency_key, event_id, uq_limit_client_scope_period)` совпадают; `server_default=true()/false()/"0"/"1"/""` в миграции vs client-side `default=` в моделях — значения эквивалентны, `alembic check` по типам молчит. `downgrade -1` убирает `funds_settled`, повторный `upgrade head` возвращает — цикл downgrade/upgrade работает | `alembic/versions/0001_initial.py:15-221`; `0002_funds_settled.py:15-23`; `app/models/tables.py` | частично (индексы/таблицы — да; downgrade-цикл — нет) |

Проверенные и **не** подтвердившиеся гипотезы (факт вместо слуха):

- Последовательный двойной сид идемпотентен по счётчикам: все 12 таблиц, включая `maintenance_windows` (31→31) и `provider_directions` (25). S1 — только про конкурентный запуск.
- Списанный баланс ресид не возвращает: `499000` после `seed()` — операционные деньги сид не трогает (хорошо, но см. S2/S5: это случайность реализации `if row is None`, а не зафиксированный контракт).
- Наивная дата в окне (`"2026-11-05 22:00:00"` без зоны) не падает: `parse_time` пришивает UTC (`app/db/seed.py:35-36`).
- `seed()` атомарен: единственный `commit` в конце (`app/db/seed.py:174`), `ValueError` из `parse_time` откатывает всё, частичного сида нет.
- Новая валюта в балансах клиента вставляется (`c_002/USD=10.00`) — insert-only работает в одну сторону.

## Подтверждённые баги

1. **S1 (P1).** Конкурентный сид дублирует `maintenance_windows` (31→61). Нет UNIQUE, нет блокировки. Две реплики с одним entrypoint — штатный путь получения дублей.
2. **S6 (P2).** Модели без индексов: `alembic check` требует снести все 9. Мина под следующую автогенерацию.
3. **S4 (P2).** Сид merge-only: удалённые из JSON связи/направления остаются в БД навсегда.
4. **S2/S3 (P2).** Неопределённая семантика перезаписи: провайдеры — overwrite, балансы — insert-only, и то и другое недокументировано. Операторская правка провайдера молча умирает при рестарте.
5. **S8/S9 (P2/P3).** Любая битая строка `data/` (дата окна, отсутствие файла) роняет весь entrypoint целиком под `set -e`.

## Не покрыто (тестов нет, нужны)

- S1: два последовательных + два параллельных `seed()`, сравнение наборов `(provider_id, from_at, to_at)` и `count(*)`.
- S2/S3: списать баланс → ресид → `available/reserved` без изменений; сменить `enabled` → ресид → зафиксировать текущую (overwrite) семантику или поменять её.
- S4: удалить direction из JSON-набора → ресид → связь отсутствует.
- S6: тест `alembic check` (пустой diff) в CI — ловит дрейф моделей/миграции навсегда.
- S8: битая дата окна → весь `seed()` откатывается (атомарность), entrypoint возвращает ненулевой код.
- S9: пустой `data_dir` → внятная ошибка конфигурации, а не `FileNotFoundError`.
- S10: либо убрать `MINUTES` из сида, либо обновить SPEC/D11 (решение + тест ветки `limits.py:109`).
- S13: тест покрытия ключей JSON (`tariffs`/`providers`/`clients`/`currencies`): каждый ключ fixture-файлов читается сидом — ловит молчаливое игнорирование новых полей.
- S14: downgrade-цикл `head → -1 → head` на scratch-БД.
- S11/S12: гонка двух `upgrade head` + двойной сид при старте двух реплик; ожидание готовности БД в entrypoint.
