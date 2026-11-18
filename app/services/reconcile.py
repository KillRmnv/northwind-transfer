from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now_utc
from app.core.settings import Settings
from app.repositories import transfers as transfer_repo
from app.services import finalize as finalize_mod
from app.services.providers import ProviderClient
from app.services.transfers import TERMINAL, run_attempts


async def reconcile_once(session: AsyncSession, settings: Settings, now: datetime) -> dict:
    stats = {"checked": 0, "completed": 0, "failed": 0, "still_open": 0}
    transfers = await transfer_repo.list_open_transfers(session, limit=100)
    client = ProviderClient(settings.mock_provider_url, settings.mock_provider_timeout_sec)
    for transfer in transfers:
        stats["checked"] += 1
        if transfer.status == "PENDING":
            status = await run_attempts(session, settings, transfer.transfer_id, now_utc())
            stats["completed" if status == "COMPLETED" else "still_open"] += 1
            continue
        attempts = await transfer_repo.latest_attempts(session, transfer.transfer_id)
        live = [a for a in attempts if a.payout_id]
        if not live:
            status = await run_attempts(session, settings, transfer.transfer_id, now_utc())
            stats["completed" if status == "COMPLETED" else "still_open"] += 1
            continue
        attempt = live[-1]
        state = await client.get_payout(attempt.provider_id, attempt.payout_id or "")
        if state is None and attempt.payout_id:
            state = await client.find_payout(attempt.provider_id, transfer.transfer_id.hex)
        if state is None:
            stats["still_open"] += 1
            continue
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
        elif state.status == "FAILED":
            status = await run_attempts(session, settings, transfer.transfer_id, now_utc())
            stats["failed" if status == "FAILED" else "still_open"] += 1
        else:
            stats["still_open"] += 1
    return stats
