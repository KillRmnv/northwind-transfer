from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DailyLimitExceededError
from app.core.money import format_money
from app.models.tables import Client, LimitDefinition
from app.repositories.transfers import sum_day_source, sum_window_source


def day_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


async def to_limit_currency(
    amount: Decimal,
    amount_currency: str,
    limit_currency: str,
    quote_rate: Decimal | None,
    quote_source: str,
    quote_target: str,
) -> Decimal | None:
    if amount_currency == limit_currency:
        return amount
    if quote_rate is None or quote_rate == 0:
        return None
    if amount_currency == quote_source and limit_currency == quote_target:
        return amount * quote_rate
    if amount_currency == quote_target and limit_currency == quote_source:
        return amount / quote_rate
    return None


async def check_daily_limit(
    session: AsyncSession,
    client_id: str,
    source_amount: Decimal,
    source_currency: str,
    quote_rate: Decimal,
    quote_source: str,
    quote_target: str,
    minor_units: dict[str, int],
    now: datetime,
    live_rates: dict[tuple[str, str], Decimal] | None = None,
) -> None:
    client = await session.get(Client, client_id)
    if client is None:
        return
    limit_currency = client.daily_limit_currency
    start, end = day_bounds(now)
    rows = await sum_day_source(session, client_id, start, end)
    spent = Decimal("0")
    for amount, currency, _, _, _ in rows:
        converted = await to_limit_currency(
            amount, currency, limit_currency, quote_rate, quote_source, quote_target
        )
        if converted is None and live_rates:
            converted = convert_via_rates(amount, currency, limit_currency, live_rates)
        if converted is not None:
            spent += converted
    addition = await to_limit_currency(
        source_amount, source_currency, limit_currency, quote_rate, quote_source, quote_target
    )
    if addition is None and live_rates:
        addition = convert_via_rates(source_amount, source_currency, limit_currency, live_rates)
    if addition is None:
        return
    limit = Decimal(client.daily_limit_amount)
    if spent + addition > limit:
        raise DailyLimitExceededError(
            f"daily limit exceeded for {client_id}",
            details={"limit": format_money(limit, minor_units[limit_currency])},
        )


async def check_limit_definitions(
    session: AsyncSession,
    client_id: str,
    source_amount: Decimal,
    source_currency: str,
    tariff_direction_id: str,
    minor_units: dict[str, int],
    live_rates: dict[tuple[str, str], Decimal],
    now: datetime,
) -> None:
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
    for definition in rows:
        if definition.scope == "direction" and definition.direction_id != tariff_direction_id:
            continue
        if definition.scope == "category":
            continue
        weight = Decimal(definition.weight)
        if weight <= 0:
            weight = Decimal("1")
        if definition.period_type == "MINUTES":
            window_start = now - timedelta(minutes=int(definition.window_days or 1))
        elif definition.period_type == "HOURLY":
            window_start = now - timedelta(hours=1)
        elif definition.period_type == "DAILY":
            window_start, _ = day_bounds(now)
        elif definition.period_type == "WINDOW_N":
            if not definition.window_days or int(definition.window_days) <= 0:
                continue
            window_start = now - timedelta(days=int(definition.window_days))
        else:
            continue
        entries = await sum_window_source(
            session,
            client_id,
            definition.direction_id if definition.scope == "direction" else None,
            window_start,
        )
        total = Decimal("0")
        for amount, currency, rate, target_currency in entries:
            converted = await to_limit_currency(
                amount, currency, definition.currency, rate, currency, target_currency
            )
            if converted is not None:
                total += converted * weight
        live_rate = live_rates.get((source_currency, definition.currency))
        addition = await to_limit_currency(
            source_amount,
            source_currency,
            definition.currency,
            _rate_value(live_rate),
            source_currency,
            definition.currency,
        )
        if addition is None:
            addition = convert_via_rates(
                source_amount, source_currency, definition.currency, live_rates
            )
        if addition is None:
            continue
        if total + addition * weight > Decimal(definition.amount):
            raise DailyLimitExceededError(
                f"limit {definition.scope}/{definition.period_type} exceeded for {client_id}",
                details={"limit": str(definition.amount)},
            )


def _rate_value(rate) -> Decimal | None:
    if rate is None:
        return None
    return rate.rate if hasattr(rate, "rate") else rate


def convert_via_rates(
    amount: Decimal,
    amount_currency: str,
    target_currency: str,
    live_rates: dict[tuple[str, str], Decimal],
) -> Decimal | None:
    from app.services.providers import convert_amount

    return convert_amount(amount, amount_currency, target_currency, live_rates)
