import asyncio
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import as_utc, now_utc
from app.core.exceptions import (
    IdempotencyConflictError,
    InsufficientFundsError,
    QuoteExpiredError,
    QuoteNotFoundError,
    TransferNotFoundError,
    ValidationError,
)
from app.core.money import format_money
from app.core.settings import Settings
from app.core.types import PayoutState, UnknownResult
from app.repositories import balances as balance_repo
from app.repositories import transfers as transfer_repo
from app.repositories.quotes import get_quote
from app.repositories.references import load_minor_units, load_providers, load_tariffs
from app.services import limits as limit_service
from app.services.providers import (
    Accepted,
    Declined,
    ProviderClient,
    ScoredProvider,
    filter_availability,
    filter_direction,
    filter_limits,
    score_providers,
)
from app.services.quotes import RateData
from app.services.rates import RatesClient
from app.services.transfer_machine import TERMINAL, TransferStateMachine


async def resolve_unknown(
    client: ProviderClient,
    provider_id: str,
    reference: str,
    *,
    attempts: int = 5,
    delay: float = 2.0,
) -> tuple[str, "PayoutState | None"]:
    for _ in range(attempts):
        await asyncio.sleep(delay)
        outcome, state = await client.probe_payout(provider_id, reference)
        if outcome == "found":
            return "found", state
        if outcome == "error":
            continue
        return "missing", None
    return "error", None


def reference_for(transfer_id: uuid.UUID) -> str:
    return transfer_id.hex


def provider_key(reference: str, provider_id: str) -> str:
    return f"{reference}:{provider_id}"


async def create_with_recovery(
    client: ProviderClient,
    provider_id: str,
    reference: str,
    amount: str,
    currency: str,
    recipient: dict,
    callback_url: str,
    idempotency_key: str,
):
    from app.core.types import Accepted as AcceptedOutcome
    from app.core.types import Declined as DeclinedOutcome
    from app.core.types import UnknownResult as UnknownOutcome

    outcome = await client.create_payout(
        provider_id,
        reference,
        amount,
        currency,
        recipient,
        callback_url,
        idempotency_key,
    )
    if isinstance(outcome, (AcceptedOutcome, DeclinedOutcome)):
        return outcome
    if not isinstance(outcome, UnknownOutcome):
        return outcome
    for _ in range(3):
        outcome, found = await resolve_unknown(
            client, provider_id, reference, attempts=1, delay=1.0
        )
        if found is not None:
            return found
        if outcome == "error":
            return UnknownOutcome(reason="provider unreadable")
        retry = await client.create_payout(
            provider_id,
            reference,
            amount,
            currency,
            recipient,
            callback_url,
            idempotency_key,
        )
        if isinstance(retry, (AcceptedOutcome, DeclinedOutcome)):
            return retry
    outcome, found = await resolve_unknown(client, provider_id, reference, attempts=4, delay=2.0)
    if found is not None:
        return found
    if outcome == "error":
        return UnknownOutcome(reason="provider unreadable")
    return None


async def live_rates_map(settings: Settings) -> dict[tuple[str, str], RateData]:
    return await RatesClient(settings.mock_provider_url, settings.mock_provider_timeout_sec).fetch()


def simple_rates(rates: dict[tuple[str, str], RateData]) -> dict[tuple[str, str], Decimal]:
    return {pair: data.rate for pair, data in rates.items()}


async def select_providers(
    session: AsyncSession,
    direction_id: str,
    client_fee_source: Decimal,
    source_amount: Decimal,
    source_currency: str,
    target_amount: Decimal,
    target_currency: str,
    min_margin_percent: Decimal,
    simple: dict[tuple[str, str], Decimal],
    minor_units: dict[str, int],
    now: datetime,
) -> list[ScoredProvider]:
    providers = await load_providers(session)
    candidates = filter_direction(providers, direction_id)
    candidates = filter_limits(candidates, target_amount, target_currency, simple)
    candidates = filter_availability(candidates, now)
    return score_providers(
        candidates,
        client_fee_source,
        source_amount,
        source_currency,
        target_amount,
        target_currency,
        simple,
        minor_units,
        min_margin_percent,
    )


async def create_transfer(
    session: AsyncSession,
    settings: Settings,
    quote_id: uuid.UUID,
    client_id: str,
    recipient: dict,
    idempotency_key: str,
    now: datetime,
) -> tuple:

    minor_units = await load_minor_units(session)
    body = {
        "quote_id": str(quote_id),
        "client_id": client_id,
        "recipient": recipient,
    }
    digest = transfer_repo.body_hash(body)
    existing = await transfer_repo.get_by_idempotency_key(session, idempotency_key)
    if existing is not None:
        if existing.idempotency_hash != digest:
            raise IdempotencyConflictError(idempotency_key)
        return existing, False
    client = await transfer_repo.lock_client(session, client_id)
    if client is None:
        raise ValidationError(f"unknown client {client_id}")
    quote = await get_quote(session, quote_id)
    if quote is None:
        raise QuoteNotFoundError(str(quote_id))
    if as_utc(quote.expires_at) <= as_utc(now):
        raise QuoteExpiredError(str(quote_id))

    rates = await live_rates_map(settings)
    simple = simple_rates(rates)
    tariffs = await load_tariffs(session)
    tariff = next(t for t in tariffs if t.direction_id == quote.direction_id)

    source_amount = Decimal(quote.source_amount)
    source_currency = quote.source_currency

    balance = await balance_repo.get_balance(session, client_id, source_currency, lock=True)
    available = Decimal(balance.available) if balance is not None else Decimal("0")
    if available < source_amount:
        raise InsufficientFundsError(
            f"insufficient funds for {client_id}",
            details={"available": format_money(available, minor_units[source_currency])},
        )
    await limit_service.check_daily_limit(
        session,
        client_id,
        source_amount,
        source_currency,
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
        source_amount,
        source_currency,
        tariff.direction_id,
        minor_units,
        simple,
        now,
    )
    await balance_repo.reserve(session, client_id, source_currency, source_amount)
    transfer, created = await transfer_repo.create_transfer_or_existing(
        session, quote, client_id, recipient, idempotency_key, digest, now
    )
    if not created:
        await balance_repo.release_reserve(session, client_id, source_currency, source_amount)
    await session.commit()
    return transfer, created


async def run_attempts(
    session: AsyncSession,
    settings: Settings,
    transfer_id: uuid.UUID,
    now: datetime,
) -> str:
    minor_units = await load_minor_units(session)
    transfer = await transfer_repo.get_transfer(session, transfer_id, lock=True)
    if transfer is None:
        raise TransferNotFoundError(str(transfer_id))
    if transfer.status in TERMINAL:
        return transfer.status
    quote = await get_quote(session, transfer.quote_id)
    client = ProviderClient(settings.mock_provider_url, settings.mock_provider_timeout_sec)
    attempts = await transfer_repo.latest_attempts(session, transfer.transfer_id)
    live = [a for a in attempts if a.payout_id and a.status in ("PROCESSING", "COMPLETED")]
    unresolved = [a for a in attempts if a.status == "PROCESSING" and not a.payout_id]
    if unresolved and not live:
        return "PROCESSING"
    if live:
        last = live[-1]
        return await wait_for_settlement(
            session,
            settings,
            client,
            transfer.transfer_id,
            last.provider_id,
            last.payout_id or "",
            reference_for(transfer.transfer_id),
            now,
        )
    tariffs = await load_tariffs(session)
    tariff = next(t for t in tariffs if t.direction_id == quote.direction_id)
    rates = await live_rates_map(settings)
    simple = simple_rates(rates)
    client_fee_source = Decimal(quote.fee_amount)
    if quote.fee_currency != quote.source_currency and quote.rate and Decimal(quote.rate) != 0:
        if quote.fee_currency == quote.target_currency:
            client_fee_source = client_fee_source / Decimal(quote.rate)
        else:
            client_fee_source = Decimal("0")
    scored = await select_providers(
        session,
        tariff.direction_id,
        client_fee_source,
        Decimal(quote.source_amount),
        quote.source_currency,
        Decimal(quote.target_amount),
        quote.target_currency,
        tariff.min_margin_percent,
        simple,
        minor_units,
        now,
    )
    if not scored:
        await TransferStateMachine(session, transfer).fail(now)
        await session.commit()
        return "FAILED"
    start_no = len(attempts) + 1
    reference = reference_for(transfer.transfer_id)
    callback_url = f"{settings.public_base_url}/webhooks/{{provider_id}}"
    target_str = format_money(Decimal(quote.target_amount), minor_units[quote.target_currency])
    recipient = {
        "name": transfer.recipient_name,
        "account": transfer.recipient_account,
        "bank_code": transfer.recipient_bank,
    }
    tried = {a.provider_id for a in attempts}
    remaining = [item for item in scored if item.provider.provider_id not in tried]
    for offset, scored_item in enumerate(remaining):
        attempt_no = start_no + offset
        provider = scored_item.provider
        pending_attempt = await transfer_repo.record_attempt(
            session,
            transfer.transfer_id,
            provider.provider_id,
            reference,
            Decimal(quote.target_amount),
            quote.target_currency,
            "PROCESSING",
            None,
            attempt_no,
            None,
            now,
        )
        await session.commit()
        outcome = await create_with_recovery(
            client,
            provider.provider_id,
            reference,
            target_str,
            quote.target_currency,
            recipient,
            callback_url.format(provider_id=provider.provider_id),
            provider_key(reference, provider.provider_id),
        )
        if isinstance(outcome, Accepted):
            await transfer_repo.update_attempt(
                session, pending_attempt, "PROCESSING", None, outcome.payout_id, now
            )
            await TransferStateMachine(session, transfer).start(now)
            await session.commit()
            settlement = await wait_for_settlement(
                session,
                settings,
                client,
                transfer.transfer_id,
                provider.provider_id,
                outcome.payout_id,
                reference,
                now,
            )
            if settlement == "RETRY":
                continue
            return settlement
        if isinstance(outcome, Declined):
            await transfer_repo.update_attempt(
                session, pending_attempt, "FAILED", outcome.failure_code, None, now
            )
            await session.commit()
            continue
        if isinstance(outcome, UnknownResult):
            await transfer_repo.update_attempt(
                session, pending_attempt, "PROCESSING", "NEEDS_REVIEW", None, now
            )
            await TransferStateMachine(session, transfer).start(now)
            await session.commit()
            return "PROCESSING"
        found = outcome if isinstance(outcome, PayoutState) else None
        if found is not None and found.status == "FAILED":
            await transfer_repo.update_attempt(
                session, pending_attempt, "FAILED", found.failure_code, found.payout_id, now
            )
            await session.commit()
            continue
        if found is not None:
            await transfer_repo.update_attempt(
                session,
                pending_attempt,
                found.status,
                found.failure_code,
                found.payout_id,
                now,
            )
            await TransferStateMachine(session, transfer).start(now)
            await session.commit()
            settlement = await wait_for_settlement(
                session,
                settings,
                client,
                transfer.transfer_id,
                provider.provider_id,
                found.payout_id,
                reference,
                now,
            )
            if settlement == "RETRY":
                continue
            return settlement
        await transfer_repo.update_attempt(
            session,
            pending_attempt,
            "FAILED",
            "PROVIDER_UNREACHABLE",
            None,
            now,
        )
        await session.commit()
    await TransferStateMachine(session, transfer).fail(now)
    await session.commit()
    return transfer.status


async def wait_for_settlement(
    session: AsyncSession,
    settings: Settings,
    client: ProviderClient,
    transfer_id: uuid.UUID,
    provider_id: str,
    payout_id: str,
    reference: str,
    started: datetime,
    poll_interval: float = 0.5,
) -> str:
    deadline = started.timestamp() + settings.api_sync_timeout_sec
    now = started
    while now.timestamp() < deadline:
        await asyncio.sleep(poll_interval)
        now = now_utc()
        state = await client.get_payout(provider_id, payout_id)
        if state is None:
            state = await client.find_payout(provider_id, reference)
        if state is None:
            continue
        attempts = await transfer_repo.latest_attempts(session, transfer_id)
        current = attempts[-1] if attempts else None
        if current is not None and (
            current.status != state.status or current.payout_id != state.payout_id
        ):
            await transfer_repo.update_attempt(
                session, current, state.status, state.failure_code, state.payout_id, now
            )
            await session.commit()
        transfer = await transfer_repo.get_transfer(session, transfer_id, lock=True)
        if transfer is None:
            return "FAILED"
        action = await TransferStateMachine(session, transfer).settle_from_payout(state.status, now)
        await session.commit()
        if action != "PROCESSING":
            return action
    transfer = await transfer_repo.get_transfer(session, transfer_id, lock=True)
    if transfer is None or transfer.status in TERMINAL:
        return transfer.status if transfer else "FAILED"
    started_processing = await TransferStateMachine(session, transfer).start(now_utc())
    await session.commit()
    if started_processing:
        return "PROCESSING"
    await session.refresh(transfer)
    return transfer.status
