from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DailyLimitExceededError, ValidationError
from app.core.money import format_money
from app.core.types import Money
from app.models.tables import Client, LimitDefinition
from app.repositories.transfers import sum_day_source, sum_window_source
from app.services.providers import convert_or_raise


def day_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Границы календарных суток UTC для дневного лимита."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def to_limit_currency(
    amount: Money,
    limit_currency: str,
    quote_rate: Decimal | None,
    quote_source: str,
    quote_target: str,
) -> Decimal | None:
    """Сумма в валюте лимита через курс котировки; None — нужна конвертация."""
    if amount.currency == limit_currency:
        return amount.amount
    if quote_rate is None or quote_rate == 0:
        return None
    if amount.currency == quote_source and limit_currency == quote_target:
        return amount.amount * quote_rate
    if amount.currency == quote_target and limit_currency == quote_source:
        return amount.amount / quote_rate
    return None


def convert_entry_or_raise(
    amount: Decimal,
    amount_currency: str,
    target_currency: str,
    quote_rate: Decimal | None,
    quote_source: str,
    quote_target: str,
    flat_rates: dict[tuple[str, str], Decimal],
) -> Decimal:
    """Сумма в валюте лимита любым путём или RATE_UNAVAILABLE."""
    converted = to_limit_currency(
        Money(amount, amount_currency),
        target_currency,
        quote_rate,
        quote_source,
        quote_target,
    )
    if converted is not None:
        return converted
    return convert_or_raise(Money(amount, amount_currency), target_currency, flat_rates)


def _minutes_window(now: datetime, window_days: int | None) -> datetime | None:
    """Окно последних N минут; минуты лежат в window_days."""
    return now - timedelta(minutes=int(window_days or 1))


def _hourly_window(now: datetime, window_days: int | None) -> datetime | None:
    """Окно последнего часа."""
    return now - timedelta(hours=1)


def _daily_window(now: datetime, window_days: int | None) -> datetime | None:
    """Окно с полуночи UTC."""
    start, _ = day_bounds(now)
    return start


def _window_n_window(now: datetime, window_days: int | None) -> datetime | None:
    """Окно последних N дней; мусор в настройке — пропуск правила."""
    if not window_days or int(window_days) <= 0:
        return None
    return now - timedelta(days=int(window_days))


PERIOD_WINDOW_STARTS: dict[str, Callable[[datetime, int | None], datetime | None]] = {
    "MINUTES": _minutes_window,
    "HOURLY": _hourly_window,
    "DAILY": _daily_window,
    "WINDOW_N": _window_n_window,
}


async def check_daily_limit(
    session: AsyncSession,
    client_id: str,
    source: Money,
    quote_rate: Decimal,
    quote_source: str,
    quote_target: str,
    minor_units: dict[str, int],
    now: datetime,
    live_rates: dict[tuple[str, str], Decimal] | None = None,
) -> None:
    """Суточный лимит клиента: учтены резервы, а не только финалы."""
    client = await session.get(Client, client_id)
    if client is None:
        return
    limit_currency = client.daily_limit_currency
    flat = {pair: _rate_value(rate) for pair, rate in (live_rates or {}).items()}
    flat_rates = {pair: value for pair, value in flat.items() if value is not None}
    start, end = day_bounds(now)
    rows = await sum_day_source(session, client_id, start, end)
    spent = Decimal("0")
    for amount, currency, _, _, _ in rows:
        spent += convert_entry_or_raise(
            amount, currency, limit_currency, quote_rate, quote_source, quote_target, flat_rates
        )
    addition = convert_entry_or_raise(
        source.amount,
        source.currency,
        limit_currency,
        quote_rate,
        quote_source,
        quote_target,
        flat_rates,
    )
    limit = Decimal(client.daily_limit_amount)
    if spent + addition > limit:
        raise DailyLimitExceededError(
            f"daily limit exceeded for {client_id}",
            details={"limit": format_money(limit, minor_units[limit_currency])},
        )


async def check_limit_definitions(
    session: AsyncSession,
    client_id: str,
    source: Money,
    tariff_direction_id: str,
    minor_units: dict[str, int],
    live_rates: dict[tuple[str, str], Decimal],
    now: datetime,
) -> None:
    """Оконные лимиты из определений: вес, направление, fail-closed."""
    rows = (
        (
            await session.execute(
                select(LimitDefinition).where(
                    LimitDefinition.client_id == client_id,
                    LimitDefinition.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )

    flat_rates = {pair: _rate_value(rate) for pair, rate in live_rates.items()}
    flat_rates = {pair: value for pair, value in flat_rates.items() if value is not None}
    for definition in rows:
        if definition.scope not in ("client", "direction"):
            raise ValidationError(f"unknown limit scope {definition.scope}")
        if definition.scope == "direction" and definition.direction_id != tariff_direction_id:
            continue
        weight = Decimal(definition.weight)
        if weight <= 0:
            weight = Decimal("1")
        start_fn = PERIOD_WINDOW_STARTS.get(definition.period_type)
        if start_fn is None:
            continue
        window_start = start_fn(now, definition.window_days)
        if window_start is None:
            continue
        entries = await sum_window_source(
            session,
            client_id,
            definition.direction_id if definition.scope == "direction" else None,
            window_start,
        )
        total = Decimal("0")
        for amount, currency, rate, target_currency in entries:
            total += (
                convert_entry_or_raise(
                    amount,
                    currency,
                    definition.currency,
                    rate,
                    currency,
                    target_currency,
                    flat_rates,
                )
                * weight
            )
        live_rate = flat_rates.get((source.currency, definition.currency))
        addition = convert_entry_or_raise(
            source.amount,
            source.currency,
            definition.currency,
            live_rate,
            source.currency,
            definition.currency,
            flat_rates,
        )
        if total + addition * weight > Decimal(definition.amount):
            raise DailyLimitExceededError(
                f"limit {definition.scope}/{definition.period_type} exceeded for {client_id}",
                details={"limit": str(definition.amount)},
            )


def _rate_value(rate) -> Decimal | None:
    """Курс из пары или сырого числа: единая нормализация входов."""
    if rate is None:
        return None
    return rate.rate if hasattr(rate, "rate") else rate
