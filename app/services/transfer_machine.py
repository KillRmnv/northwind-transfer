from datetime import datetime
from decimal import Decimal
from functools import partial

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Transfer
from app.repositories import balances as balance_repo
from app.repositories import transfers as transfer_repo

TERMINAL = frozenset({"COMPLETED", "FAILED"})

NEEDS_REVIEW = "NEEDS_REVIEW"

EDGES: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"PROCESSING", "COMPLETED", "FAILED", "NEEDS_REVIEW"}),
    "PROCESSING": frozenset({"COMPLETED", "FAILED", "NEEDS_REVIEW"}),
    "NEEDS_REVIEW": frozenset({"PROCESSING", "COMPLETED", "FAILED"}),
    "COMPLETED": frozenset(),
    "FAILED": frozenset(),
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

    async def _previous_locked(self) -> str:
        locked = await transfer_repo.get_transfer(
            self._session, self._transfer.transfer_id, lock=True
        )
        if locked is not None:
            await self._session.refresh(locked)
            return locked.status
        return self._transfer.status

    async def _move_unsettled(self, target: str, now: datetime, actor: str) -> bool:
        if not self.can(target):
            return False
        previous = await self._previous_locked()
        moved = await transfer_repo.set_status_if_unsettled(
            self._session, self._transfer.transfer_id, target, now
        )
        if moved:
            await transfer_repo.record_transition(
                self._session, self._transfer.transfer_id, previous, target, actor, now
            )
            await self._session.refresh(self._transfer)
        return moved

    async def _settle_with_effect(self, target, now, actor, make_effect) -> bool:
        if not self.can(target):
            return False
        settled = await self._settle(target, now, actor)
        if not settled:
            return False
        await make_effect()()
        await self._sync_attempt(target, now)
        await self._session.refresh(self._transfer)
        return True

    def _release_effect(self):
        return partial(
            balance_repo.release_reserve,
            self._session,
            self._transfer.client_id,
            self._transfer.source_currency,
            Decimal(self._transfer.source_amount),
        )

    def _consume_effect(self):
        return partial(
            balance_repo.consume_reserve,
            self._session,
            self._transfer.client_id,
            self._transfer.source_currency,
            Decimal(self._transfer.source_amount),
        )

    async def start(self, now: datetime, actor: str = "service") -> bool:
        return await self._move_unsettled("PROCESSING", now, actor)

    async def complete(self, now: datetime, actor: str = "service") -> bool:
        return await self._settle_with_effect("COMPLETED", now, actor, self._consume_effect)

    async def fail(self, now: datetime, actor: str = "service") -> bool:
        return await self._settle_with_effect("FAILED", now, actor, self._release_effect)

    async def needs_review(self, now: datetime, actor: str = "service") -> bool:
        return await self._move_unsettled(NEEDS_REVIEW, now, actor)

    async def settle_from_payout(
        self, payout_status: str, now: datetime, actor: str = "service"
    ) -> str:
        if self._transfer.status in TERMINAL:
            return self._transfer.status
        action = PAYOUT_SETTLEMENT.get(payout_status, "wait")
        if action == "complete":
            await self.complete(now, actor)
        elif action == "retry":
            return "RETRY"
        await self._session.refresh(self._transfer)
        if self._transfer.status in TERMINAL:
            return self._transfer.status
        return "PROCESSING"

    async def repair(self, target: str, now: datetime, actor: str = "service") -> bool:
        await self._session.refresh(self._transfer)
        if self.status in TERMINAL:
            return False
        if not self._transfer.funds_settled:
            if target == "COMPLETED":
                return await self.complete(now, actor)
            if target == "FAILED":
                return await self.fail(now, actor)
            return False
        previous = await self._previous_locked()
        moved = await transfer_repo.force_status(
            self._session, self._transfer.transfer_id, target, now
        )
        if moved:
            await transfer_repo.record_transition(
                self._session, self._transfer.transfer_id, previous, target, actor, now
            )
            await self._session.refresh(self._transfer)
        return moved

    async def _settle(self, target: str, now: datetime, actor: str) -> bool:
        previous = await self._previous_locked()
        result = await self._session.execute(
            sa.update(Transfer)
            .where(
                Transfer.transfer_id == self._transfer.transfer_id,
                Transfer.funds_settled.is_(False),
                Transfer.status.not_in(TERMINAL),
            )
            .values(status=target, funds_settled=True, updated_at=now)
        )
        if result.rowcount != 1:
            return False
        await transfer_repo.record_transition(
            self._session, self._transfer.transfer_id, previous, target, actor, now
        )
        return True

    async def _sync_attempt(self, status: str, now: datetime) -> None:
        attempts = await transfer_repo.latest_attempts(self._session, self._transfer.transfer_id)
        if not attempts:
            return
        latest = attempts[-1]
        if latest.status != status:
            await transfer_repo.update_attempt(
                self._session, latest, status, latest.failure_code, latest.payout_id, now
            )
