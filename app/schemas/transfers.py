from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Recipient(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str = Field(default="", max_length=255)
    account: str = Field(min_length=1, max_length=64)
    bank_code: str = Field(default="", max_length=32)


class TransferRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    quote_id: str = Field(min_length=36, max_length=36)
    client_id: str = Field(min_length=1, max_length=32)
    recipient: Recipient


class Attempt(BaseModel):
    model_config = ConfigDict(strict=True)

    provider_id: str
    payout_id: str | None = None
    status: str
    failure_code: str | None = None
    attempt_no: int
    created_at: datetime


class HistoryEntry(BaseModel):
    model_config = ConfigDict(strict=True)

    status: str
    at: datetime
    actor: str


class TransferResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    transfer_id: str
    quote_id: str
    client_id: str
    status: str
    source_amount: dict
    target_amount: dict
    provider_id: str | None = None
    attempts: list[Attempt] = Field(default_factory=list)
    history: list[HistoryEntry] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
