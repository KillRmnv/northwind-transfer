# Ревью раунда 2: деньги, калькулятор котировок, округление, валюты

Область: `app/services/quotes.py`, `app/core/money.py`, `app/api/v1/quotes.py`, `app/api/mappers.py`, `data/*.json`, `examples/`, решения D2/D3/D4/D23/D24/D25.

Метод: чтение актуального кода после фиксов D25 + воспроизведение на реальных тарифах/курсах из `data/` и через `httpx.ASGITransport` с in-memory SQLite (в репозитории PG на `:5433` нет, поэтому интеграционные тесты котировок в прогоне недоступны: `23 passed` по юнитам, интеграция падает на подключении к БД — это инфраструктура, не находка). Все probing-скрипты лежат в `/tmp/opencode/`, файлы проекта не менялись.

Исходный уровень: первый отчёт `docs/test-cases/reviews/money-quotes.md` (F1–F19), фиксы D25 (`docs/DECISIONS.md:188-204`), новые регрессионные тесты `tests/unit/test_quote_edge_cases.py`. **Находки первого отчёта здесь не повторяются**; в конце дана только однострочная ведомость их статуса.

Severity: **P0** — клиент получает меньше денег/неверную сумму по API; **P1** — нарушение контракта/лимита с денежным эффектом; **P2** — латентный дефект, устойчивость, точность отображения, мёртвый код.

## 1. Сводная таблица новых находок

| ID | Sev | Кейс | Вход | Ожидание | Фактическое поведение | Файл:строка | Предлагаемый тест |
|----|-----|------|------|----------|-----------------------|-------------|-------------------|
| N1 | **P0** | TARGET: ветка min-комиссии **недостижима** — недоплата сохраняется после фикса D25 | `RU-TH-BANK`, `420.00 THB`, `TARGET`, rate `0.42`, fee_min `50.00 RUB`, percent `1.5` | `target_amount >= 420.00`, source `1050.00 RUB` | `201`, `source=1015.23`, `fee=50.00`, `to_convert=965.23`, `target=405.40 THB` (−14.60, −3.5%). Также `1000 THB→994.23`, `RU-TH-CARD 1015→1014.97`, `RU-VN 2 050 000→2 049 960`, `RU-JP 3800→3680` | `app/services/quotes.py:186,191,214` (предикат `fee_amount < fee_min_q`) + `:114,:135` (fee всегда `max(..., fee_min_q)`) | Unit: параметризованный sweep TARGET-сумм в зоне min-комиссии с `assert target >= amount`; API: POST `420.00` TARGET → `target_amount.amount >= "420.00"` |
| N2 | P1 | Наивный фикс предиката N1 всё равно недоплачивает: HALF_UP комиссии «съедает» потолок source | `RU-TH-CARD` (fixed `30`, min `80`), TARGET `1016.73 THB`, rate `0.42`; кандидат `2500.79 RUB` | source подобран так, что `target >= 1016.73` | `fee_raw=80.0158 → HALF_UP 80.02`, `to_convert=2420.77`, `target=1016.72` (−0.01); нужно `source=2500.80` | `app/services/quotes.py:187-195`, `:113-122` | Unit: для тех же входов проверка `calculate_target(...).target_amount >= amount`; тест «после выбора кандидата сделать финальный forward-прогон и инкрементировать source до выполнения инварианта» |
| N3 | P1 | TARGET: ложное отвержение по **min** лимита (проверка до комиссии) | `RU-TH-BANK`, TARGET `419.99 THB`, rate `0.42`, min `1000 RUB` | либо `AMOUNT_BELOW_MIN` соответствует реально списываемой сумме, либо котировка | `419.99/0.42 = 999.976 < 1000` → 400 `AMOUNT_BELOW_MIN`, хотя реальный source был бы `1015.21 RUB > min`; на `420.00` проходит и даёт минус N1 | `app/api/v1/quotes.py:86-88`, `app/services/quotes.py:274` | API: TARGET `419.99` и `420.00` → инвариант «решение по лимиту принимается по `source_amount` из результата» |
| N4 | P1 | Протухший **inverse**-курс молча подменяется кроссом (асимметрия с фиксом F4) | direct-курса нет, `KWD→RUB=270` age 10 дней, `RUB→EUR=0.0110` и `EUR→KWD=0.3300` свежие, `source=RUB`, `target=KWD`, ttl 300 | `RATE_UNAVAILABLE` (D4/A3: «молчаливых подмен нет») | `resolve_rate` возвращает `0.00363000` (кросс 0.0110×0.33) вместо отказа | `app/services/quotes.py:64-69` (нет `raise` на stale inverse), `:70-84` | Unit: stale inverse + свежие две ноги → `pytest.raises(RateUnavailableError)`; API: `RU-KW-BANK` с `freeze_rates` |
| N5 | P2 | Кросс-курс ограничен **двумя** рёбрами, хотя D24-конвертер умеет 3 | `AAA→BBB=2`, `BBB→CCC=3`, `CCC→DDD=5`; запрос `AAA→DDD` | курс `30` (путь из 3 рёбер) — или явный документированный отказ | `RATE_UNAVAILABLE: no rate AAA -> DDD`; в `providers.convert_amount` `max_hops=3` уже реализован | `app/services/quotes.py:70-84`; `app/services/providers.py:33-38` | Unit: 3-hop цепочка → ожидаемый курс; плюс граф с циклом/вторым путём для проверки детерминизма |
| N6 | P2 | `expires_at` в GET теряет таймзону: POST и GET дают **разные строки** | POST `/quotes`, затем GET того же id (SQLite-профиль) | одинаковый `expires_at` (или оба naive / оба aware) | POST `"2026-11-19T09:18:53.008126Z"`, GET `"2026-11-19T09:18:53.008126"` | `app/api/v1/quotes.py:93` (aware), `app/api/mappers.py:45` (сырой `quote.expires_at`), `app/models/tables.py:128` | Integration: `assert post.expires_at == get.expires_at`; отдельно `expires_at == created_at + quote_ttl_sec` |
| N7 | P2 | `calculate_source` не проверяет курс: `0`/`-1`/`NaN`/`sNaN` проходят в расчёт | `calculate_source(10000.00, тариф, rate)` с `rate ∈ {0, -1, NaN, sNaN, Infinity}` | доменная ошибка (как в `calculate_target:174-175`) | `0 → target=0.00, fee=150.00`; `-1 → target=-9850.00`; `NaN → target=NaN`; `sNaN/±Infinity → decimal.InvalidOperation` (→ 500 при прямом вызове) | `app/services/quotes.py:100-160` (нет `_valid_rate`), сравнить `:174-175`, `:87-88` | Unit: параметризовать `0/-1/NaN/sNaN/Infinity` → `RateUnavailableError` в `calculate_source` и `calculate_target` |
| N8 | P2 | Граничный случай SPEC#7: произведение **влезает** в `NUMERIC(20,8)`, но квантование до minor units переполняет колонку | `99999999999.99999999 × 10.00000000` | stored `999999999999.99999990` (SPEC money-rounding #7), ответ по minor units | `round_money(..., 2) = 1000000000000.00` — 13 целых цифр > 12 → DB overflow/500 | `app/core/money.py:12-13`, `app/services/quotes.py:122,137`, `app/models/tables.py:19,120-126` | Repository-тест: сохранить результат, читать raw `NUMERIC`, ожидать 8 знаков; unit на точный продукт до квантования |
| N9 | P2 | Мёртвый дубль `return QuoteResult(...)` после уже выполненного `return` в `calculate_target` | чтение кода, `charged_in=TARGET` | одного return на ветку | 12 строк недостижимого кода (`:229-240`), копия `:217-228`; маскирует настоящую логику при правке N1/N2 | `app/services/quotes.py:217-240` | Arch/lint-тест на недостижимый код или просто удалить блок при фиксе; тест — регрессия N1/N2 |

## 2. Новые подтверждённые баги

### N1. TARGET недоплачивает в зоне минимальной комиссии — P0

**Корень.** В D25 ветка выбора комиссии переписана на «прямой прогон + проверку», но проверка использует то же самое значение, которое `calculate_source` уже зажал снизу:

```python
# app/services/quotes.py:114 (и :135)
fee = max(_quant(fee_raw, ...), _quant(tariff.fee_min, ...))   # fee >= fee_min_q ВСЕГДА
# app/services/quotes.py:186, :191, :214
if forward.fee_amount < fee_min_q:                              # => ВСЕГДА False
```

`fee_min_q` в `calculate_target` считается тем же `_quant(fee_min, …)`. Следовательно **обе** ветки минимальной комиссии (`charged_in=SOURCE` и `charged_in=TARGET`) недостижимы. Кандидат из процентной формулы D3 `(target/rate + fixed)/(1−p)` предполагает, что комиссия равна `p·source + fixed`; когда `p·source + fixed < fee_min`, фактическая комиссия больше, `to_convert` меньше, и target недоплачивает. Чем ближе сумма к нижней границе, тем больше относительная недоплата.

Воспроизведение (реальные тарифы и курсы, `now=2026-11-05T12:00Z`, rate из `data/rates.json`):

| Направление | TARGET (запрос) | Значение в check_limits | Source | Fee | Target | Дельта |
|---|---|---|---|---|---|---|
| `RU-TH-BANK` | 420.00 THB | 1000.00 RUB (ровно min, проходит) | 1015.23 RUB | 50.00 | **405.40 THB** | **−14.60 (−3.5%)** |
| `RU-TH-BANK` | 1000.00 THB | 2380.95 RUB | 2417.22 RUB | 50.00 | 994.23 THB | −5.77 |
| `RU-TH-CARD` | 1015.00 THB | 2416.67 RUB | 2496.60 RUB | 80.00 | 1014.97 THB | −0.03 |
| `RU-VN-BANK` (0 minor, `charged_in=TARGET`) | 2 050 000 VND | 6833.33 RUB | 6933.20 RUB | 30 000 VND | 2 049 960 VND | −40 |
| `RU-JP-BANK` (0 minor) | 3800 JPY | 2000 RUB | 2036.66 RUB | 100.00 RUB | 3680 JPY | −120 |

API-подтверждение (`POST /quotes`, `amount_type=TARGET`, `amount="420.00"`, rate `0.42`): `201`, breakdown честно показывает:

```
fee  = max(1015.23 RUB × 1.50000% + 0.00 RUB, min 50.00 RUB) = 50.00 RUB
to_convert = 1015.23 − 50.00 = 965.23 RUB
target = 965.23 × 0.4200 = 405.40 THB
```

То есть инверсия D3 сломана именно на стыке «процент < минимум»: source посчитан под комиссию 15.23, списано 50.00. Корректный source для 420.00 THB — `1000.00 + 50.00 = 1050.00 RUB`.

Почему регрессионные тесты D25 зелёные: `test_target_never_underpays_*` (`tests/unit/test_quote_edge_cases.py:42-86`) берут суммы **выше** порога min-комиссии (`1020 THB` с CARD, `2 070 000 VND`, `19 890 JPY`), где `p·source ≥ min` и процентная формула работает. Первый отчёт F1 описывал тот же верхний диапазон; нижний диапазон не покрыт нигде.

Предлагаемый тест (unit + API):

```python
@pytest.mark.parametrize(("direction", "amount", "rate"), [
    ("RU-TH-BANK", "420.00",  "0.42"),
    ("RU-TH-BANK", "1000.00", "0.42"),
    ("RU-TH-CARD", "1015.00", "0.42"),
    ("RU-VN-BANK", "2050000", "300"),
    ("RU-JP-BANK", "3800",    "1.9"),
])
def test_target_never_underpays_in_min_fee_zone(direction, amount, rate):
    res = service.calculate_target(Decimal(amount), tariff(direction), Decimal(rate), MINOR)
    assert res.target_amount >= Decimal(amount)

async def test_api_target_420_thb(api, freeze_rates, live_rates):
    freeze_rates(live_rates)
    r = await api.post("/quotes", json=quote_payload(amount="420.00", amount_type="TARGET"))
    assert r.status_code == 201
    assert Decimal(r.json()["target_amount"]["amount"]) >= Decimal("420.00")
```

Подсказка для фикса: предикат должен сравнивать **неквантованную** процентную комиссию с минимумом (или просто проверять `forward.target_amount < amount`) и после выбора кандидата делать финальный forward-прогон с инкрементом source до `minor_units`, если инвариант ещё не выполнен (см. N2).

### N2. Восстановление ветки min-комиссии само по себе недостаточно — P1

Если заменить предикат N1 на «процентная комиссия ниже минимума», остаётся остаточная недоплата из-за HALF_UP комиссии:

| Вход | Кандидат source | `fee_raw` | `fee` (HALF_UP) | `to_convert` | Target | Ожидание |
|---|---|---|---|---|---|---|
| `RU-TH-CARD`, TARGET `1016.73 THB`, rate `0.42` | `2500.79 RUB` | `80.0158` | `80.02` | `2420.77` | `1016.72` | `>= 1016.73`; достаточно `source=2500.80` |

Диапазон остаточной недоплаты на этом тарифе — `1016.73…1016.78 THB` (−0.01). Причина: `to_convert_exact + fee_min` не учитывает, что `calculate_source` округляет саму комиссию HALF_UP вверх. Это и есть «несоответствие округления fee и amount_to_convert»: `source − fee == to_convert` верно, но `to_convert` уже меньше `target/rate`.

Предлагаемый тест: тот же параметризованный тест N1 с шагом `0.01` вокруг `1016.40…1017.00` — он уловит и этот случай, если фикс написан «в лоб».

### N3. TARGET: min-лимит проверяется по сумме до комиссии — P1

`amount_in_limit_currency` (`app/services/quotes.py:274`) делит TARGET на rate, а `check_limits` вызывается до расчёта (`app/api/v1/quotes.py:86-88`). Для **max** это даёт известный обход F2; для **min** — зеркальную ложную ошибку:

| TARGET | Передано в `check_limits` | Решение API | Реальный source (если бы считали) |
|---|---|---|---|
| `419.99 THB` | `999.976 RUB` | 400 `AMOUNT_BELOW_MIN` | `1015.21 RUB` (выше min `1000`) |
| `420.00 THB` | `1000.00 RUB` | 201 (затем недоплата N1) | `1015.23 RUB` |

Клиент, готовый заплатить больше минимума, получает отказ. Ожидание: граница `min` применяется к итоговой списываемой сумме (или решение принимается после `calculate_target`). Предлагаемый тест: API-параметризация `419.99 / 420.00` с проверкой, что при 201 `source_amount >= min`, а при 400 действительно нет допустимого source.

### N4. Протухший inverse подменяется кроссом — P1

D25 закрыл F4 только для direct-ветки (`app/services/quotes.py:57-63`). Для inverse (`64-69`) при `_fresh() == False` нет `raise`, управление переходит к двухзвенному кроссу. Воспроизведение:

```python
rates = {
    ("KWD", "RUB"): RateData(Decimal("270"), now - timedelta(days=10)),  # stale inverse
    ("RUB", "EUR"): RateData(Decimal("0.0110"), now),
    ("EUR", "KWD"): RateData(Decimal("0.3300"), now),
}
resolve_rate(rates, "RUB", "KWD", now, 300, MINOR)  # -> 0.00363000
```

Ожидание по D4: `RateUnavailableError`. Побочный риск: для `RU-KW-BANK` курс тихо меняется с `1/270 = 0.0037037…` на `0.00363` (~2%), то есть сумма в KWD у клиента отличается от «честного» обратного курса. Предлагаемый тест — как в таблице, плюс симметричный случай «invalid inverse + валидный кросс» (там сейчас `raise`, что не симметрично stale-ветке).

### N5. Трёхзвенный кросс и циклы графа — P2

`resolve_rate` (`app/services/quotes.py:70-84`) перебирает только `source → X → target`. D24 уже ввёл графовый `convert_amount` с `max_hops=3` (`app/services/providers.py:33-38`), но калькулятор котировок его не использует. Проверка: `AAA→BBB=2`, `BBB→CCC=3`, `CCC→DDD=5` → `RateUnavailableError` вместо курса `30`. На текущих `data/rates.json` ни одно направление не требует 3 переходов (поэтому P2), но решение D24 делает такой путь легитимным. Дополнительно не протестирован выбор между двумя равносвежими путями: `best` обновляется только при `freshness > best[1]` (`:80`), то есть победитель зависит от порядка ключей в словаре/JSON.

Предлагаемый тест: 3-hop цепочка; граф с циклом `A→B→A` и вторым путём; два пути с одинаковой `updated_at` — ожидать детерминированный выбор, зафиксированный в ASSUMPTIONS.

### N6. GET-контракт: таймзона и "протухший" GET (связь с F6)

- **Новое:** `POST` отдаёт `expires_at` с `Z` (`app/api/v1/quotes.py:93`), `GET` — без него (`app/api/mappers.py:45`, SQLite возвращает naive `DateTime(timezone=True)`). Воспроизведено: `"2026-11-19T09:18:53.008126Z"` vs `"2026-11-19T09:18:53.008126"`. Клиент, сравнивающий строки или парсящий naive как локальное время, получит расхождение до часов. `as_utc()` в `transfers.py:183` проблему обхода TTL решает, но маппер её не использует.
- **Наследие (F6, не дублируем):** `GET /quotes/{id}` по-прежнему отдаёт `200` для котировки с `expires_at = now − 1с` — проверка есть только в `create_transfer:183`. Расхождение с объявленным в роутере 410 остаётся.

Предлагаемый тест: `assert post.json()["expires_at"] == get.json()["expires_at"]` и `expires_at == created_at + quote_ttl_sec`; отдельно (перенос из F6) GET после истечения → 410.

### N7–N9. Прочее

- **N7.** В отличие от `calculate_target:174-175`, `calculate_source` не вызывает `_valid_rate`. Прямые вызовы сервиса с `rate=0/-1/NaN` молча дают `0.00` / отрицательный / `NaN` target; `sNaN` и `±Infinity` бросают undecorated `InvalidOperation` (в API — 500). Через `POST /quotes` курс проходит через `resolve_rate`, поэтому это латентный P2, но контракт «курс непригоден → RATE_UNAVAILABLE» нарушен на уровне сервиса. Тест: параметризовать все пять значений для обеих функций.
- **N8.** SPEC money-rounding #7: `99999999999.99999999 × 10` даёт точный продукт `999999999999.99999990` (влезает в 12 целых цифр), но `round_money(..., 2) = 1000000000000.00` — 13 цифр, вставка упадёт. Это прямое следствие F5 (квантование до записи), но **новый конкретный пограничный кейс** из SPEC, которого нет ни в первом отчёте, ни в тестах. Тест: repository, читать raw `NUMERIC(20,8)` и ожидать 8 знаков.
- **N9.** `app/services/quotes.py:229-240` — недостижимая копия `return QuoteResult(...)` после `return` на `:217-228`. Внесена фиксом D25. При правке N1 легко поправить мёртвую ветку и подумать, что фикс готов. Тест-индикатор — регрессия N1/N2; минимальное действие — удалить блок.

## 3. Не покрыто тестами (новые дыры после D25)

| Область | Что именно не проверено | Ссылка |
|---|---|---|
| TARGET, min-комиссия | Sweep `420 / 1000 / 1010 / 1015 / 1016.40 / 1016.73 THB`, `2 050 000 VND`, `3800 JPY` с инвариантом `target >= amount`; граница «процентная комиссия == fee_min» и HALF_UP-остаток (N1/N2) | `tests/unit/test_quote_edge_cases.py:42-86`, `app/services/quotes.py:177-216` |
| TARGET, лимиты | min-граница `419.99/420.00 THB` (N3); после фикса N1 — повторная проверка max `124200/126000 THB`, `1 545 000 JPY`, `60 000 000 VND` (F2, остаётся открытым) | `app/api/v1/quotes.py:86-88`, `app/services/quotes.py:262-280` |
| Курсы | stale inverse + живой кросс (N4); invalid inverse + живой кросс; трёхзвенный путь и граф с циклом (N5); два равносвежих пути; случай `rate` с >8 знаками при сохранении и показе | `app/services/quotes.py:46-93`, `app/services/providers.py:33-38` |
| TTL / время | `expires_at` POST↔GET, включая таймзону (N6); `age == limit`, `limit+1s`, будущее `+60/+61s`; direction TTL 300/600/900/1800 (тесты везде `10**9`); GET после истечения (F6) | `app/services/quotes.py:56,91-93`, `app/api/v1/quotes.py:93,108-113`, `tests/conftest.py:123-131` |
| Деньги/округление | HALF_UP-таблицы для `minor_units=0/2/3` и отрицательных значений, `-0.00` (F17); SPEC#7 на потолке (N8); точный продукт в `NUMERIC(20,8)` (F5) | `app/core/money.py:4-17`, `app/models/tables.py:19,116-129` |
| Валидация входа | Отрицательная сумма на уровне сервиса (`calculate_source(-10000, …)` возвращает отрицательный target — F17), `NaN`/`Infinity` в `calculate_source` (N7); unicode-цифры и длина строки (F12) | `app/core/money.py:20-32`, `app/api/v1/quotes.py:67-76`, `app/services/quotes.py:100-160` |

## 4. Статус находок первого раунда (без повторного разбора)

| ID | Статус по актуальному коду |
|---|---|
| F1 (TARGET upper zone 1020 THB / 2 070 000 VND / 10 420 JPY / 19 890 JPY) | Закрыто D25; **но нижняя зона той же ошибки осталась — N1** |
| F2 (TARGET обходит max по source) | Открыт: `124200 THB → source 300217.55 RUB > max 300000` |
| F3 (курс 0/отрицательный/NaN) | Закрыто на `resolve_rate`/`calculate_target`; пробел в `calculate_source` — N7 |
| F4 (stale direct → cross) | Закрыто; асимметрия для inverse — N4 |
| F5 (квантование до записи) | Открыт; SPEC#7 — N8 |
| F6 (GET протухшей котировки) | Открыт; таймзона — N6 |
| F10/F11/F12/F13/F14/F15/F16/F17/F18/F19 | Без изменений; дублирование в этом отчёте опущено |

## 5. Порядок добавления тестов

1. **Сначала падающие P0/P1:** N1 (sweep TARGET в зоне min-комиссии, включая `420.00 THB` через API), N2 (шаг 0.01 вокруг `1016.73`), N3 (min-граница `419.99/420.00`), N4 (stale inverse).
2. **Затем контракт/курсы:** N5 (3-hop, детерминизм), N6 (expires_at POST↔GET + наследие F6).
3. **Устойчивость:** N7, N8, N9 + перенос не покрытых таблиц из раздела 3.
