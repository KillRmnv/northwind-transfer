import hashlib
import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import PayoutAttempt, Quote, Transfer, WebhookEvent
from app.repositories.balances import for_update


def body_hash(body: dict) -> str:
    canonical = "|".join(f"{key}={body.get(key)}" for key in sorted(body))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def get_transfer(
    session: AsyncSession, transfer_id: uuid.UUID, lock: bool = False
) -> Transfer | None:
    query = sa.select(Transfer).where(Transfer.transfer_id == transfer_id)
    if lock:
        query = for_update(query, session)
    return (await session.execute(query)).scalar_one_or_none()


async def lock_client(session: AsyncSession, client_id: str):
    from app.models.tables import Client

    query = sa.select(Client).where(Client.client_id == client_id)
    if session.bind and session.bind.dialect.name != "sqlite":
        query = query.with_for_update()
    return (await session.execute(query)).scalar_one_or_none()


async def get_transfer_by_reference(session: AsyncSession, reference: str) -> Transfer | None:
    try:
        transfer_id = uuid.UUID(reference)
    except ValueError:
        return None
    return await get_transfer(session, transfer_id)


async def get_by_idempotency_key(session: AsyncSession, key: str) -> Transfer | None:
    query = sa.select(Transfer).where(Transfer.idempotency_key == key)
    return (await session.execute(query)).scalar_one_or_none()


async def create_transfer(
    session: AsyncSession,
    quote: Quote,
    client_id: str,
    recipient: dict,
    idempotency_key: str,
    idempotency_hash: str,
    now: datetime,
) -> Transfer:
    row = Transfer(
        transfer_id=uuid.uuid4(),
        quote_id=quote.quote_id,
        client_id=client_id,
        status="PENDING",
        source_amount=quote.source_amount,
        source_currency=quote.source_currency,
        target_amount=quote.target_amount,
        target_currency=quote.target_currency,
        recipient_name=recipient.get("name", ""),
        recipient_account=recipient["account"],
        recipient_bank=recipient.get("bank_code", ""),
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
        funds_settled=False,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def force_status(session: AsyncSession, transfer_id, status: str, now: datetime) -> bool:
    result = await session.execute(
        sa.update(Transfer)
        .where(
            Transfer.transfer_id == transfer_id,
            Transfer.status.not_in(["COMPLETED", "FAILED", "CANCELLED"]),
        )
        .values(status=status, updated_at=now)
    )
    return result.rowcount == 1


async def set_status_if_unsettled(
    session: AsyncSession, transfer_id, status: str, now: datetime
) -> bool:
    result = await session.execute(
        sa.update(Transfer)
        .where(
            Transfer.transfer_id == transfer_id,
            Transfer.funds_settled.is_(False),
            Transfer.status.not_in(["COMPLETED", "FAILED", "CANCELLED"]),
        )
        .values(status=status, updated_at=now)
    )
    return result.rowcount == 1


async def set_status(session: AsyncSession, transfer: Transfer, status: str, now: datetime) -> None:
    transfer.status = status
    transfer.updated_at = now
    await session.flush()


async def list_open_transfers(session: AsyncSession, limit: int = 100) -> list[Transfer]:
    query = (
        sa.select(Transfer)
        .where(Transfer.status.in_(["PENDING", "PROCESSING"]))
        .order_by(Transfer.created_at)
        .limit(limit)
    )
    return list((await session.execute(query)).scalars().all())


async def sum_day_source(
    session: AsyncSession,
    client_id: str,
    day_start: datetime,
    day_end: datetime,
) -> list[tuple[Decimal, str, Decimal, str, str]]:
    transfer_alias = Transfer.__table__
    quote_alias = Quote.__table__
    query = (
        sa.select(
            transfer_alias.c.source_amount,
            transfer_alias.c.source_currency,
            quote_alias.c.rate,
            quote_alias.c.source_currency,
            quote_alias.c.target_currency,
        )
        .join(quote_alias, quote_alias.c.quote_id == transfer_alias.c.quote_id)
        .where(
            transfer_alias.c.client_id == client_id,
            transfer_alias.c.created_at >= day_start,
            transfer_alias.c.created_at < day_end,
            transfer_alias.c.status.in_(["PENDING", "PROCESSING", "COMPLETED"]),
        )
    )
    rows = (await session.execute(query)).all()
    return [(Decimal(r[0]), r[1], Decimal(r[2]), r[3], r[4]) for r in rows]


async def sum_window_source(
    session: AsyncSession,
    client_id: str,
    direction_id: str | None,
    window_start: datetime,
) -> list[tuple[Decimal, str, Decimal, str]]:
    transfer_alias = Transfer.__table__
    quote_alias = Quote.__table__
    query = (
        sa.select(
            transfer_alias.c.source_amount,
            quote_alias.c.source_currency,
            quote_alias.c.rate,
            quote_alias.c.target_currency,
        )
        .join(quote_alias, quote_alias.c.quote_id == transfer_alias.c.quote_id)
        .where(
            transfer_alias.c.client_id == client_id,
            transfer_alias.c.created_at >= window_start,
            transfer_alias.c.status.in_(["PENDING", "PROCESSING", "COMPLETED"]),
        )
    )
    if direction_id is not None:
        query = query.where(quote_alias.c.direction_id == direction_id)
    rows = (await session.execute(query)).all()
    return [(Decimal(r[0]), r[1], Decimal(r[2]), r[3]) for r in rows]


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


async def update_attempt(
    session: AsyncSession,
    attempt: PayoutAttempt,
    status: str,
    failure_code: str | None,
    payout_id: str | None,
    now: datetime,
) -> None:
    attempt.status = status
    attempt.failure_code = failure_code
    if payout_id is not None:
        attempt.payout_id = payout_id
    attempt.updated_at = now
    await session.flush()


async def latest_attempts(session: AsyncSession, transfer_id) -> list[PayoutAttempt]:
    query = (
        sa.select(PayoutAttempt)
        .where(PayoutAttempt.transfer_id == transfer_id)
        .order_by(PayoutAttempt.attempt_no)
    )
    return list((await session.execute(query)).scalars().all())


async def find_attempt_by_payout(
    session: AsyncSession, provider_id: str, payout_id: str
) -> PayoutAttempt | None:
    query = sa.select(PayoutAttempt).where(
        PayoutAttempt.provider_id == provider_id,
        PayoutAttempt.payout_id == payout_id,
    )
    return (await session.execute(query)).scalars().first()


async def store_webhook_event(
    session: AsyncSession,
    event_id: str,
    provider_id: str,
    payout_id: str | None,
    reference: str | None,
    status: str,
    occurred_at: datetime,
    now: datetime,
) -> bool:
    existing = await session.get(WebhookEvent, event_id)
    if existing is not None:
        return False
    session.add(
        WebhookEvent(
            event_id=event_id,
            provider_id=provider_id,
            payout_id=payout_id,
            reference=reference,
            status=status,
            occurred_at=occurred_at,
            received_at=now,
        )
    )
    await session.flush()
    return True


async def webhook_events_for_transfer(session: AsyncSession, reference: str) -> list[WebhookEvent]:
    query = (
        sa.select(WebhookEvent)
        .where(WebhookEvent.reference == reference)
        .order_by(WebhookEvent.occurred_at)
    )
    return list((await session.execute(query)).scalars().all())
