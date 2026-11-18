from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings
from app.models.tables import Transfer
from app.repositories import balances as balance_repo

TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


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


async def complete_transfer(
    session: AsyncSession, settings: Settings, transfer, now: datetime
) -> bool:
    if not await _claim(session, transfer.transfer_id, "COMPLETED", now):
        return False
    await balance_repo.consume_reserve(
        session, transfer.client_id, transfer.source_currency, Decimal(transfer.source_amount)
    )
    await session.refresh(transfer)
    return True


async def fail_transfer(session: AsyncSession, settings: Settings, transfer, now: datetime) -> bool:
    if not await _claim(session, transfer.transfer_id, "FAILED", now):
        return False
    await balance_repo.release_reserve(
        session, transfer.client_id, transfer.source_currency, Decimal(transfer.source_amount)
    )
    await session.refresh(transfer)
    return True
