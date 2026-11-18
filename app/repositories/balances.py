from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Balance


def for_update(query, session: AsyncSession):
    if session.bind and session.bind.dialect.name == "sqlite":
        return query
    return query.with_for_update()


async def get_balances(
    session: AsyncSession, client_id: str, lock: bool = False
) -> dict[str, Balance]:
    query = sa.select(Balance).where(Balance.client_id == client_id)
    if lock:
        query = for_update(query, session)
    rows = (await session.execute(query)).scalars().all()
    return {row.currency: row for row in rows}


async def get_balance(
    session: AsyncSession, client_id: str, currency: str, lock: bool = False
) -> Balance | None:
    query = sa.select(Balance).where(Balance.client_id == client_id, Balance.currency == currency)
    if lock:
        query = for_update(query, session)
    return (await session.execute(query)).scalar_one_or_none()


async def ensure_balance(session: AsyncSession, client_id: str, currency: str) -> Balance:
    row = await get_balance(session, client_id, currency, lock=True)
    if row is None:
        row = Balance(
            client_id=client_id, currency=currency, available=Decimal("0"), reserved=Decimal("0")
        )
        session.add(row)
        await session.flush()
    return row


async def reserve(session: AsyncSession, client_id: str, currency: str, amount: Decimal) -> None:
    row = await ensure_balance(session, client_id, currency)
    row.available = Decimal(row.available) - amount
    row.reserved = Decimal(row.reserved) + amount
    await session.flush()


async def consume_reserve(
    session: AsyncSession, client_id: str, currency: str, amount: Decimal
) -> None:
    row = await ensure_balance(session, client_id, currency)
    row.reserved = Decimal(row.reserved) - amount
    await session.flush()


async def release_reserve(
    session: AsyncSession, client_id: str, currency: str, amount: Decimal
) -> None:
    row = await ensure_balance(session, client_id, currency)
    row.reserved = Decimal(row.reserved) - amount
    row.available = Decimal(row.available) + amount
    await session.flush()
