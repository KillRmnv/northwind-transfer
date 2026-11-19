# Раунд 3 — adversarial: как сломать систему мимо chaos_run (код и тесты НЕ менялись)

Дата: 2026-11-19. База: chaos 300 зелёный (275 COMPLETED / 25 FAILED, 0 нарушений).
Прочитано перед написанием, не дублируется:
`docs/test-cases/reviews/round2/*.md`, `docs/test-cases/reviews/*.md`
(включая `round2-adversarial.md` ADV-01…ADV-23, `round2-contract.md` C-01…C-20,
`round2-limits.md` R2-D/R2-L, `round2/infra-security.md` R2-01…R2-07,
`round2/money-quotes.md` N1…N9, `round2/providers-rates.md` R2-1…R2-10,
`round2/test-quality.md`, `round2/transfers-limits.md`, `round2/webhooks-reconcile.md`),
`docs/DECISIONS.md` (все D, включая D21–D30),
`mock_provider/behavior.py`, `main.py`, `store.py`, `webhooks.py`, `reference_data.py`, `config.py`,
`scripts/chaos_run.py` целиком.

Принцип отбора: только то, что **зелёный chaos_run не ловит** — с указанием,
какая строка `chaos_run.py` слепа и почему. Повторы ADV/C/R2/N из раунда 2
сознательно исключены (регистр/пробелы ключа, два ключа на quote, phantom-попытка,
future `occurred_at`, NEEDS_REVIEW-недостижимость, stale/inverse подмены, TARGET-min,
маржа после округления, две реплики reconcile, пытки `limit_definitions` — всё это уже описано).

## A. Векторы атак (каждый — мимо инвариантов chaos)

### A-01. Балансы клиентов вне плана не проверяются вообще
- **Вектор:** взять `client_id`, которого нет в `plan` (бедный клиент, клиент с балансом
  в валюте вне лимита, клиент с нулевым балансом), прогнать через него переводы
  вручную/параллельным скриптом во время chaos. Испортить его `available/reserved`
  (двойной consume, потеря release) — chaos промолчит.
- **Почему chaos не ловит:** `client_ids = sorted({item["client_id"] for item in plan})`
  (`chaos_run.py:429`), `snapshot()` и `check_balances()` ходят только по этому множеству
  (`chaos_run.py:303,429-430,458`). Чужие клиенты не снепшотятся ни до, ни после.
- **Ожидаемый эффект:** `available + reserved + debited != start` у затронутого клиента,
  `RESULT: OK` при реальных потерях денег.
- **Тест/детектор:** снепшот **всех** клиентов из `data/clients.json` до/после + сверка
  `GET /clients/{id}/balance` для полного множества; отдельный инвариант
  «ни один клиент вне плана не изменил балансы».

### A-02. Валюты клиента вне плана не проверяются
- **Вектор:** клиент из плана имеет RUB+KWD, план трогает только RUB (фильтр
  `key[1] == currency`, `chaos_run.py:148`). Параллельно гонять KWD-переводы этого же
  клиента и ломать KWD-баланс.
- **Почему не ловит:** `check_balances` итерирует только валюты из `after`
  (`chaos_run.py:329`), а `before.get(client_id, {}).get(code, (0,0))` для невиданной
  валюты даёт `(0,0)` (`chaos_run.py:331`) — дебет чужой валюты посчитается, но
  стартовый ноль скроет дрейф, если валюта отсутствовала в первом снепшоте;
  главное — KWD-переводы вне `transfers[]` вообще не входят в `debited/expected_held`.
- **Эффект:** неверный `reserved`/`available` в непроверяемой валюте, `stuck_reserves`
  молчит.
- **Детектор:** снепшот всех `(client, currency)` из сида, а не только встреченных
  в `after`; сверять `reserved - start_reserved == expected_held` по полному декартову
  произведению.

### A-03. Дебет считается только по `source_amount` из quote, комиссия отдельной строкой не сверяется
- **Вектор:** подменить `fee` между quote и переводом (или уронить `consume_reserve`
  на величину без комиссии / с двойной комиссией). `source_amount` в `transfers[]`
  берётся из quote один раз (`chaos_run.py:217`), дальше инвариант
  `total_after = available + reserved + debited` (`chaos_run.py:339-341`).
- **Почему не ловит:** chaos не читает `transfers.source_amount/fee` из БД и не
  сверяет их с quote; любое систематическое занижение/завышение списания,
  одинаково применённое ко всем переводам, сходится в инварианте, пока API и
  quote врут согласованно. Отдельной сверки «дебет == source_amount в БД == quote»
  нет.
- **Эффект:** тихий недосчёт/пересчёт комиссии при зелёном `balance_invariant`.
- **Детектор:** после прогона выгрузить `transfers(source_amount, fee)` и сравнить
  построчно с `quote` из `transfers[]`; инвариант считать по БД-значениям, а не по
  запомненным при submit.

### A-04. Направления, отвергнутые `discover_directions`, исключены из chaos навсегда
- **Вектор:** направление, где `low*2` не проходит (`400/404/409/422`, `chaos_run.py:104-114`),
  никогда не попадает в `usable` и тем более в `plan`. Туда можно спрятать любой баг:
  `RATE_UNAVAILABLE` вместо кросс-курса, `AMOUNT_ABOVE_MIN` вместо `BELOW_MIN`,
  TARGET-обход max (C-07/F2), stale-inverse подмену (N4).
- **Почему не ловит:** `build_plan` итерирует только `usable` (`chaos_run.py:137-166`);
  отчёт печатает `N directions usable` (`chaos_run.py:426`), но отсутствие направления —
  не violation. Нет метрики «сколько тарифов из `tariffs.json` не покрыто».
- **Эффект:** целые коридоры (например KZT, KWD-inverse, PH) не тестируются годами,
  chaos зелёный.
- **Детектор:** assert покрытия: каждое `direction_id` из `tariffs.json` либо в плане,
  либо в явном allowlist с причиной; отдельный `discover`-отчёт с кодами отказа.

### A-05. План смещён к богатым клиентам и средним суммам; границы min/max не трогаются
- **Вектор:** `eligible` требует `left >= amount*3` (`chaos_run.py:148`),
  `amount ∈ [low + 5%..95% диапазона]` (`chaos_run.py:145-147`), берутся 5 самых богатых
  (`chaos_run.py:151`), `spendable` уменьшается только на `amount` без комиссии
  (`chaos_run.py:153`). Границы `min`, `max`, `min+1 minor`, `max-1 minor`, бедные клиенты,
  ровно-лимит — вне выборки.
- **Почему не ловит:** BVA-границы (R2-D-03, TR-LIM-05) и «баланс ровно сумма» (ADV-01)
  конструктивно не генерируются; `client_rejected` (`chaos_run.py:204-205`) глотает
  `INSUFFICIENT_FUNDS/DAILY_LIMIT_EXCEEDED` молча.
- **Эффект:** off-by-one в лимитах/балансе (`>` vs `>=`, округление на границе)
  при зелёном прогоне.
- **Детектор:** детерминированный BVA-слой поверх chaos: по одному переводу на
  `(min, min+ε, max-ε, max)` каждого usable-направления + клиент «ровно сумма»;
  `client_rejected` разбить по причинам и алертить при всплеске.

### A-06. Два перевода на один quote с разными ключами — разрешённый дубль выплаты
- **Вектор:** один `quote_id`, два `POST /transfers` с ключами `k1/k2` (уже описано как
  факт в TR-IDEM-03/ADV-04, здесь — именно слепота chaos): повторить потерю ответа
  с **новым** ключом.
- **Почему не ловит:** `submit()` создаёт ровно один quote на один `transfer`
  (`chaos_run.py:185`) и никогда не переиспользует `quote_id`; `double_submit`
  шлёт **тот же ключ дважды** (`chaos_run.py:191-200`) и проверяет только
  `len(ids) > 1` для одного ключа (`chaos_run.py:196-198`). Quote-reuse не генерируется
  и не детектируется.
- **Эффект:** две выплаты по одной котировке, ledger покажет два разных `account`
  (разные `transfer_id.hex` → разные reference? нет — account уникален на индекс,
  см. A-12) — но если атакующий переиспользует и `account`, chaos свалит оба payout
  в один `account` и наконец-то заметит `double_payouts`; при разных `account`
  это два «легитимных» перевода и тишина.
- **Детектор:** chaos-режим «потерял ответ → повторил новым ключом»; сверка
  `payouts by reference`: один `quote_id` → не более одного `COMPLETED`.

### A-07. Перевод после истечения quote новым ключом
- **Вектор:** дождаться `expires_at`, затем `POST /transfers` с тем же `quote_id`
  (TR-IDEM-01/14 фиксировали 410/выплату по старому курсу — здесь про chaos):
  chaos никогда не ждёт истечения.
- **Почему не ловит:** `quote` используется сразу же в `submit()` (`chaos_run.py:185-189`),
  TTL 300–1800 с, прогон не пережидает; проверки `410` нет, политики «курс зафиксирован
  vs отказ» нет.
- **Эффект:** выплата по протухшему курсу или ложный 410 после коммита — оба мимо отчёта.
- **Детектор:** пачка quote с `sleep(ttl+ε)` + повтор POST; ожидать либо единый
  зафиксированный курс, либо 410 **до** любой выплаты.

### A-08. Смена суммы через вебхук (`amount` в событии ≠ `target_amount`)
- **Вектор:** подписанный вебхук `COMPLETED` с тем же `payout_id/reference`, но другим
  `amount/currency`. Если API доверяет событию без сверки с провайдером/quote —
  перевод закроется, деньги уйдут не те.
- **Почему не ловит:** `compare_with_ledgers` сверяет `entry[amount/currency]`
  с `transfer[target_amount]` (`chaos_run.py:294-300`), но `entry` — это **ledger
  провайдера**, а не тело вебхука. Лживое событие, подтверждённое реальным payout
  в правильной сумме, не видно; лживое событие без payout даст
  `success_without_payout` — но только если итог success. Подмена суммы при FAILED→COMPLETED
  через forged (см. A-10) вне прицела.
- **Детектор:** логировать тела всех вебхуков chaos-прогона, сверять
  `event.amount == quote.target` построчно; тест «COMPLETED с чужой суммой не завершает».

### A-09. Forged COMPLETED при живом FAILED: гонка, которую ledger-сверка прощает
- **Вектор:** мок в 6% шлёт forged с **противоположным** статусом
  (`behavior.py:93-94`, `webhooks.py:57-67`, отправка раньше settle `main.py:51-54`):
  реальный итог FAILED, forged COMPLETED приходит первым. Если API закроет перевод
  по forged без `GET payout` — `success_without_payout` должен сработать… но только
  если ledger к моменту `fetch_ledgers` уже FAILED. Окно: forged принят, реальный
  settle ещё не наступил, затем сверка «подтверждает» forged чтением в `stale_read`
  окне (1–3 с вранья PROCESSING, `behavior.py:85`, `store.py:36-39`) или мимо
  `visibility_lag` (3–15 с 404, `behavior.py:84`).
- **Почему не ловит:** `fetch_ledgers` читает `ledger_entry().status` — **истинный**
  статус, а не видимый (`store.py:53-65` vs `public()`); `compare_with_ledgers`
  не знает, что API видел forged; `success_without_payout` (`chaos_run.py:290-291`)
  ловит только финальное расхождение, а не факт приёма forged. Подпись forged
  (`missing/invalid`) должна дать 401 — но chaos не считает 401/принятия.
- **Эффект:** перевод закрыт по подделке, резерв списан, ledger позже тоже COMPLETED
  по другому поводу или FAILED — во втором случае chaos заметит, в первом нет.
- **Детектор:** считать в мок-логах `deliver_forged` и сверять с API-логом
  «forged rejected 401»; любой `COMPLETED` без предшествующего `GET payout 200
  COMPLETED` — violation.

### A-10. Потерянные вебхуки (12%) + `lost_webhook` без детектора доставки
- **Вектор:** `lost_webhook = rng < 0.12` (`behavior.py:87`) — финальный `deliver_final`
  не вызывается (`main.py:62-63`). Перевод должен закрыться только поллингом/сверкой.
- **Почему не ловит:** chaos проверяет только финальный статус (`poll_until_settled`,
  `chaos_run.py:235-253` + перечитывание `chaos_run.py:452-455`), но не различает
  «закрыт вебхуком» vs «закрыт сверкой». Сломанная сверка при живых вебхуках
  останется зелёной, и наоборот.
- **Детектор:** разметить переводы по `plan.lost_webhook` (нужен экспорт плана мока)
  и требовать: lost → закрыт без webhook-события; не-lost → событие в `webhook_events`.

### A-11. Дубли (30%), `late_processing` (20%), `contradiction` (4%) — chaos их генерирует, но не утверждает
- **Вектор:** `duplicates 1-2` (`behavior.py:89-90`), `late_processing` PROCESSING после
  финала (`behavior.py:91`, `webhooks.py:86-89`), `contradiction` противоположный статус
  (`behavior.py:92`, `webhooks.py:90-94`), `unknown_event` 5% (`behavior.py:95`,
  `webhooks.py:69-77`).
- **Почему не ловит:** `chaos_run.py` нигде не читает `webhook_events`/`payout_attempts`;
  нет проверок «дубль — 200 без эффекта», «late PROCESSING не перетёр терминал»,
  «contradiction с меньшим occurred_at проигнорирован», «unknown reference — 200 +
  warn». Единственный их след — косвенно через финальные статусы.
- **Эффект:** сломанный дедуп/монотонность при зелёном chaos.
- **Детектор:** после прогона дамп `webhook_events(event_id, payout_id, status,
  occurred_at)` + assert'ы монотонности и уникальности; счётчик `unknown_event`
  сверить с warn-логом.

### A-12. Reference-коллизии между провайдерами
- **Вектор:** `reference = transfer_id.hex` общий для всех провайдеров; `by_reference`
  хранится **на провайдера** (`store.py:69-75,101`), `find_payout` ищет только внутри
  своего `provider_id` (`main.py:206-218`). Fallback ко второму провайдеру создаёт
  вторую запись с тем же reference у другого провайдера. `fetch_ledgers` группирует
  по `account`, а не по `reference` (`chaos_run.py:259-268`).
- **Почему не ловит:** `compare_with_ledgers` сопоставляет `account → entries`
  (`chaos_run.py:285`), `reference` не сверяется ни с `transfer_id.hex`, ни между
  провайдерами. Два живых payout с одним reference у разных провайдеров под разными
  `account` (переиспользование reference атакующим) не склеятся.
- **Эффект:** двойная выплата, разложенная по двум провайдерам, мимо `double_payouts`.
- **Детектор:** глобальная сверка `reference → [(provider, payout_id, status)]`
  по всем ledger'ам; один reference → не более одного `COMPLETED` во всех провайдерах
  суммарно; `entry.reference == transfer_id.hex` для каждого `account`.

### A-13. Ключ идемпотентности мока с пробелами/регистром — обход дедупа провайдера
- **Вектор:** мок сравнивает `Idempotency-Key` точным `==` (`main.py:131,133`) и строит
  хаос-ключи как `seed:post:provider:key:attempt` (`behavior.py:36`) — то есть чувствителен
  к каждому байту. API-нормализация (D26: `strip()`) и мок-строгость расходятся:
  `key` vs ` key ` — два разных `by_key` у провайдера.
- **Почему не ловит:** chaos всегда шлёт канонический `chaos-{run_id}-{index}`
  (`chaos_run.py:189`) без пробелов/регистра; `replay_response`/`IDEMPOTENCY_KEY_REUSED`
  (`main.py:133-134,148-151`) в chaos-ветке не упражняются.
- **Детектор:** fuzz ключей (`upper`, `strip`-варианты, trailing `\n`) на мок напрямую;
  требовать либо канонизации в моке, либо документированного «ключи opaque».

### A-14. Гонки за пределами `settle_timeout` (420 с) и `ledger_grace` (30 с)
- **Вектор:** `delay` до 25 с (`behavior.py:79`), `visibility_lag` до 15 с,
  `stale_read` до 3 с — в сумме < 420 с, но реальный провал сверки (API лежал 9 часов
  в round2-разборе) chaos не моделирует: `poll_until_settled` ждёт
  `settle_timeout` (`chaos_run.py:236`), затем `sleep(ledger_grace)` (`chaos_run.py:451`),
  затем **одно** перечитывание (`chaos_run.py:452-455`).
- **Почему не ловит:** перевод, завершённый сверкой через 421+ с, попадёт в
  `stuck_transfers` (`chaos_run.py:445-449`) — хорошо; но перевод, завершённый
  **после** финального перечитывания (гонка settle во время `fetch_ledgers`),
  даст ложный `success_without_payout`/`failed_but_paid`. И наоборот: payout,
  досеттлившийся после `fetch_ledgers`, останется невидимым навсегда.
- **Эффект:** флаки и слепота к «долгим хвостам».
- **Детектор:** два перечитывания с паузой `ledger_grace` + сверка `settled_at`
  из ledger против времени чтения; `stuck_transfers` с разбивкой «API-статус vs
  ledger-статус на момент чтения».

### A-15. Рестарт API посреди chaos — нулевое покрытие
- **Вектор:** `SIGTERM`/OOM/`docker restart api` во время `submit`/`wait_for_settlement`/
  `reconcile` (ADV-08/09/22 фиксировали проблему статикой и точечными пробами).
- **Почему не ловит:** `chaos_run.py` не перезапускает ничего; `submit` ретраит только
  `status < 500` (`chaos_run.py:175-180`), обрыв (`status 0`) после 4 попыток сдаётся
  (`chaos_run.py:180`), `pending` после дедлайна — сразу violation. Нет фази «убить API,
  поднять, убедиться что резервы сошлись».
- **Эффект:** потеря `record_attempt` (PHANTOM), висящий резерв, двойной payout
  при ре-скоринге — всё мимо зелёного прогона.
- **Детектор:** chaos-режим с `docker restart api` в середине submit-поллинга +
  повтор всех незавершённых тем же ключом; assert «один payout, один терминал».

### A-16. MOCK_SEED-детерминизм сломан `run_id`
- **Вектор:** `run_id = uuid4().hex[:8]` (`chaos_run.py:421`), а хаос-жребий мока
  считается от `key = chaos-{run_id}-{index}` (`behavior.py:36,73`). Один и тот же
  `--seed` при разных `run_id` даёт разные `chaos_action`/`chaos_settle`.
- **Почему не ловит:** воспроизвести зелёный/красный прогон по `--seed` невозможно;
  `MOCK_SEED` в `config.py:23` тоже строка, а не число — детерминизм мнимый.
- **Эффект:** «у нас 300 зелёных» невоспроизводимо; баг, пойманный однажды, не
  ловится повторно тем же сидом.
- **Детектор:** печатать и сохранять `run_id → per-transfer actions` (нужен
  debug-эндпоинт мока «какое действие выпало для key»); тест «два прогона с тем же
  seed и тем же run_id дают тот же план действий».

### A-17. Admin-эндпоинты мока — читерский пульт во время chaos
- **Вектор:** `PUT /{provider}/admin/scenario` (`main.py:230-239`),
  `DELETE .../admin/scenario` (`main.py:242-247`), `GET /{provider}/admin/ledger`
  (`main.py:221-227`) — без аутентификации (R2-03 уже фиксировал, здесь — именно
  влияние на chaos): любой сосед по сети во время прогона переключает провайдера
  в `decline`/`slow`/`error_5xx`, читает `recipient/account/idempotency_key` из ledger.
- **Почему не ловит:** сам chaos пользуется `admin/ledger` (`chaos_run.py:260`);
  подмена `scenario` снаружи выглядит как «естественный» хаос, детектора
  постороннего вмешательства нет; `GET /health` светит `mode/seed`
  (`main.py:34-36`, `chaos_run.py:415-419` только предупреждает).
- **Эффект:** сорванный/искажённый прогон, утечка PII получателей, ложный OK/FAIL.
- **Детектор:** перед/после прогона опрашивать `admin/scenario` всех провайдеров
  (должен быть `null`), запретить `PUT/DELETE` в chaos-окне фаерволом; вынести
  admin-ручки на отдельный порт.

### A-18. Вебхуки, потерянные молча: `unknown_event` и `forged` без assert'а приёма
- **Вектор:** `unknown_event` (5%, чужой `payout_id`+`reference`, `webhooks.py:69-77`)
  и forged (`webhooks.py:57-67`) доставляются реальным `POST /webhooks/*`.
  Ожидается `200 + warn` (unknown) и `401` (forged).
- **Почему не ловит:** chaos не читает ни HTTP-коды доставки, ни логи API, ни
  `webhook_events`; `compare_with_ledgers` проверяет только `paid`/`open`
  по известным `account` (`chaos_run.py:272-284`): unknown-событие с чужим account
  не в `known` и без `paid` — тишина (`chaos_run.py:282-283` проверяет только
  `paid`-случай).
- **Эффект:** отклонённый forged, который на самом деле принят (INF-01-класс),
  или проглоченный unknown без warn — оба мимо отчёта.
- **Детектор:** скрейп логов API за прогон: count(`unknown`) == count(dostavlennykh
  `unknown_event`), count(`401 forged`) == count(`forged`); каждый unknown — 200.

### A-19. `double_submit` проверяет только `transfer_id`, но не число попыток/payout
- **Вектор:** два параллельных `POST` с одним ключом (`chaos_run.py:191-200`):
  проверка — `len(ids) > 1` (`chaos_run.py:196-198`). Дубли `payout_attempts`
  (ADV-02: один `attempt_no` дважды), два `create_payout` у разных провайдеров
  с разными ключами провайдера, вторая попытка при живом payout — всё проходит.
- **Почему не ловит:** нет чтения `payout_attempts`/ledger в момент дубля;
  `double_payouts` ловит только финал по `account`, а два payout где один быстро
  FAILED, а второй COMPLETED — это `paid + open == 1`, тишина.
- **Детектор:** после каждого `double_submit` выгружать попытки перевода:
  `len(attempts)` по `(provider)` без дублей `attempt_no`, число `POST` к моку == 1
  на провайдера.

### A-20. Классификация статусов через CLI-флаги: неизвестный статус = pending
- **Вектор:** API вернул новый статус (например `NEEDS_REVIEW`, `CANCELLED` вне списков):
  `classify()` (`chaos_run.py:224-231`) кладёт неизвестное в `pending`,
  `poll_until_settled` ждёт до дедлайна (`chaos_run.py:236-253`).
- **Почему не ловит:** это даёт либо ложный `stuck_transfers`, либо — если статус
  добавлен в `--review-state` — попадание в `expected_held` (`chaos_run.py:327-328`)
  без сверки с ledger (`compare_with_ledgers` проверяет только success/failure,
  `chaos_run.py:287-293`). REVIEW с реальным payout и REVIEW без payout неразличимы.
- **Эффект:** `NEEDS_REVIEW`-деньги (резерв висит) выглядят как «ожидаемо удерживаемые»,
  прогон зелёный при зависших деньгах.
- **Детектор:** REVIEW-требует ledger-доказательства: либо `paid`, либо явный
  `stale/404`-след; отдельный violation `review_without_hold_explanation`.

## B. Дыры самого `chaos_run.py` (инструмент врёт/недоговаривает)

### B-01. `submit()` глотает клиентские отказы и transport-ошибки
- `code in CLIENT_REJECTIONS → return None` (`chaos_run.py:203-206`): превышенный
  лимит/недостаток средств не violation, а «не отправлено». Массовый fail-open лимитов
  выглядит как маленький `submitted`.
- `post_transfer` ретраит только `status < 500` 4 раза (`chaos_run.py:175-180`),
  затем возвращает последний ответ; `transfer_id is None → api_errors`
  (`chaos_run.py:207-210`) — но обрыв после созданной выплаты (DROP_CREATED/HANG)
  даёт `status 0` + повтор, который мок реплеит тем же payout (`main.py:148-151`):
  хорошо; а обрыв **до** создания при исчерпанных ретраях — молча потерянный перевод
  без violation на деньги (резерва не было — повезло).
- Детектор: отчёт «сколько `quote_rejected/client_rejected` от плана»; алерт при
  `submitted / len(plan) < порога`.

### B-02. `poll_until_settled` широким мазком перезаписывает статусы
- Пуллинг `ThreadPoolExecutor(max_workers=concurrency)` внутри цикла
  (`chaos_run.py:239-249`), `pending[tid]["status"]` перезаписывается каждым ответом;
  классификация по `args.success/failure/review_states` (`chaos_run.py:224-230`).
  Перевод, сходивший `COMPLETED → FAILED` (противоречие) или наоборот, фиксируется
  последним увиденным — история переходов теряется.
- Детектор: хранить всю историю статусов per-transfer, violation при любом
  терминальном переходе `COMPLETED ↔ FAILED`.

### B-03. `fetch_ledgers` фильтрует по префиксу `CH<run_id>` — коллизии и мусор
- `account.startswith(tag)` (`chaos_run.py:264`): чужой `ADVRACE*`/`ADV*`-мусор
  (см. round2-adversarial §Метод) отфильтрован — хорошо; но атакующий, знающий `run_id`
  (он печатается в лог, `chaos_run.py:426`), может подсунуть payout с тем же префиксом
  и отравить `double_payouts`/`amount_mismatch`. И наоборот: легитимный payout,
  чей `account` мок обрезал/нормализовал, выпадет из выборки.
- Детектор: сверка по `reference == transfer_id.hex` вместо/вместе с префиксом account.

### B-04. `compare_with_ledgers` не проверяет `reference`, `idempotency_key`, `failure_code`
- Сверяется только `amount/currency/status` (`chaos_run.py:294-300`); `ledger_entry()`
  отдаёт `reference/idempotency_key/failure_code/created_at/settled_at`
  (`store.py:53-65`), chaos их игнорирует.
- Детектор: `entry.reference == transfer.transfer_id без дефисов`,
  `entry.idempotency_key == reference:provider`, `failure_code` согласован со статусом.

### B-05. `check_balances` считает дебет только по success и держит review/pending в `expected_held`
- `debited` — только `success` (`chaos_run.py:324-326`), `expected_held` — review+pending
  (`chaos_run.py:327-328`); `FAILED`/`CANCELLED` (все `FAILURE_STATES`,
  `chaos_run.py:16`) не держат ничего — правильно iff возврат резерва атомарен (D27:
  `funds_settled`), но chaos не проверяет атомарность, а предполагает её.
- `reserved - start_reserved == expected_held` (`chaos_run.py:333-338`) сверяет дельту,
  а не абсолют; систематический сдвиг `start` (сид перезаписан между снепшотами,
  INF-06) сокращается.
- Детектор: абсолютная сверка по ledger движений + проверка `funds_settled` флага.

### B-06. `parse_balances`/`walk_balances` хрупок к форме ответа
- Ищет первые `available/reserved` ключи рекурсией (`chaos_run.py:63-89`); при смене
  контракта (`available_balance`, вложенность по валютам) молча возьмёт не то;
  `snapshot` падает только если вообще ничего не распарсил (`chaos_run.py:309-314`).
  Частичный провал (одна валюта из трёх) — тихий дрейф.
- Детектор: строгая схема баланса + assert множества валют == сиду.

### B-07. Отчёт режет нарушения до 10 строк и выходит 0/1 без машинного диффа
- `print_report` показывает первые 10 violation каждого вида (`chaos_run.py:369-373`),
  полный список — только в `--json` (`chaos_run.py:462-474`, опционален).
  Без `--json` разбор «25 FAILED — это норма или те же 25, что вчера» невозможен.
- Детектор: всегда писать `--json`, хранить артефакт прогона, диффовать по `run_id`.

### B-08. Конкурентность и таймауты chaos_раннера искажают результат
- `ThreadPoolExecutor(max_workers=concurrency=8)` на submit (`chaos_run.py:435-442`)
  и на каждый тик поллинга (`chaos_run.py:239`): при 300 переводах это очереди,
  а не 300 параллельных; гонки «50–100 конкурентных» (D13/D14) недожимаются.
- `request_timeout=70` (`chaos_run.py:389`) при `slow 30 с` + `hang 35 с` + ретраях
  `1+attempt` (`chaos_run.py:179`) может дать ложные `transport_error → api_errors`.
- Детектор: параметризовать `concurrency` до 50–100, мерить `duration_sec`
  (`chaos_run.py:460`) и число `transport_error`.

### B-09. `discover_directions`/`build_plan` используют живой API как оракул
- `discover` шлёт `POST /quotes` с `low*2` (`chaos_run.py:106-113`) и роняет весь прогон
  `SystemExit` при `status >= 500` (`chaos_run.py:112-113`) — один больной провайдер
  убивает прогон, а не фиксирует violation.
- `build_plan` RNG на `args.seed` (`chaos_run.py:422,428`), но `run_id` случаен —
  см. A-16: план невоспроизводим при фиксированном сиде.
- Детектор: разделить «разведку» (не фатальна, идёт в отчёт) и «план» (seed включает
  `run_id` явно или фиксируется флагом `--run-id`).
