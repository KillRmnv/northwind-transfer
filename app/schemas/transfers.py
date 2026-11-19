from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.types import FlexibleUUID


class Recipient(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str = Field(
        default="",
        max_length=255,
        description="Имя получателя",
        examples=["Somchai Jaidee"],
    )
    account: str = Field(
        min_length=1,
        max_length=64,
        description="Счёт/карта/телефон получателя в его стране",
        examples=["1234567890"],
    )
    bank_code: str = Field(
        default="",
        max_length=32,
        description="Код банка получателя, если требуется направлением",
        examples=["BKKB"],
    )


class TransferRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    quote_id: FlexibleUUID = Field(description="Котировка, по которой фиксируется цена перевода")
    client_id: str = Field(
        min_length=1,
        max_length=32,
        description="Клиент-плательщик; резерв списывается с его баланса",
        examples=["c_001"],
    )
    recipient: Recipient = Field(description="Получатель выплаты")


class Attempt(BaseModel):
    model_config = ConfigDict(strict=True)

    provider_id: str = Field(
        description="Провайдер, которому была отправлена попытка",
        examples=["prov_alpha"],
    )
    payout_id: str | None = Field(
        default=None,
        description="Идентификатор выплаты у провайдера; null — запрос не дошёл или ответ потерян",
    )
    status: str = Field(
        description="Статус попытки: PROCESSING, COMPLETED, FAILED",
        examples=["PROCESSING"],
    )
    failure_code: str | None = Field(
        default=None,
        description="Код отказа провайдера или NEEDS_REVIEW при неподтверждённом итоге",
    )
    attempt_no: int = Field(description="Порядковый номер попытки в рамках перевода", examples=[1])
    created_at: datetime = Field(description="Момент создания попытки (UTC)")


class HistoryEntry(BaseModel):
    model_config = ConfigDict(strict=True)

    status: str = Field(description="Статус после перехода", examples=["PROCESSING"])
    at: datetime = Field(description="Момент перехода (UTC)")
    actor: str = Field(
        description="Кто инициировал переход: client, service, provider:<id>, reconcile",
        examples=["service"],
    )


class TransferResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    transfer_id: str = Field(description="Идентификатор перевода")
    quote_id: str = Field(description="Котировка, по которой создан перевод")
    client_id: str = Field(description="Клиент-плательщик")
    status: str = Field(
        description="Текущий статус: PENDING, PROCESSING, NEEDS_REVIEW, COMPLETED, FAILED",
        examples=["PROCESSING"],
    )
    source_amount: dict = Field(description="Списанная сумма: {amount, currency}")
    target_amount: dict = Field(description="Сумма к выплате: {amount, currency}")
    provider_id: str | None = Field(
        default=None, description="Провайдер последней попытки с payout_id"
    )
    attempts: list[Attempt] = Field(
        default_factory=list,
        description="Попытки у провайдеров: кто, когда, с каким результатом",
    )
    history: list[HistoryEntry] = Field(
        default_factory=list, description="История переходов статусов с акторами"
    )
    created_at: datetime = Field(description="Момент создания (UTC)")
    updated_at: datetime = Field(description="Момент последнего изменения (UTC)")
