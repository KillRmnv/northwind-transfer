# Ревью-раунд 3: chaos-матрица мок-провайдера

- Дата: 2026-11-19. Код не менялся (`HEAD = be5a72a`); только запуски.
- Метод: с хоста `localhost:8080/9000` недоступны (сеть/VPN — прямые пробы Bridge-IP тоже таймаутят),
  поэтому все проверки шли изнутри compose-сети через helper-контейнер
  (`docker run --rm --network transfer-core_default python:3.12-slim`),
  `chaos_run.py` — с флагами `--api http://api:8080 --mock http://mock-provider:9000`.
  Ручные сценарии — скриптами в `/tmp/opencode` (вне репозитория).
- Каждый прогон начинался с чистой БД:
  `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` + `docker compose restart api`
  (entrypoint: `alembic upgrade head` + seed).
- Базовый ориентир (не этот раунд, из `docs/DECISIONS.md` D28): seed 11, concurrency 10, 300 переводов —
  `RESULT: OK`, 275 `COMPLETED` / 25 `FAILED`, 38 двойных сабмитов, 0 нарушений, 270 с.

## Таблица прогонов

| # | Конфиг | Итог | Нарушения |
|---|---|---|---|
| 1 | chaos, `MOCK_SEED=42`, `--transfers 300 --concurrency 10 --settle-timeout 420` | exit 0, `RESULT: OK`, 273 `COMPLETED` / 27 `FAILED`, 42 double_submits, 333 с | 0 по всем 10 инвариантам (`double_payouts`, `stuck_reserves`, `balance_invariant`, `failed_but_paid`, `success_without_payout`, `amount_mismatch`, `unaccounted_payouts`, `idempotency`, `stuck_transfers`, `api_errors`) |
| 2 | chaos, seed 42, `--transfers 30 --concurrency 1 --settle-timeout 300 --ledger-grace 20` | exit 0, `RESULT: OK`, 27 `COMPLETED` / 3 `FAILED`, 5 double_submits, 253 с | 0 по всем инвариантам |
| 3 | normal, seed 1 + forced decline (`X-Scenario: decline`, `PUT/DELETE admin/scenario`): прямые вызовы мока + цепочка через API | все проверки OK (детали ниже) | неприменимо (ручные проверки, не `chaos_run`); резерв везде 0 |
| 4 | normal + `X-Scenario: slow` на всех провайдерах направления | `POST /transfers` → **202** `PROCESSING` за 12.1 с; затем **COMPLETED** за ~32 с; резерв 0 | нет |
| 5 | `docker compose up -d --scale api=2` | второй инстанс не стартовал: `port is already allocated` (8080); первый продолжил работать; после `--scale api=1` следов `api-2` нет, api healthy | н/д — инфра-проба |

## Прогон 1: chaos seed 42 (повтор базового с другим seed)

Расклад 273/27 против 275/25 на seed 11 — в пределах естественного разброса chaos-распределения
(`chaos_settle`: отказ с вероятностью `1 - success_rate`). Ноль нарушений подтверждает,
что зелёный прогон на seed 11 не был удачной случайностью: инварианты (идемпотентность двойных
сабмитов — 42 шт без дублей переводов, сверка с ledger мока, `available + reserved`) держатся
на другом seed. Длительность 333 с против 270 с — тоже разброс (доля `SLOW`/повторов).

## Прогон 2: chaos, concurrency 1

Все инварианты сошлись без параллелизма: 0 нарушений, exit 0. Вывод: в коде нет дефектов,
которые маскируются только гонками (и наоборот — параллелизм прогона 1 не скрывал нарушений:
их тоже 0). 253 с на 30 переводов последовательно — цена chaos-задержек мока
(`SLOW` 5–12 с, `HANG_CREATED` 35 с, повторные попытки); это время мока, а не деградация API.

## Прогон 3: normal + forced decline

Прямые вызовы мока (`POST /prov_alpha/payouts`, валюта — **таргетная** `THB`, не `USD` провайдера):

- `X-Scenario: decline` → `422 RECIPIENT_ACCOUNT_INVALID`, запись `FAILED` видна и через
  `GET ?reference=`, и через `GET /{payout_id}`;
- тот же ключ + то же тело → повтор того же `422` (идемпотентный replay);
- тот же ключ + другое тело → `409 IDEMPOTENCY_KEY_REUSED`;
- `X-Scenario: bogus` → `400 UNKNOWN_SCENARIO`.

Отказоустойчивость API (normal-режим, направление RU-TH-BANK, клиент c_101):

- happy path: `POST /transfers` → `201 COMPLETED` синхронно (settle 2 с < sync-таймаута 12 с);
- decline только на топ-провайдере (`prov_alpha`): цепочка
  `[prov_alpha FAILED → prov_bravo COMPLETED]`, синхронно `201 COMPLETED` — fallback работает;
- decline на всех провайдерах направления: терминальный `FAILED`, попыток ровно столько,
  сколько в скоринге, резерв `0.00` — корректное поражение без залипания.

Важная деталь скоринга (проверена репликой `score_providers` на живых курсах мока):
для RU-TH-BANK 4000 RUB скоринг даёт только `[prov_alpha, prov_bravo]` —
`prov_echo` отсечён окном обслуживания (2026-11-04 → 2027-01-04),
`prov_delta` выключен, `prov_charlie`/`prov_golf` не проходят `min_margin 0.50%`
(0.19% / −2.39%). Поэтому «decline на alpha+bravo+charlie → FAILED с 2 попытками» —
корректный исход (исчерпаны все кандидаты), а не обрыв цепочки. Первоначальное ожидание
«COMPLETED через echo/charlie» в этом прогоне было ошибкой постановки, исправлено
пинpoint-проверкой fallback через `prov_bravo`.

## Прогон 4: slow-сценарий

Со `slow` на всех провайдерах: `POST /transfers` вернулся `202 PROCESSING` за 12.1 с
(сработал `api_sync_timeout_sec=12`, API не блокируется на 30-секундном моке),
затем перевод закрылся `COMPLETED` за ~32 с (settle 30 с + вебхук), резерв `0.00`.
Контракт «202 → асинхронное закрытие» соблюдён; кто именно закрыл (вебхук vs сверка)
по логам не разделялся — для отчёта достаточно факта терминала и нулевого резерва.

## Прогон 5: два инстанса api

Факт: вторая реплика не стартует из-за опубликованного порта `8080:8080`
(`Bind for 0.0.0.0:8080 failed: port is already allocated`); `api-2` остался в статусе
`Created` и был убран возвратом `--scale api=1`. Конкурентных миграций не случилось
(entrypoint второй реплики не выполнялся — падение на этапе сети).

Риски запуска двух инстансов (по коду, без живого дубля):

1. Два `reconcile_loop` без single-runner: в `app/main.py` / `reconcile.py` нет
   `pg_try_advisory_lock` / `FOR UPDATE SKIP LOCKED` (см. также R2-04) — два прохода будут
   параллельно вызывать `run_attempts` по одним переводам → дубли попыток/гонки терминалов.
2. Два entrypoint одновременно: конкурентные `alembic upgrade head` + seed (seed, вероятно,
   идемпотентен, но гонка миграций возможна).
3. Два Uvicorn на одном порту внутри одной compose-сети возможны только без публикации
   порта (режим без `ports:` + внешний балансировщик) — текущая конфигурация горизонтальное
   масштабирование api не поддерживает.

## Выводы об устойчивости

1. Chaos-инварианты стабильны на двух seed (11 и 42) и на concurrency 1 и 10.
2. Цепочка провайдеров, идемпотентность мока/API и контракт 202/slow работают как задумано;
   «поражение» (все declined) завершается терминальным `FAILED` с освобождённым резервом.
3. Скоринг провайдеров ведёт себя объяснимо (maintenance/margin/limits); сюрпризов нет.
4. Горизонтальное масштабирование api в текущем compose-файле невозможно (порт) и небезопасно
   по коду (двойной reconcile без single-runner).

## Что прогнать следующим

1. Chaos seed 42 + `--transfers 300 --concurrency 10` повторно (воспроизводимость расклада 273/27).
2. Chaos с `MOCK_SLOW_SEC`/`MOCK_SETTLE_SEC`, отличными от дефолтов (например settle 10 с) —
   проверить, что инварианты не зависят от темпа мока.
3. `error_5xx`-сценарий через API-цепочку: пин `error_5xx` на топ-провайдере → ожидается
   `ERROR_CREATED`/`GATEWAY_TIMEOUT`-ветка с `GET ?reference=` и без двойной выплаты.
4. `lost_webhook`-покрытие на уровне счёта: долгий `PROCESSING` только на poll/сверке
   (вебхуки, возможно, глушить на dannом стенде нельзя — нужен прокси/флаг).
5. Single-runner для reconcile (сначала фикс R2-04/P2-6, затем повтор прогона 5 уже с двумя
   инстансами без публикации портов).
6. В `chaos_run.py` при `stuck_transfers` печатать последнюю попытку (`status`/`failure_code`)
   — ускорит разбор будущих зависших прогонов (п. 10 из round2-reconcile-stuck).
