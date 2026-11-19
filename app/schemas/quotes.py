from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QuoteRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    source_country: str = Field(
        min_length=2,
        max_length=2,
        description="Код страны отправителя (ISO 3166-1 alpha-2)",
        examples=["RU"],
    )
    source_currency: str = Field(
        min_length=3,
        max_length=3,
        description="Валюта отправителя (ISO 4217)",
        examples=["RUB"],
    )
    target_country: str = Field(
        min_length=2,
        max_length=2,
        description="Код страны получателя (ISO 3166-1 alpha-2)",
        examples=["TH"],
    )
    target_currency: str = Field(
        min_length=3,
        max_length=3,
        description="Валюта получателя (ISO 4217)",
        examples=["THB"],
    )
    payout_method: str = Field(
        min_length=1,
        max_length=32,
        description="Способ выплаты в стране получателя",
        examples=["BANK_ACCOUNT"],
    )
    amount: str = Field(
        min_length=1,
        max_length=32,
        description="Сумма строкой с точностью minor_units валюты",
        examples=["10000.00"],
    )
    amount_type: Literal["SOURCE", "TARGET"] = Field(
        description="SOURCE — отправить столько; TARGET — получатель должен получить столько",
        examples=["SOURCE"],
    )


class Money(BaseModel):
    model_config = ConfigDict(strict=True)

    amount: str = Field(
        description="Сумма строкой, знаков после точки — minor_units валюты",
        examples=["10000.00"],
    )
    currency: str = Field(description="Код валюты (ISO 4217)", examples=["RUB"])


class QuoteResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    quote_id: str = Field(
        description="Идентификатор котировки для POST /transfers",
        examples=["3fa85f64-5717-4562-b3fc-2c963f66afa6"],
    )
    direction_id: str = Field(
        description="Идентификатор направления и тарифа", examples=["RU-TH-BANK"]
    )
    source_amount: Money = Field(description="Сумма списания с отправителя")
    fee: Money = Field(description="Комиссия сервиса отдельной строкой")
    amount_to_convert: Money = Field(
        description="Сумма, уходящая в конвертацию (за вычетом комиссии)"
    )
    rate: str = Field(
        description="Курс: единиц валюты получателя за единицу валюты отправителя, 8 знаков",
        examples=["0.42000000"],
    )
    target_amount: Money = Field(description="Сумма к выплате получателю")
    breakdown: list[str] = Field(
        default_factory=list, description="Шаги расчёта в свободной форме для аудита"
    )
    expires_at: datetime = Field(
        description="Момент истечения котировки (UTC); истёкшая отклоняется с QUOTE_EXPIRED"
    )


class ErrorDetail(BaseModel):
    model_config = ConfigDict(strict=True)

    code: str = Field(
        description="Машиночитаемый код ошибки из контракта",
        examples=["AMOUNT_BELOW_MIN"],
    )
    message: str = Field(
        description="Человекочитаемое описание",
        examples=["amount below min 1000.00 RUB"],
    )
    details: dict[str, str] | None = Field(
        default=None, description="Дополнительные поля ошибки (лимиты, значения)"
    )


class ErrorResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    type: str = Field(default="about:blank", description="RFC 9457: URI типа проблемы")
    title: str = Field(
        description="RFC 9457: краткое название, совпадает с code",
        examples=["AMOUNT_BELOW_MIN"],
    )
    status: int = Field(description="RFC 9457: HTTP-статус, дублирует код ответа", examples=[400])
    detail: str = Field(
        description="RFC 9457: детали конкретного случая",
        examples=["amount below min 1000.00 RUB"],
    )
    instance: str | None = Field(
        default=None, description="RFC 9457: путь запроса, где возникла ошибка"
    )
    error: ErrorDetail = Field(description="Вложенный объект контракта: code/message/details")
