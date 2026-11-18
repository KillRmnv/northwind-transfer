# Ревью: деньги и калькулятор котировок

Область: `app/core/money.py`, `app/services/quotes.py`, `app/schemas/quotes.py`, API котировок, `data/*.json`, `examples/*.json`, решения D2/D3/D4, тест-кейсы `money-rounding` / `quote-contract` / `rate-freshness`.

Метод: чтение кода + воспроизведение расчётов на реальных тарифах и курсах из `data/` (скрипты запускались через `.venv/bin/python`, файлы проекта не менялись). Базовый прогон `tests/unit/test_quote_calculator.py` — 15 passed, то есть найденное ниже тестами не ловится.

Severity: **P0** — неверные деньги у клиента/отрицательные суммы; **P1** — нарушение лимита/контракта/решения с денежным эффектом, обязательное по SPEC; **P2** — латентный дефект, точность отображения, устойчивость.

## 1. Сводная таблица находок

| ID | Sev | Кейс | Вход | Ожидание | Текущее поведение | Файл:строка | Предлагаемый тест |
|----|-----|------|------|----------|-------------------|-------------|-------------------|
| F1 | P0 | TARGET: неверно выбирается ветка комиссии (min vs percent) | `RU-TH-CARD`, `amount=1020.00`, `TARGET`, rate `0.42` | `target_amount=1020.00` (не меньше запрошенного) | `1019.93` (недоплата 0.07); также `RU-VN` 2 070 000 → 2 069 800 (−200), `RU-JP` 10 420 → 10 419 (−1), `KW-JP` 19 890 → 19 887 (−3) | `app/services/quotes.py:167-175`, `:190-195`, `:176`, `:197` | Unit + API: для набора TARGET-сумм `assert result.target_amount >= Decimal(amount)`; отдельно точные 1020.00 / 2 070 000 / 10 420 / 19 890 |
| F2 | P1 | TARGET: лимит max проверяется по `target/rate`, а не по фактическому source с комиссией | `RU-TH-BANK`, `amount=124200.00`, `TARGET`, rate `0.42` | `AMOUNT_ABOVE_MAX` либо `source_amount <= 300000.00` | 201; `source_amount=300217.55`; при `126000.00` → `304568.53` (> max на 1.5%) | `app/services/quotes.py:86-88`, `:232-250`; `app/services/transfers.py:188-219` (повторной проверки max нет) | API: TARGET `124200.00` → 400 `AMOUNT_ABOVE_MAX`; инвариант `source_amount <= tariff.max_amount` |
| F3 | P0 | Нулевой/отрицательный/нечисловой курс принимается (проверка только у inverse) | direct rate `0`, `-1`, `NaN`, `Infinity` | `RATE_UNAVAILABLE` (money-rounding #5), деньги не считаются | `resolve_rate` возвращает `0`/`-1`; SOURCE `10000.00` RUB → `target=0.00`; `RU-VN` rate 0 → `target=-30000` VND; rate `-1` → `target=-9850.00` / `source=-950.00` | `app/services/quotes.py:57-59`, `:60-62`, `:64-74` | Unit: direct/inverse/cross с `0`, `-1`, `NaN`, `Infinity` → `RateUnavailableError`; `calculate_source` не возвращает 0/отрицательный target |
| F4 | P1 | Протухший direct-курс молча подменяется свежим кросс-курсом | `RU-PH-CASH`, `now=2026-11-05T12:00Z`, ttl 600: `RUB→PHP` от 2026-11-01 (stale), `RUB→USD` и `USD→PHP` свежие (2 мин) | `RATE_UNAVAILABLE`: пара `RUB→PHP` протухла, «молчаливых подмен нет» (D4/A3) | `resolve_rate` возвращает `0.0120 × 58.0000 = 0.6960` вместо 0.7000; котировка выдаётся | `app/services/quotes.py:57-77`; данные `data/rates.json:32-36`, `:20-24`, `:56-60` | Unit: stale direct + свежие обе ноги → `pytest.raises(RateUnavailableError)`; API-тест с `freeze_rates` на эти три курса |
| F5 | P1 | Квантование до сохранения: в БД пишется уже округлённая сумма, а A1/D2 требуют хранить точно | rate `1.90000001`, `RU-JP-BANK`, amount `10000.00` | в `quotes.target_amount` `18658.00009820`, в ответе `"18658"` | `calculate_source` квантует target до `minor_units`; в БД `18658.00000000`, точность продукта потеряна | `app/services/quotes.py:110-111`, `:125-126`; `app/models/tables.py:19`, `:125`; `docs/ASSUMPTIONS.md:7`; `docs/DECISIONS.md:11-18` | Repository-тест: сохранить котировку, прочитать raw `NUMERIC`, ожидать 8 знаков продукта; unit — точный продукт `0.025` для `0.05 × 0.5`, в ответе `0.03` |
| F6 | P1 | `GET /quotes/{id}` не проверяет `expires_at` | котировка с `expires_at=now-1s` | 410 `QUOTE_EXPIRED` (quote-contract #12) | 200 с телом котировки; expiry проверяется только при создании перевода | `app/api/v1/quotes.py:99-113`; `docs/test-cases/quote-contract/SPEC.md:33`; `app/services/transfers.py:163` | Integration: вставить quote с прошедшим `expires_at`, `GET /quotes/{id}` → 410; плюс тест `expires_at == created_at + quote_ttl_sec` |
| F7 | P1 | Нет BVA на границах min/max: тесты берут значения далеко от границ | 999.99 / 1000.00 / 300000.00 / 300000.01 RUB | ниже min → `AMOUNT_BELOW_MIN`, ровно min/max → 201, выше max → `AMOUNT_ABOVE_MAX` | Границы не проверены; тесты используют 500.00 и 500000.00 | `tests/unit/test_quote_calculator.py:152-169`; `tests/integration/test_quotes_api.py:67-76`; `docs/test-cases/quote-contract/SPEC.md:24` | Параметризовать 4 границы для SOURCE и TARGET (включая KWD/JPY) |
| F8 | P1 | Не покрыты 7 из 11 направлений и ключевые денежные пути | `RU-KW-BANK` (inverse rate), `RU-AE-BANK`, `RU-PH-CASH`, `KW-TH-BANK` (KWD→THB), `KW-JP-BANK` (TARGET+JPY), `US-PH-BANK`, `KZ-TH-BANK` | эталонные суммы для каждого | `POSITIVE_CASES` содержит только 7 файлов; inverse-курс и `charged_in=TARGET` в TARGET-режиме не считаются ни разу; для KZ есть только общий тест «курса нет» | `tests/unit/test_quote_calculator.py:99-107`; `examples/` | Добавить examples/тесты для inverse `RUB→KWD=1/270`, `KW-JP` TARGET, `RU-VN` TARGET, `KW-TH` (3→2 minor) |
| F9 | P1 | Интеграционные тесты котировок проверяют только SOURCE | payload всегда `amount_type="SOURCE"` и RUB/THB | TARGET через API, JPY/KWD/VND-строки | Ни одного API-теста TARGET; не проверены строки `10000` (JPY) и `50.000` (KWD) | `tests/integration/test_quotes_api.py:10-21`; `:24-123` | API: TARGET 201, KWD 3 знака, JPY 0 знаков, VND `charged_in=TARGET` |
| F10 | P1 | Не проверены границы свежести и TTL направления | ровно `min(ttl, 6ч)`, ±1 с, будущее `+60/+61 с`, ttl 300/600/900/1800 | на границе — fresh, за границей — `RATE_UNAVAILABLE` | Unit всегда передаёт `ttl=10**9` (работает только 6-часовой cap); direction TTL 300/600/1800 не exercised | `tests/unit/test_quote_calculator.py:127`, `:141`, `:162`, `:208`, `:215`; `app/services/quotes.py:56`, `:80-82` | Параметризовать `(updated_at, ttl)` вокруг `limit_sec`; отдельно `age=-60` и `-61` |
| F11 | P2 | `format_rate` использует HALF_EVEN, а не HALF_UP; малые курсы показываются как 0 | `1.23445`, `0.00005`, `1.00005`; inverse `RUB→KWD=1/270` | `1.2345`, `0.0001`, `1.0001`; rate `0.0037…` | `1.2344`, `0.0000`, `1.0000`; `Decimal.__format__` округляет half-even | `app/services/quotes.py:253-254`; `app/api/mappers.py:28`, `:42`; тест зеркалит форматирование `tests/unit/test_quote_calculator.py:89` | Unit: `format_rate` на литеральных ожиданиях; API: rate у inverse-направления |
| F12 | P2 | `amount_string_is_valid` пропускает не-ASCII цифры и «²», не ограничивает длину | `"١٢٣.٤٥"`, `"１２３.４５"`, `"².00"`, `"0.00"`, 30 цифр | только `[0-9]`, ровно `minor_units`, >0, в пределах разумной длины | `"١٢٣.٤٥"` → True и `Decimal` даёт 123.45; `"².00"` → True, но `Decimal` падает (422 «amount is not a number»); `"0.00"` доходит до `amount <= 0` | `app/core/money.py:20-28`; `app/schemas/quotes.py:15`; `app/api/v1/quotes.py:67-76` | Таблица валидных/невалидных строк, включая unicode, `0.00`, `+1.00`, `1_000.00`, `1e3`, пробелы, 30 цифр |
| F13 | P2 | Огромные значения: `InvalidOperation` вместо именованного отказа; `NUMERIC(20,8)` вмещает только 12 целых цифр | `9`×30, 29 цифр + `.00`, rate `1e13`, target `>=1e12` | 400 с именованным кодом (money-rounding #6/#11) | `round_money` бросает `decimal.InvalidOperation`; необработанный → 500. Курс/сумма за `12` целых цифр → DB overflow → 500 | `app/core/money.py:12-13`; `app/services/quotes.py:110-113`; `app/models/tables.py:19`, `:125`; `app/api/exception_handlers.py:34-46` | Unit: `round_money(Decimal("9"*30), 0)` → доменная ошибка; integration: mock rate `1e10`, сумма 1000 → 400, не 500 |
| F14 | P2 | `amount_in_limit_currency` молча возвращает сумму без конвертации | rate 0 или валюта лимита не source/target | явная ошибка/`RATE_UNAVAILABLE`, лимит в правильной валюте | `return amount` при `rate == 0` и в fallback-ветке | `app/services/quotes.py:240-250` | Unit: limit currency вне пары, rate 0 → ошибка, а не «сумма как есть» |
| F15 | P2 | Примитивы `money.py` почти не покрыты | `quantum(0/2/3)`, `round_money` `0.005`, `-0.005`, `2.675`, `1.005`, `0.5`/`1.5`/`2.5` для 0 знаков | HALF_UP, отрицательные — от нуля, `minor_units=0` — целые | В тестах только 3 happy-case `format_money` | `app/core/money.py:4-17`; `tests/unit/test_quote_calculator.py:218-224` | Прямые таблицы `quantum` / `round_money` / `format_money` |
| F16 | P2 | `parse_money` не используется и принимает `NaN`/`Infinity`/`1_0` | `"NaN"`, `"Infinity"`, `"1_0"` | либо удалить, либо валидировать finite | `Decimal("NaN")`, `Decimal("1_0") == 10` | `app/core/money.py:31-32` | Unit: `parse_money` на эти входы (или arch-тест «нет мёртвого кода») |
| F17 | P2 | Отрицательный ноль/отрицательные суммы в сервисе | `format_money(-0.001, 2)`, fee > amount | `"0.00"` либо отказ; отрицательный `target/source` невозможен | `"-0.00"`; `calculate_source` при fee > amount даёт отрицательный `to_convert/target` | `app/core/money.py:16-17`; `app/services/quotes.py:110-111` | Unit на `-0.001` и на fee > amount → доменная ошибка |
| F18 | P2 | `GET /quotes/{id}` теряет `breakdown`; POST и GET не сверяются | POST → GET одной котировки | одинаковые `breakdown`, rate, суммы | GET отдаёт `breakdown=[]`; тест проверяет только `quote_id` | `app/api/mappers.py:35-46`, `:44`; `tests/integration/test_quotes_api.py:47-51` | Integration: сравнить POST-тело и GET-тело целиком (с допустимым отличием только `expires_at`/id) |
| F19 | P2 | Заявленный в D4 кэш курсов на `quote_ttl_sec` отсутствует | два POST подряд в пределах TTL | второй расчёт без запроса в mock-provider | Каждый POST делает `RatesClient.fetch()`; тестов на число вызовов нет | `app/api/v1/quotes.py:77-82`; `app/services/rates.py:14-28`; `docs/DECISIONS.md:26-30` | Integration с mock-счётчиком: два POST → один `GET /rates`; тест инвалидации внутри TTL |

## 2. Подтверждённые баги

### F1. TARGET-режим систематически недоплачивает (P0)

Причина: в TARGET-режиме ветка «процент или минимум» выбирается по комиссии, посчитанной от промежуточной суммы, а не от итогового source:

- `charged_in=SOURCE`: `fee_guess = to_convert_exact × percent + fixed` (`app/services/quotes.py:168`) вместо `source × percent + fixed`. Минимум выбирается слишком часто.
- `charged_in=TARGET`: `gross_exact_fee = amount × percent + fixed` (`:190`) вместо `gross × percent + fixed`.

Когда фактическая комиссия в прямом прогоне оказывается больше `fee_min`, формула `source = to_convert + fee_min` (`:175`, `:195`) даёт source меньше нужного, и прямой `calculate_source` возвращает target меньше запрошенного (`:177-188`, `:198-209`). Это не «1 minor unit» из A2, а до 273 minor units.

Воспроизведение (реальные тарифы и курсы, `now=2026-11-05T12:00Z`):

| Направление | Запрошено TARGET | Получено target | Недоплата | Source |
|---|---|---|---|---|
| `RU-TH-CARD` (`data/tariffs.json:38-50`) | 1 020.00 THB | 1 019.93 | 0.07 THB | 2 508.58 RUB |
| `RU-TH-CARD` | 1 030.00 THB | 1 029.73 | 0.27 THB | 2 532.39 RUB |
| `RU-VN-BANK` (`:88-100`) | 2 070 000 VND | 2 069 800 | 200 VND | 7 000.00 RUB |
| `RU-JP-BANK` (`:53-76`) | 10 420 JPY | 10 419 | 1 JPY | 5 585.79 RUB |
| `KW-JP-BANK` (`:213-225`) | 19 890 JPY | 19 887 | 3 JPY | 39.208 KWD |

Граница корректной работы для `RU-TH-CARD` — 1 016.40 THB: при 1 020.00 уже недоплата. Для `RU-VN-BANK` корректный порог — около 2 053 333 VND, дальше до ~2 083 333 идёт недоплата.

Ожидание: `target_amount` не меньше запрошенной суммы, а source минимален; для 1 020.00 корректная формула даёт ровно 1 020.00 при source 2 508.75 RUB.

Предлагаемый тест:

```python
@pytest.mark.parametrize(("direction_id", "target", "rate"), [
    ("RU-TH-CARD", "1020.00", "0.4200"),
    ("RU-VN-BANK", "2070000", "300.0000"),
    ("RU-JP-BANK", "10420", "1.9000"),
    ("KW-JP-BANK", "19890", "515.0000"),
])
def test_target_mode_never_underpays(direction_id, target, rate, now):
    result = service.calculate_target(Decimal(target), tariff(direction_id),
                                      Decimal(rate), MINOR)
    assert result.target_amount >= Decimal(target)
```

Плюс API-вариант на `POST /quotes` с `amount_type=TARGET` для 1 020.00 и 2 070 000: проверять `target_amount.amount` и `source_amount.amount`.

### F2. TARGET обходит max направления (P1)

`check_limits` вызывается с `amount_in_limit_currency(...)` (`app/api/v1/quotes.py:86-88`). Для TARGET эта функция делит target на rate (`app/services/quotes.py:240-244`), то есть проверяет сумму до комиссии. Фактический `source_amount` после `calculate_source` больше на величину комиссии и может выйти за `max_amount`; при создании перевода направление повторно не проверяется (`app/services/transfers.py:188-219`).

| Направление | TARGET | Переданный в лимит | Фактический source | max |
|---|---|---|---|---|
| `RU-TH-BANK` | 124 200.00 THB | 295 714.29 RUB | 300 217.55 | 300 000.00 |
| `RU-TH-BANK` | 126 000.00 THB | 300 000.00 RUB | 304 568.53 | 300 000.00 |
| `KW-JP-BANK` | 1 545 000 JPY | 3 000.000 KWD | 3 045.686 KWD | 3 000.000 |
| `RU-VN-BANK` | 60 000 000 VND | 200 000.00 RUB | 202 446.02 RUB | 200 000.00 |

Ожидание: `AMOUNT_ABOVE_MAX` (400) либо лимит применяется к итоговой списываемой сумме. Если лимит задуман как сумма «до комиссии», это нужно зафиксировать в ASSUMPTIONS и всё равно не пропускать `source_amount > max` в перевод.

Предлагаемый тест: POST `RU-TH-BANK`, `amount="124200.00"`, `amount_type="TARGET"` → 400 `AMOUNT_ABOVE_MAX`; для 201-варианта — инвариант `Decimal(body["source_amount"]["amount"]) <= 300000.00`.

### F3. Нулевой/отрицательный/NaN курс не отвергается (P0)

`resolve_rate` проверяет только `inverse.rate != 0` (`app/services/quotes.py:61`). Direct-ветка (`:57-59`) возвращает `0`, `-1`, `NaN`, `Infinity` как валидный курс. Кросс-ветка (`:64-74`) проверяет только `first.rate == 0`, но не `second.rate` и не знак.

Фактическое поведение (синтетические rate, реальные тарифы):

| rate | SOURCE RU-TH 10 000 RUB | RU-VN charged_in=TARGET 7 000 RUB | TARGET RU-TH 1 000 THB |
|---|---|---|---|
| `0` | `target=0.00`, fee 150.00 | `target=-30000` VND | `RateUnavailableError` |
| `-1` | `target=-9850.00` | — | `source=-950.00` |

Ожидание по `docs/test-cases/money-rounding/SPEC.md:29` (кейс 5): «курс непригоден → `RATE_UNAVAILABLE`, перевод не роняется молча». Дополнительно: `calculate_source/calculate_target` должны отказывать при `rate <= 0` и не возвращать отрицательные суммы.

Предлагаемый тест:

```python
@pytest.mark.parametrize("rate", ["0", "-1", "NaN", "Infinity"])
def test_nonpositive_or_nonfinite_rate_is_unavailable(rate, now):
    with pytest.raises(exceptions.RateUnavailableError):
        service.resolve_rate({("RUB", "THB"): RateData(Decimal(rate), now)},
                             "RUB", "THB", now, 900, MINOR)
```

Отдельно: inverse `0`; кросс, где `second.rate` равен 0 или отрицателен; `calculate_source` с rate 0 для `charged_in=TARGET` (не должно быть `target=-30000`).

### F4. Протухший direct-курс подменяется кросс-курсом (P1)

D4 (`docs/DECISIONS.md:26-30`) и A3 (`docs/ASSUMPTIONS.md:13-15`) требуют `RATE_UNAVAILABLE`, если курс пары старше `min(ttl, 6ч)`, «молчаливых подмен нет». Код после stale direct (`app/services/quotes.py:57-59`) сразу пробует inverse (`:60-62`) и двухзвенный кросс (`:63-77`).

Воспроизведено на данных, которые отдаёт `mock_provider.ReferenceData.live_rates` для `now=2026-11-05T12:00Z`:

- `RUB→PHP` — direct `0.7000`, `updated_at=2026-11-01` → stale;
- `RUB→USD` — `0.0120`, возраст 2 мин;
- `USD→PHP` — `58.0000`, возраст 2 мин;
- ttl `RU-PH-CASH` = 600 с.

`resolve_rate` возвращает `0.6960` (0.0120 × 58.0), то есть `RU-PH-CASH` получает котировку по другому курсу, хотя обязательная пара протухла.

Существующий тест `test_stale_rate_rejected` (`tests/unit/test_quote_calculator.py:212-215`) берёт `RUB→AED`, для которого кросс-пути нет, поэтому зелёный и дефект не ловит.

Предлагаемый тест:

```python
def test_stale_direct_rate_is_not_replaced_by_cross(now):
    rates = {
        ("RUB", "PHP"): RateData(Decimal("0.7000"), now - timedelta(days=4)),
        ("RUB", "USD"): RateData(Decimal("0.0120"), now - timedelta(minutes=2)),
        ("USD", "PHP"): RateData(Decimal("58.0000"), now - timedelta(minutes=2)),
    }
    with pytest.raises(exceptions.RateUnavailableError):
        service.resolve_rate(rates, "RUB", "PHP", now, 600, MINOR)
```

И API-вариант через `freeze_rates` с ожиданием 422 `RATE_UNAVAILABLE`.

### F5. В БД сохраняется округлённая сумма, а не точный продукт (P1)

A1 (`docs/ASSUMPTIONS.md:7`) и D2 (`docs/DECISIONS.md:11-18`) фиксируют split scale: хранить `NUMERIC(20,8)` точно, округлять HALF_UP только на показе. `money-rounding` кейсы 1–3 (`docs/test-cases/money-rounding/SPEC.md:25-27`) требуют хранить `0.025`, `0.005`, `999.99001000`.

Фактически `calculate_source` квантует target до `minor_units` (`app/services/quotes.py:111`), `calculate_target` — тоже (`:126`), и это же значение уходит в `save_quote` (`app/repositories/quotes.py:17-31`). Для `RU-JP-BANK`, amount `10000.00`, fee `180.00` (to_convert `9820.00`), rate `1.90000001`:

- точный продукт: `18658.0000982`;
- в БД: `18658.00000000`;
- в ответе: `"18658"`.

Ожидание: в `quotes.target_amount` восемь знаков продукта, в ответе — `"18658"`. Для `0.05 × 0.5` — в БД `0.025`, в ответе `0.03` (кейс 1).

Предлагаемый тест: unit на `calculate_source` с ожиданием точного `Decimal("18658.0000982")` и отдельной проверкой `format_money(...)=="18658"`; repository-тест читает raw `target_amount` после `save_quote`.

### F6. `GET /quotes/{id}` отдаёт протухшую котировку (P1)

`read_quote` (`app/api/v1/quotes.py:99-113`) загружает строку и сразу маппит её, не сравнивая `expires_at` с `now`. В OpenAPI этого же роутера задекларирован 410 (`:44`), а quote-contract #12 (`docs/test-cases/quote-contract/SPEC.md:33`) ожидает `QUOTE_EXPIRED`. Проверка есть только в создании перевода (`app/services/transfers.py:163`).

Факт: `GET /quotes/{id}` с `expires_at` в прошлом → 200. Ожидание: 410 `QUOTE_EXPIRED` (или явно зафиксировать в контракте, что GET не проверяет TTL, и убрать 410 из документации).

Предлагаемый тест: integration — создать/вставить quote с `expires_at = now - 1s`, `GET` → 410; плюс тест, что `expires_at` в POST равен `created_at + quote_ttl_sec` (900/600/300/1800 для разных направлений).

### F11. Отображение rate идёт по HALF_EVEN, малые курсы превращаются в ноль (P2)

`format_rate` (`app/services/quotes.py:253-254`) и мапперы (`app/api/mappers.py:28`, `:42`) используют `f"{rate:.4f}"`. Форматирование `Decimal` не использует `ROUND_HALF_UP`; по умолчанию это half-even. Воспроизведено:

| Вход | `format_rate` | HALF_UP |
|---|---|---|
| `1.23445` | `1.2344` | `1.2345` |
| `0.00005` | `0.0000` | `0.0001` |
| `1.00005` | `1.0000` | `1.0001` |

Для inverse-направления `RU-KW-BANK` (`data/rates.json:74-78`, `KWD→RUB=270`) rate = `0.0037037037…`, в ответе `"0.0037"`. Пересчёт клиентом по показанному rate: `10000 × 0.0037 = 37.00` KWD, фактический target — `36.44` KWD (ошибка ~1.5%). Для курсов < `0.00005` в ответе будет `"0.0000"`, хотя money-rounding кейс 4 требует, чтобы минимальный положительный курс оставался курсом.

Дополнительно: тест `check_result` сравнивает с `f"{result.rate:.4f}"` (`tests/unit/test_quote_calculator.py:89`), то есть кодирует ту же ошибку форматирования и не может её поймать.

Предлагаемый тест: литеральные ожидания `format_rate(Decimal("1.23445")) == "1.2345"`, `format_rate(Decimal("0.00005")) == "0.0001"`; API-тест `RU-KW-BANK` с проверкой rate и target.

### F12. Валидация строки суммы пропускает нецифровые/не-ASCII значения (P2)

`amount_string_is_valid` (`app/core/money.py:20-28`) использует `str.isdigit()`, который принимает арабско-индийские и полноширинные цифры, а также надстрочные символы. Воспроизведено:

| Вход | `minor_units` | `amount_string_is_valid` | Что дальше |
|---|---|---|---|
| `"١٢٣.٤٥"` | 2 | True | `Decimal` парсит как `123.45`, котировка создаётся |
| `"１２３.４５"` | 2 | True | то же |
| `"².00"` | 2 | True | `Decimal("².00")` падает → 422 «amount is not a number» |
| `"00010000.00"` | 2 | True | leading zeros принимаются |
| `9`×30 | 0 | True | на уровне сервиса `quantize` → `InvalidOperation` (см. F13); через API раньше срабатывает max тарифа |

Плюс расхождение с `Field(max_length=32)` (`app/schemas/quotes.py:15`): сервисная функция длину не ограничивает. Ожидание: только ASCII `[0-9]`, ровно `minor_units` знаков, сумма > 0, длина в пределах колонки. Для `"0.00"` ожидание — `VALIDATION_ERROR`/`AMOUNT_BELOW_MIN`; сейчас доходит до `amount <= 0` (`app/api/v1/quotes.py:75`) и даёт 422 `VALIDATION_ERROR`, но это не закреплено тестом (в интеграции есть только `-5.00`, который отсекается раньше — на `amount_string_is_valid`).

Предлагаемый тест: параметризованная таблица форматов (включая `""`, `" "`, `"+1.00"`, `"1.00 "`, `"1 000.00"`, `"1,00"`, `"1e3"`, `"NaN"`, unicode, 30 цифр) и отдельный кейс `0.00`.

### F13. Огромные суммы/курсы дают 500 вместо именованного отказа (P2, латентный)

`round_money` вызывает `quantize` с текущим контекстом Decimal (precision 28) и не ловит `InvalidOperation` (`app/core/money.py:12-13`). Воспроизведено: `round_money(Decimal("9"*30), 0)` и `Decimal("9"*29 + ".00")` бросают `decimal.InvalidOperation`, который глобальный handler превращает в 500 (`app/api/exception_handlers.py:34-46`).

Схема хранит `NUMERIC(20,8)` (`app/models/tables.py:19`, `:125`) — это максимум 12 целых цифр. Курс `1e13` или target `>= 1e12` не помещаются и дают DB-ошибку → 500. Для `POST /quotes` суммы сейчас частично защищены проверкой max тарифа (300 000 и т.п.), но курс от провайдера не валидируется: rate `1e10` × amount 1000 = `1e13` target → overflow.

Ожидание по `docs/test-cases/money-rounding/SPEC.md:30-35` (кейсы 6 и 11): именованный 400 с полем, а не падение на вставке.

Предлагаемый тест: unit на `round_money` с огромным значением (ожидать доменную ошибку); repository/integration — котировка с rate `1e10` (или target `1e13`) → 4xx, не 500.

### F14. Лимит может проверяться в «как есть» без конвертации (P2)

`amount_in_limit_currency` (`app/services/quotes.py:232-250`) возвращает `amount` без изменений, если `rate == 0` (`:244`) или если валюта лимита не совпадает ни с source, ни с target (`:250`). Это молчаливая подмена, а не ошибка; лимит направления может быть проверен в чужой валюте. На текущих `tariffs.json` все `amount_limits.currency` равны source, поэтому ветки не исполняются и тестами не покрыты.

Предлагаемый тест: unit с `limit_currency`, отличной от пары, и с rate 0 → ожидать `RateUnavailableError`/явную ошибку конфигурации; integration — тариф с лимитом в третьей валюте.

### F17. Отрицательный ноль и отрицательные суммы в сервисе (P2)

`format_money(Decimal("-0.001"), 2)` возвращает `"-0.00"`: HALF_UP для отрицательного значения уходит от нуля, но при сумме меньше половины minor unit получается отрицательный ноль. Если в target/source попадёт отрицательное значение (например, из-за F3 или fee > amount), API отдаст отрицательную строку. Валидации `fee <= amount` в `calculate_source` нет (`app/services/quotes.py:101-111`), как и проверки `rate > 0`.

Предлагаемый тест: `format_money(-0.001, 2) == "0.00"` (или доменная ошибка); `calculate_source` с fee > amount → ошибка, не отрицательный target.

### F18. `GET /quotes/{id}` теряет breakdown (P2)

`quote_row_to_response` всегда подставляет `breakdown=[]` (`app/api/mappers.py:44`), хотя POST возвращает заполненный breakdown. `test_create_and_read_quote` после GET проверяет только `quote_id` (`tests/integration/test_quotes_api.py:47-51`), поэтому расхождение не видно. Для калькулятора это потеря объяснения расчёта; quote-contract #2 ожидает наличие breakdown в ответе.

Предлагаемый тест: сохранить POST-ответ, сделать GET и сравнить `direction_id`, `source_amount`, `fee`, `amount_to_convert`, `rate`, `target_amount`, `breakdown` (с допустимым отличием `expires_at`).

## 3. Не покрыто тестами

### 3.1. `core/money.py` — примитивы

- `quantum(0)`, `quantum(2)`, `quantum(3)`, `quantum(-1)` (ожидается `ValueError`).
- `round_money` на HALF_UP-границах: `0.005 → 0.01`, `-0.005 → -0.01`, `2.675 → 2.68`, `1.005 → 1.01`, `0.0005` при 3 знаках, `0.5/1.5/2.5` при 0 знаках (2.5 → 3, а не 2).
- `format_money` для 0/2/3 знаков и для значений с лишними знаками (`1.005`, `-0.001`).
- `amount_string_is_valid`: полная таблица форматов (см. F12), включая `minor_units=0` (`"10000"` валидно, `"10000.00"` — нет), пустую строку, точку, пробелы, `+`, `-`, запятую, экспоненту, unicode, ведущие нули.
- `parse_money`: сейчас не используется; если остаётся — тесты на `NaN`/`Infinity`/`1_0` (F16).

Файлы: `app/core/money.py:4-32`; текущее покрытие — только `tests/unit/test_quote_calculator.py:218-224`.

### 3.2. Границы сумм

- SOURCE: `999.99`, `1000.00`, `300000.00`, `300000.01` для `RU-TH-BANK`; `20.000`/`5000.000` для `KW-TH-BANK`; `2000`/`500000` для `RU-JP-BANK`.
- TARGET: те же границы после конвертации; отдельно TARGET-граница max (F2) и min: `target = min × rate ± 1 minor unit`.
- `amount="0.00"` → 422 (сейчас), `amount="0"` для JPY, `amount="-"`/`"+1000.00"` → 422.
- Amount как JSON-число (`10000.0`) при `strict=True` → 422; теста нет.
- Fee > amount и `rate <= 0` на сервисном уровне (F17/F3).

Файлы: `tests/unit/test_quote_calculator.py:152-169` (только 500/500000), `tests/integration/test_quotes_api.py:67-76`.

### 3.3. TARGET-режим

- Инвариант «target не меньше запрошенного» на диапазоне, а не на одном примере `03_target_mode.json`.
- Обратный прогон `TARGET → SOURCE → TARGET`: текущий `test_target_source_roundtrip` (`tests/unit/test_quote_calculator.py:138-149`) проверяет только `SOURCE → TARGET → SOURCE` на одном значении 10 000 RUB, которое попадает в корректную ветку; зона недоплаты F1 не покрыта.
- Порог минимальной комиссии: `RU-TH-CARD` 1 016.40 / 1 020.00 / 1 050.00; `RU-VN-BANK` 2 053 333 / 2 060 000 / 2 083 333; `RU-JP-BANK` 10 420; `KW-JP-BANK` 19 890.
- TARGET + `charged_in=TARGET` (`RU-VN-BANK`, `KW-JP-BANK`) — ни одного теста.
- TARGET + `minor_units=0` (JPY) и `minor_units=3` (KWD source).
- TARGET с `percent=100` (деление на ноль) и отрицательным `fee_percent` — латентно, данных нет, но валидации тоже нет (`app/services/quotes.py:172`, `:193`).

### 3.4. Курсы

- Direct `0`, `-1`, `NaN`, `Infinity`; inverse `0`; кросс с нулевой/отрицательной второй ногой (F3).
- Stale direct + свежие inverse/кросс (F4); stale direct + stale inverse; кросс, где одна нога stale.
- Границы `_fresh`: `age == limit`, `limit + 1s`, будущее `+60s` и `+61s` (`app/services/quotes.py:80-82`); сейчас допуск `-60` нигде не описан в ASSUMPTIONS.
- `limit_sec = min(ttl, 6ч)` для ttl 300/600/900/1800: тесты везде передают `10**9` (`tests/unit/test_quote_calculator.py:127`, `:141`, `:162`, `:208`, `:215`), поэтому реальные TTL направлений не exercised.
- Кросс-курс: две равные по свежести цепочки (детерминизм выбора `best`), кросс через валюту, которой нет в `minor_units`.
- Inverse-курс `RUB→KWD=1/270` и точность продукта (F11).
- Rate с >4 знаками: отображение, сохранение в `NUMERIC(20,8)`, влияние на `target`.

### 3.5. Направления и данные

`POSITIVE_CASES` (`tests/unit/test_quote_calculator.py:99-107`) содержит 7 файлов из 11 направлений. Нет ни одного направленного теста для:

- `RU-KW-BANK` — единственный inverse-курс (`1/270`), 3 minor units у target;
- `RU-AE-BANK` — stale-курс без кросс-пути;
- `RU-PH-CASH` — `CASH_PICKUP`, stale direct + живой кросс (F4);
- `KW-TH-BANK` — KWD→THB, `fixed=0.500`, `min=1.000`, лимиты 20.000/5000.000;
- `KW-JP-BANK` — `charged_in=TARGET`, JPY 0 знаков;
- `US-PH-BANK` — USD→PHP;
- `KZ-TH-BANK` — отсутствующий курс (`RATE_UNAVAILABLE`).

### 3.6. API котировок

- Нет ни одного TARGET-запроса (`tests/integration/test_quotes_api.py:10-21` — всегда SOURCE).
- Нет KWD/JPY/VND-строк и `charged_in=TARGET` через API.
- Нет проверки `expires_at` (значение, TTL по направлению, GET протухшей — F6).
- Нет проверки `breakdown` на GET (F18); в unit-тестах `check_result` (`tests/unit/test_quote_calculator.py:67-96`) не проверяет шаги breakdown вообще, хотя SPEC требует soft assertions по шагам расчёта (`docs/SPEC.md:82`).
- Нет проверки нулевой суммы, huge-суммы, 30-значной строки, JSON-числа, unicode-цифр.
- Нет проверки, что POST и GET дают одинаковые деньги и rate (маппер GET форматирует из `Decimal(quote.rate)`).
- `test_stale_rate_unavailable` (`:111-123`) использует фиксированную дату `2026-10-01` и реальный `now_utc`, то есть тест зависит от календаря и не проверяет границу TTL; `live_rates` в conftest (`tests/conftest.py:118-127`) ставит всем курсам `updated_at=now`, поэтому direction TTL через API никогда не проверяется.
- `freeze_rates` (`tests/conftest.py:102-115`) подменяет метод класса глобально; при `pytest-xdist` это может влиять на другие тесты в процессе.

### 3.7. Персистентность и NUMERIC(20,8)

- Нет теста, что `Quote.rate` с >8 знаками сохраняется/читается без неожиданностей (`app/models/tables.py:125`).
- Нет теста на переполнение `NUMERIC(20,8)` (12 целых цифр) и на именованный отказ вместо 500 (F13).
- Нет теста, что точный продукт хранится с 8 знаками, а не квантуется до `minor_units` (F5).
- Нет теста `POST`/`GET`-эквивалентности для inverse-курса, где POST форматирует полный Decimal, а GET — значение из БД с 8 знаками.

### 3.8. Кэш курсов (D4)

D4 (`docs/DECISIONS.md:26-30`) обещает кэш на `quote_ttl_sec`, но `create_quote` каждый раз создаёт `RatesClient` и делает `fetch()` (`app/api/v1/quotes.py:77-82`). Тестов `rate-freshness` #4/#5 (повторный POST без запроса к провайдеру; обновление внутри TTL) нет. Это не ошибка калькулятора, но решение D4 не выполнено и не проверено.

## 4. Порядок добавления тестов

1. **Сначала падающие P0**: F1 (TARGET ≥ запрошенного), F3 (rate ≤ 0 / NaN), F2 (source ≤ max в TARGET).
2. **P1-контракт**: F4 (stale direct не подменяется), F6 (GET expired), F5 (raw NUMERIC), F7/F8/F9/F10 (границы, направления, API TARGET, TTL).
3. **P2-устойчивость**: F11–F19 (формат rate, строки, overflow, лимит без конвертации, примитивы, breakdown, кэш).
