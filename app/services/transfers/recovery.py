import asyncio
import math
import uuid
from dataclasses import dataclass
from datetime import timedelta

from app.core.clock import as_utc, now_utc
from app.core.settings import Settings
from app.core.types import (
    Accepted,
    AttemptAction,
    AttemptDecision,
    Declined,
    PayoutState,
    ProbeOutcome,
    UnknownResult,
)
from app.repositories import attempts as attempt_repo
from app.services.providers import ProviderClient
from app.services.transfer_machine import TransferStateMachine
from app.services.transfers.context import TransferContext
from app.services.transfers.waiting import wait_for_settlement

UNRESOLVED_RETRY_AFTER_SEC = 60


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int
    delay: timedelta


SHORT_PROBE = RetryPolicy(attempts=1, delay=timedelta(seconds=1))


def final_probe_policy(settings: Settings) -> RetryPolicy:
    """Окно финальных проб из настроек: grace обязана превышать лаг видимости провайдера."""
    grace = settings.provider_visibility_grace_sec
    delay = settings.provider_probe_delay_sec
    attempts = max(1, math.ceil(grace / delay))
    return RetryPolicy(attempts=attempts, delay=timedelta(seconds=delay))


async def probe_with_retries(
    client: ProviderClient,
    provider_id: str,
    reference: str,
    policy: RetryPolicy,
) -> tuple[str, "PayoutState | None"]:
    """Опрашивает выплату до первого итога: найдена, точно нет или провайдер нечитаем."""
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
    """Детерминированный reference перевода: hex его id."""
    return transfer_id.hex


def provider_key(reference: str, provider_id: str) -> str:
    """Ключ идемпотентности у провайдера: пара reference и провайдер."""
    return f"{reference}:{provider_id}"


async def create_with_recovery(
    client: ProviderClient,
    settings: Settings,
    provider_id: str,
    reference: str,
    amount: str,
    currency: str,
    recipient: dict,
    callback_url: str,
    idempotency_key: str,
) -> AttemptDecision:
    """Выплата с восстановлением: повтор тем же ключом и опросы вместо слепого ретрая."""
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
    outcome, found = await probe_with_retries(
        client, provider_id, reference, final_probe_policy(settings)
    )
    if found is not None:
        return _adopt_or_fail(found)
    if outcome is ProbeOutcome.ERROR:
        return AttemptDecision(AttemptAction.PARK_FOR_REVIEW)
    return AttemptDecision(AttemptAction.FAIL_AND_NEXT, failure_code="PROVIDER_UNREACHABLE")


def _adopt_or_fail(found: PayoutState) -> AttemptDecision:
    """Найденная опросом выплата: привязать живую, следующей — закрыть мёртвую."""
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


async def _probe_pending(ctx: TransferContext, attempts: list) -> list:
    """Опрос неподтверждённых попыток: привязать найденные, протухшие закрыть."""
    session = ctx.session
    for pending in [a for a in attempts if a.status == "PROCESSING" and not a.payout_id]:
        outcome, state = await ctx.client.probe_payout(pending.provider_id, ctx.reference)
        probed_at = now_utc()
        if outcome is ProbeOutcome.FOUND and state is not None:
            await attempt_repo.update_attempt(
                session, pending, state.status, state.failure_code, state.payout_id, probed_at
            )
            await session.commit()
        elif (
            outcome is ProbeOutcome.MISSING
            and (probed_at - as_utc(pending.created_at)).total_seconds()
            >= UNRESOLVED_RETRY_AFTER_SEC
        ):
            await attempt_repo.update_attempt(
                session, pending, "FAILED", "PROVIDER_UNREACHABLE", None, probed_at
            )
            await session.commit()
    return await attempt_repo.latest_attempts(session, ctx.transfer.transfer_id)


def _young_unconfirmed(attempts: list) -> list:
    """Молодые неподтверждённые попытки: перебор запрещён, ждать проб."""
    moment = now_utc()
    return [
        a
        for a in attempts
        if a.status == "PROCESSING"
        and not a.payout_id
        and (moment - as_utc(a.created_at)).total_seconds() < UNRESOLVED_RETRY_AFTER_SEC
    ]


async def _settle_expired(ctx: TransferContext, actor: str) -> str:
    """Истёкшая котировка: сначала пробы, терминал только по подтверждённому итогу."""
    session = ctx.session
    transfer = ctx.transfer
    now = ctx.now
    attempts = await attempt_repo.latest_attempts(session, transfer.transfer_id)
    attempts = await _probe_pending(ctx, attempts)
    live = [a for a in attempts if a.payout_id and a.status in ("PROCESSING", "COMPLETED")]
    if live:
        last = live[-1]
        settlement = await wait_for_settlement(ctx, last.provider_id, last.payout_id or "")
        if settlement != "RETRY":
            return settlement
    if _young_unconfirmed(attempts):
        await TransferStateMachine(session, transfer).needs_review(now, actor)
        await session.commit()
        return "NEEDS_REVIEW"
    await TransferStateMachine(session, transfer).fail(now, actor)
    await session.commit()
    return "FAILED"
