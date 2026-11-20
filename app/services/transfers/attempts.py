import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import as_utc, now_utc
from app.core.locks import transfer_lock
from app.core.money import format_money
from app.core.settings import Settings
from app.core.types import AttemptAction, Money
from app.repositories import attempts as attempt_repo
from app.repositories.references import load_tariffs
from app.services.providers import ScoredProvider
from app.services.quotes import find_tariff
from app.services.transfer_machine import TERMINAL, TransferStateMachine
from app.services.transfers import recovery
from app.services.transfers.context import TransferContext, load_context
from app.services.transfers.creation import (
    _fee_in_source,
    select_providers,
    simple_rates,
)
from app.services.transfers.waiting import wait_for_settlement


async def _plan_attempts(
    ctx: TransferContext, attempts: list
) -> tuple[list, int, str, dict, str] | None:
    """Всё для цикла: отранжированные провайдеры, нумерация, шаблоны. None — некого пробовать."""
    quote = ctx.quote
    now = ctx.now
    tariffs = await load_tariffs(ctx.session)
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
        ctx.session,
        tariff.direction_id,
        client_fee,
        source,
        target,
        tariff.min_margin_percent,
        simple_rates(ctx.rates, now),
        now,
    )
    if not scored:
        return None
    start_no = len(attempts) + 1
    callback_url = f"{ctx.settings.public_base_url}/webhooks/{{provider_id}}"
    target_str = format_money(Decimal(quote.target_amount), ctx.minor_units[quote.target_currency])
    recipient = {
        "name": ctx.transfer.recipient_name,
        "account": ctx.transfer.recipient_account,
        "bank_code": ctx.transfer.recipient_bank,
    }
    tried = {a.provider_id for a in attempts}
    remaining = [item for item in scored if item.provider.provider_id not in tried]
    return remaining, start_no, target_str, recipient, callback_url


async def _attempt_with_provider(
    ctx: TransferContext,
    machine: TransferStateMachine,
    scored_item: ScoredProvider,
    attempt_no: int,
    target_str: str,
    recipient: dict,
    callback_url: str,
    actor: str,
) -> str | None:
    """Одна попытка у провайдера: итог перевода или None для следующего."""
    session = ctx.session
    transfer = ctx.transfer
    quote = ctx.quote
    now = ctx.now
    await session.refresh(transfer)
    if transfer.status in TERMINAL:
        return transfer.status
    provider = scored_item.provider
    pending_attempt = await attempt_repo.record_attempt(
        session,
        transfer.transfer_id,
        provider.provider_id,
        ctx.reference,
        Decimal(quote.target_amount),
        quote.target_currency,
        "PROCESSING",
        None,
        attempt_no,
        None,
        now,
    )
    await session.commit()
    decision = await recovery.create_with_recovery(
        ctx.client,
        ctx.settings,
        provider.provider_id,
        ctx.reference,
        target_str,
        quote.target_currency,
        recipient,
        callback_url.format(provider_id=provider.provider_id),
        recovery.provider_key(ctx.reference, provider.provider_id),
    )
    if decision.action is AttemptAction.ADOPT_LIVE:
        await attempt_repo.update_attempt(
            session, pending_attempt, "PROCESSING", None, decision.payout_id, now
        )
        await machine.start(now_utc(), actor)
        await session.commit()
        settlement = await wait_for_settlement(
            ctx,
            provider.provider_id,
            decision.payout_id or "",
            actor=actor,
        )
        return None if settlement == "RETRY" else settlement
    if decision.action is AttemptAction.FAIL_AND_NEXT:
        await attempt_repo.update_attempt(
            session,
            pending_attempt,
            "FAILED",
            decision.failure_code,
            decision.payout_id,
            now,
        )
        await session.commit()
        return None
    await attempt_repo.update_attempt(
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


async def run_attempts_locked(
    session: AsyncSession,
    settings: Settings,
    transfer_id: uuid.UUID,
    now: datetime,
    actor: str = "service",
) -> str:
    """Перебор провайдеров под замком перевода: один процесс доводит перевод."""
    async with transfer_lock(f"tx:{transfer_id}"):
        return await run_attempts(session, settings, transfer_id, now, actor)


async def run_attempts(
    session: AsyncSession,
    settings: Settings,
    transfer_id: uuid.UUID,
    now: datetime,
    actor: str = "service",
) -> str:
    """Исполнение перевода: пробы, живые выплаты, перебор провайдеров до терминала."""
    loaded = await load_context(session, settings, transfer_id, now)
    if isinstance(loaded, str):
        return loaded
    ctx = loaded
    transfer = ctx.transfer
    quote = ctx.quote
    if as_utc(quote.expires_at) <= as_utc(now):
        return await recovery._settle_expired(ctx, actor)
    attempts = await attempt_repo.latest_attempts(session, transfer.transfer_id)
    attempts = await recovery._probe_pending(ctx, attempts)
    if recovery._young_unconfirmed(attempts):
        await TransferStateMachine(session, transfer).needs_review(now, actor)
        await session.commit()
        return "NEEDS_REVIEW"
    live = [a for a in attempts if a.payout_id and a.status in ("PROCESSING", "COMPLETED")]
    if live:
        last = live[-1]
        settlement = await wait_for_settlement(ctx, last.provider_id, last.payout_id or "")
        if settlement != "RETRY":
            return settlement
        attempts = await attempt_repo.latest_attempts(session, transfer.transfer_id)
    if as_utc(quote.expires_at) <= as_utc(now):
        return await recovery._settle_expired(ctx, actor)
    planned = await _plan_attempts(ctx, attempts)
    if planned is None:
        await TransferStateMachine(session, transfer).fail(now, actor)
        await session.commit()
        return "FAILED"
    remaining, start_no, target_str, recipient, callback_url = planned
    machine = TransferStateMachine(session, transfer)
    for offset, scored_item in enumerate(remaining):
        outcome = await _attempt_with_provider(
            ctx,
            machine,
            scored_item,
            start_no + offset,
            target_str,
            recipient,
            callback_url,
            actor,
        )
        if outcome is not None:
            return outcome
    await TransferStateMachine(session, transfer).fail(now, actor)
    await session.commit()
    return transfer.status
