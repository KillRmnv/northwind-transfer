import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IdempotencyConflictError
from app.core.parse import try_parse_uuid
from app.models.tables import Client, Quote, Transfer, TransferStatusHistory
from app.repositories.balances import for_update


async def get_transfer(
    session: AsyncSession, transfer_id: uuid.UUID, lock: bool = False
) -> Transfer | None:
    """Перевод по id; lock держит строку на время решения."""
    query = sa.select(Transfer).where(Transfer.transfer_id == transfer_id)
    if lock:
        query = for_update(query, session)
    return (await session.execute(query)).scalar_one_or_none()


async def lock_client(session: AsyncSession, client_id: str):
    """Клиент под FOR UPDATE: сериализация конкурентных заявок."""
    query = sa.select(Client).where(Client.client_id == client_id)
    return (await session.execute(for_update(query, session))).scalar_one_or_none()


async def record_transition(
    session: AsyncSession,
    transfer_id,
    from_status: str | None,
    to_status: str,
    actor: str,
    now: datetime,
) -> None:
    """Строка истории: кто, когда, откуда и куда."""
    session.add(
        TransferStatusHistory(
            transfer_id=transfer_id,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            at=now,
        )
    )
    await session.flush()


async def history_for_transfer(session: AsyncSession, transfer_id) -> list:
    """История переводов по времени для ответа API."""
    query = (
        sa.select(TransferStatusHistory)
        .where(TransferStatusHistory.transfer_id == transfer_id)
        .order_by(TransferStatusHistory.at, TransferStatusHistory.id)
    )
    return list((await session.execute(query)).scalars().all())


async def get_transfer_by_reference(session: AsyncSession, reference: str) -> Transfer | None:
    """Перевод по reference мока; мусор мимо."""
    transfer_id = try_parse_uuid(reference)
    if transfer_id is None:
        return None
    return await get_transfer(session, transfer_id)


async def get_by_idempotency_key(
    session: AsyncSession, key: str, client_id: str | None = None
) -> Transfer | None:
    """Поиск дубля по ключу в пределах клиента."""
    query = sa.select(Transfer).where(Transfer.idempotency_key == key)
    if client_id is not None:
        query = query.where(Transfer.client_id == client_id)
    return (await session.execute(query)).scalar_one_or_none()


async def create_transfer_or_existing(
    session: AsyncSession,
    quote: Quote,
    client_id: str,
    recipient: dict,
    idempotency_key: str,
    idempotency_hash: str,
    now: datetime,
) -> tuple[Transfer, bool]:
    """Вставка перевода или победа чужой транзакции: уникальный ключ решает гонку."""
    try:
        async with session.begin_nested():
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
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.flush()
        return row, True
    except IntegrityError as exc:
        winner = await get_by_idempotency_key(session, idempotency_key, client_id)
        if winner is None:
            raise
        if winner.idempotency_hash != idempotency_hash:
            raise IdempotencyConflictError(idempotency_key) from exc
        return winner, False


async def force_status(session: AsyncSession, transfer_id, status: str, now: datetime) -> bool:
    """Принудительный статус мимо обычных правил: только починка."""
    result = await session.execute(
        sa.update(Transfer)
        .where(
            Transfer.transfer_id == transfer_id,
            Transfer.status.not_in(["COMPLETED", "FAILED"]),
        )
        .values(status=status, updated_at=now)
    )
    return result.rowcount == 1


async def set_status_if_unsettled(
    session: AsyncSession, transfer_id, status: str, now: datetime
) -> bool:
    """Смена статуса, если деньги ещё не осели."""
    result = await session.execute(
        sa.update(Transfer)
        .where(
            Transfer.transfer_id == transfer_id,
            Transfer.funds_settled.is_(False),
            Transfer.status.not_in(["COMPLETED", "FAILED"]),
        )
        .values(status=status, updated_at=now)
    )
    return result.rowcount == 1


async def list_open_transfers(
    session: AsyncSession, limit: int = 100, skip_locked: bool = False
) -> list[Transfer]:
    """Открытые переводы для сверки, старейшие вперёд."""
    query = (
        sa.select(Transfer)
        .where(Transfer.status.in_(["PENDING", "PROCESSING", "NEEDS_REVIEW"]))
        .order_by(Transfer.updated_at)
        .limit(limit)
    )
    bind = session.bind
    if skip_locked and bind is not None and bind.dialect.name != "sqlite":
        query = query.with_for_update(skip_locked=True)
    return list((await session.execute(query)).scalars().all())
