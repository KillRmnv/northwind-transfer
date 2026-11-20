from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now_utc
from app.core.settings import Settings
from app.models.tables import Transfer
from app.repositories import transfers as transfer_repo
from app.services.providers import ProviderClient, confirm_payout
from app.services.transfer_machine import TERMINAL, TransferStateMachine
from app.services.transfers import run_attempts_locked as run_attempts

RECONCILE_ADVISORY_KEY = 410729831


async def try_reconcile_lock(session: AsyncSession) -> bool:
    bind = session.bind
    if bind is None or bind.dialect.name != "postgresql":
        return True
    await session.connection()
    row = await session.execute(sa.text(f"SELECT pg_try_advisory_lock({RECONCILE_ADVISORY_KEY})"))
    return bool(row.scalar_one())


async def release_reconcile_lock(session: AsyncSession) -> None:
    bind = session.bind
    if bind is None or bind.dialect.name != "postgresql":
        return
    await session.execute(sa.text(f"SELECT pg_advisory_unlock({RECONCILE_ADVISORY_KEY})"))


def empty_stats() -> dict[str, int]:
    return {"checked": 0, "completed": 0, "failed": 0, "still_open": 0}


async def list_open_transfers(session: AsyncSession, limit: int = 200) -> list:
    return await transfer_repo.list_open_transfers(session, limit=limit, skip_locked=True)


async def reconcile_once(session: AsyncSession, settings: Settings, now: datetime) -> dict:
    stats = empty_stats()
    if not await try_reconcile_lock(session):
        return stats
    try:
        transfers = await list_open_transfers(session)
        client = ProviderClient(settings.mock_provider_url, settings.mock_provider_timeout_sec)
        for transfer in transfers:
            stats["checked"] += 1
            await _reconcile_one(session, settings, client, transfer, now, stats)
            await _touch(session, transfer.transfer_id, now_utc())
        await session.commit()
    finally:
        await release_reconcile_lock(session)
        await session.commit()
    return stats


async def reconcile_transfer(
    session: AsyncSession,
    settings: Settings,
    transfer,
    now: datetime,
    client: ProviderClient | None = None,
) -> dict:
    stats = empty_stats()
    stats["checked"] = 1
    client = client or ProviderClient(
        settings.mock_provider_url, settings.mock_provider_timeout_sec
    )
    await _reconcile_one(session, settings, client, transfer, now, stats)
    await _touch(session, transfer.transfer_id, now_utc())
    return stats


async def _sync_live_attempt(session: AsyncSession, attempt, state) -> None:
    if attempt.status != state.status or attempt.payout_id != state.payout_id:
        await transfer_repo.update_attempt(
            session, attempt, state.status, state.failure_code, state.payout_id, now_utc()
        )
        await session.commit()


async def _settle_completed(session: AsyncSession, transfer, stats: dict) -> None:
    locked = await transfer_repo.get_transfer(session, transfer.transfer_id, lock=True)
    if locked is None or locked.status in TERMINAL:
        await session.refresh(transfer)
        stats["completed" if transfer.status == "COMPLETED" else "still_open"] += 1
        return
    machine = TransferStateMachine(session, locked)
    done = await machine.complete(now_utc(), "reconcile")
    if not done:
        done = await machine.repair("COMPLETED", now_utc(), "reconcile")
    await session.commit()
    stats["completed" if done else "still_open"] += 1


async def _reconcile_one(
    session: AsyncSession,
    settings: Settings,
    client: ProviderClient,
    transfer,
    now: datetime,
    stats: dict,
) -> None:
    await session.refresh(transfer)
    attempts = await transfer_repo.latest_attempts(session, transfer.transfer_id)
    live = [a for a in attempts if a.payout_id and a.status in ("PROCESSING", "COMPLETED")]
    if transfer.status == "PENDING" or not live:
        status = await run_attempts(session, settings, transfer.transfer_id, now_utc(), "reconcile")
        _tally(stats, status)
        return
    attempt = live[-1]
    state = await confirm_payout(
        client, attempt.provider_id, attempt.payout_id or "", transfer.transfer_id.hex
    )
    if state is None:
        stats["still_open"] += 1
        return
    fresh = await transfer_repo.get_transfer(session, transfer.transfer_id)
    if fresh is None or fresh.status in TERMINAL:
        key = "completed" if fresh is not None and fresh.status == "COMPLETED" else "still_open"
        stats[key] += 1
        return
    await _sync_live_attempt(session, attempt, state)
    if state.status == "COMPLETED":
        await _settle_completed(session, transfer, stats)
        return
    if state.status == "FAILED":
        status = await run_attempts(session, settings, transfer.transfer_id, now_utc(), "reconcile")
        _tally(stats, status)
        return
    stats["still_open"] += 1


def _tally(stats: dict, status: str) -> None:
    if status == "COMPLETED":
        stats["completed"] += 1
    elif status == "FAILED":
        stats["failed"] += 1
    else:
        stats["still_open"] += 1


async def _touch(session: AsyncSession, transfer_id, now: datetime) -> None:
    await session.execute(
        sa.update(Transfer).where(Transfer.transfer_id == transfer_id).values(updated_at=now)
    )
