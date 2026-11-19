from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WebhookEvent(BaseModel):
    model_config = ConfigDict(strict=True)

    event_id: str = Field(min_length=1, max_length=64)
    payout_id: str = Field(min_length=1, max_length=64)
    reference: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=24)
    occurred_at: datetime

    @field_validator("occurred_at", mode="after")
    @classmethod
    def _ensure_tz(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
