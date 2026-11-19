from pydantic import BaseModel, ConfigDict, Field


class CurrencyBalance(BaseModel):
    model_config = ConfigDict(strict=True)

    currency: str = Field(description="Код валюты (ISO 4217)", examples=["RUB"])
    available: str = Field(
        description="Доступный остаток строкой с точностью minor_units",
        examples=["490000.00"],
    )
    reserved: str = Field(
        description="Сумма в резерве под незавершённые переводы",
        examples=["0.00"],
    )
    spent: str = Field(
        description="Суммарно списано завершёнными переводами",
        examples=["10000.00"],
    )


class BalanceResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    client_id: str = Field(description="Клиент, чей баланс запрошен", examples=["c_001"])
    balances: dict[str, CurrencyBalance] = Field(
        description="Балансы по валютам, ключ — код валюты"
    )
