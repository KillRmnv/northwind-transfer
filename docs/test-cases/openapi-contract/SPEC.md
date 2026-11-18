# openapi-contract — контракт как артефакт

## Цель
OpenAPI описывает оба семейства ручек (`/quotes`, `/transfers`, балансы, вебхуки)
с именами полей как на проводе и схемами ошибок RFC 9457. Документ — артефакт
сборки, его пустоту/расхождение ловит тест, а не ночной workflow.

## Источник в Java
- `OpenApiContractTests`: документ содержит оба семейства (`/api/v1/transactions`,
  `/api/v1/limits`); `SoftAssertions` на пути.
- Урок `known-gaps` из javadoc теста: сгенерированные имена свойств (`limitSum`)
  расходились с проводом (`limit_sum`) — у springdoc не было что прочитать
  (`@JsonNaming` по типам). Тест это сознательно не ассертил; нам так нельзя.
- `rule/wire_contract.feature`: имена на проводе — snake_case из ТЗ, `400`
  называет поле как в запросе.

## Кейсы (вход → ожидание)

| # | Вход | Ожидание |
|---|------|----------|
| 1 | `GET /openapi.json` (dev) | есть пути `/quotes`, `/quotes/{id}`, `/transfers`, `/transfers/{id}`, `/clients/{id}/balance`, `/webhooks/{provider_id}` (+ корневые дубли) |
| 2 | Имена свойств в схемах | snake_case как на проводе (`source_amount`, `target_amount`, `expires_at`), сверка с реальным ответом |
| 3 | Каждый ответ-ошибка | схема RFC 9457 + `error.code`, `Content-Type: application/problem+json` |
| 4 | Каждое поле DTO | `summary/description`, `Field(examples=…)`, все коды ответов (`200/201/400/404/409/422`) |
| 5 | Прод (`prod`) | `openapi.json`/swagger выключены (D12) |
| 6 | Пустой/негенерированный документ | тест красный (урок Java: bump `springdoc` проходил suite с пустым документом) |

## Адаптация под наш контракт
- FastAPI генерирует схему из Pydantic — проверять проводные имена тестом
  на реальный ответ, а не только на схему.
- `docs/openapi.json` — только dev-профиль (D12).
- Алиасы Pydantic: сериализация в snake_case без сюрпризов глобальной стратегии.

## Приоритет
**P2** (контракт, артефакт).
