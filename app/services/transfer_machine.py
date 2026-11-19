from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Transfer
from app.repositories import balances as balance_repo
from app.repositories import transfers as transfer_repo

TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})

EDGES: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"PROCESSING", "COMPLETED", "FAILED", "CANCELLED"}),
    "PROCESSING": frozenset({"COMPLETED", "FAILED", "CANCELLED"}),
    "COMPLETED": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
}

PAYOUT_SETTLEMENT = {"COMPLETED": "complete", "FAILED": "retry"}


class TransferStateMachine:
    def __init__(self, session: AsyncSession, transfer: Transfer) -> None:
        self._session = session
        self._transfer = transfer

    @property
    def status(self) -> str:
        return self._transfer.status

    def can(self, target: str) -> bool:
        return target in EDGES.get(self.status, frozenset())

    async def start(self, now: datetime) -> bool:
        if not self.can("PROCESSING"):
            return False
        moved = await transfer_repo.set_status_if_unsettled(
            self._session, self._transfer.transfer_id, "PROCESSING", now
        )
        if moved:
            await self._session.refresh(self._transfer)
        return moved

    async def complete(self, now: datetime) -> bool:
        if not self.can("COMPLETED"):
            return False
        settled = await self._settle("COMPLETED", now)
        if not settled:
            return False
        await balance_repo.consume_reserve(
            self._session,
            self._transfer.client_id,
            self._transfer.source_currency,
            Decimal(self._transfer.source_amount),
        )
        await self._sync_attempt("COMPLETED", now)
        await self._session.refresh(self._transfer)
        return True

    async def fail(self, now: datetime) -> bool:
        if not self.can("FAILED"):
            return False
        settled = await self._settle("FAILED", now)
        if not settled:
            return False
        await balance_repo.release_reserve(
            self._session,
            self._transfer.client_id,
            self._transfer.source_currency,
            Decimal(self._transfer.source_amount),
        )
        await self._sync_attempt("FAILED", now)
        await self._session.refresh(self._transfer)
        return True

    async def settle_from_payout(self, payout_status: str, now: datetime) -> str:
        action = PAYOUT_SETTLEMENT.get(payout_status, "wait")
        if action == "complete":
            await self.complete(now)
            return "COMPLETED"
        if action == "retry":
            return "RETRY"
        return "PROCESSING"

    async def repair(self, target: str, now: datetime) -> bool:
        if self.status in TERMINAL:
            return False
        return await transfer_repo.force_status(
            self._session, self._transfer.transfer_id, target, now
        )

    async def _settle(self, target: str, now: datetime) -> bool:
        result = await self._session.execute(
            sa.update(Transfer)
            .where(
                Transfer.transfer_id == self._transfer.transfer_id,
                Transfer.funds_settled.is_(False),
                Transfer.status.not_in(TERMINAL),
            )
            .values(status=target, funds_settled=True, updated_at=now)
        )
        return result.rowcount == 1

    async def _sync_attempt(self, status: str, now: datetime) -> None:
        attempts = await transfer_repo.latest_attempts(self._session, self._transfer.transfer_id)
        if not attempts:
            return
        latest = attempts[-1]
        if latest.status != status:
            await transfer_repo.update_attempt(
                self._session, latest, status, latest.failure_code, latest.payout_id, now
            )
