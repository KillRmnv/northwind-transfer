# Ревью (раунд 2): инфраструктура, конфиг, БД, логи, безопасность

Дата: 2026-11-19. Ревизия: `1baaa9b`. Окружение: Python 3.12.14, PostgreSQL 16.16 на `localhost:5433`, mock-провайдер на `127.0.0.1:9000`, `uv`-venv репозитория. Код и тесты этим ревью не менялись; все эксперименты выполнены во временных БД (`transfer_probe_r2`, `transfer_probe_mig`) и в `/tmp/opencode/`. В ходе ревью в working tree появились параллельные правки других сессий (`app/repositories/transfers.py`, `app/services/{limits,providers,transfers}.py`, `tests/...`), поэтому номера строк в не затронутых ими файлах актуальны на момент ревью, а соседние могут сдвинуться.

Этот отчёт **не повторяет** `docs/test-cases/reviews/infra-security.md` (раунд 1). Там разбирались P0-обход HMAC, гонки миграции/сидера, точность Numeric, timezone, xdist, бюджет пула, root-контейнер, published DB-порт и т.д. Здесь — только новые кейсы, подтверждённые на текущей ревизии, плюс снятые гипотезы из списка запроса.

## Матрица новых кейсов

| ID | Severity | Кейс | Вход | Ожидание | Факт | Файл:строка | Тест |
|---|---|---|---|---|---|---|---|
| R2-01 | P1 | Слишком длинный `Idempotency-Key` роняет запрос и пишет SQL в лог | `POST /api/v1/transfers` с валидным `quote_id` и `Idempotency-Key` из 129/200 символов (PG) | 422 `VALIDATION_ERROR` (лимит 128 объявлен схемой), в логах нет SQL/параметров | 500 `INTERNAL_ERROR`; в БД `value too long for type character varying(128)`; в JSON-логе `exception` — полный `INSERT INTO transfers ...` и `[parameters: (...)]` с `recipient_name`, `recipient_account`, самим ключом | `app/api/v1/transfers.py:55-58`; `app/models/tables.py:145`; `app/main.py:88-92`; `app/core/logging.py:83-85` | `test_overlong_idempotency_key_is_422_and_sql_params_not_logged` |
| R2-02 | P1 | Секреты в `exception`/traceback не маскируются | `logger.exception("request failed")` с исключением, в сообщении которого DSN `postgresql+asyncpg://u:EXCPASS@db:5432/t`, `access_token=ECTOK`, `client_secret=ECSEC`, `session_id=ECSID` | все значения заменены на `*****` | В поле `exception` JSON-лога остаются `EXCPASS`, `ECTOK`, `ECSEC`, `ECSID`. Паттерны используют `\b`, который не срабатывает после `_`, и не покрывают userinfo DSN; на stdlib traceback free-text регулярки вообще не рассчитаны | `app/core/logging.py:26-42,83-85` | `test_exception_traceback_redacts_underscore_secrets_and_dsn` |
| R2-03 | P1 | Неаутентифицированные admin-ручки мок-провайдера на публичном порту | `PUT http://127.0.0.1:9000/prov_alpha/admin/scenario {"scenario":"decline"}`; `GET /prov_alpha/admin/ledger` | 401/404 или порт не доступен вне compose-сети | Оба `200`: сценарий провайдера меняется (`{"scenario":"decline"}`), ledger отдаёт `recipient.name/account/idempotency_key`. Compose публикует `9000:9000` на всех интерфейсах | `mock_provider/main.py:230-247,274-276`; `docker-compose.yml:63-64` | `test_mock_provider_admin_not_reachable_from_host` |
| R2-04 | P1 | Секрет в URL/query не маскируется free-text маскировкой (реальный путь лога) | Строка лога/исключения вида `GET /cb?access_token=SECRET1&client_secret=SECRET2` или `session_id=SECRET3` | `*****` вместо значений | `redact_text` возвращает строку без изменений: `access_token`, `client_secret`, `session_id` не покрываются `\b(...)\b`. Плюс `X-Api-Key`/`api-key` как **структурные ключи** dict тоже не маскируются (`_is_secret_key` ищет `api_key`/`apikey` с подчёркиванием), а безобидный `tokenizer` маскируется ложным срабатыванием | `app/core/logging.py:12-24,26-42,58-60` | `test_redact_query_and_header_key_matrix` |
| R2-05 | P2 | Пустой `PROVIDER_WEBHOOK_SECRET` не мешает старту | Запуск без `.env` (секрет `""` по умолчанию), `GET /health`, `POST /webhooks/prov_alpha` | Fail-fast на старте: конфигурация без HMAC-секрета не поднимается | Старт успешен, `/health` → 200, любой webhook → 503 `SERVICE_UNAVAILABLE` **без единого warning-лога**; проблема видна только по отказам провайдера | `app/core/settings.py:26`; `app/services/webhooks.py:47-48`; `app/main.py:72-77` | `test_empty_webhook_secret_fails_fast_at_startup` |
| R2-06 | P2 | Неверный `Content-Type` на 404/405 | `GET /nope`, `GET /docs` (env≠dev), `POST /health` | `application/problem+json`, как у доменных ошибок (D10/INF-41) | `application/json` с телом `{"detail":"Not Found"}` / `{"detail":"Method Not Allowed"}` — FastAPI-дефолт, обработчик `problem+json` не задействован | `app/main.py:75-77`; `app/api/exception_handlers.py:8-24` | `test_all_error_responses_use_problem_json` |
| R2-07 | P2 | `/health` мок-провайдера раскрывает конфигурацию | `GET http://127.0.0.1:9000/health` | `{"status":"ok"}` (как D12 для API) | `{"status":"ok","mode":"normal","seed":"1"}` — раскрывается режим/сид chaos-настройки на публичном порту | `mock_provider/main.py:34-36`; `docker-compose.yml:63-64` | `test_mock_health_has_no_config_fields` |

## Новые подтверждённые баги

1. **P1. `Idempotency-Key` длиннее 128 символов → 500 и утечка SQL в логи (R2-01).** На свежей БД `transfer_probe_r2` через `TestClient` с подменённым `get_session` и подставным курсом: ключ 128 символов → `202`, ключ 129 и 200 → `500 INTERNAL_ERROR`. В логе `transfer-core.http / request failed`:
   ```
   sqlalchemy.exc.DBAPIError: ... value too long for type character varying(128)
   [SQL: INSERT INTO transfers (transfer_id, quote_id, client_id, status, ... idempotency_key, ...) VALUES (...)]
   [parameters: (UUID('...'), 'c_001', 'PENDING', Decimal('1000.00000000'), 'RUB', ..., 'KKKK...[200]', '6d2a2d...', ...)]
   ```
   Два независимых дефекта: нет валидации длины заголовка (схема говорит 128) и `JsonFormatter` кладёт весь `formatException(record.exc_info)` в лог, где SQLAlchemy уже подставил все значения. Клиент получает безобидный 500, а оператор — PII и ключи в логах. Воспроизведение: `PYTHONPATH=. .venv/bin/python /tmp/opencode/probe_pg_longkey.py`.

2. **P1. Секреты остаются в exception-логе (R2-02).** Прямой вызов `JsonFormatter` + `RedactingFilter` на `LogRecord(..., exc_info=sys.exc_info())` для `RuntimeError("connect postgresql+asyncpg://u:EXCPASS@db:5432/t failed; access_token=ECTOK client_secret=ECSEC session_id=ECSID query ?api_key=AK")` дал в `exception`:
   ```
   RuntimeError: connect postgresql+asyncpg://u:EXCPASS@db:5432/t failed; access_token=ECTOK client_secret=ECSEC session_id=ECSID query ?api_key=*****
   ```
   Маскировался только `api_key`. `redact_text("GET /cb?access_token=SECRET_ACCESS1&client_secret=SECRET_CLIENT2")` и `redact_text("session_id=SECRET_SESSION3 user=bob")` также возвращаются без изменений. В `redact_value({"headers": {"X-Api-Key":"K4","api-key":"K5","Api-Key":"K6","tokenizer":"harmless"}})` не замаскированы первые три, но замаскирован `tokenizer`. Это не «теоретический» риск: единственный exception-лог приложения — `access_logger.exception` в `app/main.py:89`, то есть любой 500 с секретом в ошибке попадает в прод-логи.

3. **P1. Мок-провайдер — открытый пульт управления (R2-03).** Живой сервис на `127.0.0.1:9000` (subagent не поднимал его, он уже был запущен):
   ```
   GET  /prov_alpha/admin/ledger        -> 200 {"entries":[{...,"recipient":{"account":"1234567890",...}}]}
   PUT  /prov_alpha/admin/scenario      -> 200 {"scenario":"decline"}
   DELETE /prov_alpha/admin/scenario    -> 200 {"scenario":null}
   ```
   Никакой аутентификации/авторизации нет; в `build_app` ручки зарегистрированы явно, в compose порт проброшен наружу. Любой, кто видит хост, может принудительно перевести провайдер в `decline`/`error_5xx`/`slow` (константы из `mock_provider/behavior.py`) и сорвать все переводы, а также читать ledger с данными получателей. Рядом `/health` отдаёт `mode`/`seed` (R2-07).

4. **P2. Тихая деградация без секрета вебхука (R2-05).** `PROVIDER_WEBHOOK_SECRET=""` → `handle_webhook` бросает `ServiceUnavailableError`, клиент получает корректные `503` + `application/problem+json`; но само приложение стартует, `/health` = 200 и в логах нет ни одного предупреждения. Раньше (раунд 1, INF-01) пустой секрет принимал подпись; сейчас fail-closed по запросу, но **fail-fast по конфигурации отсутствует** — деплой с забытым секретом выглядит здоровым до первого вебхука.

5. **P2. Разный `Content-Type` для ошибок (R2-06).** Доменные ошибки и `RequestValidationError` отдают `application/problem+json`, а дефолтные `404`/`405`, которые генерирует сам FastAPI до доменного обработчика, — `application/json`. Клиентский парсер, который ветвится по `problem+json`, ломается на `/docs` в проде и на неверном методе.

## Снятые гипотезы (проверено, багом не является)

- **X-Request-ID подмена клиентом.** Middleware всегда генерирует `new_request_id()`, игнорирует входящий заголовок и перезаписывает его: `GET /health` с `X-Request-ID: ATTACKER_CONTROLLED_123` → ответ содержит заново сгенерированный ID. Подмены нет. (Потеря заголовка на 500 — уже раунд 1, INF-38, здесь не дублируется; на 401/503 и 4xx заголовок присутствует.)
- **`Content-Type` при 401/503.** Пустой секрет → `503 application/problem+json`; неверная подпись → `401 application/problem+json`. Обе корректны; проблема только на 404/405 (R2-06).
- **Тело ошибки Pydantic.** `RequestValidationError` мапится в `details = {loc: msg}`; `exc.errors()[*]["input"]` в ответ не попадает. Тест с `amount=12345` (strict str) и лишним полем `password=LEAKPWD` дал `{"body.amount":"Input should be a valid string"}` — значения нет. Аналогично `WebhookEvent.model_validate` перехватывает `ValidationError` и отдаёт фиксированную строку.
- **DSN в логах SQLAlchemy.** `create_async_engine` с несуществующим хостом и `password=SECRETPASS` при `ConnectionRefusedError` не пишет DSN/пароль ни в исключение, ни в логи движка; `alembic upgrade head` с неверным портом тоже печатает только `Connect call failed ('127.0.0.1', 1)`. Утечка DSN подтверждена только на уровне `redact_text`/traceback (R2-02), не на уровне SQLAlchemy.
- **Миграция downgrade.** На свежей БД `transfer_probe_mig`: `upgrade head` → `current=0001_initial (head)` → seed (10 валют) → `downgrade base` → в `public` остаётся только `alembic_version` → повторный `upgrade head` успешен. Round-trip работает, бага нет (раунд 1 INF-29 про отсутствие теста остаётся в силе).
- **Сидер с изменёнными балансами.** `session.get(Balance, ...)` не перезаписывает существующие строки; изменённый `available` сохраняется. Поведение описано в INF-06/INF-31, новый баг не найден.
- **Alembic при двух репликах, SQLite timezone, MySQL-профиль, отсутствие `USER` в Dockerfile, published PG port, работа без `.env` (docs/дефолты).** Все уже описаны в раунде 1 (INF-03, INF-08, INF-19/20/48, INF-34, INF-17, INF-18/45/49) и здесь не дублируются; состояние на текущей ревизии не изменилось.

## Не покрыто тестами (новое)

- Нет теста на предел длины `Idempotency-Key` и на то, что в логах 500-ответов не появляются SQL/параметры (R2-01). Сейчас `tests/integration` проверяют только happy-path и конфликт того же ключа.
- Нет теста на маскирование *реально отформатированного* traceback: `test_logging_redaction.py` проверяет `message`, `record.args` и `redact_value`, но не `payload["exception"]` (R2-02).
- Нет теста на матрицу ключей query/headers с подчёркиванием и дефисом (`access_token`, `client_secret`, `session_id`, `X-Api-Key`, `api-key`) и негативных (`tokenizer`) в обоих путях: `redact_text` и `redact_value` (R2-04).
- Нет статического/интеграционного теста, что мок-провайдер не публикует admin-ручки и `/health` без конфигурации (R2-03, R2-07).
- Нет стартовой проверки конфигурации: ни один тест не требует fail-fast при пустом `PROVIDER_WEBHOOK_SECRET`, невалидном `LOG_LEVEL`, `DB_POOL_SIZE<=0` или `RECONCILE_INTERVAL_SEC<=0` (R2-05).
- Нет теста единообразия `Content-Type` для всех кодов ответа, включая дефолтные 404/405 (R2-06).
- Нет теста, что `Dockerfile` не тащит в образ `var/*.sqlite3`, `tests/`, `*.zip` и `.env.example` — `.dockerignore` по-прежнему покрывает только `.env` и кэши (перекличка с INF-33, но для образа).
