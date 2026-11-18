# providers-rates — edge-кейсы и дыры в тестах

Ревью выбора провайдера, fallback, неизвестного итога, конвертации и свежести курсов.
Код не менялся, тесты не писались. Кроме статического чтения, поведение точечно проверено
прогонами функций на `data/rates.json` с эмуляцией `mock_provider/reference_data.live_rates`
и на синтетических наборах (результаты отмечены как «подтверждено пробой»).

Severity: **P0** — двойная выплата, зависший резерв, неверный терминальный статус;
**P1** — неверный выбор провайдера/курса или существенное нарушение контракта;
**P2** — границы, детерминизм, диагностика, редкие коды.

Текущее покрытие: `tests/unit/test_provider_selection.py` проверяет только чистые фильтры
на синтетике; `tests/integration/test_transfers_api.py` целиком подменяет `ProviderClient`
и не проверяет ни один HTTP-код, ни состав выбранных провайдеров, ни восстановление после
обрыва. Единственный тест свежести (`test_stale_rate_rejected`) берёт RUB→AED, где нет
кросс-пути, поэтому дыра со stale direct + fresh cross не видна.

## A. Доступность: enabled, направление, maintenance-окно

| Severity | Кейс | Вход | Ожидание | Текущее поведение | Файл:строка | Предлагаемый тест |
|---|---|---|---|---|---|---|
| P1 | A1. Выключенный `prov_delta` отсекается на seeded-данных | `load_providers` из `data/providers.json`, направление RU-TH-BANK | `prov_delta` нет в `scored` | Код фильтрует (`enabled=False`), но e2e-теста нет: интеграционные тесты не проверяют, кто выбран | `app/services/providers.py:29-30`; `app/repositories/references.py:15-42`; `data/providers.json:209` | `tests/integration/test_provider_selection_seed.py::test_delta_disabled_excluded` |
| P1 | A2. `prov_echo` в окне обслуживания отсекается | seeded окно `2026-11-04..2027-01-04`, `now=2026-11-19T12:00Z` | `prov_echo` нет в `scored`, остальные на месте | Код фильтрует, но ни один тест не запускает выбор на реальных окнах | `app/services/providers.py:25-30`; `data/providers.json:233-234` | `...::test_echo_maintenance_excluded` |
| P2 | A3. Начало окна включительно | `now == from_at` | Провайдер исключён | Подтверждено пробой: исключён (`<=`) | `app/services/providers.py:26`; тест `tests/unit/test_provider_selection.py:48-61` не покрывает границу | `test_filter_availability_window_start_boundary` |
| P2 | A4. Конец окна исключительно | `now == to_at` | Провайдер доступен | Подтверждено пробой: доступен (`< to_at`), совпадает с моком | `app/services/providers.py:26`; `mock_provider/reference_data.py:54-58` | `test_filter_availability_window_end_boundary` |
| P2 | A5. Несколько окон, пересечение, нулевое окно | `windows=(w1,w2)`, `from_at == to_at` | Активно хотя бы одно — исключён; нулевое не срабатывает | `any(...)`, поведение корректно; теста нет | `app/services/providers.py:25-26` | `test_filter_availability_multiple_windows` |
| P2 | A6. Провайдер без направлений | `directions=frozenset()` | Отсечён `filter_direction` до остальных стадий | Корректно, теста нет | `app/services/providers.py:21-22` | `test_filter_direction_empty_directions` |
| P1 | A7. Naive-время окна из SQLite | `DB_PROFILE=sqlite`, seeded `maintenance_windows`, `now` aware | Сравнение работает (нормализация через `as_utc`) | Подтверждено пробой: SQLite возвращает naive `datetime`, `from_at <= now` даёт `TypeError`, POST /transfers → 500 | `app/services/providers.py:26,29-30`; `app/repositories/references.py:22-26`; `app/models/tables.py:97-98` | `test_provider_selection_sqlite_naive_windows` (профиль sqlite) |
| P2 | A8. Порядок не меняется фильтрами | 3 провайдера, один disabled, один в окне | Оставшиеся сохраняют исходный порядок | List comprehension сохраняет порядок; теста нет | `app/services/providers.py:21-30` | `test_filters_preserve_order` |

## B. Лимиты провайдера и конвертация

| Severity | Кейс | Вход | Ожидание | Текущее поведение | Файл:строка | Предлагаемый тест |
|---|---|---|---|---|---|---|
| P1 | B1. Сумма ровно `limit_min` | провайдер USD, `limit_min=10`, target USD 10.00 | Провайдер проходит | Подтверждено: `<=` включительно | `app/services/providers.py:98`; тест `...:64-68` далёк от границы | `test_filter_limits_min_boundary_inclusive` |
| P1 | B2. Сумма ровно `limit_max` | USD 5000.00 при `limit_max=5000` | Провайдер проходит | Подтверждено: `<=` включительно | `app/services/providers.py:98` | `test_filter_limits_max_boundary_inclusive` |
| P1 | B3. На 0.01 за границей | USD 9.99 и 5000.01 | Оба отсечены | Подтверждено; теста нет | `app/services/providers.py:98` | `test_filter_limits_outside_boundaries` |
| P1 | B4. Допуск мока ±10% не расширяет наш фильтр | converted = 0.9×min и 1.1×max | Наш фильтр отсекает, хотя мок принял бы | Код строже; теста нет, расхождение не зафиксировано | `app/services/providers.py:87-100`; `mock_provider/reference_data.py:87-94` | `test_filter_limits_stricter_than_mock_tolerance` |
| P1 | B5. Матрица валют провайдера | target THB 4137; провайдеры USD, EUR, JPY, KWD; реальные курсы | Пересчёт в валюту провайдера и проверка его min/max | Работает через BFS, но тест только один (USD) | `app/services/providers.py:95`; `...:64-68` | `test_filter_limits_currency_matrix` |
| P2 | B6. Нет пути конвертации к валюте провайдера | target THB, провайдер KZT, курсов KZT нет | Провайдер молча отсечён; если это все — пустой `scored` | `convert_amount` → None → `continue`; теста нет | `app/services/providers.py:95-97` | `test_filter_limits_no_rate_drops_provider` |
| P2 | B7. Zero-decimal target (VND) | RU-VN-BANK, target 29 635 000 VND, провайдеры EUR/USD | Пересчёт без дробей, лимиты в EUR/USD соблюдены | Подтверждено: 29 635 000 VND → 1086.62 EUR; теста на выбор нет | `app/services/providers.py:95`; `data/currencies.json` VND=0 | `test_filter_limits_zero_decimal_target` |
| P2 | B8. Три знака у KWD | провайдер KWD `3.000..1500.000`, target THB/KWD | Границы 3.000 и 1500.000 включительно | Работает, теста нет | `app/services/providers.py:98`; `data/providers.json` prov_golf | `test_filter_limits_kwd_three_decimals` |
| P2 | B9. Округление на границе | target THB 3508.77 / 3508.78, провайдер USD min=10, THB→USD 0.0285 | Зафиксировать контракт: сравниваем неокруглённое или округлённое до minor_units USD | Подтверждено: 3508.77 → 9.9999945 < 10 (отсечён), 3508.78 → 10.000023 (прошёл) | `app/services/providers.py:95-98` | `test_filter_limits_boundary_rounding_contract` |
| P1 | B10. Свежесть не проверяется в конвертации лимитов | stale RUB→AED 0.044, stale RUB→PHP 0.700 | Конвертация для лимитов использует тот же курс, что quote; stale не подставляется молча | Подтверждено: `convert_amount` возвращает 0.044/0.700 по протухшим курсам | `app/services/providers.py:43-61`; `app/services/quotes.py:57-77` | `test_filter_limits_uses_quote_consistent_rate` |
| P2 | B11. Три шага | synthetic A→B→C→D | Находит путь длиной 3 | Код поддерживает (`max_hops=3`); тест только на 2 шага | `app/services/providers.py:48-61`; `...:75-82` | `test_convert_three_hops` |
| P2 | B12. Четыре шага | synthetic A→B→C→D→E | `None` | Ограничение работает, теста нет | `app/services/providers.py:38,52-53` | `test_convert_max_hops_exceeded` |
| P2 | B13. Равная длина, разные произведения | EUR→THB: пути через KWD 37.5375, USD 38.043, RUB 38.1818 | Выбор пути детерминирован и задокументирован | Подтверждено: `convert_amount` берёт 38.1818 (порядок вставки), `resolve_rate` — 37.5375 | `app/services/providers.py:43-61` | `test_convert_path_choice_is_deterministic` |
| P2 | B14. Обратное ребро только на первом шаге | JPY→THB: JPY нет как base, но есть JPY→RUB (обратное к RUB→JPY) | Оба резолвера ведут себя одинаково | Подтверждено: `resolve_rate` → RATE_UNAVAILABLE, `convert_amount` → 0.221 | `app/services/quotes.py:63-77`; `app/services/providers.py:43-61` | `test_rate_resolver_vs_amount_converter_parity` |
| P2 | B15. Округление комиссии провайдера по minor_units источника | JPY-провайдер, target JPY 18658, source JPY 0 знаков; KWD 3 знака | Fee округляется до minor_units source | Подтверждено: 50.16 RUB для JPY-кейса; отдельных тестов нет | `app/services/providers.py:78-84`; `app/core/money.py:12-13` | `test_provider_fee_rounding_per_minor_units` |
| P2 | B16. Одинаковая валюта без курсов | `convert_amount(100, "USD", "USD", {})` | 100 | Возвращает amount; теста нет | `app/services/providers.py:40-41` | `test_convert_same_currency_without_rates` |
| P2 | B17. Нулевой курс в графе | `{("A","B"): 0}` | Пути нет, без деления на ноль | Подтверждено: ребро пропускается, результат `None` | `app/services/providers.py:43-47` | `test_convert_zero_rate_leg_is_skipped` |

## C. Маржа и скоринг

| Severity | Кейс | Вход | Ожидание | Текущее поведение | Файл:строка | Предлагаемый тест |
|---|---|---|---|---|---|---|
| P1 | C1. Маржа ровно `min_margin_percent` | source 1000 RUB, client fee 1.00 RUB, provider fee 0.50 RUB, min 0.05% | Провайдер проходит (сравнение строгое `<`) | Подтверждено: fee 0.50 → margin 0.0500% → включён | `app/services/providers.py:121-123` | `test_margin_equals_threshold_is_included` |
| P1 | C2. Маржа на шаг ниже порога | provider fee 0.51 RUB, min 0.05% | Отсечён | Подтверждено кодом; тест `...:110-123` использует заведомо убыточного | `app/services/providers.py:121-123` | `test_margin_below_threshold_excluded` |
| P1 | C3. Комиссия клиента в TARGET-валюте смешивается с source | RU-VN-BANK, source 100 000 RUB, rate 300, `fee_amount=365 000 VND`; провайдер EUR с fee 10% | Клиентская комиссия приводится к RUB (1216.67), провайдер с fee 9878.33 RUB и маржой −8.66% отсечён | Подтверждено: `score_providers` получает 365000 как «RUB», даёт score 385.22 и включает провайдера | `app/services/transfers.py:247`; `app/services/providers.py:116-123`; `app/models/tables.py:121-127` | `test_margin_target_charged_fee_currency` для RU-VN-BANK и KW-JP-BANK |
| P2 | C4. `source_amount == 0` | source_amount 0 | Провайдеры не падают, список пуст/фильтруется | Молча `continue`; теста нет | `app/services/providers.py:119-120` | `test_scoring_zero_source_amount` |
| P2 | C5. Fee провайдера не конвертируется | валюта провайдера без пути к source | Провайдер отсечён, не exception | `fee_source is None` → `continue`; теста нет | `app/services/providers.py:116-120` | `test_scoring_unconvertible_provider_fee_skipped` |
| P2 | C6. Равный score | два провайдера с идентичными fee/success/latency | Детерминированный tie-break (например, `provider_id`) | Подтверждено: stable sort сохраняет порядок входа, а `load_providers` идёт без `ORDER BY` | `app/services/providers.py:132`; `app/repositories/references.py:16` | `test_scoring_tie_is_deterministic` |
| P2 | C7. Веса не перекрывают друг друга случайно | одинаковая маржа, разная latency/success | Побеждает ожидаемый по формуле | Формула `100·margin + 50·success − p95/1000`; тест только на явном разрыве | `app/services/providers.py:124-128`; `...:85-107` | `test_scoring_weight_sensitivity` |
| P2 | C8. Маржа в долях, а не процентах | min_margin_percent 0.5 | Сравнение с 0.005 | Код делит на 100; тест есть только для «далеко за порогом» | `app/services/providers.py:121-122` | `test_margin_percent_to_fraction_conversion` |

## D. Неизвестный итог и fallback

| Severity | Кейс | Вход | Ожидание | Текущее поведение | Файл:строка | Предлагаемый тест |
|---|---|---|---|---|---|---|
| P1 | D1. `DROP_LOST` | POST → transport error, записи нет, find 404, повторный POST создаёт | Один POST-ключ, одна выплата, попытка с новым `payout_id` | Подтверждено на fake-клиенте: 2 POST, 1 find, Accepted | `app/services/transfers.py:92-107` | `test_recovery_drop_lost_creates_once` |
| P1 | D2. `DROP_CREATED` + `visibility_lag` | запись создана, find 404 из-за lag, повторный POST тем же ключом | Мок реплеит 201 с тем же `payout_id`, второй выплаты нет | Подтверждено: повторный POST возвращает существующую запись (мок `replay_response` не ждёт visibility) | `app/services/transfers.py:96-106`; `mock_provider/main.py:148-151`; `mock_provider/behavior.py:84` | `test_recovery_drop_created_visibility_lag_replay` |
| P1 | D3. Find попал с первого раза | POST → Unknown, find сразу PROCESSING | 1 POST, 1 find, попытка привязана к найденному `payout_id` | Подтверждено: 1 POST, 1 find | `app/services/transfers.py:93-95` | `test_recovery_adopts_found_payout_without_repost` |
| P1 | D4. `HANG_CREATED` | POST таймаут 5 с, запись создана до hang | Сначала find, запись найдена, fallback нет | Код так делает; теста нет | `mock_provider/main.py:179-181`; `mock_provider/config.py:30` | `test_recovery_hang_created_adopts_record` |
| P1 | D5. `ERROR_CREATED` (504) | POST → 504 после создания | Unknown → find → привязка, не fallback | `create_payout` мапит 504 в Unknown; теста нет | `app/services/providers.py:182-183`; `mock_provider/main.py:182-183` | `test_recovery_504_created_adopts_record` |
| P1 | D6. 500 при существующей записи | chaos `ERROR_5XX` на повторном POST при существующей выплате | Find находит запись, второй выплаты нет | Мок так умеет (`exists` + 15% 500), наш код идёт в find; теста нет | `mock_provider/behavior.py:62-65`; `app/services/providers.py:182-183` | `test_recovery_5xx_with_existing_record_no_fallback` |
| P0 | D7. Неотличимый отказ чтения от «записи нет» | find возвращает 500/timeout, запись у провайдера есть | 404 — единственный признак отсутствия; 5xx/timeout — не повод к следующему провайдеру | Подтверждено кодом: `find_payout` возвращает `None` на любой non-200 и transport error; после 10 проб `create_with_recovery` отдаёт `None` | `app/services/providers.py:202-213`; `app/services/transfers.py:107,385-397` | `test_find_payout_error_is_not_not_found` (respx 500/timeout) |
| P0 | D8. Все пробы не нашли, а запись есть | POST timeout, все find → None, все повторные POST → Unknown | Не переходить к следующему провайдеру при недоказанном отсутствии записи | Подтверждено: 5 POST + 10 find, возврат `None`, затем `FAILED PROVIDER_UNREACHABLE` и следующий провайдер | `app/services/transfers.py:92-107,385-397` | `test_unknown_result_never_falls_back_without_404` (сверка ledger мока: 2 живые выплаты) |
| P2 | D9. `decline (422)` не запускает find | create → Declined | Сразу следующий провайдер, find не вызывается | Подтверждено: 1 POST, 0 find | `app/services/transfers.py:88-89`; `...:326-341` | `test_decline_does_not_probe_provider` |
| P1 | D10. 500 без записи | create → 500, find 404 | Следующий провайдер, попытка FAILED записана | Код делает через recovery (5 POST/10 find); теста нет | `app/services/providers.py:182-183`; `app/services/transfers.py:385-398` | `test_500_without_record_falls_back_once` |
| P2 | D11. 503 | create → 503 PROVIDER_UNAVAILABLE | Declined, сразу следующий | Мапится в `Declined`; теста нет | `app/services/providers.py:180-181` | `test_503_maps_to_declined_fallback` |
| P2 | D12. 409 `IDEMPOTENCY_KEY_REUSED` | create → 409 | Явный конфликт, без 21 с слепых повторов | Мапится в `UnknownResult`, 5 POST/10 find, затем fallback | `app/services/providers.py:188-190` | `test_409_is_not_retried_as_unknown` |
| P2 | D13. 400/404/429 | create → 400 INVALID_REQUEST / 404 UNKNOWN_PROVIDER / 429 | Осмысленный исход, не «неизвестный итог» | Все идут в `UnknownResult`, 21 с повторов | `app/services/providers.py:184-190` | `test_unexpected_status_codes_are_not_unknown` |
| P1 | D14. Битый 201 | 201 с невалидным JSON или без `payout_id` | `UnknownResult`/контролируемая ошибка, резерв не теряется | `response.json()`/`payload["payout_id"]` не обёрнуты → исключение, 500 при уже взятом резерве | `app/services/providers.py:172-176` | `test_create_malformed_201` (respx) |
| P2 | D15. Ключ и тело на всех повторах | 5 POST внутри одного attempt | Везде `Idempotency-Key == reference:provider_id`, одинаковое тело | Код детерминирован; ни один тест не проверяет заголовок | `app/services/transfers.py:61-62,96-104` | `test_recovery_reuses_same_idempotency_key` |
| P1 | D16. Бюджет recovery vs D5 | 3–5 провайдеров, все Unknown | POST /transfers ≤ `api_sync_timeout_sec` (12 с), дальше 202 | Минимум 21 с на провайдера (4×1.5 + 6×2.5), до 46 с с сетевыми таймаутами; 5 провайдеров — 105–230 с | `app/services/transfers.py:92-107`; `app/core/settings.py:30`; `app/api/v1/transfers.py:73` | `test_unknown_recovery_respects_sync_timeout` |
| P2 | D17. Неизвестный provider_id | POST 404 UNKNOWN_PROVIDER | Не перебирать 10 раз | 21 с повторов, затем fallback | `app/services/providers.py:184-190`; `mock_provider/main.py:115-117` | `test_unknown_provider_404` |

## E. Settlement, polling, reconcile

| Severity | Кейс | Вход | Ожидание | Текущее поведение | Файл:строка | Предлагаемый тест |
|---|---|---|---|---|---|---|
| P0 | E1. Асинхронный FAILED | Accepted → GET возвращает FAILED, webhook потерян | `FAILED` + резерв возвращён (или следующий провайдер по политике) в ограниченное время | `wait_for_settlement` делает `break`, затем ставит PROCESSING; reconcile вызывает `run_attempts`, который снова берёт эту же FAILED-попытку как live и снова её поллит — вечный цикл | `app/services/transfers.py:444-450,264-277`; `app/services/reconcile.py:47-49`; `mock_provider/behavior.py:87` | `test_async_failed_finalizes_and_releases_reserve` + двойной `reconcile_once` |
| P2 | E2. COMPLETED и однократное списание | GET → COMPLETED | Резерв списан ровно один раз | Happy path покрыт; гонка webhook+sync на списание не проверяется | `app/services/transfers.py:438-443`; `app/services/finalize.py:14-35` | `test_complete_is_idempotent_under_webhook_race` |
| P1 | E3. `visibility_lag` больше sync-бюджета | lag 15 с, `api_sync_timeout_sec=12` | 202 PROCESSING, не FAILED; позже reconcile/webhook доводит | Код вернёт PROCESSING; теста нет | `app/services/transfers.py:419-428,446-450`; `mock_provider/behavior.py:84` | `test_visibility_lag_exceeding_sync_timeout` |
| P1 | E4. `stale_read` | GET врёт PROCESSING 1–3 с после settle | Одиночный GET не итог; поллинг сходится к COMPLETED | Поллинг продолжает; теста нет | `mock_provider/store.py:36-42`; `app/services/transfers.py:421-443` | `test_stale_read_does_not_finish_as_processing` |
| P1 | E5. Оба чтения недоступны | get/find → None до дедлайна | PROCESSING, резерв сохранён, reconcile позже | Код так делает; теста нет | `app/services/transfers.py:424-428` | `test_settlement_reads_unavailable_keeps_processing` |
| P2 | E6. COMPLETED при уже терминальном FAILED | webhook успел поставить FAILED, GET вернул COMPLETED | Ответ консистентен: статус FAILED и код не 201 | Возвращает строку `COMPLETED`, claim не проходит; роут отдаёт 201 с телом FAILED | `app/services/transfers.py:438-443`; `app/api/v1/transfers.py:79-84` | `test_wait_completed_does_not_override_terminal_failed` |
| P2 | E7. Изменение только `failure_code` | status PROCESSING, failure_code меняется | Попытка обновляется | Условие обновления смотрит только status/payout_id | `app/services/transfers.py:431-437` | `test_wait_updates_failure_code_without_status_change` |
| P2 | E8. Recovery съел бюджет settlement | recovery 21 с при бюджете 12 с | Понятный 202 и последующий reconcile | `started` = начало запроса, цикл не выполняется, сразу PROCESSING | `app/services/transfers.py:316-325,419-421` | `test_settlement_budget_after_recovery` |
| P2 | E9. Ровно дедлайн | `now.timestamp() == started + timeout` | Цикл завершён без лишнего GET | `<` строгое; теста нет | `app/services/transfers.py:421` | `test_settlement_deadline_boundary` |

## F. Курсы и resolve_rate

| Severity | Кейс | Вход | Ожидание | Текущее поведение | Файл:строка | Предлагаемый тест |
|---|---|---|---|---|---|---|
| P1 | F1. Свежий ровно на границе | `age == min(ttl, 6ч)` | Курс годен | `<=`; теста на границу нет | `app/services/quotes.py:56,80-82` | `test_rate_fresh_at_ttl_boundary` |
| P1 | F2. Старше на 1 мкс | `age == limit + 1µs` | RATE_UNAVAILABLE | Код отвергает; теста нет | `app/services/quotes.py:80-82` | `test_rate_stale_just_over_ttl` |
| P1 | F3. `min(ttl, 6ч)` | ttl=900 и ttl=86400 | Для 900 — 900 с, для 86400 — 21600 с | Формула есть; теста нет | `app/services/quotes.py:56`; `data/tariffs.json` | `test_rate_ttl_capped_by_six_hours` |
| P1 | F4. Stale direct не подменяется кроссом | RUB→PHP: direct от 2026-11-01 (stale), RUB→USD и USD→PHP свежие | RATE_UNAVAILABLE по SPEC rate-freshness №2 | Подтверждено пробой: возвращается 0.696 через RUB→USD→PHP | `app/services/quotes.py:57-77`; `docs/test-cases/rate-freshness/SPEC.md:24` | `test_stale_direct_does_not_fall_back_to_cross` |
| P2 | F5. Курс «из будущего» | `now - updated_at = -60` и `-61` | Намеренный допуск задокументирован; -61 отвергается | `-60 <= age`; теста нет | `app/services/quotes.py:80-82` | `test_rate_future_skew_boundary` |
| P1 | F6. Нулевой direct | `rates[(RUB,THB)] = 0` | RATE_UNAVAILABLE | Подтверждено: возвращает 0, SOURCE-котировка даёт target 0.00 (TARGET-режим при этом падает) | `app/services/quotes.py:57-59,111,163-164` | `test_zero_direct_rate_rejected_in_both_modes` |
| P1 | F7. Нулевая вторая нога 2-hop | RUB→USD 0.012, USD→THB 0 | RATE_UNAVAILABLE | Подтверждено: 0.000; `convert_amount` при этом даёт `None` | `app/services/quotes.py:64-74`; `app/services/providers.py:43-47` | `test_zero_second_hop_rate_rejected` |
| P2 | F8. 2-hop: равная свежесть, разные ставки | два пути с одинаковым `min(updated_at)` | Детерминированный выбор | `freshness > best` сохраняет первый по порядку словаря; теста нет | `app/services/quotes.py:72-74` | `test_cross_rate_tie_is_deterministic` |
| P2 | F9. Одинаковые валюты | `resolve_rate(RUB,RUB,{})` | 1 | Возвращает 1; теста нет | `app/services/quotes.py:54-55` | `test_rate_same_currency_is_one` |
| P2 | F10. Naive `updated_at` | RateData с naive datetime | Понятная ошибка/нормализация | `now - updated_at` → TypeError; `RatesClient` нормализует, прямой вызов нет | `app/services/quotes.py:80-82`; `app/services/rates.py:21-23` | `test_rate_naive_updated_at` |
| P1 | F11. Кэш на TTL отсутствует | два POST /quotes подряд в пределах ttl | Один вызов `/rates` (D4, SPEC rate-freshness №4-5) | Каждый запрос идёт в `/rates`; кэша нет ни в quote, ни в transfer | `app/api/v1/quotes.py:77-82`; `app/services/transfers.py:183-184,242-243` | `test_rates_cached_within_quote_ttl` |
| P2 | F12. `/rates` 500 в quote | mock /rates → 500 | RATE_UNAVAILABLE | `httpx.HTTPError` ловится; отдельного теста на HTTP-код нет | `app/api/v1/quotes.py:77-82` | `test_quote_rates_upstream_500` |
| P1 | F13. `/rates` 500 в transfer | transfer уже PENDING, `/rates` → 500 | Контролируемый код, резерв не висит без следа | Исключение не ловится в `run_attempts` → 500, PENDING + резерв до reconcile | `app/services/transfers.py:242-243` | `test_rates_outage_during_transfer` |
| P2 | F14. Мусор в курсах | rate=0, отрицательный, дубль пары | Валидация/отказ | Дубли перезаписываются последним, отрицательные принимаются | `app/services/rates.py:19-27` | `test_rates_payload_validation` |
| P2 | F15. Обратная вторая нога | A→B есть, C→B есть, B→C нет | Контракт одинаков для quote и limits | `resolve_rate` вторую ногу только вперёд; `convert_amount` — в обе стороны | `app/services/quotes.py:63-69`; `app/services/providers.py:46-47` | `test_cross_rate_inverse_second_leg` |
| P2 | F16. Direct и inverse расходятся | THB→USD 0.0285 и USD→THB 35 | Один источник истины | `resolve_rate` берёт direct 0.0285, `convert_amount` — inverse 1/35 | `app/services/quotes.py:57-62`; `app/services/providers.py:43-47` | `test_direct_and_inverse_rate_consistency` |

## G. Идемпотентность и повтор

| Severity | Кейс | Вход | Ожидание | Текущее поведение | Файл:строка | Предлагаемый тест |
|---|---|---|---|---|---|---|
| P1 | G1. Повтор того же ключа в PROCESSING | первый POST принят, payout PROCESSING, повтор | Тот же transfer, ни одной новой попытки/выплаты | Код идёт в live-ветку и поллит; теста нет | `app/services/transfers.py:264-277` | `test_repeat_processing_reuses_attempt` |
| P0 | G2. Повтор в PROCESSING, когда фильтры опустели | live payout есть; повторный POST в момент maintenance/смены курсов, `scored == []` | Live payout не трогаем: PROCESSING/ждать, не FAILED | `fail_transfer` вызывается до проверки live-попыток: резерв возвращён, выплата у провайдера жива | `app/services/transfers.py:244-262`; `app/api/v1/transfers.py:73` | `test_repeat_processing_with_empty_candidates_keeps_reserve` |
| P2 | G3. Повтор после терминала | transfer COMPLETED/FAILED, тот же ключ | Тот же transfer, без новых попыток | `run_attempts` рано возвращает статус; теста нет | `app/services/transfers.py:237-238` | `test_repeat_terminal_returns_existing` |
| P1 | G4. Настоящая параллельность | два одновременных POST с одним ключом | Один transfer, одна резервная операция, один payout | Есть только последовательный тест; UNIQUE-гонка не проверяется | `tests/integration/test_transfers_api.py:200-214`; `app/services/transfers.py:175-181` | `test_concurrent_same_key_single_transfer` (`asyncio.gather`) |
| P1 | G5. Возобновление PENDING после падения | transfer PENDING, попыток нет | `run_attempts` доводит до терминала без дублей | Код есть в reconcile; теста нет | `app/services/reconcile.py:19-28` | `test_reconcile_resumes_pending` |
| P1 | G6. Повтор после истечения quote | transfer создан, `now > expires_at`, тот же ключ и тело | Тот же transfer (D8), не ошибка | Проверка `expires_at` стоит до поиска по `idempotency_key` → 410 QUOTE_EXPIRED | `app/services/transfers.py:163-164,175-181` | `test_idempotent_repeat_after_quote_expiry` |
| P2 | G7. Непрерывность `attempt_no` | после 2 FAILED-попыток новая | `attempt_no` = 3, без пропусков/дублей | `len(attempts)+1`; теста нет | `app/services/transfers.py:264,278` | `test_attempt_numbers_contiguous` |
| P2 | G8. Стабильность хеша тела | тот же recipient, другой порядок ключей JSON | Один и тот же `idempotency_hash` | `model_dump()` стабилен, но теста нет | `app/services/transfers.py:169-174`; `app/repositories/transfers.py:13-15` | `test_body_hash_is_order_insensitive_for_recipient` |

## Подтверждённые баги

### BUG-1. P0 — асинхронный FAILED не финализируется, резерв зависает
- Путь: Accepted → `wait_for_settlement` получает `FAILED` → `break` (`app/services/transfers.py:444-445`) → transfer ставится в `PROCESSING` (`:446-450`).
- `reconcile_once` на FAILED вызывает `run_attempts` (`app/services/reconcile.py:47-49`), а тот берёт эту же попытку как live (`app/services/transfers.py:264-277`) и снова поллит уже FAILED payout. Цикл повторяется бесконечно.
- Мок теряет вебхук в 12% случаев (`mock_provider/behavior.py:87`), поэтому это не редкий сценарий: резерв остаётся навсегда, `chaos_run` ловит `stuck_reserves`.
- Ожидание: `fail_transfer` (возврат резерва) либо следующий провайдер по явной политике; терминал за ограниченное время.
- Тест: fake get возвращает FAILED; проверить статус, `reserved == 0`, отсутствие повторных GET после финализации; отдельно два прогона `reconcile_once`.

### BUG-2. P0 — fail_transfer может сработать при живой выплате у провайдера
- `run_attempts` сначала пересобирает `scored`; если список пуст (maintenance, изменение курсов, лимиты), вызывает `fail_transfer` (`app/services/transfers.py:244-262`) и только потом смотрит live-попытки (`:264-277`). Роут вызывает `run_attempts` и для повторного POST с тем же ключом (`app/api/v1/transfers.py:73`).
- Сценарий: перевод уже PROCESSING с `payout_id`; окно обслуживания началось или маржа перестала проходить; повторный POST того же ключа возвращает резерв, хотя провайдер платит. Инвариант `failed_but_paid` нарушен.
- Ожидание: live-попытка проверяется до любых отказов; перевод с живой выплатой нельзя переводить в FAILED.
- Тест: один провайдер, первый POST Accepted/GET PROCESSING; затем monkeypatch фильтра в пустой список; повторный POST — статус PROCESSING, резерв на месте, в ledger мока одна живая выплата.

### BUG-3. P0 — «записи нет» и «не смогли прочитать» не различаются
- `find_payout` возвращает `None` и на 404, и на 500/timeout (`app/services/providers.py:202-213`); то же у `get_payout` (`:192-200`).
- `create_with_recovery` после 4 повторов POST и 10 проб чтения отдаёт `None` (`app/services/transfers.py:92-107`), а `run_attempts` трактует это как «записи нет» и идёт к следующему провайдеру (`:385-397`). Если запись существует, это двойная выплата.
- Ожидание: только явный 404 означает отсутствие; 5xx/timeout — повтор/`NEEDS_REVIEW`, никогда не fallback.
- Тест: respx — create timeout, find 500; проверить, что второй провайдер не вызывается и в ledger одна выплата.

### BUG-4. P1 — маржа для TARGET-комиссии считается в разных валютах
- `run_attempts` передаёт `Decimal(quote.fee_amount)` как `client_fee_source` (`app/services/transfers.py:247`), игнорируя `quote.fee_currency` (`app/models/tables.py:123`). Для RU-VN-BANK и KW-JP-BANK комиссия в VND/JPY.
- Подтверждено: RU-VN-BANK, source 100 000 RUB, rate 300, комиссия клиента 365 000 VND; провайдер EUR с fee 10% имеет реальную комиссию 9878.33 RUB и маржу −8.66%, но `score_providers` видит 365000 «RUB», даёт score 385.22 и включает его.
- Ожидание: комиссия клиента приводится к source (365000 VND / 300 = 1216.67 RUB) до формулы маржи; убыточный провайдер отсечён.
- Тест: `test_margin_target_charged_fee_currency` на RU-VN-BANK и KW-JP-BANK с провайдером, чья комиссия выше клиентской.

### BUG-5. P1 — stale direct молча заменяется кросс-курсом
- `resolve_rate` при протухшем direct пробует inverse и 2-hop (`app/services/quotes.py:57-77`), хотя D4 и SPEC rate-freshness №2 требуют RATE_UNAVAILABLE для протухшей пары.
- Подтверждено на live-семантике мока: RUB→PHP direct от 2026-11-01 протух, но RUB→USD и USD→PHP свежие → возвращается 0.696 вместо ошибки.
- Существующий `test_stale_rate_rejected` берёт RUB→AED, где кросс-пути нет, поэтому баг не ловится (`tests/unit/test_quote_calculator.py:212-215`); интеграционный `test_stale_rate_unavailable` замораживает одну пару без кросс-путей (`tests/integration/test_quotes_api.py:111-123`).
- Ожидание: stale direct → RATE_UNAVAILABLE либо явно задокументированное правило кросс-фолбэка; сейчас контракт и код расходятся.

### BUG-6. P1 — нулевой курс даёт нулевую котировку вместо ошибки
- Direct не проверяется на 0 (`app/services/quotes.py:57-59`); вторая нога 2-hop тоже (`:64-74`). `calculate_source` умножает на 0 (`:111`), `check_limits` при limit_currency == source курс не использует, поэтому SOURCE-котировка возвращает 201 с target 0.00.
- Подтверждено: `{(RUB,THB): 0}` → rate 0; `{(RUB,USD): 0.012, (USD,THB): 0}` → rate 0.000. `calculate_target` при этом падает (`:163-164`) — режимы расходятся.
- Ожидание: RATE_UNAVAILABLE в обоих режимах.
- Тест: `test_zero_direct_rate_rejected_in_both_modes`, `test_zero_second_hop_rate_rejected`.

### BUG-7. P1 — recovery не укладывается в sync-бюджет D5
- На каждый Unknown: 4 × 1.5 с + 6 × 2.5 с = 21 с минимум, до 46 с с сетевыми таймаутами (`app/services/transfers.py:92-107`). При 5 провайдерах — 105–230 с, при `api_sync_timeout_sec=12` (`app/core/settings.py:30`).
- D5 обещает 10–15 с и затем 202; фактически POST /transfers может висеть дольше клиентского таймаута (в `chaos_run.py` — 70 с).
- Ожидание: общий дедлайн на весь recovery-цикл, затем 202/оставление PROCESSING.

### BUG-8. P1 — maintenance-фильтр падает на SQLite
- SQLite возвращает naive `datetime` для `DateTime(timezone=True)`; `in_maintenance` сравнивает его с aware `now` (`app/services/providers.py:26,29-30`; окна грузятся в `app/repositories/references.py:22-26`).
- Подтверждено: `TypeError: can't compare offset-naive and offset-aware datetimes`; POST /transfers → 500. Профиль `sqlite` заявлен в D13.
- Ожидание: нормализация через `app/core/clock.py:as_utc`, как уже сделано для `quote.expires_at` (`app/services/transfers.py:163`).

### BUG-9. P1 — кэша курсов нет (отклонение от D4)
- Каждый POST /quotes делает `GET /rates` (`app/api/v1/quotes.py:77-82`); create_transfer и run_attempts делают это ещё раз (`app/services/transfers.py:183-184,242-243`). Кэша на `quote_ttl_sec` нет.
- SPEC rate-freshness №4-5 и D4 требуют кэш и один вызов на пару в полёте; сейчас 50–100 параллельных котировок дают шторм и тесты кэша в принципе не могут пройти.
- Ожидание: TTL-кэш, промах не кэшируется, обновление видно сразу.

### BUG-10. P1 — два независимых конвертера дают разные курсы, conversion игнорирует свежесть
- `convert_amount` (`app/services/providers.py:43-61`) не проверяет `updated_at` и берёт первый путь BFS; `resolve_rate` (`app/services/quotes.py:57-77`) проверяет свежесть и выбирает 2-hop по freshness. D7 требует «конвертация тем же курсом, что quote».
- Подтверждено на live-семантике курсов: EUR→THB 38.1818 против 37.5375; THB→USD 0.0285714 против 0.0285; THB→EUR 0.0261905 против 0.02622; KWD→PHP 189 против 188.5; stale RUB→AED даёт 0.044, stale RUB→PHP — 0.700, хотя quote даёт 0.696.
- Следствие: фильтр лимитов может включить/исключить провайдера на границе иначе, чем ожидает котировка.
- Ожидание: единый резолвер (direct → inverse → кросс) с проверкой свежести и детерминированным tie-break.

### BUG-11. P1 — идемпотентный повтор после истечения quote даёт 410
- `create_transfer` проверяет `expires_at` (`app/services/transfers.py:163-164`) до поиска по `idempotency_key` (`:175-181`). Повтор того же ключа и тела после TTL возвращает QUOTE_EXPIRED вместо существующего transfer, что противоречит D8.
- Ожидание: сначала idempotency lookup, затем expiry только для новой операции.

### BUG-12. P2 — `wait_for_settlement` возвращает «COMPLETED» при терминальном FAILED
- Если вебхук уже поставил FAILED, а GET вернул COMPLETED, `complete_transfer` не проходит claim, но функция всё равно возвращает строку `COMPLETED` (`app/services/transfers.py:438-443`); роут отдаёт 201 с телом FAILED (`app/api/v1/transfers.py:79-84`).
- Ожидание: возвращаемый статус совпадает с фактическим статусом transfer.

### BUG-13. P2 — 409 трактуется как неизвестный итог
- `create_payout` мапит 409 в `UnknownResult` (`app/services/providers.py:188-190`), после чего recovery делает 5 POST и 10 find. Для конфликта ключа это бессмысленно и задерживает fallback.
- Ожидание: отдельный исход/немедленная ошибка без повторного POST.

### BUG-14. P2 — битый 201 роняет обработку
- `response.json()` и `payload["payout_id"]` не защищены (`app/services/providers.py:172-176`); исключение уходит наверх при уже зарезервированных деньгах (transfer PENDING).
- Ожидание: `UnknownResult`/контролируемая ошибка, попытка и reconcile не теряются.

### BUG-15. P2 — пустой `scored` не оставляет диагностики
- При отсутствии провайдеров по марже/лимитам/доступности вызывается `fail_transfer` без записи попытки (`app/services/transfers.py:257-262`); в истории нет причины, хотя A6 требует пометку для отказа ниже `min_margin_percent`.
- Ожидание: попытка/событие с причиной (margin/limits/availability).

### BUG-16. P1 вне основного scope — `convert_with_hints` вызывается с неверной арностью
- `app/services/limits.py:130-132` передаёт 4 аргумента, функция объявлена с 6 (`:142-149`), два обязательных (`quote_source`, `live_rates`) отсутствуют → `TypeError` при любой активной строке `limit_definitions`.
- Сейчас не проявляется, потому что сидер не создаёт `limit_definitions`; тест на определения лимитов это вскроет.

## Не покрыто тестами

### Выбор и доступность
- Границы maintenance `from_at`/`to_at`, несколько и пересекающиеся окна, нулевое окно.
- Реальные seeded-провайдеры: `prov_delta` (disabled) и `prov_echo` (окно) никогда не проверяются e2e; интеграционные тесты не проверяют состав `scored` и порядок.
- Naive-время из SQLite/иного драйвера; нормализация `as_utc` для окон.
- Провайдер без направлений; провайдер без окон; disabled + окно одновременно.

### Лимиты и конвертация
- Точные `limit_min`/`limit_max`, ±0.01 за границей, допуск мока ±10% (0.9×min, 1.1×max).
- Матрица «валюта провайдера × валюта перевода» (USD/EUR/JPY/KWD/VND), zero-decimal и three-decimal.
- Нет пути конвертации: провайдер отсечён, а не exception; пустой `scored` после конвертации.
- Округление на границе (3508.77 THB vs 10 USD), контракт «округлённое или точное сравнение».
- Свежесть курсов в `convert_amount`; stale direct/inverse; 3-hop, 4-hop, max_hops, циклы.
- Детерминизм выбора пути при равной длине; расхождение прямого и обратного курса.
- Обратная вторая нога 2-hop в `resolve_rate`; паритет `resolve_rate` и `convert_amount`.
- Округление комиссии провайдера до minor_units source (JPY 0, KWD 3).

### Маржа и скоринг
- Маржа ровно `min_margin_percent` и на копейку ниже.
- Комиссия клиента в TARGET-валюте (RU-VN-BANK, KW-JP-BANK) — сейчас баг BUG-4.
- `source_amount == 0`, непреобразуемая комиссия провайдера, отрицательная маржа.
- Равные score и отсутствие tie-break; зависимость выбора от порядка `select(Provider)` без `ORDER BY`.
- Чувствительность весов: success/latency против небольшой разницы в марже.
- Отсутствие провайдера после всех фильтров: пустой список, `FAILED`, причина в истории.

### Неизвестный итог и HTTP-клиент
- `DROP_LOST`, `DROP_CREATED`, `HANG_CREATED`, `ERROR_CREATED` (504) по отдельности; сейчас есть один общий «unknown + find hit».
- 5xx без записи, 5xx при существующей записи, 503, 409, 400/404/429.
- Таймаут и slow 30 с; `MOCK_SLOW_SEC`/`MOCK_HANG_SEC` против `mock_provider_timeout_sec=5`.
- Find/get 500 и timeout: нельзя выводить «записи нет» (BUG-3).
- Битый 201 (не JSON, нет `payout_id`) и битый JSON в get/find.
- Повторное использование ключа и тела на всех POST; число POST/find на один attempt.
- Общий бюджет recovery против `api_sync_timeout_sec` (BUG-7).
- 404 неизвестного provider_id: 21 с бессмысленных повторов.

### Fallback и терминальные состояния
- `decline` на первом провайдере: find не вызывается, номер попытки и провайдер в истории.
- Все провайдеры declined: покрыто, но без проверки количества/порядка попыток и отсутствия вызовов find.
- Все провайдеры Unknown: fallback и итоговый FAILED, сверка ledger на отсутствие живых выплат.
- Асинхронный FAILED: финализация и возврат резерва (BUG-1), reconcile не должен зацикливаться.
- `COMPLETED` после уже терминального FAILED; монотонность статусов.
- `failure_code` без смены статуса; обновление попытки при stale_read.
- `visibility_lag` > sync-таймаута; `stale_read`; оба чтения недоступны до дедлайна.
- Recovery, съевший бюджет settlement; отсутствие поллинга после 21 с.

### Свежесть курсов
- Границы `age == limit`, `limit + 1µs`, `min(ttl, 6ч)`, отрицательный age (-60/-61).
- Stale direct с кросс-путём (RUB→PHP) — BUG-5.
- Нулевой direct и нулевая вторая нога — BUG-6.
- Равная свежесть двух кросс-путей, порядок словаря, недетерминизм.
- `source == target` без курсов; naive `updated_at`.
- Кэш TTL, инвалидация, промах не кэшируется, один вызов на пару в полёте (D4) — BUG-9.
- `/rates` 500/timeout в quote и в transfer; валидация нулевых/отрицательных/дублирующихся пар.
- `resolve_rate` не умеет обратную первую ногу, `convert_amount` умеет — паритет не зафиксирован.

### Идемпотентность
- Повтор в PROCESSING: та же попытка, нет нового payout.
- Повтор в PROCESSING при опустевших фильтрах — BUG-2.
- Повтор после истечения quote — BUG-11.
- Истинно параллельные POST с одним ключом (сейчас тест последовательный) и гонка UNIQUE.
- Возобновление PENDING после падения между резервом и попыткой.
- Непрерывность `attempt_no`, отсутствие дублей попыток при resume.
- Стабильность `body_hash` при разном порядке ключей recipient.

### Форматирование и payload
- Строка суммы для провайдера при minor_units 0 (JPY/VND) и 3 (KWD); сейчас проверяется только `format_money`.
- Соответствие `amount`/`currency` тела POST валидации мока (`amount_is_valid`).
- Нулевая/отрицательная сумма после конвертации не должна уходить провайдеру.

### Интеграция и chaos
- Интеграционные тесты подменяют `ProviderClient`, поэтому не проверяют: respx-коды, реальные заголовки, таймауты, `X-Scenario`, ledger мока.
- Ни один интеграционный тест не утверждает, **какой** провайдер выбран и в каком порядке; порядок зависит от `select(Provider)` без `ORDER BY`.
- `chaos_run.py` проверяет ledger и балансы, но не сверяет `payout_attempts` (ключи, `payout_id`, отсутствие живых выплат у FAILED-попыток) и не нацелен на unknown-result сценарии (`DROP_CREATED` при недоступном чтении).
- Нет теста, что при `MOCK_MODE=chaos` и серии `X-Scenario` (`ok/decline/error_5xx/slow`) каждый сценарий доходит до терминала за отведённое время.

## Порядок закрытия (предложение)

1. P0: BUG-1, BUG-2, BUG-3 + D7/D8 (защита от двойной выплаты и зависшего резерва).
2. P1: BUG-4 (маржа TARGET), BUG-5/BUG-6 (курсы), BUG-8 (SQLite), BUG-7 (sync-бюджет), BUG-11 (идемпотентность после TTL).
3. P1 тестовые дыры: seeded selection (A1/A2), DROP-сценарии (D1–D6), границы лимитов (B1–B5), settlement (E3–E5), кэш (BUG-9).
4. P2: детерминизм сортировки/путей, границы окон, коды 409/400/404, диагностика пустого `scored`, форматирование minor_units.
