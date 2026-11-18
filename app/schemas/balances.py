from pydantic import BaseModel, ConfigDict


class CurrencyBalance(BaseModel):
    model_config = ConfigDict(strict=True)

    currency: str
    available: str
    reserved: str


class BalanceResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    client_id: str
    balances: dict[str, CurrencyBalance]
