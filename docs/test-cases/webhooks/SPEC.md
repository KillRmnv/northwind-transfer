# webhooks — дубли, порядок, подпись

## Цель
Строгий вариант D10/A7–A8: дубль — `200` без эффекта; не-терминал не перетирает
терминал; неизвестный `payout_id` — `200 + warn + метрика` (не `404`);
подпись HMAC обязательна (`401` без/кривая).

## Источник в Java
- Прямого аналога вебхуков в Java нет; техника монотонности — из
  `service/conversion_boundaries.feature` (`A rate published after the day does
  not price it`, `findLatestOnOrBefore`) и `TransactionIsolationTests`
  (невидимость неподтверждённого как обоснование сверки).
- Поведение мока (starter-notes, сверено с `mock_provider/webhooks.py`):
  дубли 1–2 (30%), `late_processing` (20%), `contradiction` (4%, решается
  `occurred_at`), `lost_webhook` (12%), `unknown_event` (5%), `forged` (~6%:
  `missing`/`invalid` с ложным статусом), обычная доставка всегда подписана.

## Кейсы (вход → ожидание)

| # | Вход | Ожидание |
|---|------|----------|
| 1 | Дубль `event_id` | `200`, второй без эффекта (`UNIQUE(event_id)`, D19) |
| 2 | `COMPLETED` затем `PROCESSING` (старый/поздний дубль) | терминал не перетирается |
| 3 | Два не-терминала в беспорядке | побеждает больший `occurred_at` |
| 4 | `contradiction` (`COMPLETED` vs `FAILED`, разные `occurred_at`) | побеждает поздний `occurred_at`, расхождение — в историю + метрику |
| 5 | Событие по неизвестному `payout_id`/`reference` | `200 + warn-лог + unknown_webhooks_total`, не `404` (не провоцировать шторм ретраев) |
| 6 | Без `X-Signature` / чужая подпись | `401`, статус перевода не меняется (ложный статус forged отклонён корректно) |
| 7 | Валидная подпись (`hex(HMAC-SHA256(body, SECRET))`) | принято, переход по state machine |
| 8 | `lost_webhook` (событие не пришло) | перевод доводится сверкой, не висит (см. reconciliation) |
| 9 | Пачка событий одновременно на один перевод | ровно один терминальный переход, история упорядочена |

## Адаптация под наш контракт
- Путь `POST /webhooks/{provider_id}`, `event_id UNIQUE`, статус события — FK
  в `attempt_statuses` (D16), а не свободная строка.
- Секрет `PROVIDER_WEBHOOK_SECRET` из `.env` (D13); проверка — константное сравнение.
- Терминальные `COMPLETED/FAILED` необратимы (D6); `RETRYING` наружу не виден.
- Статус события вебхука — тоже FK (D11/D16).

## Приоритет
**P0** (Level 3, chaos: дубли/беспорядок/неизвестные).
