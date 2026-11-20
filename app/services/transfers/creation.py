import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import as_utc
from app.core.exceptions import (
    ClientNotFoundError,
    IdempotencyConflictError,
    InsufficientFundsError,
    QuoteExpiredError,
    QuoteNotFoundError,
)
from app.core.idempotency import body_hash
from app.core.money import format_money
from app.core.settings import Settings
from app.core.types import Money, ScoredProvider
from app.models.tables import Transfer
from app.repositories import balances as balance_repo
from app.repositories import transfers as transfer_repo
from app.repositories.quotes import get_quote
from app.repositories.references import load_minor_units, load_providers, load_tariffs
from app.services import limits as limit_service
from app.services.providers import (
    filter_availability,
    filter_direction,
    filter_limits,
    score_providers,
)
from app.services.quotes import RateData, filter_fresh_rates, find_tariff
from app.services.rates import RatesClient


async def live_rates_map(settings: Settings) -> dict[tuple[str, str], RateData]:
    """Живые курсы мока одной картой base/quote."""
    return await RatesClient(settings.mock_provider_url, settings.mock_provider_timeout_sec).fetch()


def simple_rates(
    rates: dict[tuple[str, str], RateData], now: datetime
) -> dict[tuple[str, str], Decimal]:
    """Только свежие курсы: протухшие отбрасываются, а не подменяются."""
    return filter_fresh_rates(rates, now)


def _fee_in_source(fee: Money, source: Money, target_currency: str, rate: Decimal | None) -> Money:
    """Комиссия клиента в валюте источника для расчёта маржи."""
    if fee.currency == source.currency:
        return fee
    if not rate:
        return Money.zero(source.currency)
    if fee.currency == target_currency:
        return Money(fee.amount / rate, source.currency)
    return Money.zero(source.currency)


async def select_providers(
    session: AsyncSession,
    direction_id: str,
    client_fee: Money,
    source: Money,
    target: Money,
    min_margin_percent: Decimal,
    simple: dict[tuple[str, str], Decimal],
    now: datetime,
) -> list[ScoredProvider]:
    """Конвейер отбора: направление, лимиты, доступность, скоринг."""
    providers = await load_providers(session)
    candidates = filter_direction(providers, direction_id)
    candidates = filter_limits(candidates, target, simple)
    candidates = filter_availability(candidates, now)
    return score_providers(
        candidates,
        client_fee=client_fee,
        source=source,
        target=target,
        rates=simple,
        min_margin_percent=min_margin_percent,
    )


async def _find_existing(
    session: AsyncSession, idempotency_key: str, client_id: str, digest: str
) -> Transfer | None:
    """Повтор по ключу идемпотентности: тот же перевод или конфликт тела."""
    existing = await transfer_repo.get_by_idempotency_key(session, idempotency_key, client_id)
    if existing is not None and existing.idempotency_hash != digest:
        raise IdempotencyConflictError(idempotency_key)
    return existing


async def create_transfer(
    session: AsyncSession,
    settings: Settings,
    quote_id: uuid.UUID,
    client_id: str,
    recipient: dict,
    idempotency_key: str,
    now: datetime,
) -> tuple:
    """Резервирует деньги и создаёт перевод: проверки, лимиты, резерв, вставка."""

    minor_units = await load_minor_units(session)
    body = {
        "quote_id": str(quote_id),
        "client_id": client_id,
        "recipient": recipient,
    }
    digest = body_hash(body)
    existing = await _find_existing(session, idempotency_key, client_id, digest)
    if existing is not None:
        return existing, False
    quote = await get_quote(session, quote_id)
    if quote is None:
        raise QuoteNotFoundError(str(quote_id))
    if as_utc(quote.expires_at) <= as_utc(now):
        raise QuoteExpiredError(str(quote_id))
    rates = await live_rates_map(settings)
    simple = simple_rates(rates, now)
    client = await transfer_repo.lock_client(session, client_id)
    if client is None:
        raise ClientNotFoundError(f"unknown client {client_id}")
    existing = await _find_existing(session, idempotency_key, client_id, digest)
    if existing is not None:
        return existing, False
    tariffs = await load_tariffs(session)
    tariff = find_tariff(tariffs, quote.direction_id)

    source = Money(Decimal(quote.source_amount), quote.source_currency)

    balance = await balance_repo.get_balance(session, client_id, source.currency, lock=True)
    available = Decimal(balance.available) if balance is not None else Decimal("0")
    if available < source.amount:
        raise InsufficientFundsError(
            f"insufficient funds for {client_id}",
            details={"available": format_money(available, minor_units[source.currency])},
        )
    await limit_service.check_daily_limit(
        session,
        client_id,
        source,
        Decimal(quote.rate),
        quote.source_currency,
        quote.target_currency,
        minor_units,
        now,
        simple,
    )
    await limit_service.check_limit_definitions(
        session,
        client_id,
        source,
        tariff.direction_id,
        minor_units,
        simple,
        now,
    )
    await balance_repo.reserve(session, client_id, source.currency, source.amount)
    transfer, created = await transfer_repo.create_transfer_or_existing(
        session, quote, client_id, recipient, idempotency_key, digest, now
    )
    if created:
        await transfer_repo.record_transition(
            session, transfer.transfer_id, None, "PENDING", "client", now
        )
    if not created:
        await balance_repo.release_reserve(session, client_id, source.currency, source.amount)
    await session.commit()
    return transfer, created
