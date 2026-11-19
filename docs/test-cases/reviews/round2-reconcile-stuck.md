# Ревью-раунд 2: почему `chaos_run` оставляет переводы в `PROCESSING` > 300 с

- Дата: 2026-11-19. Состояние кода: `HEAD = 1baaa9b`. Автор отчёта код не менял. Номера строк — по `HEAD`; в рабочем дереве параллельно идут незакоммиченные правки других ревью-раундов (`transfers.py`, `providers.py`, `limits.py`, `repositories/transfers.py`), поэтому строки ниже могут смещаться на 1–4.
- Контекст: `chaos_run: 300 transfers, seed 11, concurrency 10, settle-timeout 300`. Застряли:
  `61cf53f8-9e34-49f2-9762-ea0e4a1c5d16`, `054d1a85-4873-4da6-8a8b-b8cf0ec3d109`, `c943d3ce-4b50-4974-8b6f-f76376c667f3`.
- Метод: чтение кода + SQL по контейнеру `transfer-core-db-1` + логи API живого стека + изолированное воспроизведение на SQLite (`/tmp/opencode/repro_stuck.py`, вне репозитория) + A/B «старый образ → HEAD».
- Важно: на момент разбора стек был поднят из **устаревшего образа**: в контейнере отсутствовал `ProviderClient.probe_payout`, а `live`-фильтр был старым — по ключевым файлам это совпадает с `c35b896` (образ собран ~22:57 UTC, chaos-прогон шёл 23:45–23:48 UTC на нём же). `HEAD` (`1baaa9b`, 23:44 UTC) и промежуточный `097de63` уже содержали исправление, но в контейнер оно не попало. После пересборки стека на `HEAD` те же три перевода закрылись на первом же проходе сверки. Ниже это разделено: «дефект, который дал застревание» и «что осталось в `HEAD`».

## Итог

**Корневая причина (P0, в `HEAD` исправлена):** live-фильтр попыток считал «живой» любую попытку с `payout_id`, включая `FAILED`; а `wait_for_settlement` при `state.status == "FAILED"` делал `break` и безусловно возвращал перевод в `PROCESSING`. В результате перевод, у которого единственная попытка завершилась асинхронным `FAILED` (нормальный chaos-сценарий мока), навсегда оставался в `PROCESSING`: сверка на каждом проходе заново опрашивала уже провалившуюся выплату, получала `FAILED`, снова ставила `PROCESSING` и не переходила к следующему провайдеру и не финализировала перевод. Резерв при этом оставался занятым.

**Что сделано в `HEAD`:** фильтр стал status-aware (`payout_id AND status IN ('PROCESSING','COMPLETED')`), ветка `FAILED` возвращает `RETRY`, вызывающий код делает `continue` на следующего провайдера, а `tried`/`remaining` не дают повторно ходить к тому же провайдеру. Живое подтверждение: попытки, висевшие 9 часов, после деплоя `HEAD` получили вторую попытку у другого провайдера и завершились за 3 с (`reconcile pass: completed 3`).

**Что осталось (P1/P2):** (1) живая попытка с постоянным `404/None` от провайдера висит вечно — нет счётчика/возраста/эскалации; (2) `reconcile_loop` молча глотает исключения отдельных переводов; (3) батч 200 + семафор 8 + до 10–12 с на перевод дают проход > 300 с; (4) нет single-runner/`SKIP LOCKED`; (5) дедлайн `wait_for_settlement` берётся от начала `run_attempts`, а не от начала попытки; (6) худший `create_with_recovery` — до ~60 с на провайдера под блокировкой строки; (7) `NEEDS_REVIEW`-перевод для `chaos_run` выглядит «застрявшим».

## Хронология и доказательства

| Время (UTC) | Событие | Источник |
|---|---|---|
| 2026-11-18 23:45–23:48 | создано 300 переводов (45/89/94/72 по минутам) | `select date_trunc('minute', created_at)` |
| 23:46:08 / 23:46:23 / 23:46:49 | созданы три перевода | `transfers.created_at` |
| 23:46:11.73 / 23:46:32.77 / 23:46:57.19 | попытка №1 переведена в `FAILED`, `payout_id` сохранён | `payout_attempts.updated_at` |
| 23:46:11.18 / 23:46:32.17 / 23:46:56.62 | получены `FAILED`-вебхуки провайдера | `webhook_events.received_at` |
| до 08:57 | статус трёх переводов `PROCESSING`, `updated_at` растёт **каждую минуту** | 08:55:54.400 / 08:55:54.975 / 08:55:55.556 |
| 08:52:50 … 08:55:55 | каждый проход сверки: `{"checked":3,"still_open":3}`, интервал ~61 с | `docker compose logs api` |
| ~08:57 стека пересобран на `HEAD` | созданы попытки №2 (`prov_charlie`, `prov_bravo`, `prov_bravo`) | `payout_attempts` |
| 08:57:57 | три перевода `COMPLETED`, резерв списан; `reconcile pass: completed 3` | `transfers.status`, лог API |

Ключевая деталь: между 23:46 и 08:57 переводы **не меняли статус, но меняли `updated_at`** — именно потому их не ловит watchdog «нет обновлений N минут». Три апдейта в 08:55:54.40 / 54.97 / 55.55 идут с шагом ~0.5 с — это ровно один `await asyncio.sleep(0.5)` внутри `wait_for_settlement`, затем чтение `FAILED` и повторная запись `PROCESSING`. Это и есть сигнатура старого цикла.

Состояние попыток до фикса (SQL):

```
61cf53f8… | 1 | prov_foxtrot | FAILED | BENEFICIARY_BANK_REJECTED | po_9011cbff9f594db6
054d1a85… | 1 | prov_alpha   | FAILED | COMPLIANCE_HOLD_REJECTED  | po_6428970d8d89f208
c943d3ce… | 1 | prov_charlie | FAILED | BENEFICIARY_BANK_REJECTED | po_adeb80cc476eceed
```

При этом `transfers.status = PROCESSING` у всех трёх. Мок на тот момент отдавал по этим `payout_id` `status=FAILED` — расхождения «локально `FAILED`, у провайдера `PROCESSING`» не было.

## Таблица кейсов

Severity: **P0** — деньги/контракт, **P1** — застревание или дубли, **P2** — масштабируемость/наблюдаемость, **OK** — проверено, дефекта нет.

| ID | Sev | Кейс | Ожидание | Факт | Файл:строка |
|---|---|---|---|---|---|
| R2-01 | **P0** | Перевод `PROCESSING`, единственная попытка `FAILED` с `payout_id` (асинхронный отказ провайдера: 2–9 с / 12–25 с, доля `1-success_rate`) | Сверка переводит на следующего провайдера либо финализирует `FAILED`, резерв освобождается | **Причина инцидента.** Старый live-фильтр `a.payout_id` считал такую попытку живой; `wait_for_settlement` на `FAILED` делал `break` и ставил `PROCESSING`. Цикл повторялся каждые 60 с бесконечно | `c35b896`: `transfers.py:265`, `:444-449`, `reconcile.py:24`, `:47-49`; **HEAD исправлено**: `transfers.py:257-269`, `:490-491`; `reconcile.py:60`, `:88-91` |
| R2-02 | P1 | Живая попытка `PROCESSING` + `payout_id`, провайдер отвечает `404`/`None` постоянно (рестарт мока, потеря ledger, сбой) | После N проходов/возраста — терминал или явный `NEEDS_REVIEW`, резерв не висит (D1) | `still_open` каждый проход, счётчика/возраста/порога нет; воспроизведено: 3 прохода → `still_open=1`, `PROCESSING`, резерв 6440 RUB | `reconcile.py:66-71`; `providers.py:192-200`; `repositories/transfers.py:77-84` |
| R2-03 | P1 | Любое исключение в обработке одного перевода (`/rates` 5xx, отсутствующий тариф → `StopIteration`, битый JSON от провайдера) | Остальные переводы обрабатываются, ошибка логируется и считается | `asyncio.gather(return_exceptions=True)` кладёт исключение в список, но цикл учитывает только `dict` — ни лога, ни метрики; такой перевод висит вечно | `main.py:45-53`; `transfers.py:270-272`; `rates.py:14-18` |
| R2-04 | P1 | Два процесса API одновременно запускают `reconcile_loop` | Single-runner (`advisory lock`/`FOR UPDATE SKIP LOCKED`), один проход | `list_open_transfers` без блокировки; `lifespan` создаёт loop в каждом процессе; возможны дубли `run_attempts` и попыток | `main.py:30-58`; `reconcile.py:17-18`; `repositories/transfers.py:77-84`; D10 |
| R2-05 | P2 | Батч и параллелизм сверки: 200 открытых, семафор 8, до 10–12 с на перевод | Проход укладывается в интервал, свежие переводы не голодают | `200/8 × 10–12 с ≈ 250–300 с` на проход + 60 с паузы; при 300 открытых лимит 200 отдаёт только самые старые (`ORDER BY created_at`), новые ждут | `main.py:34`, `:45-47`; `reconcile.py:17`; `repositories/transfers.py:77-84` |
| R2-06 | P2 | `wait_for_settlement` получает `started`, зафиксированный в начале `run_attempts` | Таймаут синхронного ожидания — на каждую попытку | После нескольких `Declined`/recovery дедлайн уже в прошлом: цикл не выполняется ни разу, перевод сразу `PROCESSING` и ждёт следующий проход (≥60 с, при R2-05 — больше) | `transfers.py:339-348`, `:418-427`, `:465-467` |
| R2-07 | P2 | Время recovery при недоступном провайдере | Ограниченное время на попытку, строка не заблокирована на HTTP | `create_with_recovery`: 3×(sleep 1 с + probe ≤5 с + POST ≤5 с) + финал 4×(2 с + ≤5 с) ≈ до 61 с на провайдера; всё под `FOR UPDATE` строки перевода | `transfers.py:41-58`, `:69-111`, `:249`; `settings.py:24` |
| R2-08 | OK | Попытка `FAILED` **без** `payout_id` (синхронный `422 Declined`) | Следующий провайдер; если все исчерпаны — `FAILED`, резерв возвращён | Работает: `tried`/`remaining` исключают провайдера, в конце `fail_transfer`. Воспроизведено: 2 попытки, `FAILED`, `reserved=0` | `transfers.py:352-367`, `:445-449` |
| R2-09 | OK | `PENDING` без попыток (сбой между созданием перевода и первой попыткой) | Сверка создаёт попытку и доводит до терминала | Работает: `start_no=1`, попытка создаётся, `COMPLETED`, резерв списан (C4 ниже) | `reconcile.py:55-58`; `transfers.py:299-351` |
| R2-10 | OK | `PROCESSING` после синхронного таймаута (`api_sync_timeout_sec=12`) | Попытка остаётся live, сверка добирает результат | Работает: `set_status(PROCESSING)`, попытка `PROCESSING+payout_id`, следующий проход завершает | `transfers.py:492-497`, `:60`; `reconcile.py:60-92` |
| R2-11 | OK | `lost_webhook` (12%), `visibility_lag` 3–15 с, `stale_read` 1–3 с | Итог добирается поллингом/сверкой | Работает: лаг меньше интервала сверки; `stale_read` отдаёт `PROCESSING`, а не ложный терминал | `behavior.py:79-87`; `mock_provider/main.py:46-47`, `:60-61`; `transfers.py:467-491` |
| R2-12 | P2 | `UnknownResult` (обрыв/5xx после отправки): попытка `PROCESSING` с `payout_id=NULL`, `failure_code=NEEDS_REVIEW`, перевод `PROCESSING` | Явный статус «на ручной проверке» либо срок, после которого резерв не висит | `chaos_run` видит `PROCESSING` → `pending` → `stuck_transfers`; для наблюдателя это ложное «застревание» | `transfers.py:368-384`; `scripts/chaos_run.py:224-232`, `:444-449` |
| R2-13 | P2 | `COMPLETED`-вебхук по переводу с попыткой `PROCESSING` | Попытка тоже `COMPLETED` (аудит, live-фильтр) | Перевод терминален, но попытка остаётся `PROCESSING`: в БД 29 таких строк из 300 | `webhooks.py:110-115`; `finalize.py:26-35` |
| R2-14 | P2 | Повторный проход сверки по уже решённому переводу | Никаких вызовов провайдера, статус не меняется | На `HEAD` так и есть (C1: второй проход — `failed=1`, вызовов нет) | `reconcile.py:60-64`; `transfers.py:252-253` |
| R2-15 | P2 | `FAILED`-вебхук блокирует ответ | Быстрый `200`, обработка вне запроса | `run_attempts` может ждать до 12 с; таймаут доставки мока 10 с → до 5 дублей | `webhooks.py:116-119`; `mock_provider/webhooks.py:13`, `:26` |

## Корневая причина (детально)

В сборке, на которой шёл chaos-прогон, было две ошибки, усиливающие друг друга.

1. **«Живая» попытка определялась только по `payout_id`** (`git show c35b896:app/services/transfers.py` — строка 265; `…/reconcile.py` — строка 24):

   ```python
   live = [a for a in attempts if a.payout_id]   # FAILED тоже «живая»
   ```

   Попытка с `status=FAILED` и `payout_id=po_…` попадала в `live`, поэтому ни `reconcile`, ни `run_attempts` не доходили до выбора следующего провайдера.

2. **`FAILED` от провайдера не завершал обработку** (`c35b896:app/services/transfers.py:444-451`):

   ```python
   if state.status == "FAILED":
       break                      # выходим из poll-цикла
   ...
   await transfer_repo.set_status(session, transfer, "PROCESSING", now_utc())
   await session.commit()
   return "PROCESSING"            # перевод «жив», попытка уже FAILED
   ```

   `fail_transfer` не вызывался, новый провайдер не выбирался. Следующий проход сверки повторял ту же ветку. Отсюда 9 часов цикла с записью `PROCESSING`/`updated_at` каждые ~60 с.

3. **Кто создал именно такой исходный набор:** мок в chaos-режиме для части выплат планирует асинхронный отказ (`behavior.chaos_settle`: `final_status=FAILED` с вероятностью `1-success_rate`, задержка 2–9 с или 12–25 с). Если отказ наступает внутри `wait_for_settlement` HTTP-запроса `POST /transfers`, попытка записывается `FAILED` с `payout_id`, а перевод остаётся `PROCESSING` — ровно состояние трёх указанных ID. Вебхук `FAILED` приходил одновременно (`webhook_events`), но и он в старом коде шёл в `run_attempts` → тот же live-фильтр → тот же `break`.

На `HEAD` оба места исправлены:

```python
# app/services/transfers.py:257
live = [a for a in attempts if a.payout_id and a.status in ("PROCESSING", "COMPLETED")]
...
# app/services/transfers.py:490
if state.status == "FAILED":
    return "RETRY"        # вызывающий делает continue на следующего провайдера
```

```python
# app/services/reconcile.py:60
live = [a for a in attempts if a.payout_id and a.status in ("PROCESSING", "COMPLETED")]
if not live:
    status = await run_attempts(...)   # перейти к следующему провайдеру / FAILED
```

A/B на живых данных:

- до деплоя: `PROCESSING` + `updated_at` каждую минуту, `still_open: 3`;
- после деплоя `HEAD`: попытка №2 у другого провайдера, `COMPLETED` за 3 с, `reconcile pass: {"checked":3,"completed":3,"still_open":0}`.

## Предлагаемый фикс

### Обязательный минимум (регрессия P0)

Уже в `HEAD`; нужно закрепить тестами и не откатывать:

1. `live` — только `payout_id AND status IN ('PROCESSING','COMPLETED')` и в `run_attempts`, и в `_reconcile_one`.
2. `FAILED` в `wait_for_settlement` → `RETRY`; вызывающий цикл делает `continue` (создаёт попытку у следующего провайдера), а после исчерпания — `fail_transfer` + `release_reserve`.
3. Никогда не писать `PROCESSING` поверх попытки, которая уже `FAILED`, без создания новой попытки.

### Надёжность (устраняет оставшиеся способы превысить 300 с)

4. **Эскалация «нет ответа провайдера» (P1).** В `_reconcile_one` при `state is None` (особенно `probe_payout == "missing"`) вести счётчик подряд идущих промахов. Практично без миграции: возраст `attempt.created_at`/`updated_at`. Политика: после `RECONCILE_STUCK_SEC=300` (5 проходов) — финальный `find_payout`, затем `fail_transfer(... failure_code="PROVIDER_NO_RESPONSE")` + `logging.warning` + метрика `reconcile_stuck_total`. Это закрывает D1 (резерв не висит) и R2-02.
5. **Не терять ошибки по переводам (P1).** В `run_one` обернуть `reconcile_transfer` в `try/except Exception`, логировать `transfer_id` и инкрементить `reconcile_errors_total`; в `total` считать `errors`. Сейчас исключения молча проглатываются (`main.py:45-53`).
6. **Single-runner и честная очередь (P1).** `pg_try_advisory_lock` на проход (или `SELECT … FOR UPDATE SKIP LOCKED` в `list_open_transfers`); `ORDER BY updated_at` вместо `created_at`; `LIMIT` из настроек; обрабатывать чанками (`semaphore × 4`), чтобы длинный проход не откладывал следующий.
7. **Бюджет времени (P2).** `asyncio.timeout(RECONCILE_TRANSFER_TIMEOUT=15)` на один перевод; семафор 8 → 16–32 (пул 10+20 позволяет); `resolve_unknown` — сначала probe, потом sleep; общий `asyncio.timeout` на `create_with_recovery` (сейчас worst-case ~61 с на провайдера, `transfers.py:96-111`).
8. **Свежий дедлайн на попытку (P2).** В `wait_for_settlement` передавать `started=now_utc()` в каждом вызове из цикла (`transfers.py:339`, `:418`), а не `now` из начала `run_attempts`.
9. **RETRY не должен выходить наружу (P2).** В early-live ветке (`transfers.py:258-269`) при `RETRY` не возвращать его вызывающему, а проваливаться в выбор провайдера — иначе ретрай откладывается на целый интервал сверки.
10. **Наблюдаемость для chaos (P2).** В `scripts/chaos_run.py` при `stuck_transfers` печатать `attempts` из `GET /transfers/{id}` (последняя попытка: `status`, `failure_code`, `payout_id`) — тогда причина видна сразу, а `NEEDS_REVIEW` можно классифицировать отдельно (`review`), а не как `pending`.

## Тесты (5 конкретных)

Все — в новых файлах `tests/integration/test_reconcile_stuck.py` и `tests/unit/test_reconcile_outcomes.py`; используют существующие `session`, `api`, `freeze_rates`, `patch_provider`, `make_quote`, `post_transfer` из `tests/conftest.py` и `tests/integration/test_transfers_api.py`. Существующий `test_reconcile_completes_lost_webhook` покрывает только `PROCESSING → COMPLETED` и остаётся зелёным на старом коде — поэтому нужен отдельный тест на `FAILED`-попытку с `payout_id`.

1. **`test_reconcile_failed_attempt_with_payout_terminates`** (P0-регрессия; на старом коде падает).
   - Arrange: `PROCESSING`-перевод, одна попытка `FAILED` с `payout_id="po_fail"`; `patch_provider(get=FAILED, find=no_find, create=Declined)`.
   - Act: `reconcile_once(session, app_settings, now_utc())`, commit; затем второй проход.
   - Assert: `status == "FAILED"`, `reserved == "0.00"`, `stats["failed"] == 1`; попытка №2 создана у другого провайдера; второй проход `reconcile_once` не видит перевод (`checked == 0`) и не делает вызовов `get_payout/create_payout` (spy-список пуст). На старом коде: `PROCESSING`, резерв `6440.00`, `wait_for_settlement` вызван.

2. **`test_run_attempts_does_not_repoll_failed_payout`** (unit, SQLite).
   - Arrange: та же фикстура, `monkeypatch.setattr(transfers, "wait_for_settlement", spy)`.
   - Act: `run_attempts(session, settings, transfer_id, now)`.
   - Assert: `spy` не вызван; попытка №2 создана / перевод терминален; `PROCESSING` не записан поверх `FAILED`.

3. **`test_reconcile_escalates_when_provider_returns_404`** (P1, после п.4 фикса).
   - Arrange: live-попытка `PROCESSING+payout_id`, `get_payout`/`find_payout` → `None`, 5 проходов с шагом `RECONCILE_STUCK_SEC`.
   - Assert: перевод вышел из `PROCESSING` (по выбранной политике `FAILED`/`NEEDS_REVIEW`), `reserved == 0`, в `caplog` есть `reconcile_stuck_total`/warn с `transfer_id`; шестой проход провайдера не опрашивает.

4. **`test_reconcile_pending_without_attempts_and_after_sync_timeout`** (P2, закрепляет R2-09/R2-10).
   - Arrange: `PENDING` без попыток; fake `create → Accepted`, `get → PROCESSING` дольше `api_sync_timeout_sec`.
   - Assert: первый проход создаёт попытку и оставляет `PROCESSING`; после `get → COMPLETED` второй проход завершает `COMPLETED`, резерв `0`; для «все `Declined`» — `FAILED` и резерв возвращён.

5. **`test_reconcile_batch_isolation_and_no_starvation`** (P1, после п.5–6 фикса).
   - Arrange: 210 открытых переводов; 200 самых старых с `get → None` (вечный 404), 10 новых с `get → COMPLETED`; один перевод с `get`, бросающим `RuntimeError`.
   - Assert: 10 новых завершены не позже второго прохода (порядок `updated_at`, не `created_at`); исключение не ломает проход и попадает в лог/`reconcile_errors_total`; `checked + errors == 210`.

Дополнительно (не входит в пятёрку): `test_two_reconcile_workers_single_runner` — два параллельных прохода на PG, `get_payout` по одному переводу вызван один раз (advisory lock).

## Изолированное воспроизведение

Скрипт `/tmp/opencode/repro_stuck.py` (SQLite, `Base.metadata.create_all` + `app.db.seed`, фейковый `ProviderClient`, живые курсы с `/rates`). Результаты:

```
[C1] HEAD: attempt FAILED + payout_id, all remaining providers decline
    elapsed=0.04s stats={'checked': 1, 'completed': 0, 'failed': 1, 'still_open': 0}
    transfer=FAILED balance(available=500000, reserved=0)
    attempts=[(1,'prov_foxtrot','FAILED','po_fail'), (2,'prov_charlie','FAILED',None), (3,'prov_bravo','FAILED',None)]
    second pass stats={'checked': 1, 'completed': 0, 'failed': 1, 'still_open': 0}

[C2] OLD build algorithm on the same data
    live=[('prov_foxtrot','FAILED')] -> PROCESSING
    transfer=PROCESSING balance(available=493560, reserved=6440)

[C3] HEAD: attempt FAILED without payout_id -> next provider accepts -> COMPLETED, reserved=0
[C4] HEAD: PENDING without attempts -> attempt created -> COMPLETED, reserved=0
[C5] HEAD: live PROCESSING, provider 404 forever -> still_open=1 три прохода, PROCESSING, резерв 6440
[C6] HEAD: live PROCESSING, provider FAILED -> retry -> FAILED по всем, reserved=0
```

C1/C2 — прямая регрессия: на старом алгоритме тот же вход даёт `PROCESSING` и занятый резерв, на `HEAD` — терминал за 0.04 с. C5 — оставшийся P1 (R2-02).

Замечание по инфраструктуре тестов: интеграционные тесты используют одну БД `transfer_test_main` для не-xdist запусков (`tests/conftest.py:20-33`). Параллельный запуск двух `pytest` (в другом процессе шли прогоны) роняет `create_transfer` на `assert fresh is not None`; поэтому в этом раунде выводы по `HEAD` подтверждены изолированным SQLite-скриптом и живым A/B, а не полным прогоном suite.

## Что проверять при следующем chaos-прогоне

- `GET /transfers/{id}` для каждого `stuck_transfers`: последняя попытка должна быть не `FAILED` с `payout_id` при `PROCESSING`-переводе.
- После прогона: `select t.status, a.status, count(*) … group by 1,2` — не должно быть `PROCESSING + FAILED` старше 5 минут.
- Счётчик `reconcile_errors_total` и warning-логи `reconcile pass` по каждому переводу (сейчас ошибки не видны).
- `reserved` по клиентам: нулевой через `settle_timeout + ledger_grace`.
