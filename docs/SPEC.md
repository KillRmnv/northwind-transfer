Привет, ИИ. В приложенном файле `Backend.md` находится описание тестового задания **«Transfer Core»** (сервис международных переводов Northwind Transfer). Ниже — строгая техническая спецификация на стек **Python**, которой ты должен следовать при проектировании, генерации кода, тестов и документации.

### Архитектура и стек (Python 3.12+)

* **Фреймворк:** FastAPI (асинхронный стек: `async`/`await`, `httpx.AsyncClient` для вызовов `mock-provider`).
* **Работа с деньгами:** Строго `decimal.Decimal` с явным контекстом округления в едином модуле (`ROUND_HALF_UP` / банковское — зафиксировать в `ASSUMPTIONS.md`). Никаких `float` ни в бизнес-логике, ни в БД (`NUMERIC`/`DECIMAL`), ни в DTO (сериализация в строку с точностью по `minor_units` валюты).
* **Управление зависимостями — только `uv`, без `pip`:** Зависимости описываются в `pyproject.toml` (`[project]`, `requires-python = ">=3.12"`) и фиксируются `uv.lock`. Запрещено использовать `pip`, `pip install`, `python -m venv`, `virtualenv` и `requirements.txt` (в т.ч. в `Dockerfile`, CI, README и скриптах). Установка окружения — `uv sync --frozen`, добавление зависимостей — `uv add` / `uv add --dev`, запуск любых команд — через `uv run ...` (например, `uv run uvicorn`, `uv run alembic`, `uv run pytest`). Версия Python закрепляется в `.python-version`.


* **Слоистая архитектура (Clean / Layered Architecture):**
1. `api/v1/` (Роутеры / Контроллеры) — только HTTP-маршрутизация, валидация входных DTO, извлечение заголовков (`Idempotency-Key`, `X-Signature`), вызов сервисов. Никакой бизнес-логики и прямых запросов в БД. **Важно по контракту:** помимо версионированного префикса `/api/v1/...`, продублируй или прокинь корневые пути (`/quotes`, `/transfers`, `/clients/{client_id}/balance`, `/webhooks/{provider_id}`), так как автотесты проверяющего запускаются по путям из раздела 6 ТЗ.


2. `schemas/` (DTO на Pydantic v2) — строгая типизация (`ConfigDict(strict=True)` там, где уместно), раздельная схема на Request, Response и внутренние объекты передачи данных. Ответ `GET /clients/{client_id}/balance` — по каждой валюте ключи `available` и `reserved` (именно эти имена ищет `scripts/chaos_run.py`).
3. `services/` — вся доменная логика (калькулятор котировок, движок скоринга и выбора провайдеров, state machine переводов, управление резервами/холдами, обработка идемпотентности и вебхуков). Расчёт, выбор провайдера и HTTP-клиент провайдера должны быть разделены на независимые классы/модули. **Правило неизвестного итога:** обрыв соединения / таймаут / `504` после отправки запроса провайдеру означает «итог неизвестен, запись может уже существовать» — сначала `GET /{provider_id}/payouts?reference=<наш id>`, следующий провайдер только если записи нет. **Правила вебхуков:** `UNIQUE(event_id)` (дубль — `200` без эффекта), не-терминальный статус не перетирает терминальный (спор решает `occurred_at`), неизвестный `payout_id`/`reference` — `200 + warn-лог + метрика` (не `404`), подпись HMAC обязательна (несовпадение/отсутствие — `401`).


4. `repositories/` — паттерн Repository (абстракция над SQLAlchemy 2.0 `AsyncSession`), инкапсуляция SQL-запросов, пессимистичных блокировок (`SELECT ... FOR UPDATE` для балансов и переходов статусов) и upsert-операций.
5. `models/` (Domain & ORM модели) — декларативные модели SQLAlchemy 2.0 + явная конечная автомат-машина (State Machine) статусов перевода.
6. `core/exceptions.py` & `api/exception_handlers.py` (аналог `@ControllerAdvice`) — иерархия собственных доменных исключений (`DomainError` -> `QuoteExpiredError`, `InsufficientFundsError`, `UnsupportedDirectionError`, `ProviderUnavailableError` и т.д.). Глобальный перехватчик исключений должен возвращать ответ в стандарте **RFC 9457 (Problem Details for HTTP APIs)** с `Content-Type: application/problem+json`, но **обязательно включать вложенный объект `"error": {"code": "...", "message": "...", "details": {...}}**`, чтобы одновременно удовлетворить RFC 9457 и автоматическим проверкам контракта из раздела 6 ТЗ.





---

### База данных, нормализация и миграции

* **Нормализация:** Схема БД должна быть нормализована минимум до **3НФ (3NF)**.
* **Никаких DB-уровневых `ENUM`:** Все справочники и статусы (валюты, страны, способы выплаты, статусы переводов, статусы попыток выплат, провайдеры) вынести в отдельные таблицы-справочники с `UNIQUE` ограничениями и внешними ключами (`FOREIGN KEY`).
* **Миграции:** Настроить **Alembic** (асинхронный конфиг). Начальные данные из `data/` (`currencies.json`, `tariffs.json`, `providers.json`, `clients.json`) загружать через идемпотентный сидер при старте или миграцией.


* **ER-диаграмма:** Создать директорию `docs/` и сгенерировать в ней файл `docs/er_diagram.puml` (и при возможности отрендеренное изображение) в **нотации Мартина (Crow's Foot)** на PlantUML, отражающий все таблицы, PK/FK, типы данных и связи.



---

### Пул подключений и конфигурация окружения

* **Пул подключений:** Настроить пул соединений SQLAlchemy (`AsyncEngine` с `pool_size`, `max_overflow`, `pool_timeout`, `pool_pre_ping=True`). Все параметры пула вынести в настройки.
* **Тюнинг PostgreSQL под RAM (обязательно):** Поднять базовые значения памяти PostgreSQL, чтобы запросы и параллельные операции шли быстрее, вместо дефолтов (часто `shared_buffers=128MB`, `work_mem=4MB`). Задать в `docker-compose.yml` через `command: postgres -c ...` (или монтируемый `postgresql.conf`) с значениями из переменных окружения и дефолтами под контейнер ~2 ГБ RAM:
* `shared_buffers` — 25% RAM контейнера (базово `512MB`);
* `effective_cache_size` — 50–75% RAM (базово `1536MB`);
* `work_mem` — `16MB` на операцию сортировки/хэша (учитывать `work_mem × число параллельных запросов`, не задирать бездумно);
* `maintenance_work_mem` — `128MB`/`256MB` (для VACUUM, индексов, миграций);
* `temp_buffers` — `16MB`/`32MB`;
* `wal_buffers` — `16MB`;
* `random_page_cost = 1.1` и `effective_io_concurrency = 200` для SSD;
* `max_connections` — не меньше `pool_size × число воркеров + резерв` (согласовать с пулом SQLAlchemy, чтобы не упираться в лимит).
Также выставить `shm_size: 1gb` для сервиса PostgreSQL в `docker-compose.yml` (иначе параллельные запросы упираются в маленький `/dev/shm`). Все значения должны быть переопределяемы через `.env` (`PG_SHARED_BUFFERS`, `PG_WORK_MEM`, ...), обоснование и формулы — в `DECISIONS.md`; проверка применения — `SHOW shared_buffers;`/`SHOW work_mem;` в контейнере.
* **Профили БД (аналог Spring Profiles):** Реализовать фабрику/конфигурацию подключения к БД на базе `pydantic-settings`, поддерживающую переключение диалектов и драйверов через переменную `DB_PROFILE`:
* `postgres` (`postgresql+asyncpg://...`) — основной профиль для `docker compose up`.


* `mysql` (`mysql+aiomysql://...`) — альтернативный реляционный профиль с корректными типами данных (учесть особенности `DECIMAL`, `VARCHAR` для индексов и блокировок).
* `sqlite` (`sqlite+aiosqlite://...`) — для локального быстрого прогона.


* **Переменные окружения — `.env.example` обязателен:** Создать `.env.example` в корне проекта — полный шаблон всех переменных с безопасными дефолтами и комментариями: настройки БД и `DB_PROFILE`, параметры пула, параметры RAM PostgreSQL (`PG_SHARED_BUFFERS`, `PG_WORK_MEM`, `PG_MAINTENANCE_WORK_MEM`, `PG_EFFECTIVE_CACHE_SIZE`, `PG_MAX_CONNECTIONS`, ...), URL и таймауты `mock-provider`, `PROVIDER_WEBHOOK_SECRET`, режим логирования. Реальный `.env` не коммитится и добавляется в `.gitignore`; `.env.example` коммитится и является источником истины по составу переменных. Приложение должно стартовать из `.env`/окружения без правок кода.



---

### Автотесты и контроль качества (Pytest)

Все тесты должны запускаться одной командой (`uv run pytest` / `docker compose run --rm api uv run pytest`). Использовать `pytest-asyncio`, `pytest-check` (для **soft assertions**), `unittest.mock` / `respx` (аналог Mockito) и `testcontainers` (`PostgresContainer`, `MySqlContainer`).

1. **Unit-тесты (изолированная бизнес-логика):**
* Тесты калькулятора котировок (`SOURCE` и `TARGET` режимы, проверка обратного расчёта на согласованность, граничные суммы, минимальная комиссия, округление).


* Тесты всех кодов ошибок из раздела 6 (`VALIDATION_ERROR`, `UNSUPPORTED_DIRECTION`, `AMOUNT_BELOW_MIN`, `AMOUNT_ABOVE_MAX`, `RATE_UNAVAILABLE`, `QUOTE_NOT_FOUND`, `QUOTE_EXPIRED`, `INSUFFICIENT_FUNDS`, `DAILY_LIMIT_EXCEEDED`). Свежесть курса: годен при `now − updated_at ≤ min(quote_ttl_sec направления, 6ч)`, иначе `RATE_UNAVAILABLE` (6ч — `STALE_AFTER` мок-провайдера, проверено в `mock_provider/reference_data.py`).


* Тесты алгоритма выбора провайдера и fallback-переключения при `decline` и `error_5xx` с мокированием HTTP-клиента (`respx`). Отдельно тесты неизвестного итога: обрыв/таймаут/`504` после отправки — сначала `GET ?reference=`, следующий провайдер только при отсутствии записи (иначе двойная выплата).


* Использование **soft assertions** (`pytest-check`) при проверке составных DTO и полей расчёта (`breakdown`).


2. **Архитектурные тесты (аналог ArchUnit):**
* Написать тесты с использованием `pytest-archon` или AST-анализатора `importlinter` / `grimp`, проверяющие соблюдение границ слоёв: `models` не импортирует `services`/`api`, `repositories` не импортирует `api`, `services` не зависит от FastAPI (`fastapi.Request`/`HTTPException`).


3. **Интеграционные тесты (`testcontainers`):**
* Поднятие реального контейнера PostgreSQL через фикстуру уровня сессии/модуля (`scope="module"` — аналог `@BeforeAll` и `@AfterAll` с гарантированным закрытием контейнера и `engine.dispose()`).
* **Строгая изоляция тестов:** каждый тест работает в изолированной транзакции с откатом (`ROLLBACK`) или очисткой таблиц после теста, чтобы промежуточные данные не протекали между тестами.
* Поддержка распараллеливания тестов через `pytest-xdist` (`pytest -n auto`) с изоляцией схем/БД на воркер.
* Проверка применённых параметров PostgreSQL (`SHOW shared_buffers;`, `SHOW work_mem;`, `SHOW max_connections;`) на контейнере из `testcontainers` — чтобы тюнинг RAM не терялся при запуске тестов.


4. **Нагрузочные и конкурентные тесты пула и балансов:**
* Тест на `asyncio.gather` / `httpx.AsyncClient`, симулирующий шквал параллельных запросов (`50–100` конкурентных запросов) к серверу: проверка состояния пула соединений (`engine.pool.checkedout()`, отсутствие `TimeoutError` пула) и проверка защиты от race condition при списании с одного баланса и при дублирующихся `Idempotency-Key`.





---

### Документация OpenAPI и обязательные артефакты ТЗ

* **Swagger / OpenAPI:** Разметить все эндпоинты и Pydantic-схемы: указать `summary`, `description`, `Field(..., description=..., examples=[...])` для каждого поля DTO и полный перечень HTTP-кодов ответов (`responses={200: ..., 201: ..., 400: ..., 404: ..., 409: ..., 422: ...}`) со схемами ошибок RFC 9457.
* **RESTful подход:** Соблюдение семантики HTTP-методов (`POST` -> `201 Created` с заголовком `Location`, `GET` -> `200 OK`, идемпотентность через `Idempotency-Key`, версионирование API). `POST /transfers`: синхронное ожидание ответа провайдера с таймаутом (~12с), дальше `202` + текущий статус; срабатывание таймаута — ожидание и сверка, а не `FAILED` с возвратом резерва.


* **Обязательные файлы по ТЗ (раздел 1):**
* `docker-compose.yml` и `Dockerfile` (запуск одной командой `docker compose up`, порт `8080`). `Dockerfile` собирается на базе образа с `uv` и использует `uv sync --frozen` — без `pip`.
* `.env.example` (полный шаблон переменных окружения, коммитится в репозиторий; реальный `.env` — в `.gitignore`).


* `README.md` (инструкция запуска с нуля через `uv sync` и `uv run ...`, прогон тестов, описание профилей БД и новых кодов ошибок — включая `IDEMPOTENCY_CONFLICT` (422: тот же `Idempotency-Key` с другим телом) и таблицу маппинга кодов мок-провайдера (`RECIPIENT_ACCOUNT_INVALID`, `AMOUNT_OUT_OF_LIMITS`, `CURRENCY_NOT_SUPPORTED`, `PROVIDER_DISABLED`, `PROVIDER_MAINTENANCE`, `GATEWAY_TIMEOUT` и др.) во внутренние статусы попыток).


* `DECISIONS.md` (обоснование архитектуры, алгоритма выбора провайдера, синхронного/асинхронного ответа `POST /transfers`, параметров RAM PostgreSQL (`shared_buffers`, `work_mem`, `effective_cache_size` и др.) и их связи с пулом соединений, текстовая/PlantUML схема State Machine с акторами переходов).


* `ASSUMPTIONS.md` (разрешённые неоднозначности: правила округления, TTL курсов, поведение при изменении тела запроса с тем же `Idempotency-Key`, fallback при нарушении `min_margin_percent`).


* `LIMITS.md` (честное описание границ решения и компромиссов).





Начни с проектирования структуры директорий и схемы БД в PlantUML (`docs/er_diagram.puml`), затем переходи к реализации моделей, миграций, слоёв и тестов по уровням Level 1 → Level 2 → Level 3.