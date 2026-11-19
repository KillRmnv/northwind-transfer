from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WebhookEvent(BaseModel):
    model_config = ConfigDict(strict=True)

    event_id: str = Field(
        min_length=1,
        max_length=64,
        description="Уникальный id события у провайдера; дубли игнорируются",
        examples=["ev_9f2c1a"],
    )
    payout_id: str = Field(
        min_length=1,
        max_length=64,
        description="Идентификатор выплаты у провайдера",
        examples=["po_2cfc02763fa1502b"],
    )
    reference: str = Field(
        min_length=1,
        max_length=64,
        description="Наш reference (hex transfer_id); неизвестные принимаются с 200 и логируются",
        examples=["013b92d9d4764750bcd4af8ec4550"],
    )
    status: str = Field(
        min_length=1,
        max_length=24,
        description="Статус выплаты у провайдера: PROCESSING, COMPLETED, FAILED",
        examples=["COMPLETED"],
    )
    occurred_at: datetime = Field(
        description="Момент события у провайдера; поздние события не перетирают более свежие"
    )

    @field_validator("occurred_at", mode="after")
    @classmethod
    def _ensure_tz(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
