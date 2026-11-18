from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now_utc
from app.core.settings import Settings
from app.repositories import transfers as transfer_repo
from app.services import finalize as finalize_mod
from app.services.providers import ProviderClient
from app.services.transfers import TERMINAL, run_attempts


def empty_stats() -> dict[str, int]:
    return {"checked": 0, "completed": 0, "failed": 0, "still_open": 0}


async def list_open_transfers(session: AsyncSession, limit: int = 200) -> list:
    return await transfer_repo.list_open_transfers(session, limit=limit)


async def reconcile_once(session: AsyncSession, settings: Settings, now: datetime) -> dict:
    stats = empty_stats()
    transfers = await list_open_transfers(session)
    client = ProviderClient(settings.mock_provider_url, settings.mock_provider_timeout_sec)
    for transfer in transfers:
        stats["checked"] += 1
        await _reconcile_one(session, settings, client, transfer, now, stats)
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
    return stats


async def _reconcile_one(
    session: AsyncSession,
    settings: Settings,
    client: ProviderClient,
    transfer,
    now: datetime,
    stats: dict,
) -> None:
    if transfer.status == "PENDING":
        status = await run_attempts(session, settings, transfer.transfer_id, now_utc())
        _tally(stats, status)
        return
    attempts = await transfer_repo.latest_attempts(session, transfer.transfer_id)
    live = [a for a in attempts if a.payout_id and a.status in ("PROCESSING", "COMPLETED")]
    if not live:
        status = await run_attempts(session, settings, transfer.transfer_id, now_utc())
        _tally(stats, status)
        return
    attempt = live[-1]
    state = await client.get_payout(attempt.provider_id, attempt.payout_id or "")
    if state is None:
        state = await client.find_payout(attempt.provider_id, transfer.transfer_id.hex)
    if state is None:
        stats["still_open"] += 1
        return
    fresh = await transfer_repo.get_transfer(session, transfer.transfer_id)
    if fresh is not None and fresh.status in TERMINAL:
        stats["still_open"] += 1
        return
    if attempt.status != state.status or attempt.payout_id != state.payout_id:
        await transfer_repo.update_attempt(
            session, attempt, state.status, state.failure_code, state.payout_id, now_utc()
        )
        await session.commit()
    if state.status == "COMPLETED":
        locked = await transfer_repo.get_transfer(session, transfer.transfer_id, lock=True)
        if locked is not None and locked.status not in TERMINAL:
            await finalize_mod.complete_transfer(session, settings, locked, now_utc())
            await session.commit()
        stats["completed"] += 1
        return
    if state.status == "FAILED":
        status = await run_attempts(session, settings, transfer.transfer_id, now_utc())
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
