import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import QuoteResult
from app.models.tables import Quote


async def save_quote(
    session: AsyncSession,
    result: QuoteResult,
    expires_at: datetime,
    created_at: datetime,
) -> uuid.UUID:
    quote_id = uuid.uuid4()
    session.add(
        Quote(
            quote_id=quote_id,
            direction_id=result.direction_id,
            source_amount=result.source_amount,
            source_currency=result.source_currency,
            fee_amount=result.fee_amount,
            fee_currency=result.fee_currency,
            amount_to_convert=result.amount_to_convert,
            rate=result.rate,
            target_amount=result.target_amount,
            target_currency=result.target_currency,
            expires_at=expires_at,
            created_at=created_at,
        )
    )
    await session.commit()
    return quote_id


async def get_quote(session: AsyncSession, quote_id: uuid.UUID) -> Quote | None:
    return await session.get(Quote, quote_id)
