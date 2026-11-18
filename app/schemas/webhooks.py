from pydantic import BaseModel, ConfigDict, Field


class WebhookEvent(BaseModel):
    model_config = ConfigDict(strict=True)

    event_id: str = Field(min_length=1, max_length=64)
    payout_id: str = Field(min_length=1, max_length=64)
    reference: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=24)
    occurred_at: str = Field(min_length=1, max_length=64)
