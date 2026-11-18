import hashlib
import hmac
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now_utc
from app.core.exceptions import (
    ServiceUnavailableError,
    UnauthorizedError,
    ValidationError,
)
from app.core.settings import Settings
from app.models.tables import Provider
from app.repositories import transfers as transfer_repo
from app.schemas.webhooks import WebhookEvent
from app.services import finalize as finalize_mod
from app.services.providers import ProviderClient
from app.services.transfers import TERMINAL, run_attempts


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
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
    if not settings.provider_webhook_secret:
        raise ServiceUnavailableError("webhook secret is not configured")
    if not verify_signature(settings.provider_webhook_secret, body, signature):
        raise UnauthorizedError("invalid webhook signature")
    import json as json_module

    try:
        payload = json_module.loads(body.decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("webhook body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError("webhook body must be an object")
    try:
        event = WebhookEvent.model_validate(payload)
        occurred_at = parse_time(event.occurred_at)
    except (ValueError, TypeError) as exc:
        raise ValidationError("webhook fields are invalid") from exc
    existing = await session.get(
        __import__("app.models.tables", fromlist=["WebhookEvent"]).WebhookEvent,
        event.event_id,
    )
    if existing is not None:
        return
    transfer = await transfer_repo.get_transfer_by_reference(session, event.reference)
    if transfer is None:
        await transfer_repo.store_webhook_event(
            session,
            event.event_id,
            provider_id,
            event.payout_id,
            event.reference,
            event.status,
            occurred_at,
            now,
        )
        await session.commit()
        return
    if transfer.status in TERMINAL:
        await transfer_repo.store_webhook_event(
            session,
            event.event_id,
            provider_id,
            event.payout_id,
            event.reference,
            event.status,
            occurred_at,
            now,
        )
        await session.commit()
        return
    previous = await transfer_repo.webhook_events_for_transfer(session, event.reference)
    newest = max((item.occurred_at for item in previous), default=None)
    if newest is not None and occurred_at < newest:
        return
    attempt = await transfer_repo.find_attempt_by_payout(session, provider_id, event.payout_id)
    if attempt is not None and attempt.transfer_id != transfer.transfer_id:
        return
    client = ProviderClient(settings.mock_provider_url, settings.mock_provider_timeout_sec)
    state = await client.get_payout(provider_id, event.payout_id)
    if state is None:
        state = await client.find_payout(provider_id, event.reference)
    if state is not None and state.reference and state.reference != event.reference:
        return
    if event.status == "COMPLETED":
        if state is None or state.status != "COMPLETED":
            return
        locked = await transfer_repo.get_transfer(session, transfer.transfer_id, lock=True)
        if locked is not None and locked.status not in TERMINAL:
            await finalize_mod.complete_transfer(session, settings, locked, now_utc())
    elif event.status == "FAILED":
        if state is not None and state.status == "COMPLETED":
            return
        await run_attempts(session, settings, transfer.transfer_id, now_utc())
    await transfer_repo.store_webhook_event(
        session,
        event.event_id,
        provider_id,
        event.payout_id,
        event.reference,
        event.status,
        occurred_at,
        now,
    )
    await session.commit()
