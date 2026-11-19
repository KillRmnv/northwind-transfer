import uuid
from decimal import Decimal, InvalidOperation

from app.core.exceptions import ValidationError


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
