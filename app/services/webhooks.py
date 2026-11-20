import hashlib
import hmac
import logging
import re
from datetime import datetime

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import as_utc, now_utc
from app.core.exceptions import ServiceUnavailableError, UnauthorizedError, ValidationError
from app.core.metrics import inc as metrics_inc
from app.core.settings import Settings
from app.models.tables import Provider, WebhookEvent
from app.repositories import attempts as attempt_repo
from app.repositories import transfers as transfer_repo
from app.repositories import webhook_events as webhook_repo
from app.schemas.webhooks import WebhookEvent as WebhookEventSchema
from app.services.providers import ProviderClient, confirm_payout
from app.services.transfer_machine import TERMINAL, TransferStateMachine
from app.services.transfers import run_attempts_locked as run_attempts

logger = logging.getLogger("transfer-core.webhooks")

PAYOUT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

EVENT_ADAPTER: TypeAdapter[WebhookEventSchema] = TypeAdapter(WebhookEventSchema)


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    """HMAC-подпись тела вебхука секретом провайдера."""
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_webhook(
    session: AsyncSession,
    settings: Settings,
    provider_id: str,
    body: bytes,
    signature: str | None,
    now: datetime,
) -> None:
    """Приём события: секрет, подпись, дедуп, затем обработка."""
    if not settings.provider_webhook_secret:
        raise ServiceUnavailableError("webhook secret is not configured")
    if not verify_signature(settings.provider_webhook_secret, body, signature):
        metrics_inc("webhook_invalid_signature_total")
        raise UnauthorizedError("invalid webhook signature")
    provider = await session.get(Provider, provider_id)
    if provider is None:
        metrics_inc("webhook_unknown_provider_total")
        logger.warning("webhook for unknown provider", extra={"provider_id": provider_id})
        raise ValidationError(f"unknown provider {provider_id}")
    event = EVENT_ADAPTER.validate_json(body)
    occurred_at = as_utc(event.occurred_at)
    if not PAYOUT_ID_RE.fullmatch(event.payout_id):
        raise ValidationError("payout_id is invalid")
    if event.status not in {"PROCESSING", "COMPLETED", "FAILED"}:
        raise ValidationError(f"unknown webhook status {event.status}")
    if await session.get(WebhookEvent, event.event_id) is not None:
        return
    stored = await webhook_repo.store_webhook_event(
        session,
        event.event_id,
        provider_id,
        event.payout_id,
        event.reference,
        event.status,
        occurred_at,
        now,
    )
    if not stored:
        return
    await _process_event(session, settings, provider_id, event, occurred_at)
    await session.commit()


async def _process_event(
    session: AsyncSession,
    settings: Settings,
    provider_id: str,
    event: WebhookEventSchema,
    occurred_at: datetime,
) -> None:
    """Одно событие: монотонность, своя выплата, подтверждение, исход."""
    transfer = await transfer_repo.get_transfer_by_reference(session, event.reference)
    if transfer is None:
        metrics_inc("webhook_unknown_reference_total")
        logger.warning(
            "webhook for unknown reference",
            extra={"provider_id": provider_id, "reference": event.reference},
        )
        return
    if transfer.status in TERMINAL:
        return
    previous = await webhook_repo.webhook_events_for_transfer(session, event.reference)
    newest = max(
        (as_utc(item.occurred_at) for item in previous if item.event_id != event.event_id),
        default=None,
    )
    if newest is not None and occurred_at <= newest:
        return
    attempt = await attempt_repo.find_attempt_by_payout(session, provider_id, event.payout_id)
    if attempt is not None and attempt.transfer_id != transfer.transfer_id:
        metrics_inc("webhook_foreign_payout_total")
        logger.warning(
            "webhook payout belongs to another transfer",
            extra={
                "provider_id": provider_id,
                "payout_id": event.payout_id,
                "reference": event.reference,
            },
        )
        return
    client = ProviderClient(settings.mock_provider_url, settings.mock_provider_timeout_sec)
    state = await confirm_payout(client, provider_id, event.payout_id, event.reference)
    if state is None:
        metrics_inc("webhook_unknown_payout_total")
        logger.warning(
            "webhook payout not confirmed at provider",
            extra={
                "provider_id": provider_id,
                "payout_id": event.payout_id,
                "reference": event.reference,
            },
        )
        return
    if event.status == "COMPLETED":
        if state is None or state.status != "COMPLETED":
            return
        locked = await transfer_repo.get_transfer(session, transfer.transfer_id, lock=True)
        if locked is not None:
            machine = TransferStateMachine(session, locked)
            await machine.complete(now_utc(), f"provider:{provider_id}")
            await session.commit()
        return
    if event.status == "FAILED":
        if state is not None and state.status == "COMPLETED":
            return
        await run_attempts(
            session, settings, transfer.transfer_id, now_utc(), f"provider:{provider_id}"
        )
