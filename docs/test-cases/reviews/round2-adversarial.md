# Ревью-раунд 2: adversarial-сценарии сверх `chaos_run`

Дата: 2026-11-19. Снимок: `HEAD 1baaa9b`; живой стек `api/db/mock-provider` на
`localhost:8080/5433/9000`. Код в рамках этого ревью **не менялся**.

Важно: во время ревью рабочее дерево параллельно правили другие сессии
(`app/services/transfers.py`, `limits.py`, `providers.py`, `repositories/transfers.py`).
Живые прогоны ниже выполнены на развёрнутом контейнере стенда; текущее дерево
проверялось статически. Образ мог быть собран до `HEAD` (см. оговорку в
`round2-reconcile-stuck.md`), поэтому там, где это важно, строка помечена «HEAD»
или «текущее дерево»; выводы по `quotes.py`/`webhooks.py`/`finalize.py` совпадают
с текущим деревом (эти файлы не менялись).
Незакоммиченные правки, которые уже влияют на выводы:

- `create_transfer` теперь берёт `lock_client` (закрывает кросс-валютную гонку лимита,
  но **не** гонку идемпотентности — см. ADV-01);
- `run_attempts` получил ветку `unresolved` для `NEEDS_REVIEW` без `payout_id`;
- `convert_amount`/`check_limit_definitions` получили часть фиксов из `round2-limits`;
- `quotes.py`, `webhooks.py`, `reconcile.py`, `finalize.py`, `_parse_state` не менялись —
  все находки по ним актуальны и для текущего дерева.

Метод: чтение кода + живые HTTP-прогоны + SQL по `transfer-core-db-1`. Для сценариев
создавались временные клиенты `c_adv_*`; после проверки их переводы, попытки, котировки,
балансы и клиенты удалены. Записи в in-memory ledger мок-провайдера остались
(`ADVRACE*`, `ADVLIMIT*`, `ADVQUOTE*`, `ADVRESTART*`, `ADVDEC*`, `ADVFUTURE*`);
`chaos_run.py` фильтрует ledger по префиксу `CH<run_id>`, поэтому на chaos они не влияют.

Отчёт не претендует на новизну всех пунктов: совпадения с `reviews/round2/*.md` и
первым раундом отмечены ссылками (`C-04`, `C-07`, `C-09`, `N1`, `R2-02`, `R2-04`,
`TR-IDEM-02/03`, `W-23` и т.д.). Ценность здесь — **adversarial-комбинации**, которые
`chaos_run` не запускает, и живое подтверждение/опровержение ожиданий.

Severity: **P0** — деньги/идемпотентность/резерв под угрозой; **P1** — узкий, но реальный
денежный или контрактный дефект; **P2** — латентный дефект/эксплуатация/наблюдаемость;
**OK** — проверено, дефекта нет.

Почему это не ловит `chaos_run`: он проверяет только `transfer_id` при двойном submit,
не проверяет `payout_attempts` и терминальные переходы (ADV-02/12); мок отдаёт только
`PROCESSING/COMPLETED/FAILED` (ADV-13); `occurred_at` в chaos всегда в прошлом
(ADV-06); нет второго инстанса/рестарта (ADV-08/09/11/22); нет сценария «баланс ровно
на сумму» (ADV-01); нет двух ключей на quote (ADV-04) и кросс-валютной гонки лимита
(ADV-19). Chaos-инвариант `available + reserved + spent = start` при этом может быть
зелёным, даже если лимит превышен или попытка осталась `PROCESSING`.

## Сводная таблица

### A. Повторный POST и идемпотентность

| ID | Severity | Сценарий | Как воспроизвести | Ожидание | Факт/риск | Предлагаемый тест |
|---|---|---|---|---|---|---|
| ADV-01 | **P0** | Двойной POST с одним ключом **до** коммита, когда свободного остатка хватает только на один перевод | Временный клиент с балансом ровно `1000.00 RUB`; `POST /quotes` на `1000.00`; два потока одновременно `POST /transfers` с одним `Idempotency-Key` и одним телом | Оба запроса получают один и тот же `transfer_id` (`201/202`), один резерв, один payout (idempotency SPEC кейс 5) | **Воспроизведено 4/4:** один `201 COMPLETED`, второй `409 INSUFFICIENT_FUNDS`. `create_transfer` ищет ключ до `lock_client` и не перечитывает его после ожидания блокировки; второй видит уже списанный баланс и падает на `available < source_amount` (`transfers.py:177-205`). Клиент считает успешный перевод неуспешным и может повторить **новым** ключом → вторая выплата | `test_concurrent_same_key_low_balance_returns_same_transfer`: баланс ровно сумма, 2–20 параллельных запросов, все `201/202`, один `transfer_id`, `sum(create_payout)==1`; повторить и на `FAILED`-терминале |
| ADV-02 | **P1** | Двойной POST с одним ключом **во время** выполнения | Два параллельных `POST /transfers` с одним ключом; провайдер сначала отвечает `decline`/`5xx`, затем accept; либо chaos `double_submit` | `run_attempts` не запускается вторым запросом; на провайдера не более одной попытки; `attempt_no` уникален | **Воспроизведено косвенно на живой БД:** 7 переводов имеют дубли `(transfer_id, provider_id, attempt_no)`, у `f37588f2…` четыре попытки, `attempt_no=2` встречается дважды (`PROCESSING` и `FAILED`). Роут после `create_transfer` **всегда** вызывает `run_attempts` (`api/v1/transfers.py:73`), а `attempt_no=len(attempts)+1` считается без блокировки/UNIQUE (`transfers.py:321`; `models/tables.py` не содержит уникальности). Payout-ключ провайдера спасает от дубля у того же провайдера, но не от дублей строк и не от гонки терминалов (ADV-12) | `test_concurrent_same_key_single_attempt_row`: два POST, провайдер `decline→accept`; `len(attempts)==1` на провайдера, `len(set(attempt_no))==len(attempts)`, один payout; добавить `UNIQUE(transfer_id, provider_id, attempt_no)` или сериализацию `run_attempts` |
| ADV-03 | OK | Двойной POST с одним ключом **после** терминала | Создать перевод, дождаться `COMPLETED`/`FAILED`, повторить тот же `POST` с тем же ключом и телом | Тот же `transfer_id`, терминальный статус, без нового резерва/payout | Работает: поиск ключа идёт до проверки `expires_at`, затем `run_attempts` видит `TERMINAL` и возвращает статус (`transfers.py:177-183`, `:258-260`). REST-код для `FAILED` — `202`, это отдельный контрактный вопрос (`C-03`) | Регрессия уже есть (`test_idempotency_same_key_returns_same_transfer`); добавить вариант после `FAILED` и после истечения quote |
| ADV-04 | **P2** | Два разных ключа на один `quote` | Один `POST /quotes` на `2000.00 RUB`; три последовательных `POST /transfers` с ключами `k1/k2/k3`, одинаковым телом | Решение должно быть зафиксировано: либо quote одноразовый (`409/422` на второй ключ), либо многоразовый, но это явно задокументировано | **Воспроизведено:** три перевода, три резерва, три payout по одному quote. В схеме нет `UNIQUE(quote_id)`/`used_at`; D8 защищает только от повтора с тем же ключом. Если клиент потерял ответ и повторил **новым** ключом, получит вторую выплату. Это же поведение подтверждено в `TR-IDEM-03` | `test_two_keys_one_quote_semantics`: либо `len(ids)==1`, либо ровно один `201` и два `409`; зафиксировать решение в D8/`quote-contract` |

### B. Вебхуки, порядок и сверка

| ID | Severity | Сценарий | Как воспроизвести | Ожидание | Факт/риск | Предлагаемый тест |
|---|---|---|---|---|---|---|
| ADV-05 | P2 | Вебхук приходит **раньше** ответа POST /transfers | Провайдер `slow` (settle 32 с); сразу после появления payout в ledger отправить подписанный `PROCESSING`-вебхук; POST ещё не вернул ответ | Вебхук принимается, не ломает sync-путь; после settle — один терминал, один резерв | **Воспроизведено:** `PROCESSING`-вебхук принят `200` во время POST; POST вернул `202 PROCESSING`. Для `COMPLETED`-вебхука код сначала подтверждает `GET payout` и завершает через `_claim`; блокировка строки кратковременная (коммит после `record_attempt`), поэтому дедлока нет. Остаточный риск — `FAILED`-вебхук запускает второй `run_attempts` параллельно с POST (ADV-02) и оставляет попытку в `PROCESSING` (`C-04`) | `test_webhook_completed_before_post_response`: fake-провайдер задерживает 201, вебхук `COMPLETED` приходит раньше; один payout, `COMPLETED`, `reserved=0`, оба ответа согласованы |
| ADV-06 | **P1** | `occurred_at` в будущем | Провайдер `slow`; сразу после появления payout отправить подписанный `PROCESSING` с `occurred_at = now + 1 день`; дождаться реального `COMPLETED` от мока | Будущее событие не должно блокировать очередь; реальный `COMPLETED` применяется (или переводит в `NEEDS_REVIEW`), перевод закрывается | **Воспроизведено:** future `PROCESSING` сохранён; реальный `COMPLETED` (occurred_at меньше future) отброшен на `webhooks.py:97-100`; провайдер уже `COMPLETED`, API остаётся `PROCESSING`; сверка закрыла перевод только через **~59 с**. Если сверка выключена/упала, резерв висит бесконечно. `now` нигде не участвует, дрейф часов провайдера не ограничен. Уточнение W-23/R2W-03: блокирует именно нетерминальное первое событие | `test_future_occurred_at_does_not_block_completed`: future `PROCESSING`/`FAILED`, затем `COMPLETED`; терминал применяется, событие хранится с clamp/флагом; параметры `+60 с`, `+1 год`, `+10 лет` |
| ADV-07 | P2 | Противоречивые события подряд | Chaos `contradiction`: финальный `COMPLETED/FAILED`, затем противоположный с `occurred_at = settled−1 с`; отдельно оба порядка и одинаковые `occurred_at` | Терминал не перетирается; спор решает `occurred_at`; расхождение видно в аудите/метрике | Стандартный chaos-случай обрабатывается: событие с меньшим `occurred_at` игнорируется, а перед `COMPLETED`/`FAILED` обязательна сверка с провайдером. Остаются W-12 (две одновременные доставки читают `newest` без блокировки) и W-37 (политика терминальных противоречий не зафиксирована); метрики расхождения нет | `test_contradiction_both_orders_and_equal_timestamps`: `COMPLETED(T2)+FAILED(T1)` и наоборот, `T1==T2`, `asyncio.gather`; финальный статус по D6, строки событий, warn/метрика |
| ADV-08 | P1 | Падение воркера во время сверки | Убить/перезапустить API во время `reconcile_once` (или в `wait_for_settlement`); проверить перевод, попытку, резерв, ledger | Один payout, резерв не потерян; после рестарта перевод доводится до терминала; ошибка одного перевода не скрывается | **Воспроизведено для рестарта во время POST:** payout один, перевод дошёл до `COMPLETED` через сверку, но попытка осталась `PROCESSING` (`C-04`). Для падения именно в сверке: каждая строка в своей сессии, `_claim` атомарен, поэтому двойного списания нет; но `gather(return_exceptions=True)` **молча** выбрасывает исключение перевода (`main.py:45-53`), и такой перевод не логируется/не считается. Если провайдер отдаёт `None`, `still_open` навсегда (ADV-10) | `test_reconcile_crash_between_attempt_and_finalize`: исключение после `update_attempt` до `complete_transfer`; после рестарта — один `consume_reserve`, `reserved=0`; исключение одного перевода видно в логе/метрике |
| ADV-09 | P2 | Shutdown во время reconcile | `docker stop api` (SIGTERM) во время прохода; смотреть `reserved`, `transfers`, `payout_attempts`; поднять снова | Незавершённая транзакция откатывается; после старта перевод доводится; нет двух payout | Код безопасности: `lifespan` отменяет задачу, сессии закрываются/откатываются, финализация атомарна. Живьём не проверялось; при отмене между POST провайдеру и `record_attempt` payout может существовать без попытки — повтор с тем же детерминированным ключом должен его подхватить (ADV-22). Риск: `create_with_recovery` до ~11–60 с под блокировкой строки может затянуть graceful shutdown | `test_sigterm_during_reconcile`: отменить задачу в середине `run_attempts`; после рестарта один payout, одна попытка, `reserved` сходится |
| ADV-10 | **P1** | Зависшая выплата навсегда | Fake-провайдер для live-попытки всегда отдаёт `PROCESSING`, `404` или `None`; сверка каждые 60 с; ждать N проходов | После порога возраста/проходов — явный `NEEDS_REVIEW` + алерт/ручной разбор; резерв не «невидим»; либо сверка с `admin/ledger` доказывает отсутствие payout | `reconcile.py:66-71` делает один `get_payout`/`find_payout`; при `None` — `still_open`, без записи в БД, без счётчика и без порога. `NEEDS_REVIEW` на уровне перевода не выставляется (только `failure_code` попытки в труднодостижимой ветке). `chaos_run` видит такой перевод как `stuck_transfers`. Резерв может висеть неограниченно. Подтверждено в `R2-02` | `test_stuck_payout_escalates`: fake `None` 10 проходов → `NEEDS_REVIEW`/лог/метрика, `reserved` объяснён; отдельно fake `ledger` без payout → можно безопасно освободить |
| ADV-11 | **P1** | Два инстанса API одновременно | Поднять два uvicorn на одной БД/моке; оба запускают `reconcile_loop`; один `PENDING`-перевод или `PROCESSING` с провайдером, который отвечает по-разному | Single-runner (`FOR UPDATE SKIP LOCKED`/advisory lock); один проход, один payout, уникальные попытки | `list_open_transfers` — обычный `SELECT` без блокировки (`repositories/transfers.py:86-93`); `reconcile_loop` создаётся в каждом процессе (`main.py:65-66`). Оба вызывают `run_attempts`; при разных live-курсах выбирают разных провайдеров → два payout; в одном инстансе дубли попыток уже воспроизведены (ADV-02). Подтверждено в `R2-04`/`TR-ST-10` | `test_two_replicas_reconcile_one_transfer`: два `reconcile_once` параллельно, fake `create_payout` spy; `sum(create_payout)==1`, `len(attempts)==1`; advisory lock на проход |
| ADV-12 | **P0** | Гонка `run_attempts`: терминал перетирается, резерв списывается дважды | Два актора на одном переводе (двойной POST, вебхук + POST, две реплики). Один исчерпывает провайдеров и делает `fail_transfer` → `FAILED`; второй в этот момент получает `Accepted` и вызывает `set_status(..., "PROCESSING")` | Терминальный статус необратим; после `FAILED` второй актор не может перевести в `PROCESSING`/списать резерв; при живом payout — `NEEDS_REVIEW`, а не тихий `FAILED` | `set_status` — безусловная запись по PK (`repositories/transfers.py:79-83`), терминальной проверки нет; `_claim` защищает только `complete_transfer`/`fail_transfer` в момент финала. Дубли попыток уже есть (ADV-02): один актор может ставить `PROCESSING` после `FAILED` другого; тогда `complete_transfer` увидит `PROCESSING`, сделает `consume_reserve` повторно → `reserved` уходит в минус/съедает чужие резервы. Двойной payout возможен, если акторы выбрали разных провайдеров (разные live-курсы). Прямого воспроизведения `FAILED→COMPLETED` в БД нет, но окно доказано дублями | `test_terminal_not_overwritten_by_concurrent_run_attempts`: две сессии, fake-провайдер A→FAILED, B→Accepted; `transfer.status` остаётся `FAILED`/`NEEDS_REVIEW`, `reserved>=0`, один payout; `set_status` заменить на `UPDATE … WHERE status NOT IN (terminal)` + `rowcount` |
### C. Ответы провайдера

| ID | Severity | Сценарий | Как воспроизвести | Ожидание | Факт/риск | Предлагаемый тест |
|---|---|---|---|---|---|---|
| ADV-13 | **P1** | Провайдер отдаёт `200` с **неизвестным статусом** | Fake `GET /payouts/{id}` → `{"status": "PENDING"}`/`"CANCELLED"`/`"SUCCESS"`; подписанный вебхук с тем же статусом | Неизвестный статус не пишется в FK; перевод остаётся в известном состоянии, событие — в карантине/`4xx`, без 500; при повторе нет штормов | Для вебхука **воспроизведено живьём:** `status="PENDING"`, `"CANCELLED"`, `"SUCCESS"` → `500`, в логе `webhook_events_status_fkey` (справочник содержит только `PROCESSING/COMPLETED/FAILED`). Для GET: `_parse_state` копирует строку как есть (`providers.py:223-230`), `wait_for_settlement`/`reconcile` пишут её в `payout_attempts.status` (`update_attempt`) → тот же FK; в POST это 500 после резерва, в сверке исключение молча проглатывается (`gather(return_exceptions=True)`) → вечное `PROCESSING`. Совпадает с `C-09`/`W-09` для вебхука; путь GET — новый | `test_provider_unknown_status_is_quarantined`: respx GET `PENDING/CANCELLED/SUCCESS`; `status` перевода не меняется на неизвестный, попытка не пишет неизвестный FK; вебхук — `4xx`/`200` без 500; отдельно `test_webhook_unknown_status_no_500` |
| ADV-14 | P2 | Провайдер отвечает `200` (не `201`) на создание payout / `201` без `payout_id` / битый JSON | respx: `POST` → `200` с `{payout_id,status}`; `201` без `payout_id`; `201` с не-JSON; `422` с не-JSON | Любой 2xx с валидной схемой принимается или превращается в контролируемый `Unknown/Declined`; отсутствие обязательных полей — 502/`NEEDS_REVIEW`, не 500 | `create_payout` принимает только `201`; `200` уходит в `UnknownResult` и recovery (может спасти `GET ?reference=`), но при неидемпотентном провайдере это второй POST. `201` без `payout_id` → `KeyError`; `response.json()` в 422/200 не обёрнут → исключение уже после резерва. Частично `R2-10` | `test_provider_post_200_and_malformed_201`: 200 с валидным телом → один payout/`Accepted`; 201 без `payout_id` → 502/`NEEDS_REVIEW`, `reserved` не растёт без попытки; параметризовать битый JSON |
| ADV-15 | **P1** | Провайдер отдаёт **чужой reference** | respx: `get_payout(payout_id)` возвращает `PayoutState(reference=<другой перевод>, status=COMPLETED)`; то же для `find_payout`/`resolve_unknown` | Все пути сверяют `state.reference` с `transfer_id.hex` и принадлежность `payout_id` попытке; при расхождении — не финализировать, а `NEEDS_REVIEW`/unknown | Проверка есть **только в вебхуке** (`webhooks.py:108`). `wait_for_settlement` (`transfers.py:480-497`) и `reconcile._reconcile_one` (`reconcile.py:66-92`) доверяют статусу из `get_payout` без сверки reference; `resolve_unknown`/`find_payout` также возвращает найденное без проверки. Если провайдер перепутал записи, мы завершим свой перевод по чужой выплате (или создадим вторую). Это расширение W-01 (там чужой `payout_id` в вебхуке) | `test_foreign_reference_not_finalized`: respx GET с чужим reference и `COMPLETED`; перевод не `COMPLETED`, попытка не привязана к чужому payout, `reserved` на месте; отдельно `find_payout` и `resolve_unknown` |

### D. Деньги, лимиты и валюты

| ID | Severity | Сценарий | Как воспроизвести | Ожидание | Факт/риск | Предлагаемый тест |
|---|---|---|---|---|---|---|
| ADV-16 | **P0** | TARGET недоплачивает в зоне минимальной комиссии | `POST /quotes` `RU-TH-BANK`, `amount="1000.00"`, `amount_type="TARGET"`; `RU-JP-BANK`, TARGET `5000`/`10000`; `RU-TH-CARD` ниже порога | `target_amount >= amount`; source минимален; для `1000 THB` корректно `source=2430.95`, `fee=50.00`, `target=1000.00` | **Воспроизведено:** `1000 THB → source 2417.22, fee 50.00, target 994.23` (−5.77, −0.58%); `5000 JPY → 4902` (−98, −1.96%); `10000 JPY → 9993`; `RU-TH-CARD` имеет остаточную недоплату из-за HALF_UP. Причина: `if forward.fee_amount < fee_min_q` сравнивает уже `max(percent, min)` с минимумом, поэтому ветка `fee_min` недостижима (`quotes.py:185-195`, `:214`). D25-регрессии берут суммы выше порога и не ловят зону. Это `N1/N2` отчёта `round2/money-quotes.md`; здесь — независимое подтверждение и влияние на 0-decimal валюты | `test_target_never_underpays_in_min_fee_zone`: sweep по каждому направлению в зоне `p·source < min` (0/2/3 minor units), `assert target >= amount`; точные эталоны source/fee/target; финальный forward-прогон с инкрементом source до выполнения инварианта |
| ADV-17 | OK | Суммы с 0 и 3 знаками после запятой | `KW-TH` `20.000 KWD`; `KW-JP` `20.000 KWD` (target JPY 0 знаков); `RU-KW` TARGET `100.000 KWD`; нулевая и «лишняя» точность | Строки соответствуют `minor_units`; мок принимает сумму; ledger/попытка совпадают с quote | **Воспроизведено:** `KW-TH 20.000 → 2161.25 THB`; `KW-JP 20.000 → 10000 JPY`, ledger мока `amount="10000"`, попытка `10000.00000000 JPY`, `201 COMPLETED`. `20.00 KWD` → `422` (нужно 3 знака), `0.000` → `422`, JPY `2000.0` → `422`. Форматирование корректно; арифметическая недоплата TARGET — отдельно ADV-16 | `test_minor_units_end_to_end`: для всех валют 0/2/3 сравнить quote, `POST payout.amount`, ledger, `PayoutAttempt.amount`, ответ API; границы `0`, `0.001`, `1.000`, `max` |
| ADV-18 | P1 | Огромные суммы и TARGET-граница max | `RU-TH` SOURCE `999999999999.00`; TARGET `126000.00`, `126500.00`; `RU-JP` TARGET около max | Сумма выше max → `400 AMOUNT_ABOVE_MAX`; при 201 `source_amount <= max` направления; нет `Numeric(20,8)` overflow | Прямой huge → `400` (не 500), overflow нет благодаря лимитам. **TARGET `126000.00` → `201`, `source=304568.53 RUB` > max `300000`** (лимит проверяется по `target/rate` до комиссии); `126500` → `400`. Это `C-07/F2`; здесь подтверждён живой границей. Дополнительно `TARGET` может ложно отвергаться по min до расчёта source (`N3`) | `test_target_max_uses_computed_source`: TARGET-параметризация вокруг max; при 201 `source <= max`, иначе 400; отдельно `min` по фактическому source; huge SOURCE → 400 без 500 |
| ADV-19 | P1 (HEAD) | Клиент с балансом в нескольких валютах: лимит и резерв одновременно | `c_006`/временный клиент с RUB+KWD, лимит 400000 RUB; одновременно `300000 RUB` (RU-TH) и `1400 KWD` (KW-TH, ≈378000 RUB) | Хотя бы один перевод `409`; сумма в валюте лимита ≤ 400000; резерв по каждой валюте сходится | **Воспроизведено на HEAD:** оба `201 COMPLETED`, сумма `300000 + 1400×270 = 678000 RUB` > лимита; блокируется только строка баланса исходной валюты (`transfers.py:194` HEAD). В **текущем дереве** добавлен `lock_client` до проверки лимита (`transfers.py:184`), который сериализует все переводы клиента и должен закрыть гонку; регрессионного теста нет, контейнер ещё старый. Резерв сам по себе корректен (`available+reserved`), проблема только в лимите. См. `R2-D-09`/`TR-BAL-11` | `test_limit_serialized_across_currencies_two_sessions`: gather 2 перевода; один `409`, `sum(spent)<=limit`; повторить на текущем дереве после деплоя; отдельно проверить, что `lock_client` не держится во время `/rates` слишком долго |
| ADV-20 | P1/P2 | Одновременные переводы разных направлений | Тот же клиент: RUB→THB и KWD→TH одновременно; также два RUB-направления одновременно | Один клиентский лимит сериализован; баланс валюты не уходит в минус; на провайдере одна попытка | В одной валюте балансовая блокировка сериализует и лимит, и резерв — корректно. В разных валютах на HEAD лимит обходится (ADV-19); на текущем дереве `lock_client` закрывает. Дубли попыток при двух ключах/акторах остаются (ADV-02), payout защищён ключом провайдера. Риск: `lock_client` держится во время `live_rates_map` и `check_limit_definitions`, поэтому медленный `/rates` блокирует все переводы клиента | `test_same_client_two_directions_same_and_cross_currency`: два gather-прогона; инвариант лимита/баланса, `len(attempts)` без дублей; отдельно таймаут `/rates` под client lock |
| ADV-21 | P1 | Лимит и резерв в одной транзакции | См. ADV-19; отдельно проверить `available + reserved + spent = start` после гонки | Резерв и лимит проверяются под одной блокировкой клиента; при отказе лимита резерв не создаётся | Резерв атомарен по строке баланса, но лимит на HEAD не был атомарен между валютами. Текущий `lock_client` делает проверку и резерв последовательными; нужен тест на откат: второй запрос получает `409` и **не** оставляет `reserved`. На HEAD подтверждён обход лимита (ADV-19), не потеря резерва | `test_limit_and_reserve_same_client_transaction`: два параллельных перевода, один `409`; после — `available+reserved` каждого баланса = start, `reserved` только у успешного |

### E. Инфраструктура и деплой

| ID | Severity | Сценарий | Как воспроизвести | Ожидание | Факт/риск | Предлагаемый тест |
|---|---|---|---|---|---|---|
| ADV-22 | P1 | Рестарт API между POST провайдеру и коммитом попытки | Провайдер `slow`; дождаться payout в ledger; `docker restart api` до `record_attempt`; клиент получает обрыв; после старта — повтор с тем же ключом | Один payout; после рестарта попытка привязана к нему; перевод терминален; клиент может безопасно повторить тот же ключ | **Воспроизведено:** один payout, перевод `COMPLETED` через сверку, но попытка осталась `PROCESSING` (`C-04`); клиент получил `Remote end closed connection`. Детерминированный ключ `reference:provider` предотвратил второй payout. Риск: если рестарт совпал с ре-скорингом и live-курсы изменились, возможен «phantom» второй payout (R2-IDEM-03); если сверка не поднимется — резерв висит (ADV-10) | `test_restart_between_provider_ack_and_record_attempt`: убить процесс после `create_payout` до `record_attempt`, изменить курсы, повторить; `sum(create_payout)==1`, одна попытка, терминал; повторить с тем же и с новым ключом |
| ADV-23 | P2/OK | Повторный запуск entrypoint (миграции + сид) | На работающей БД: `docker exec api uv run --frozen alembic upgrade head`; `... python -m app.db.seed`; сравнить counts/суммы до/после | Идемпотентно: миграции не падают, сид не сбрасывает балансы/историю | **Воспроизведено последовательно:** counts `transfers/balances/clients/providers/windows/quotes` и `sum(available)` не изменились (`307/15/13/7/31/314`, `292640115.80`). Сид делает `merge` справочников, создаёт отсутствующие балансы, пересоздаёт окна обслуживания; существующие балансы не трогает. Остаточный риск — **одновременный** старт двух реплик: Alembic без advisory lock падает на `UniqueViolationError` (`INF-03`), сид с `merge` может давать гонки вставок | `test_entrypoint_rerun_idempotent` (два последовательных запуска, counts/balances неизменны) + `test_two_replicas_migrate_seed_concurrently` (advisory lock, оба exit 0, одна `alembic_version`) |

## Топ-5 самых опасных

### 1. TARGET недоплачивает клиенту (ADV-16, P0)

Подтверждено живьём: `1000 THB` → `994.23`, `5000 JPY` → `4902`, `10000 JPY` → `9993`.
Клиент просит выплату в валюте получателя, API отвечает `201`, но отправляет меньше.
Причина — мёртвая ветка `fee_min` в `calculate_target`: предикат сравнивает уже
`max(процент, минимум)` с минимумом. Это прямой денежный дефект и нарушение D3/D25;
регрессии D25 выбраны выше порога min-комиссии и зону не покрывают. Уже описано в
`round2/money-quotes.md` (N1/N2), но здесь подтверждено на живом стеке и на 0-decimal
валюте. Первый приоритет — параметризованный sweep TARGET в зоне min-комиссии с
`assert target >= requested` и точными эталонами source/fee/target.

### 2. Один ключ + мало денег: второй параллельный POST получает 409 (ADV-01, P0)

Четыре из четырёх прогонов: один `201 COMPLETED`, второй `409 INSUFFICIENT_FUNDS`
при одинаковом `Idempotency-Key`. Это нарушает D8 и idempotency SPEC кейс 5.
Опасность не в самом 409, а в реакции клиента: он видит «недостаточно средств»,
считает операцию невыполненной и повторяет с **новым** ключом — получает вторую
выплату. Нужно перечитывать ключ после получения `lock_client` (или делать
`INSERT … ON CONFLICT` до проверки баланса) и тест с балансом ровно на одну операцию.

### 3. Гонка `run_attempts` может перетереть терминал и списать резерв дважды (ADV-12, P0)

`set_status` пишет статус безусловно; `_claim` защищает только финальный
`complete/fail`. Два актора на одном переводе (двойной POST, вебхук + POST,
две реплики) уже дают дубли `attempt_no` в живой БД (7 переводов, у одного —
четыре попытки, `attempt_no=2` дважды). Если один актор успел поставить `FAILED`
и вернуть резерв, а второй в этот момент получил `Accepted`, он может вернуть
`PROCESSING`; последующая финализация увидит не-терминал и спишет резерв повторно
(`reserved` в минус) либо закроет перевод по чужой выплате. Нужны терминальный
guard в `set_status`, `UNIQUE(transfer_id, provider_id, attempt_no)` и
сериализация `run_attempts` на перевод.

### 4. Неизвестный статус провайдера → FK 500 и вечное `PROCESSING` (ADV-13, P1)

Живьём: подписанный вебхук со `status=PENDING/CANCELLED/SUCCESS` → `500`
(`webhook_events_status_fkey`), провайдер повторяет доставку. Для GET путь тот же:
`_parse_state` копирует строку, `update_attempt` пишет её в `payout_attempts.status`
(FK), в POST это 500 после резерва, в сверке исключение молча проглатывается.
Один неизвестный статус (реальный провайдер вполне может прислать `PENDING` или
`CANCELLED`) вешает перевод и резерв. Нужен allowlist/маппинг статусов и карантин
события, а не запись в FK.

### 5. Чужой `reference` в GET/reconcile принимается за наш (ADV-15, P1)

Единственная проверка `state.reference != event.reference` есть в вебхуке.
`wait_for_settlement`, `reconcile` и `resolve_unknown` доверяют статусу из
`get_payout(payout_id)`/`find_payout(reference)` без сверки reference и без
проверки принадлежности payout попытке. Если провайдер перепутал записи, мы
завершим свой перевод по чужой выплате — или создадим вторую. Нужна единая
проверка ownership во всех путях; при расхождении — `NEEDS_REVIEW`, не терминал.

На грани топ-5: future `occurred_at` (ADV-06, подтверждено: реальный `COMPLETED`
отброшен, перевод ждал сверку ~59 с) и «зависшая выплата навсегда» (ADV-10,
резерв может висеть без эскалации). Оба требуют продуктового решения о
`NEEDS_REVIEW`/ручном разборе, а не только кода.

## Предлагаемые тесты (порядок)

1. `test_target_never_underpays_in_min_fee_zone` — sweep TARGET по всем
   направлениям в зоне `p·source < fee_min`, 0/2/3 minor units, точные эталоны.
2. `test_concurrent_same_key_low_balance_returns_same_transfer` — баланс ровно
   на сумму, 2–20 параллельных POST, один `transfer_id`, один payout.
3. `test_terminal_not_overwritten_by_concurrent_run_attempts` — две сессии,
   `FAILED` + `Accepted`, `reserved >= 0`, один payout; плюс schema-тест
   `UNIQUE(transfer_id, provider_id, attempt_no)`.
4. `test_provider_unknown_status_is_quarantined` — GET и вебхук с
   `PENDING/CANCELLED/SUCCESS`, нет 500, нет записи неизвестного FK.
5. `test_foreign_reference_not_finalized` — respx `get_payout`/`find_payout` с
   чужим reference, перевод не терминален, резерв на месте.
6. `test_future_occurred_at_does_not_block_completed` — future `PROCESSING`,
   затем реальный `COMPLETED`; терминал применяется, событие с clamp/флагом.
7. `test_stuck_payout_escalates` — fake `None` N проходов → `NEEDS_REVIEW`/алерт;
   отдельно `ledger` без payout → безопасное решение.
8. `test_two_replicas_reconcile_one_transfer` и
   `test_limit_serialized_across_currencies_two_sessions` — single-runner и
   клиентская блокировка; проверить уже добавленный `lock_client`.
9. `test_restart_between_provider_ack_and_record_attempt` — kill после
   `create_payout`, смена курсов, повтор; один payout, одна попытка.
10. `test_webhook_completed_before_post_response` — вебхук раньше 201/202,
    один терминал, `reserved=0`.
11. `test_entrypoint_rerun_idempotent` + concurrent migration/seed
    (`INF-03`): два запуска, counts/balances неизменны; две реплики — advisory lock.
12. `test_two_keys_one_quote_semantics` — зафиксировать одноразовость quote или
    многоразовость; сейчас три ключа дают три payout.
