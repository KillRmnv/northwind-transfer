from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import WebhookEvent


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
    """Сохранение вебхука: False означает дубль, обрабатывать нечего."""
    existing = await session.get(WebhookEvent, event_id)
    if existing is not None:
        return False
    try:
        async with session.begin_nested():
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
    except IntegrityError:
        return False


async def webhook_events_for_transfer(session: AsyncSession, reference: str) -> list[WebhookEvent]:
    """События перевода по времени для монотонности."""
    query = (
        sa.select(WebhookEvent)
        .where(WebhookEvent.reference == reference)
        .order_by(WebhookEvent.occurred_at)
    )
    return list((await session.execute(query)).scalars().all())
