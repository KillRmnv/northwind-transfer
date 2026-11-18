import hashlib
import hmac
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now_utc
from app.core.exceptions import ValidationError
from app.core.settings import Settings
from app.models.tables import Provider
from app.repositories import transfers as transfer_repo
from app.schemas.webhooks import WebhookEvent
from app.services import finalize as finalize_mod
from app.services.transfers import TERMINAL, run_attempts


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret:
        return True
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def handle_webhook(
    session: AsyncSession,
    settings: Settings,
    provider_id: str,
    body: bytes,
    signature: str | None,
    now: datetime,
) -> None:
    provider = await session.get(Provider, provider_id)
    if provider is None:
        raise ValidationError(f"unknown provider {provider_id}")
    if settings.provider_webhook_secret and not verify_signature(
        settings.provider_webhook_secret, body, signature
    ):
        raise ValidationError("invalid webhook signature")
    import json as json_module

    try:
        payload = json_module.loads(body.decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("webhook body is not valid JSON") from exc
    event = WebhookEvent.model_validate(payload)
    stored = await transfer_repo.store_webhook_event(
        session,
        event.event_id,
        provider_id,
        event.payout_id,
        event.reference,
        event.status,
        parse_time(event.occurred_at),
        now,
    )
    await session.commit()
    if not stored:
        return
    transfer = await transfer_repo.get_transfer_by_reference(session, event.reference)
    if transfer is None:
        return
    if transfer.status in TERMINAL:
        return
    if event.status == "COMPLETED":
        locked = await transfer_repo.get_transfer(session, transfer.transfer_id, lock=True)
        if locked is not None and locked.status not in TERMINAL:
            await finalize_mod.complete_transfer(session, settings, locked, now_utc())
        await session.commit()
        return
    if event.status == "FAILED":
        await run_attempts(session, settings, transfer.transfer_id, now_utc())
