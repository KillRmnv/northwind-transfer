from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QuoteRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    source_country: str = Field(min_length=2, max_length=2)
    source_currency: str = Field(min_length=3, max_length=3)
    target_country: str = Field(min_length=2, max_length=2)
    target_currency: str = Field(min_length=3, max_length=3)
    payout_method: str = Field(min_length=1, max_length=32)
    amount: str = Field(min_length=1, max_length=32)
    amount_type: Literal["SOURCE", "TARGET"]


class Money(BaseModel):
    model_config = ConfigDict(strict=True)

    amount: str = Field(examples=["10000.00"])
    currency: str = Field(examples=["RUB"])


class QuoteResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    quote_id: str = Field(examples=["3fa85f64-5717-4562-b3fc-2c963f66afa6"])
    direction_id: str = Field(examples=["RU-TH-BANK"])
    source_amount: Money
    fee: Money
    amount_to_convert: Money
    rate: str = Field(examples=["0.4200"])
    target_amount: Money
    breakdown: list[str] = Field(default_factory=list)
    expires_at: datetime


class ErrorDetail(BaseModel):
    model_config = ConfigDict(strict=True)

    code: str = Field(examples=["AMOUNT_BELOW_MIN"])
    message: str = Field(examples=["amount below min 1000.00 RUB"])
    details: dict[str, str] | None = Field(default=None)


class ErrorResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    type: str = Field(default="about:blank")
    title: str = Field(examples=["AMOUNT_BELOW_MIN"])
    status: int = Field(examples=[400])
    detail: str = Field(examples=["amount below min 1000.00 RUB"])
    instance: str | None = Field(default=None)
    error: ErrorDetail
