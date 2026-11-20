from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Quote, Transfer


async def sum_day_source(
    session: AsyncSession,
    client_id: str,
    day_start: datetime,
    day_end: datetime,
) -> list[tuple[Decimal, str, Decimal, str, str]]:
    """Строки дневного окна для лимита."""
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
            transfer_alias.c.status.in_(["PENDING", "PROCESSING", "NEEDS_REVIEW", "COMPLETED"]),
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
    """Строки скользящего окна, опционально по направлению."""
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
            transfer_alias.c.status.in_(["PENDING", "PROCESSING", "NEEDS_REVIEW", "COMPLETED"]),
        )
    )
    if direction_id is not None:
        query = query.where(quote_alias.c.direction_id == direction_id)
    rows = (await session.execute(query)).all()
    return [(Decimal(r[0]), r[1], Decimal(r[2]), r[3]) for r in rows]
