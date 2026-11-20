import hashlib

from app.core.exceptions import ValidationError


def body_hash(body: dict) -> str:
    """Хеш тела заявки для сверки повторного ключа."""
    canonical = "|".join(f"{key}={body.get(key)}" for key in sorted(body))
    return hashlib.sha256(canonical.encode()).hexdigest()


def require_idempotency_key(value: str | None) -> str:
    """Ключ идемпотентности: непустая строка до 128 символов."""
    key = (value or "").strip()
    if not key or len(key) > 128:
        raise ValidationError("Idempotency-Key must be 1..128 non-blank characters")
    return key
