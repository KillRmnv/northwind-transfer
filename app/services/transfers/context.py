from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import QuoteNotFoundError, TransferNotFoundError
from app.core.settings import Settings
from app.models.tables import Quote, Transfer
from app.repositories import transfers as transfer_repo
from app.repositories.quotes import get_quote
from app.repositories.references import load_minor_units
from app.services.providers import ProviderClient
from app.services.quotes import RateData
from app.services.transfer_machine import TERMINAL
from app.services.transfers.creation import live_rates_map


@dataclass
class TransferContext:
    """Всё для исполнения перевода: сессия, настройки, клиент, строки, курсы."""

    session: AsyncSession
    settings: Settings
    client: ProviderClient
    transfer: Transfer
    quote: Quote
    reference: str
    rates: dict[tuple[str, str], RateData]
    minor_units: dict[str, int]
    now: datetime


async def load_context(
    session: AsyncSession, settings: Settings, transfer_id, now: datetime
) -> TransferContext | str:
    """Загрузка перевода, котировки и курсов; строка — уже терминальный статус."""
    minor_units = await load_minor_units(session)
    rates = await live_rates_map(settings)
    transfer = await transfer_repo.get_transfer(session, transfer_id, lock=True)
    if transfer is None:
        raise TransferNotFoundError(str(transfer_id))
    if transfer.status in TERMINAL:
        return transfer.status
    quote = await get_quote(session, transfer.quote_id)
    if quote is None:
        raise QuoteNotFoundError(str(transfer.quote_id))
    client = ProviderClient(settings.mock_provider_url, settings.mock_provider_timeout_sec)
    return TransferContext(
        session=session,
        settings=settings,
        client=client,
        transfer=transfer,
        quote=quote,
        reference=transfer.transfer_id.hex,
        rates=rates,
        minor_units=minor_units,
        now=now,
    )
