import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import as_utc, now_utc
from app.core.exceptions import (
    ClientNotFoundError,
    IdempotencyConflictError,
    InsufficientFundsError,
    QuoteExpiredError,
    QuoteNotFoundError,
    TransferNotFoundError,
)
from app.core.locks import transfer_lock
from app.core.money import format_money
from app.core.settings import Settings
from app.core.types import (
    AttemptAction,
    AttemptDecision,
    Money,
    PayoutState,
    ProbeOutcome,
    UnknownResult,
)
from app.models.tables import Transfer
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
    confirm_payout,
    filter_availability,
    filter_direction,
    filter_limits,
    score_providers,
)
from app.services.quotes import RateData, filter_fresh_rates, find_tariff
from app.services.rates import RatesClient
from app.services.transfer_machine import TERMINAL, TransferStateMachine

UNRESOLVED_RETRY_AFTER_SEC = 60


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int
    delay: timedelta


SHORT_PROBE = RetryPolicy(attempts=1, delay=timedelta(seconds=1))
FINAL_PROBE = RetryPolicy(attempts=4, delay=timedelta(seconds=2))


async def probe_with_retries(
    client: ProviderClient,
    provider_id: str,
    reference: str,
    policy: RetryPolicy,
) -> tuple[str, "PayoutState | None"]:
    for _ in range(policy.attempts):
        await asyncio.sleep(policy.delay.total_seconds())
        outcome, state = await client.probe_payout(provider_id, reference)
        if outcome is ProbeOutcome.FOUND:
            return ProbeOutcome.FOUND, state
        if outcome is ProbeOutcome.ERROR:
            continue
        return ProbeOutcome.MISSING, None
    return ProbeOutcome.ERROR, None


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
) -> AttemptDecision:
    outcome = await client.create_payout(
        provider_id,
        reference,
        amount,
        currency,
        recipient,
        callback_url,
        idempotency_key,
    )
    if isinstance(outcome, Accepted):
        return AttemptDecision(
            AttemptAction.ADOPT_LIVE, payout_id=outcome.payout_id, status=outcome.status
        )
    if isinstance(outcome, Declined):
        return AttemptDecision(AttemptAction.FAIL_AND_NEXT, failure_code=outcome.failure_code)
    if isinstance(outcome, PayoutState):
        if outcome.status == "FAILED":
            return AttemptDecision(
                AttemptAction.FAIL_AND_NEXT,
                payout_id=outcome.payout_id,
                status=outcome.status,
                failure_code=outcome.failure_code,
            )
        return AttemptDecision(
            AttemptAction.ADOPT_LIVE,
            payout_id=outcome.payout_id,
            status=outcome.status,
            failure_code=outcome.failure_code,
        )
    if not isinstance(outcome, UnknownResult):
        return AttemptDecision(AttemptAction.FAIL_AND_NEXT, failure_code="PROVIDER_UNREACHABLE")
    for _ in range(3):
        outcome, found = await probe_with_retries(client, provider_id, reference, SHORT_PROBE)
        if found is not None:
            return _adopt_or_fail(found)
        if outcome is ProbeOutcome.ERROR:
            return AttemptDecision(AttemptAction.PARK_FOR_REVIEW)
        retry = await client.create_payout(
            provider_id,
            reference,
            amount,
            currency,
            recipient,
            callback_url,
            idempotency_key,
        )
        if isinstance(retry, Accepted):
            return AttemptDecision(
                AttemptAction.ADOPT_LIVE, payout_id=retry.payout_id, status=retry.status
            )
        if isinstance(retry, Declined):
            return AttemptDecision(AttemptAction.FAIL_AND_NEXT, failure_code=retry.failure_code)
    outcome, found = await probe_with_retries(client, provider_id, reference, FINAL_PROBE)
    if found is not None:
        return _adopt_or_fail(found)
    if outcome is ProbeOutcome.ERROR:
        return AttemptDecision(AttemptAction.PARK_FOR_REVIEW)
    return AttemptDecision(AttemptAction.FAIL_AND_NEXT, failure_code="PROVIDER_UNREACHABLE")


def _adopt_or_fail(found: PayoutState) -> AttemptDecision:
    if found.status == "FAILED":
        return AttemptDecision(
            AttemptAction.FAIL_AND_NEXT,
            payout_id=found.payout_id,
            status=found.status,
            failure_code=found.failure_code,
        )
    return AttemptDecision(
        AttemptAction.ADOPT_LIVE,
        payout_id=found.payout_id,
        status=found.status,
        failure_code=found.failure_code,
    )


async def _probe_pending(
    session: AsyncSession,
    client: ProviderClient,
    transfer_id: uuid.UUID,
    reference: str,
    attempts: list,
) -> list:
    for pending in [a for a in attempts if a.status == "PROCESSING" and not a.payout_id]:
        outcome, state = await client.probe_payout(pending.provider_id, reference)
        probed_at = now_utc()
        if outcome is ProbeOutcome.FOUND and state is not None:
            await transfer_repo.update_attempt(
                session, pending, state.status, state.failure_code, state.payout_id, probed_at
            )
            await session.commit()
        elif (
            outcome is ProbeOutcome.MISSING
            and (probed_at - as_utc(pending.created_at)).total_seconds()
            >= UNRESOLVED_RETRY_AFTER_SEC
        ):
            await transfer_repo.update_attempt(
                session, pending, "FAILED", "PROVIDER_UNREACHABLE", None, probed_at
            )
            await session.commit()
    return await transfer_repo.latest_attempts(session, transfer_id)


def _young_unconfirmed(attempts: list) -> list:
    moment = now_utc()
    return [
        a
        for a in attempts
        if a.status == "PROCESSING"
        and not a.payout_id
        and (moment - as_utc(a.created_at)).total_seconds() < UNRESOLVED_RETRY_AFTER_SEC
    ]


async def _settle_expired(
    session: AsyncSession,
    settings: Settings,
    client: ProviderClient,
    transfer,
    reference: str,
    now: datetime,
    actor: str,
) -> str:
    attempts = await transfer_repo.latest_attempts(session, transfer.transfer_id)
    attempts = await _probe_pending(session, client, transfer.transfer_id, reference, attempts)
    live = [a for a in attempts if a.payout_id and a.status in ("PROCESSING", "COMPLETED")]
    if live:
        last = live[-1]
        settlement = await wait_for_settlement(
            session,
            settings,
            client,
            transfer.transfer_id,
            last.provider_id,
            last.payout_id or "",
            reference,
            now,
            actor=actor,
        )
        if settlement != "RETRY":
            return settlement
    if _young_unconfirmed(attempts):
        await TransferStateMachine(session, transfer).needs_review(now, actor)
        await session.commit()
        return "NEEDS_REVIEW"
    await TransferStateMachine(session, transfer).fail(now, actor)
    await session.commit()
    return "FAILED"


async def live_rates_map(settings: Settings) -> dict[tuple[str, str], RateData]:
    return await RatesClient(settings.mock_provider_url, settings.mock_provider_timeout_sec).fetch()


def simple_rates(
    rates: dict[tuple[str, str], RateData], now: datetime
) -> dict[tuple[str, str], Decimal]:
    return filter_fresh_rates(rates, now)


def _fee_in_source(fee: Money, source: Money, target_currency: str, rate: Decimal | None) -> Money:
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

    minor_units = await load_minor_units(session)
    body = {
        "quote_id": str(quote_id),
        "client_id": client_id,
        "recipient": recipient,
    }
    digest = transfer_repo.body_hash(body)
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


async def run_attempts_locked(
    session: AsyncSession,
    settings: Settings,
    transfer_id: uuid.UUID,
    now: datetime,
    actor: str = "service",
) -> str:
    async with transfer_lock(f"tx:{transfer_id}"):
        return await run_attempts(session, settings, transfer_id, now, actor)


async def run_attempts(
    session: AsyncSession,
    settings: Settings,
    transfer_id: uuid.UUID,
    now: datetime,
    actor: str = "service",
) -> str:
    minor_units = await load_minor_units(session)
    rates = await live_rates_map(settings)
    simple = simple_rates(rates, now)
    transfer = await transfer_repo.get_transfer(session, transfer_id, lock=True)
    if transfer is None:
        raise TransferNotFoundError(str(transfer_id))
    if transfer.status in TERMINAL:
        return transfer.status
    quote = await get_quote(session, transfer.quote_id)
    if quote is None:
        raise QuoteNotFoundError(str(transfer.quote_id))
    client = ProviderClient(settings.mock_provider_url, settings.mock_provider_timeout_sec)
    reference = reference_for(transfer.transfer_id)
    if as_utc(quote.expires_at) <= as_utc(now):
        return await _settle_expired(session, settings, client, transfer, reference, now, actor)
    attempts = await transfer_repo.latest_attempts(session, transfer.transfer_id)
    attempts = await _probe_pending(session, client, transfer.transfer_id, reference, attempts)
    if _young_unconfirmed(attempts):
        await TransferStateMachine(session, transfer).needs_review(now, actor)
        await session.commit()
        return "NEEDS_REVIEW"
    live = [a for a in attempts if a.payout_id and a.status in ("PROCESSING", "COMPLETED")]
    if live:
        last = live[-1]
        settlement = await wait_for_settlement(
            session,
            settings,
            client,
            transfer.transfer_id,
            last.provider_id,
            last.payout_id or "",
            reference,
            now,
        )
        if settlement != "RETRY":
            return settlement
        attempts = await transfer_repo.latest_attempts(session, transfer.transfer_id)
    if as_utc(quote.expires_at) <= as_utc(now):
        return await _settle_expired(session, settings, client, transfer, reference, now, actor)
    tariffs = await load_tariffs(session)
    tariff = find_tariff(tariffs, quote.direction_id)
    source = Money(Decimal(quote.source_amount), quote.source_currency)
    target = Money(Decimal(quote.target_amount), quote.target_currency)
    client_fee = _fee_in_source(
        Money(Decimal(quote.fee_amount), quote.fee_currency),
        source,
        quote.target_currency,
        Decimal(quote.rate) if quote.rate else None,
    )
    scored = await select_providers(
        session,
        tariff.direction_id,
        client_fee,
        source,
        target,
        tariff.min_margin_percent,
        simple,
        now,
    )
    if not scored:
        await TransferStateMachine(session, transfer).fail(now, actor)
        await session.commit()
        return "FAILED"
    start_no = len(attempts) + 1
    callback_url = f"{settings.public_base_url}/webhooks/{{provider_id}}"
    target_str = format_money(Decimal(quote.target_amount), minor_units[quote.target_currency])
    recipient = {
        "name": transfer.recipient_name,
        "account": transfer.recipient_account,
        "bank_code": transfer.recipient_bank,
    }
    tried = {a.provider_id for a in attempts}
    remaining = [item for item in scored if item.provider.provider_id not in tried]
    machine = TransferStateMachine(session, transfer)
    for offset, scored_item in enumerate(remaining):
        await session.refresh(transfer)
        if transfer.status in TERMINAL:
            return transfer.status
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
        decision = await create_with_recovery(
            client,
            provider.provider_id,
            reference,
            target_str,
            quote.target_currency,
            recipient,
            callback_url.format(provider_id=provider.provider_id),
            provider_key(reference, provider.provider_id),
        )
        if decision.action is AttemptAction.ADOPT_LIVE:
            await transfer_repo.update_attempt(
                session, pending_attempt, "PROCESSING", None, decision.payout_id, now
            )
            await machine.start(now_utc(), actor)
            await session.commit()
            settlement = await wait_for_settlement(
                session,
                settings,
                client,
                transfer.transfer_id,
                provider.provider_id,
                decision.payout_id or "",
                reference,
                now,
                actor=actor,
            )
            if settlement == "RETRY":
                continue
            return settlement
        if decision.action is AttemptAction.FAIL_AND_NEXT:
            await transfer_repo.update_attempt(
                session,
                pending_attempt,
                "FAILED",
                decision.failure_code,
                decision.payout_id,
                now,
            )
            await session.commit()
            continue
        await transfer_repo.update_attempt(
            session,
            pending_attempt,
            "PROCESSING",
            "NEEDS_REVIEW",
            None,
            now,
        )
        await machine.needs_review(now, actor)
        await session.commit()
        return "NEEDS_REVIEW"
    await TransferStateMachine(session, transfer).fail(now, actor)
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
    poll_interval: timedelta = timedelta(milliseconds=500),
    actor: str = "service",
) -> str:
    deadline: datetime | None = None
    now = started
    while True:
        await asyncio.sleep(poll_interval.total_seconds())
        now = now_utc()
        if deadline is None:
            deadline = now + timedelta(seconds=settings.api_sync_timeout_sec)
        if now >= deadline:
            break
        state = await confirm_payout(client, provider_id, payout_id, reference)
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
        action = await TransferStateMachine(session, transfer).settle_from_payout(
            state.status, now, actor
        )
        await session.commit()
        if action != "PROCESSING":
            return action
    transfer = await transfer_repo.get_transfer(session, transfer_id, lock=True)
    if transfer is None or transfer.status in TERMINAL:
        return transfer.status if transfer else "FAILED"
    started_processing = await TransferStateMachine(session, transfer).start(now_utc(), actor)
    await session.commit()
    if started_processing:
        return "PROCESSING"
    await session.refresh(transfer)
    return transfer.status
