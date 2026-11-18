from datetime import datetime, timezone


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
