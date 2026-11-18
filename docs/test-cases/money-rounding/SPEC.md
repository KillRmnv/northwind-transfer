# money-rounding — округление и minor_units

## Цель
Закрепить единственное место округления (`core/money.py`, `ROUND_HALF_UP`, D2/A1):
храним `NUMERIC(20, 8)`, ставку не округляем до умножения, показываем строкой
по `minor_units` валюты. Мелкие суммы не должны превращаться в `0.00` на записи.

## Источник в Java
- `service/conversion_boundaries.feature`: `What is stored is the product, not the product rounded`
  (7 строк), `The rate is not rounded before the amount is multiplied`,
  `The smallest positive rate is still a rate`, `A product the ledger column
  cannot hold is refused, not written`, `A rate of exactly ten keeps a sum at
  the boundary inside the column`, `A currency whose rate is below one cannot
  overflow at any legal sum`, `The ledger currency needs no rate at all`.
- `rule/ledger_policy.feature`: `Rounding is half-up at the configured scale`,
  `A different scale is honoured rather than assumed`, `The storage ceiling is
  the widest value the column holds`.
- `api/client_api.feature`: `What the row keeps exactly, the response rounds
  for reading` (строка `22.22099990`, ответ `22.22`).

## Кейсы (вход → ожидание)

| # | Вход | Ожидание |
|---|------|----------|
| 1 | `rate=0.5, sum=0.05` | stored `0.025` (полцента не ноль) |
| 2 | `rate=0.5, sum=0.01` | stored `0.005` |
| 3 | `rate=1.00000001, sum=999.99` | stored `999.99001000`, ставка не округлена до умножения |
| 4 | `rate=0.00000001` (минимальный положительный) | конвертирует, не отбрасывается |
| 5 | `rate=0 / -1` | курс непригоден → `RATE_UNAVAILABLE`, перевод не роняется молча |
| 6 | `sum=99999999999.99999999 × rate=10.00000001` | `400`, именованный отказ (произведение за потолком колонки), а не падение на вставке |
| 7 | тот же `sum × rate=10` ровно | помещается (`999999999999.99999990`) |
| 8 | `sum` на потолке × `rate<1` | уходит вниз, переполнения нет |
| 9 | HALF_UP на показе: `0.025→0.03`, `-0.025→-0.03`, `2.675→2.68` | только в `core/money.py` |
| 10 | Показ по `minor_units`: JPY/VND `0`, KWD `3`, остальные `2` | строка в ответе, хранение без изменений |
| 11 | Потолок хранения `999999999999.99999999` внутри, `1000000000000.00` за ним | отказ с именем поля |

## Адаптация под наш контракт
- Пути: unit-уровень `core/money.py` + `POST /quotes` (`target_amount`, `breakdown`).
- Ошибки: переполнение произведения → `VALIDATION_ERROR`/`AMOUNT_ABOVE_MAX`
  с `details.field`, формат RFC 9457 + `error` (A11).
- `minor_units` из `currencies.json` (JPY/VND `0`, KWD `3`); примеры `examples/`
  с TARGET-режимом и KWD перепроверить строкой.
- Кросс-курс `convert` (прямой/обратный/двухзвенный) округляется один раз, в конце.

## Приоритет
**P0** (Level 1, деньги).
