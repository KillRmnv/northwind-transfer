# Ревью round 2: переводы, идемпотентность, балансы, лимиты

Снимок: **2026-11-19 ~09:05 UTC**, живая БД `transfer` (`docker exec transfer-core-db-1 psql -U transfer -d transfer`)
+ код рабочего дерева. Код и тесты ревьюером **не изменялись**.

Не дублирую `reviews/transfers-idempotency.md` (B1–B10, TR-IDEM-01…14, TR-BAL-01…11,
TR-LIM-01…15, TR-ST-01…11). Там разобраны «первый уровень»: истёкший quote до поиска ключа,
гонка двух запросов с одним ключом, два ключа на один quote как факт, валюта лимита вне пары,
`convert_with_hints(4 арг.)`, `FAILED` на settlement, отсутствие тестов на HOURLY/WINDOW_N/weight.
Здесь — **новые механизмы**: нормализация ключа, канонизация тела, phantom-попытка до записи,
обрыв соединения, ре-скоринг после частичного сбоя, MINUTES/асимметрия конвертации истории,
кросс-валютная гонка лимита, SPLIT-округление, а также разбор зависших переводов chaos.

Severity: **P0** — деньги/лимит не защищены или нарушен контракт идемпотентности с висящим
резервом; **P1** — денежный риск/500 на штатном сценарии/новый денежный кейс без решения;
**P2** — латентный дефект или контрактная неоднозначность. «Факт» без пометки «подтверждено»
выведен из кода; где сделан прямой опыт — указано.

---

## Таблица новых кейсов

### Идемпотентность и ключи

| Severity | Кейс | Вход | Ожидание | Факт | Файл:строка | Тест |
|---|---|---|---|---|---|---|
| **P1** | Регистр и пробелы в `Idempotency-Key` не нормализуются | Один quote 10000 RUB, ключи `k-1`, `K-1`, ` k-1`, `k-1 `, последовательно | Один перевод/один payout (канонизация ключа) либо явный 422 на неканоничный ключ | `get_by_idempotency_key` — точное `==`, `Header` не делаёт `.strip()`; 4 разных ключа → 4 строки `transfers`, 4 резерва по 10000, 4 payout. D8 описывает «тот же ключ», но не определяет канонизацию | `app/api/v1/transfers.py:55-58`; `app/repositories/transfers.py:35-37` | `test_key_case_whitespace_is_one_transfer`: 4 варианты ключа → `len(ids)==1`, `sum(create_payout)==1`; либо зафиксировать 422 |
| **P2** | Граница длины и «пустой» ключ из пробелов | Ключ ровно 128 символов; 129; `"   "`; ключ с NUL/emoji | 128 — ок; 129 — 422 `VALIDATION_ERROR`; `"   "` — 422; не-ASCII — либо ок, либо 422 по контракту | Валидируется только `if not idempotency_key`: `"   "` truthy → принимается; 128 проходит, 129 → `DataError` из `VARCHAR(128)` → 500 (в R1 был только случай >128, границы и whitespace не рассматривались). NUL/emoji принимаются; для PostgreSQL это потенциальная проблема с индексом | `app/api/v1/transfers.py:57-58`; `app/models/tables.py:145` | `test_idempotency_key_length_boundary`: parametrise `[128→201, 129→422, "   "→422]` |
| **P1** | **PHANTOM: падение между POST payout и `record_attempt` + ре-скоринг** | Провайдер `A` принял payout (`po_A`), процесс упал до `record_attempt`; на повторе живые курсы изменились так, что `A` больше не top-1 | Тот же payout `po_A` привязан к переводу, второй выплаты нет (D7/D21) | `create_with_recovery` вызывается **до** `record_attempt`; ключ провайдера детерминирован, но `remaining` строится только из `attempts` в БД и заново `score_providers` по свежим курсам. Нет строки → `tried` пуст → выбирается новый top-1 `B` → **второй payout**, при этом `po_A` жив у провайдера. Chaos-проверка `more than one live payout` ловит это как `double_payouts` | `app/services/transfers.py:313-336` (POST до записи), `:280-292` (ре-скоринг), `:308-309` (`tried` только из БД) | `test_phantom_attempt_rerate_no_second_payout`: бросить исключение сразу после `create_payout`, изменить `live_rates`, повторить → один payout в обоих ledger-ах, одна попытка |
| **P1** | Резерв закоммичен, ответ потерян, повтор упирается в live-курсы | `create_transfer` закоммитил PENDING+резерв, `run_attempts` упал (курсы/провайдер), клиент видит 500; повтор тем же ключом при недоступном `/rates` | Повтор возвращает тот же `transfer_id` и текущий статус, не зависит от `/rates` (D8) | После D25 поиск ключа раньше expiry — ок, но роут **всегда** вызывает `run_attempts` (`transfers.py:73`), а тот читает `live_rates_map()` до работы с существующими попытками → 500 при живом резерве. R1 TR-IDEM-12 описывал 500 как симптом; здесь новый вход — потерянный ответ после commit | `app/api/v1/transfers.py:73`; `app/services/transfers.py:242-273` | `test_replay_after_lost_response_rates_down`: первый POST с падающим `run_attempts`, второй с падающим `RatesClient.fetch` → 200/202, тот же id, резерв не удвоен |
| **P1** | Обрыв соединения клиентом (отмена ASGI-таска) | Клиент отправляет POST, провайдер уже вернул `Accepted`, соединение рвётся до ответа; клиент повторяет **новым** ключом | Либо один перевод, либо понятная 409 «уже выполняется»; деньги не платятся дважды | `CancelledError` не ловится `except Exception`; таск uvicorn отменяется произвольно: если до commit — payout есть, попытки нет (→ R2-IDEM-03); если после commit — перевод и резерв есть, а клиент считает операцию неуспешной и создаёт второй перевод с новым ключом | `app/api/v1/transfers.py:51-84`; `app/services/transfers.py:313-338` | `test_client_abort_after_provider_ack`: отменить таск через `asyncio.Event` после `create_payout`, повторить новым ключом → один payout на аккаунт |
| **P2** | Хеш тела зависит от Python-представления вложенного dict | Тот же ключ, тот же `recipient`, но `body_hash` вызван с dict в другом порядке сборки | Стабильный хеш семантически равного тела | `body_hash` делает `f"{key}={body.get(key)}"`; для `recipient` это `str(dict)` с порядком вставки. На уровне функции подтверждено: два равных `recipient` дают `46c2…` и `4883…`. В API путь пока стабилен порядком полей Pydantic (`Recipient.name/account/bank_code`), но любая перестановка полей в схеме или замена `model_dump()` на `model_dump(mode="json")` сделает **все** старые ключи конфликтующими | `app/repositories/transfers.py:13-15`; `app/schemas/transfers.py:6-11`; `app/api/v1/transfers.py:69` | `test_body_hash_is_canonical_for_nested_dict`: перестановка ключей `recipient` → одинаковый hash; `test_replay_survives_recipient_schema_reorder` |

### Резервы, балансы, SPLIT

| Severity | Кейс | Вход | Ожидание | Факт | Файл:строка | Тест |
|---|---|---|---|---|---|---|
| **P1** | Комиссия в третьей валюте в TARGET-режиме обнуляет маржу | `fee_currency` — ни `source`, ни `target` quote (например комиссия в USD при RUB→THB) | Маржа считается по той же валюте, что и сумма клиента (D7) | `client_fee_source = 0` → маржа `= (0 − fee_provider)/amount`, то есть завышена на всю комиссию клиента; убыточный провайдер проходит фильтр. Расчёт молчалив, в логах нет следа | `app/services/transfers.py:274-279`; `app/services/providers.py` (scoring) | `test_target_fee_third_currency_margin`: два провайдера с разной маржой, комиссия в USD → выбран контрагент с корректной (отрицательной) маржой или перевод FAILED без выплаты |
| **P2** | SPLIT: 8 знаков хранения против 0–3 `minor_units` на границе | Лимит 400000.00 RUB, перевод на 400000.00000001 RUB; или лимит в JPY (0 знаков) и перевод 1 JPY | Граница проверяется по тем же деньгам, что показывает API | `source_amount` из quote хранится `NUMERIC(20,8)`, а `details.limit` уходит через `format_money(..., minor_units)`; клиент видит «ровно лимит», сервер отвечает 409 по невидимой ему дроби. В R1 TR-LIM-05 была та же идея в RUB(2), но не 0/3-значные валюты и не расхождение «показ vs проверка» | `app/core/money.py`; `app/services/limits.py:72,140`; `app/models/tables.py` (NUMERIC(20,8)) | `test_limit_boundary_zero_minor_units`: JPY, граница 1 → 201; 1.00000001 → 409 и в `details` не должно быть «равно» |
| **P2** | `format_money` target-суммы может не совпасть с тем, что ждёт мок-провайдер | `RU-VN` / `RU-TH`, `target_amount` с дробной частью, валюта с 0 minor units | Сумма в POST payout совпадает с квотой и с ledger-ом | `target_str = format_money(target_amount, minor_units[target])` — HALF_UP; мок `amount_is_valid` требует ровно `minor_units` знаков и считает по своей строке. При расхождении провайдер возвращает 422 → `Declined` → все провайдеры перебраны → `FAILED` с возвратом резерва, хотя деньги ушли бы. Chaos ловит это как `amount_mismatch` | `app/services/transfers.py:302`; `app/core/money.py`; `mock_provider/main.py:72-74` | `test_split_amount_matches_provider_precision`: для каждой валюты из `currencies.json` сравнить строку POST и `target_amount` квоты |

### Лимиты

| Severity | Кейс | Вход | Ожидание | Факт | Файл:строка | Тест |
|---|---|---|---|---|---|---|
| **P1** | `MINUTES` (и любой неизвестный период) молча выключает правило | `LimitDefinition(period_type="MINUTES", amount=1000 RUB)` + перевод | Либо поддержка минутного окна, либо доменная ошибка при создании правила | В справочнике только `HOURLY/DAILY/WINDOW_N` (подтверждено запросом: `SELECT * FROM limit_period_types`), а `check_limit_definitions` делает `else: continue` → правило **fail-open**, перевод проходит. D11 перечисляет три периода, но SPEC упоминает минуты — решение не зафиксировано | `app/services/limits.py:104-111`; `app/models/tables.py:189` | `test_unknown_period_type_does_not_fail_open`: вставить MINUTES → 422 на создание правила либо 409 на перевод |
| **P1** | Асимметрия конвертации истории в `check_limit_definitions` | Лимит в EUR, история перевода в KWD, текущий RUB→THB; `rate` строки KWD→JPY | История конвертируется в EUR своим курсом (как в `check_daily_limit` через `live_rates`) | Для строки истории `to_limit_currency(amount, currency, definition.currency, rate, currency, target_currency)` знает только пару `source↔target` quote; третья валюта → `None` → строка **пропускается** из `total`. Для `addition` есть fallback `convert_via_rates`. История не считается, лимит завышен. R1 B6 описывал ту же ошибку в `check_daily_limit` (rate текущего quote), здесь — другой путь (тихий `skip` в defects) | `app/services/limits.py:119-124` против `:125-139` | `test_weighted_limit_history_third_currency`: история 1×KWD + addition в RUB → лимит 409 |
| **P1** | Конвертация лимита умеет только 1 переход, хотя D24 требует BFS до 3 | Лимит в EUR, курсы есть `KWD→RUB`, `RUB→EUR`, но прямого `KWD→EUR` нет | Перевод конвертируется в EUR (как `providers.convert_amount`) | `convert_via_rates` — только `direct` и `inverse`; двухпереходный путь не находится → `addition is None` → `continue` / `return` (fail-open). В R1 B1/B4 была другая причина (4 аргумента / не та пара); здесь fail-open остаётся даже после их фикса, и он же объясняет, почему D24 применён только к провайдерам | `app/services/limits.py:147-164`, `:67-70`, `:134-139`; `app/services/providers.py` (`convert_amount`) | `test_limit_two_hop_conversion_enforced`: {KWD→RUB, RUB→EUR}, лимит 100 EUR, перевод 200 KWD → 409 |
| **P1** | Гонка двух сессий/валют на один клиентский лимит | `c_006`: RUB 750000 + KWD 2500, лимит 400000 RUB; параллельно RUB→THB и KWD→JP на сумму, близкую к лимиту | Лимит общий на клиента и сериализован (D9/D11) | `FOR UPDATE` берётся только на строке баланса **в валюте перевода**; `check_daily_limit`/`check_limit_definitions` агрегируют `transfers` без блокировки клиента. Две сессии читают `spent=0` и обе проходят → суммарно лимит превышен в 2 раза. R1 TR-BAL-11 только указывал на это, здесь конкретный вход и инвариант | `app/services/transfers.py:194-222`; `app/repositories/balances.py:25-31`; `app/services/limits.py:49-76` | `test_limit_serialized_across_currencies_two_sessions`: gather 2 перевода → один 201/202, второй 409, `sum(spent) <= limit` |
| **P2** | Вес и будущие `created_at` размывают окна | `weight=0`/`-1`; строка перевода с `created_at = now + 1h`; HOURLY/WINDOW_N 7/16 | Вес > 0 и валидируется; окно имеет обе границы | `weight` — `Numeric(8,5)` без CHECK/`Field(gt=0)`: `0` отключает правило, `-1` увеличивает остаток. `sum_window_source` имеет только `created_at >= window_start`, без `< now` — будущая строка учитывается всеми окнами (и HOURLY, и 7/16 дней) неограниченно долго. R1 TR-LIM-04 упоминал `window_days=0`, но не отрицательный/нулевой вес и не верхнюю границу окна | `app/services/limits.py:104-140`; `app/repositories/transfers.py:115-140`; `app/models/tables.py:193` | `test_weight_zero_rejected`, `test_window_ignores_future_created_at`: patch `now_utc`, вставить будущую строку → не влияет |

### Состояние и конкурентность

| Severity | Кейс | Вход | Ожидание | Факт | Файл:строка | Тест |
|---|---|---|---|---|---|---|
| **P1** | Transient 500 при создании payout без записи у провайдера отравляет `tried` | Мок: первые 3 POST `500` (**до** создания payout), затем `Accepted` | 5xx без созданной записи → сразу следующий attempt у того же провайдера (D7), без 11 с сна | `create_payout` → `UnknownResult`; `create_with_recovery` тратит `3×(1 s probe) + 3 retry POST + 4×2 s probe ≈ 11 s` жёстких sleep, возвращает `None`; `run_attempts` пишет `PROCESSING/NEEDS_REVIEW` **без payout_id** и возвращает 202, а `tried` на следующем проходе исключает этого провайдера, хотя денег у него нет. R1 TR-ST-08 был про 503→Declined, не про 5xx-без-записи | `app/services/transfers.py:96-111`, `:368-384`, `:308-309` | `test_transient_500_retries_same_provider`: 500,500,Accepted → 201, `len(attempts)==1`, вызовов `get_payout` нет, задержки инъекятся |
| **P1** | Две реплики API: reconciler не однопоточен | Два инстанса API, общая БД, 1 PENDING-перевод | `FOR UPDATE SKIP LOCKED`, один воркер обрабатывает перевод (D10) | `list_open_transfers` — обычный `SELECT` без блокировки; `main.reconcile_loop` поднимается в каждом процессе (`env != test`). Обе реплики вызывают `run_attempts`; детерминированный ключ провайдера спасает от второго payout у того же провайдера, но при разном `live_rates` в репликах выбираются разные провайдеры → два payout + дубли `attempt_no` (нет UNIQUE) | `app/main.py:27-58,65-66`; `app/repositories/transfers.py:77-84,143-171` | `test_two_replicas_reconcile_one_transfer`: два `reconcile_once` параллельно → `sum(create_payout)==1`, `len(attempts)==1` |
| **P2** | Повтор после `FAILED` как часть веерной финализации | `FAILED`, затем одновременно: повтор ключа, FAILED-вебхук, сверка, ещё один FAILED-вебхук | Ровно один `release_reserve`, доступно = старт | `run_attempts` возвращает `FAILED` early (`:252-253`), `_claim` защищает двойной return, но теста на такой веер нет; в R1 TR-BAL-08 было два вызова, здесь четыре и с участием идемпотентного повтора. Дополнительно: повтор после `FAILED` получает 202 без `Location` — HTTP-контракт не зафиксирован | `app/services/transfers.py:252-253`; `app/services/finalize.py:14-23,38-45`; `app/api/v1/transfers.py:73,79-84` | `test_failed_replay_webhook_reconcile_release_once`: gather 4 действий → `reserved==0`, `available==start`, `count(attempts)` без дублей |

---

## Разбор 3 зависших переводов chaos

### Что видно в живой БД и логах

1. **Прямой опрос БД** в момент расследования: `COMPLETED=251, FAILED=55, PROCESSING=1` (позднее
   FAILED стало 56, а PROCESSING-строка `30a672f9-4c63-4aa5-abb1-0684d5ac492b` была удалена
   параллельной активностью родительской сессии — БД живая и пересоздаётся). У единственного
   PROCESSING-перевода:
   - `client_id = c_adv_restart` (не из `data/clients.json` — сценарий «restart»),
   - `created_at = updated_at = 2026-11-19 09:01:15.931579+00` — **ни один компонент больше не писал в строку**,
   - одна попытка `attempt_no=1`, `provider_id=prov_alpha`, `payout_id=po_2c9f2455f9936acd`,
     `status=PROCESSING`, `created_at=updated_at` тоже равны `created_at` перевода.
2. **Тот же payout у мок-провайдера уже `COMPLETED`** — и по `GET /prov_alpha/payouts/po_2c9f2455f9936acd`,
   и по `GET /prov_alpha/payouts?reference=30a672f94c634aa5abb10684d5ac492b` (HTTP 200,
   `reference` совпадает с `transfer_id.hex`). То есть деньги ушли, а перевод не закрыт.
3. **Логи API** показывают рестарт ровно в этот момент: `Shutting down` → `Started server process [21]`
   между 09:01:15 и 09:01:30. Ин-процессный `wait_for_settlement` был убит вместе с таском.
4. **Проходы сверки**: `08:57:57 checked=3 completed=3` (переводы от 23:46 предыдущего дня закрылись
   только через ~9 часов, когда поднялся API), `08:59:57 checked=1 still_open=1`,
   `09:01:30 checked=1 still_open=1`, `09:02:30 checked=0`. Интервал 60 с.
5. Настройки: `reconcile_interval_sec=60`, `mock_provider_timeout_sec=5`, `api_sync_timeout_sec=12`,
   `list_open_transfers(limit=200)`, `Semaphore(8)`, `gather(..., return_exceptions=True)`.

### Гипотеза

**Первичная причина — рестарт API в момент ожидания settlement + слишком медленная и «слепая»
сверка.**

- Payout уже создан и попытка закоммичена как `PROCESSING`. После рестарта единственный путь
  закрытия — `reconcile_loop`. Он бежит раз в 60 с, берёт не более 200 строк и обрабатывает их
  по 8 параллельно. Для строк без живой попытки `_reconcile_one` вызывает `run_attempts`, внутри
  которого жёсткие `asyncio.sleep`: ~11 с в `create_with_recovery` и до 12 с в
  `wait_for_settlement` (`poll_interval=0.5`). Проход по ~100 открытым переводам при семафоре 8
  занимает минуты, поэтому эффективный цикл «проход + 60 с сна» существенно больше 60 с. Часть
  переводов из пачки 300 попросту **не попадает в следующий проход до истечения 300 с** — ровно
  наблюдаемые 3 штуки.

- Вторая половина проблемы — **`still_open` как тупик**. В `_reconcile_one` для живой попытки
  делается **один** `get_payout`, при `None` — один `find_payout`. Если payout ещё не виден
  (`visibility_lag` 3–15 с в chaos) или ещё не урегулирован (settle delay до 25 с), код просто
  `stats["still_open"] += 1` и **ничего не пишет в БД** — даже `updated_at` не двигается. Следующая
  попытка только через весь цикл сверки. Если запись провайдера потеряна (in-memory store мока при
  рестарте) или lag больше интервала, перевод висит **навсегда** с резервом. D10 прямо требует
  сверки с `admin/ledger`, но `reconcile.py` его не вызывает нигде.

- Третья — **молчаливые ошибки**. `gather(..., return_exceptions=True)` в `main.py:45-47` выбрасывает
  исключения по переводам из статистики и не логирует их. В логах того же периода виден
  `IntegrityError` (`webhook_events_status_fkey`, `status='BOGUS'`) — то есть точки, где обработка
  может упасть, реально существуют; упавший перевод останется PROCESSING и не появится в отчёте
  сверки (`checked=0` при непустом множестве открытых переводов — это наблюдалось в 09:02:30).

Инвариант при этом не нарушается: `PROCESSING`-перевод считается «в работе», резерв ожидаем,
поэтому chaos даёт 0 `stuck_reserves`/`balance_invariant`, но фиксирует `stuck_transfers`.

### Предлагаемый фикс (конкретный)

**1. Сверка должна быть быстрой и видеть ошибки** — `app/main.py:27-58`.

```python
async def reconcile_loop() -> None:
    while True:
        try:
            async with session_factory() as session:
                pending = await list_open_transfers(session, limit=None)  # убрать 200
            semaphore = asyncio.Semaphore(32)                             # было 8
            async def run_one(transfer_id, sem=semaphore) -> dict:
                async with sem, session_factory() as session:
                    transfer = await transfer_repo.get_transfer(session, transfer_id)
                    if transfer is None:
                        return {}
                    return await reconcile_transfer(session, settings, transfer, now_utc())
            batches = await asyncio.gather(
                *[run_one(item.transfer_id) for item in pending], return_exceptions=True
            )
            for item, batch in zip(pending, batches):
                if isinstance(batch, BaseException):                        # НЕ swallowing
                    logger.error("reconcile transfer failed",
                                 extra={"transfer_id": str(item.transfer_id),
                                        "error": repr(batch)})
            ...
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("reconcile pass failed: %s", exc)
        await asyncio.sleep(settings.reconcile_interval_sec)  # default 5–10 в settings.py:31
```

Заодно `list_open_transfers` (`app/repositories/transfers.py:77-84`) — `FOR UPDATE SKIP LOCKED`
и `ORDER BY created_at` (приоритет oldest), чтобы вторая реплика не билась над тем же переводом.
Семантика `env != test` для запуска цикла оставить.

**2. Убрать тупик `still_open`: опрашивать провайдера несколько раз внутри прохода + читать ledger** — `app/services/reconcile.py:59-92`.

```python
import asyncio
from app.core.types import PayoutState

async def provider_state(client, provider_id, reference, payout_id):
    for _ in range(3):                      # перекрыть visibility_lag 3–15 c и stale_read 1–3 c
        state = await client.get_payout(provider_id, payout_id)
        if state is None:
            state = await client.find_payout(provider_id, reference)
        if state is not None and state.status != "PROCESSING":
            return state
        await asyncio.sleep(1.0)
    entries = await client.admin_ledger(provider_id, reference)   # новый метод, см. ниже
    if entries:
        last = entries[-1]
        return PayoutState(payout_id=last["payout_id"], reference=reference,
                           status=last["status"], failure_code=last.get("failure_code"))
    return None
```

`ProviderClient.admin_ledger` (`app/services/providers.py`) — `GET {base}/{provider_id}/admin/ledger`
с фильтрацией `entry["reference"] == reference` (этот же путь использует `chaos_run.fetch_ledgers`).

Дальше в `_reconcile_one`:

```python
state = await provider_state(client, attempt.provider_id, transfer.transfer_id.hex,
                             attempt.payout_id or "")
if state is None:
    # ни payout, ни ledger-строки: деньги не уходили, безопасно закрыть и вернуть резерв
    await finalize_mod.fail_transfer(session, settings, transfer, now)
    await session.commit()
    stats["failed"] += 1
    return
if state.status == "PROCESSING":
    age = (now - attempt.created_at).total_seconds()
    if age > settings.reconcile_stale_after_sec:     # новая настройка, 120 с
        logger.warning("payout stuck at provider",
                       extra={"transfer_id": str(transfer.transfer_id), "age_sec": age})
    stats["still_open"] += 1                        # резерв оставляем: money-safe
    return
# COMPLETED / FAILED — существующая ветка без изменений
```

Важно: в приведённом коде `provider_state` **уже** опрашивает ledger перед возвратом `None`.
Если убрать этот шаг, `state is None` из-за `visibility_lag` даст ложный `FAILED` и chaos поймает
`failed_but_paid`. Ledger — арбитр, как и требует D10.

**3. Делать успешную сверку заметной**: писать `updated_at` при каждом визите (`set_status` с тем же
статусом) или завести `last_reconciled_at`; тогда строка с `updated_at == created_at` через 70 с —
явный сигнал, что воркер её не видит. Плюс метрика `reconcile_still_open_total` с возрастом.

**4. На уровне приёма запроса** (дополнительно к R2-IDEM-03/R2-ST-01): не вызывать `run_attempts`
на идемпотентном повторе, если по переводу уже есть живая попытка или статус терминален —
`app/api/v1/transfers.py:73`. Это снимет dependency на `/rates` и лишние 11 с при повторе после
потерянного ответа.

Проверка фикса: тот же chaos 300 с `--settle-timeout 300`; ожидаемые `stuck_transfers=0` при
сохранённых `double_payouts=0`, `stuck_reserves=0`, `balance_invariant=0`. Регрессионный тест —
`test_reconcile_recovers_transfer_after_api_restart` (создать PROCESSING+attempt, «убить» in-process
поллинг, подменить `get_payout` на COMPLETED, прогнать `reconcile_once` → COMPLETED, `reserved=0`).
