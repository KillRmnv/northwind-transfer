import asyncio
from datetime import datetime, timedelta

from app.core.clock import now_utc
from app.repositories import attempts as attempt_repo
from app.repositories import transfers as transfer_repo
from app.services.providers import confirm_payout
from app.services.transfer_machine import TERMINAL, TransferStateMachine
from app.services.transfers.context import TransferContext


async def wait_for_settlement(
    ctx: TransferContext,
    provider_id: str,
    payout_id: str,
    poll_interval: timedelta = timedelta(milliseconds=500),
    actor: str = "service",
) -> str:
    """Ожидание сеттлмента живой выплаты до синхронного дедлайна."""
    session = ctx.session
    settings = ctx.settings
    client = ctx.client
    transfer_id = ctx.transfer.transfer_id
    reference = ctx.reference
    deadline: datetime | None = None
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
        attempts = await attempt_repo.latest_attempts(session, transfer_id)
        current = attempts[-1] if attempts else None
        if current is not None and (
            current.status != state.status or current.payout_id != state.payout_id
        ):
            await attempt_repo.update_attempt(
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
