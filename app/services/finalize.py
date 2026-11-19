from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings
from app.models.tables import Transfer
from app.repositories import balances as balance_repo
from app.repositories import transfers as transfer_repo

TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


async def _sync_attempt(session: AsyncSession, transfer, status: str, now: datetime) -> None:
    attempts = await transfer_repo.latest_attempts(session, transfer.transfer_id)
    if not attempts:
        return
    latest = attempts[-1]
    if latest.status != status:
        await transfer_repo.update_attempt(
            session, latest, status, latest.failure_code, latest.payout_id, now
        )


async def _claim(session: AsyncSession, transfer_id, target: str, now: datetime) -> bool:
    result = await session.execute(
        sa.update(Transfer)
        .where(
            Transfer.transfer_id == transfer_id,
            Transfer.status.not_in(TERMINAL_STATUSES),
        )
        .values(status=target, updated_at=now)
    )
    return result.rowcount == 1


async def _claim_funds(session: AsyncSession, transfer_id) -> bool:
    result = await session.execute(
        sa.update(Transfer)
        .where(Transfer.transfer_id == transfer_id, Transfer.funds_settled.is_(False))
        .values(funds_settled=True)
    )
    return result.rowcount == 1


async def complete_transfer(
    session: AsyncSession, settings: Settings, transfer, now: datetime
) -> bool:
    if not await _claim_funds(session, transfer.transfer_id):
        return False
    await _claim(session, transfer.transfer_id, "COMPLETED", now)
    await balance_repo.consume_reserve(
        session, transfer.client_id, transfer.source_currency, Decimal(transfer.source_amount)
    )
    await _sync_attempt(session, transfer, "COMPLETED", now)
    await session.refresh(transfer)
    return True


async def fail_transfer(session: AsyncSession, settings: Settings, transfer, now: datetime) -> bool:
    if not await _claim(session, transfer.transfer_id, "FAILED", now):
        return False
    await balance_repo.release_reserve(
        session, transfer.client_id, transfer.source_currency, Decimal(transfer.source_amount)
    )
    await _sync_attempt(session, transfer, "FAILED", now)
    await session.refresh(transfer)
    return True
