import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from app.core.exceptions import ValidationError


def try_parse_uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def parse_uuid(value: object, field: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValidationError(f"{field} must be a UUID") from exc


def parse_decimal(value: object, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValidationError(f"{field} is not a number") from exc


def parse_rate_entry(entry: object) -> dict | None:
    if not isinstance(entry, dict):
        return None
    base = entry.get("base")
    quote = entry.get("quote")
    if not isinstance(base, str) or not isinstance(quote, str) or not base or not quote:
        return None
    try:
        rate = Decimal(str(entry.get("rate")))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not rate.is_finite() or rate <= 0:
        return None
    try:
        updated_at = datetime.fromisoformat(str(entry.get("updated_at")).replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return {"base": base, "quote": quote, "rate": rate, "updated_at": updated_at}
