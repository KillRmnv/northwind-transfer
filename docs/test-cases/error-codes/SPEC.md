# error-codes — формат и коды ошибок

## Цель
Все отказы §6 возвращаются единым конвертом (RFC 9457 + вложенный `error`),
с правильным HTTP-статусом и именованным полем. Пустой список — тоже контракт.

## Источник в Java
- `api/request_validation.feature` + `api/client_api.feature`: `An invalid body
  is answered with a ProblemDetail` (тип + поле в `errors`), `An unknown category
  is answered as a ProblemDetail 404`, батч с невалидной строкой — тот же формат,
  что у одиночного (`A batch with an invalid transaction is refused and nothing
  is stored`, атомарно: записано 0).
- `api/currency_pairs.feature`: `404 + not-found` для вне-lookup, `400 +
  malformed-request` для malformed-кода.
- `quota/rate_limit.feature`: отказ `429` несёт `Retry-After` + ProblemDetail
  `rate-limited` (у нас квоты нет — кейс не портируется, принцип «отказ несёт
  заголовок/тип» сохраняется для `202`-поллинга).

## Кейсы (вход → ожидание)

| # | Вход | Ожидание |
|---|------|----------|
| 1 | Любая `4xx/5xx` | `Content-Type: application/problem+json`, поля RFC 9457 + `error: {code, message, details}` (A11) |
| 2 | Коды §6: `VALIDATION_ERROR, UNSUPPORTED_DIRECTION, AMOUNT_BELOW_MIN, AMOUNT_ABOVE_MAX, RATE_UNAVAILABLE, QUOTE_NOT_FOUND, QUOTE_EXPIRED, INSUFFICIENT_FUNDS, DAILY_LIMIT_EXCEEDED` | на каждый — минимум один тест Level 1 |
| 3 | Свой код `IDEMPOTENCY_CONFLICT` (422) | тот же ключ + другое тело (D8/A4) |
| 4 | Маппинг кодов мока (`RECIPIENT_ACCOUNT_INVALID, CURRENCY_NOT_SUPPORTED, AMOUNT_OUT_OF_LIMITS, PROVIDER_DISABLED, PROVIDER_MAINTENANCE, GATEWAY_TIMEOUT…`) | во внутренние статусы попыток, таблица в README (SPEC) |
| 5 | Неизвестный служебный путь (`/env`, `/actuator`, `/metrics`) | `404`, без утечки конфига (D12) |
| 6 | Атомарность отказа создания перевода | при отказе записи нет (резерв не висит) |
| 7 | Пустой результат (нет направлений/переводов) | `200` + пустой список, а не `404` |

## Адаптация под наш контракт
- Единый `exception_handlers` (аналог `@ControllerAdvice`): иерархия
  `DomainError → …`, один перехватчик (arch-тест это закрепляет).
- `details` называет поле в нотации запроса (snake_case), как `wire_contract`.
- `message` может быть русским (пример §6), `code` — стабильный английский идентификатор.
- Категории `gold → 404` из Java нет; аналог — неизвестный `client_id`/направление.

## Приоритет
**P0** (Level 1, автопроверка по §6).
