from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import PayoutAttempt


async def record_attempt(
    session: AsyncSession,
    transfer_id,
    provider_id: str,
    reference: str,
    amount: Decimal,
    currency: str,
    status: str,
    failure_code: str | None,
    attempt_no: int,
    payout_id: str | None,
    now: datetime,
) -> PayoutAttempt:
    """Попытка выплаты с номером; гонка номеров разруливается."""
    try:
        async with session.begin_nested():
            row = PayoutAttempt(
                transfer_id=transfer_id,
                provider_id=provider_id,
                payout_id=payout_id,
                reference=reference,
                amount=amount,
                currency=currency,
                status=status,
                failure_code=failure_code,
                attempt_no=attempt_no,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.flush()
        return row
    except IntegrityError:
        existing = await latest_attempts(session, transfer_id)
        taken = {attempt.attempt_no for attempt in existing}
        retry_no = attempt_no
        while retry_no in taken:
            retry_no += 1
        row = PayoutAttempt(
            transfer_id=transfer_id,
            provider_id=provider_id,
            payout_id=payout_id,
            reference=reference,
            amount=amount,
            currency=currency,
            status=status,
            failure_code=failure_code,
            attempt_no=retry_no,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        return row


async def update_attempt(
    session: AsyncSession,
    attempt: PayoutAttempt,
    status: str,
    failure_code: str | None,
    payout_id: str | None,
    now: datetime,
) -> None:
    """Новый статус попытки от провайдера."""
    attempt.status = status
    attempt.failure_code = failure_code
    if payout_id is not None:
        attempt.payout_id = payout_id
    attempt.updated_at = now
    await session.flush()


async def latest_attempts(session: AsyncSession, transfer_id) -> list[PayoutAttempt]:
    """Попытки перевода по порядку номеров."""
    query = (
        sa.select(PayoutAttempt)
        .where(PayoutAttempt.transfer_id == transfer_id)
        .order_by(PayoutAttempt.attempt_no)
    )
    return list((await session.execute(query)).scalars().all())


async def find_attempt_by_payout(
    session: AsyncSession, provider_id: str, payout_id: str
) -> PayoutAttempt | None:
    """Попытка по выплате провайдера для вебхука."""
    query = sa.select(PayoutAttempt).where(
        PayoutAttempt.provider_id == provider_id,
        PayoutAttempt.payout_id == payout_id,
    )
    return (await session.execute(query)).scalars().first()
