# providers/rates — второй раунд ревью

Зона: выбор провайдера, HTTP-клиент, неизвестный итог, курсы. Код и тесты **не менялись**.
Кроме статического чтения, поведение точечно проверено прогонами функций на текущем коде
(отмечено «проба»). Отчёт 1 (`../providers-rates.md`) и D25 учтены: повторно его баги не
перечисляются, за исключением случаев, где после D25 появился **новый корень / новый путь**.

Severity: **P0** — двойная выплата и нарушения с резервом; **P1** — неверный выбор/итог
или нарушение контракта D7/D24; **P2** — границы, детерминизм, диагностика.

## Новая таблица кейсов

| Severity | Кейс | Вход | Ожидание | Факт (проба/код) | Файл:строка | Тест |
|---|---|---|---|---|---|---|
| P2 | R2-1. Отрицательный курс в графе | `rates={("RUB","USD"): Decimal("-0.012")}`, `convert_amount(1000,"RUB","USD",rates)` | Отказ/`None`, как в `resolve_rate` после D25 | `-12.000` — ребро не отфильтровано; NaN даёт `NaN` ( `Decimal("NaN")` проходит то же условие) | `app/services/providers.py:43-47` | `tests/unit/test_provider_selection.py::test_convert_rejects_nonpositive_rate` |
| P2 | R2-2. Маржа ровно порог **после округления fee** | source 1000 RUB, `client_fee=1.00`, `min_margin=0.05%`, `fee_fixed=0.5004`, комиссия считается в RUB | raw-маржа 0.04996% → провайдер отсечён | fee округляется до 0.50 (minor_units=2), маржа становится ровно 0.0005 → провайдер **проходит**. Проба: 0.499/0.500/0.5004 → 0.50/pass; 0.505 → 0.51/fail | `app/services/providers.py:84,121-122`; `app/core/money.py:12-13` | `test_margin_boundary_after_fee_rounding` |
| P1 | R2-3. Unknown → NEEDS_REVIEW не доходит до записи | реальный `ProviderClient`: POST 500, `probe_payout` → `("error",None)` 7 раз | попытка `PROCESSING/NEEDS_REVIEW`, переход к следующему провайдеру **запрещён** | `create_with_recovery` возвращает `None` (4 POST + 7 probe), в `run_attempts` пишется `FAILED/PROVIDER_UNREACHABLE` и цикл идёт дальше; ветка `UnknownResult` (368-384) **недостижима**: клиент никогда не возвращает `UnknownResult` из recovery | `app/services/transfers.py:50-58,92-111,368-384,431-444`; `app/services/providers.py:165-190,206-221` | `test_unknown_never_falls_back_when_find_errors` + инспекция ledger мока |
| P1 | R2-4. Повтор POST после NEEDS_REVIEW | первая попытка `PROCESSING/NEEDS_REVIEW`, `payout_id=None`, запись у провайдера есть | не трогать провайдера, дождаться/сверить | `live` требует `payout_id` → попытка не live; `tried` исключает провайдера → повтор уходит **к другому провайдеру** (риск второй выплаты). Усугубляется R2-3: в проде NEEDS_REVIEW не пишется вовсе | `app/services/transfers.py:257,308-309,368-384`; `scripts/chaos_run.py:18` | `test_repeat_after_needs_review_does_not_switch_provider` |
| P2 | R2-5. Петля в графе курсов меняет путь конвертации | `A→B=2, B→C=3, C→A=4, C→D=5`; конвертация A→D | 3-переходный путь A→B→C→D = 30 | граф строит обратные рёбра для каждой пары, петля даёт ребро A→C=1/4; BFS возвращает 2-переходный A→C→D = **1.25**. Проба: без петли A→D=24 корректно, 4 перехода → `None` | `app/services/providers.py:43-62`; `docs/DECISIONS.md:160,184` | `test_convert_amount_cycle_shorter_inverse_path` |
| P2 | R2-6. Детерминизм BFS при равной длине | `A→X=2,X→D=10` и `A→Y=3,Y→D=8`, разный порядок ключей | задокументированный tie-break | проба: порядок 1 → 20, порядок 2 → 24; результат зависит от порядка вставки `rates`, который наследуется от `/rates` (D24 «первое найденное произведение» — контракт есть, но не зафиксирован тестом) | `app/services/providers.py:43-62`; `app/services/rates.py:19-27` | `test_convert_bfs_tie_depends_on_rate_order` |
| P2 | R2-7. Веса скоринга обесценивают маржу | `min_margin=0.05%`, два провайдера у порога | маржа влияет на выбор сопоставимо с success/latency | проба: маржа у порога даёт `100·0.0005 = 0.05` балла; `+0.01` success = 0.5 балла, `1000 ms` = 1.0 балла. Внутри прошедших фильтр маржа фактически не влияет на порядок (формула из D21, но декларация D7 «взвешенный» не выполняется по смыслу) | `app/services/providers.py:16-18,124-132`; `docs/DECISIONS.md:52,144` | `test_scoring_margin_weight_is_not_dominated` |
| P1 | R2-8. 409 `IDEMPOTENCY_KEY_REUSED` с другой записью | мок: ключ `ref:prov` уже занят payload'ом с другим amount → 409 | явный конфликт, без повторного POST и без выплаты у другого провайдера | 409 → `UnknownResult` → 3 повторных POST (все 409) → `None` → `FAILED/PROVIDER_UNREACHABLE` → следующий провайдер создаёт выплату по своему ключу. Отчёт 1 (BUG-13) фиксировал только «21 с повторов», но не факт выплаты у следующего провайдера | `app/services/providers.py:188-190`; `mock_provider/main.py:133-134`; `app/services/transfers.py:92-111,431-444` | `test_409_with_foreign_record_does_not_pay_elsewhere` |
| P1 | R2-9. `/rates` недоступен после резерва | transfer уже PENDING/reserve снят, `GET /rates` → 500 | контролируемый код (202/PROCESSING), резерв не висит без следа | `RatesClient.fetch` → `raise_for_status()` → `HTTPStatusError` не ловится в `run_attempts`; 500, баланс `reserved>0`, попыток нет. Повтор POST того же ключа вернёт тот же перевод, но снова упадёт, пока mock не поднялся | `app/services/rates.py:14-17`; `app/services/transfers.py:186,242,272`; `app/api/v1/transfers.py:73` | `test_rates_upstream_error_after_reserve` |
| P2 | R2-10. Битый JSON в 422/200 get/find | `422` с не-JSON или `200` c невалидным payload | `Declined`/None, контролируемая ошибка | `response.json()` в 422 и `_parse_state(response.json())` в get/find не обёрнуты → исключение при уже зарезервированных деньгах. Отчёт 1 описывал только битый 201 | `app/services/providers.py:177-179,192-200,224-230` | `test_create_malformed_422`, `test_probe_malformed_200` |

## Новые подтверждённые баги

### NEW-1. P2 — `convert_amount` слабее `resolve_rate` после D25
D25 добавил `_valid_rate` (`is_finite() and > 0`) в `app/services/quotes.py:87-88` и применяет
его к direct/inverse/обеим ногам. Конвертер лимитов и комиссий (`convert_amount`,
`app/services/providers.py:43-47`) проверяет только `rate == 0`. Проба: отрицательный курс
даёт отрицательную сумму, NaN — NaN. Следствие: фильтр лимитов и `provider_fee_in_source`
могут принять провайдера по невалидному курсу, тогда как quote по той же паре вернёт
`RATE_UNAVAILABLE`. Ожидание: единая валидация перед построением графа.
Тест: `test_convert_rejects_nonpositive_rate` с `-0.012`, `0`, `NaN`, `Infinity`.

### NEW-2. P2 — округление комиссии сдвигает границу маржи
`provider_fee_in_source` округляет fee до minor_units **source** (`providers.py:84`) до того,
как считается маржа (`:121`). Проба с порогом 0.05%: `fee_fixed=0.5004` даёт raw-маржу
0.04996% (ниже порога), но после HALF_UP до 0.50 маржа становится ровно 0.0005 и провайдер
проходит по строгому `<` (`:122`). Контракт «сравниваем raw или округлённое» не зафиксирован;
сейчас убыточный провайдер может быть отобран. Тест: `test_margin_boundary_after_fee_rounding`
с 0.499/0.5004/0.505 и KWD (3 знака).

### NEW-3. P1 — после D25 «неизвестный итог» по-прежнему кончается fallback'ом, причём другим путём
В отчёте 1 (BUG-3/D7/D8) корень был в `find_payout`, который не различал 404 и 5xx. Сейчас в
коде **появился** `probe_payout` с явными исходами `"missing"/"error"/"found"`
(`providers.py:206-221`) — то есть примитив для правильного решения есть. Но `resolve_unknown`
(`transfers.py:50-58`) схлопывает `"error"` и `"missing"` в один `None` (`:57`), а
`create_with_recovery` (`:96-111`) после этого делает повторный POST. Проба с `probe → error`:
4 POST + 7 probe → `None`. Далее `run_attempts` (`:431-444`) пишет `FAILED/PROVIDER_UNREACHABLE`
и переходит к следующему провайдеру. Если запись у первого провайдера реально создана, а чтение
бито — двойная выплата. Дополнительно: ветка `isinstance(outcome, UnknownResult)` (`:368-384`,
status `NEEDS_REVIEW`) **недостижима** ни для одного возврата реального `ProviderClient`:
create_payout никогда не отдаёт `ServerError`, а recovery никогда не возвращает
`UnknownResult`. В проде `NEEDS_REVIEW` не появляется, хотя `chaos_run.py:18` и
`starter-notes.md:43` считают его легальным статусом.
Ожидание: `"error"` → не fallback, а `NEEDS_REVIEW`/продолжение зондирования; `"missing"`
только по явному 404. Тест: пробы `"error"` и `"missing"` раздельно + сверка ledger мока.

### NEW-4. P1 — повтор POST после `NEEDS_REVIEW` уходит к другому провайдеру
Даже если NEEDS_REVIEW всё же записан (тест/fake-клиент), он имеет `payout_id=None`, поэтому
не попадает в `live` (`transfers.py:257`) и его `provider_id` попадает в `tried` (`:308-309`),
то есть исключается из `remaining`. Повтор POST того же ключа инициирует выплату у второго
провайдера. Это новый путь, отличный от описанного в отчёте 1 BUG-2 (там пустой `scored` и
fail_transfer при живой выплате). Тест: two-provider fake, первая попытка NEEDS_REVIEW с
реальной записью у провайдера → повтор → 0 новых payout, статус PROCESSING/сверка.

### NEW-5. P2 — обратные рёбра превращают цикл в короткий альтернативный путь
D24 обещает «первое найденное произведение» при BFS до 3 рёбер; граф строит ребро в обе
стороны. Проба `A→B→C→A` + `C→D`: ожидаемый 3-шаговый путь даёт 30, реальный результат 1.25
через обратное ребро A→C. Детерминизм есть, но семантика «кросс-курс как в reference_data»
(D7) нарушается: `ReferenceData.convert` (`mock_provider/reference_data.py:60-74`) использует
только прямые пары и не строит обратные рёбра для первого шага. Тест:
`test_convert_amount_cycle_shorter_inverse_path` + паритет `resolve_rate` и `convert_amount`.

## Проверено, новых дефектов не найдено (не дублируем отчёт 1)

- **Границы maintenance ровно в `from_at`/`to_at`:** проба — `from_at` → `True` (провайдер
  исключён), `to_at − 1µs` → `True`, ровно `to_at` → `False` (доступен). Совпадает с моком
  (`mock_provider/reference_data.py:54-58`). Это A3/A4 отчёта 1; тест по-прежнему отсутствует.
- **Несколько окон / нулевое окно / `enabled=false` + окна:** `any(...)` и короткое
  замыкание `p.enabled and not in_maintenance(...)` корректны; проба нулевого окна → `False`.
  Это A5/A7-смежные кейсы отчёта 1; новых багов нет, тестов нет.
- **3+ перехода и 4 перехода в конвертации:** проба — 3 перехода дают 24, 4 → `None`
  (max_hops=3); бага нет, совпадает с B11/B12 отчёта 1.
- **Маржа ровно порог без округления:** `margin < min/100` строгое, 0.50 RUB при 0.05%
  проходит — C1 отчёта 1, подтверждено повторно.
- **504 после создания:** по-прежнему Unknown → probe → found → привязка; D5 отчёта 1,
  нового при visibility_lag нет (мок `replay_response` не зависит от visible).
- **Score tie:** `sort` стабилен, порядок входа из `select(Provider)` без `ORDER BY` — C6
  отчёта 1; новый лишь количественный факт R2-7.
- **Кэш `/rates`:** после D25 по-прежнему отсутствует (поиск по `app` не нашёл кэша);
  BUG-9 отчёта 1 остаётся открытым, повторно в баги не выношу.
- **Все провайдеры с FAILED-попытками:** при Declined/found-FAILED цикл доходит до
  `fail_transfer` и возвращает резерв; тест есть только для «all declined»
  (`tests/integration/test_transfers_api.py:130-149`).
- **stale_read / visibility_lag > sync timeout:** `wait_for_settlement` продолжает поллинг
  и оставляет PROCESSING — E3/E4 отчёта 1, новые дефекты не обнаружены, тестов нет.

## Не покрыто тестами (новые дыры)

1. Валидация курсов в `convert_amount`: отрицательные, NaN, Inf (R2-1). Единого конвертера
   quote/limits после D25 нет: `resolve_rate` и `convert_amount` расходятся в валидации и в
   выборе пути (BUG-10 отчёта 1 актуален, но тест на отрицательные и NaN — новый).
2. Маржа на границе с учётом округления комиссии `round_money` (R2-2), включая KWD (3 знака) и JPY (0 знаков).
3. Разделение `"missing"` и `"error"` в `resolve_unknown`: тест с probe 5xx/timeout и
   сверкой, что второй провайдер **не** вызывается (R2-3). Сейчас `test_unknown_result_adopts_existing_payout`
   (`tests/integration/test_transfers_api.py:152-171`) подменяет `probe_payout` обёрткой над
   `find` (`:51-55`) и никогда не возвращает `"error"`, поэтому ветка не проверяется.
4. Недостижимость NEEDS_REVIEW: тест-инвариант «реальный клиент не порождает UnknownResult из
   recovery» или, наоборот, исправление recovery с возвратом UnknownResult.
5. Повтор POST в PROCESSING/NEEDS_REVIEW с `payout_id=None` и живой записью у провайдера (R2-4).
6. 409 с чужим payload → повторные POST → выплата у второго провайдера (R2-8).
7. `GET /rates` → 500/timeout между резервом и попыткой: код ответа и состояние резерва (R2-9).
8. Мусорный JSON в 422 и в payload get/find (R2-10); сейчас respx-тесты покрывают только
   валидные JSON и битый 201 не покрыт вовсе.
9. Циклы и обратные рёбра в графе курсов, детерминизм BFS при путях равной длины (R2-5/R2-6);
   тест `test_multi_hop_with_inverse_leg` (`tests/unit/test_provider_selection.py:77-85`) —
   единственный на граф и он не содержит петли.
10. Количественная проверка весов скоринга (R2-7): маржа у порога vs +0.01 success vs +1000 ms.
