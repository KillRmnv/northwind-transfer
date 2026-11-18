import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.db.engine import engine, session_factory
from app.models.tables import (
    AttemptStatus,
    Balance,
    Client,
    Country,
    Currency,
    Direction,
    LimitPeriodType,
    MaintenanceWindow,
    PayoutMethod,
    Provider,
    ProviderDirection,
    TransferStatus,
)

TRANSFER_STATUSES = ["PENDING", "PROCESSING", "COMPLETED", "FAILED", "CANCELLED"]
ATTEMPT_STATUSES = ["PROCESSING", "COMPLETED", "FAILED"]
LIMIT_PERIOD_TYPES = ["HOURLY", "DAILY", "WINDOW_N"]


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def seed(session: AsyncSession, data_dir: Path) -> None:
    currencies = json.loads((data_dir / "currencies.json").read_text(encoding="utf-8"))
    for entry in currencies:
        await session.merge(Currency(code=entry["code"], minor_units=entry["minor_units"]))

    tariffs = json.loads((data_dir / "tariffs.json").read_text(encoding="utf-8"))
    for tariff in tariffs:
        await session.merge(Country(code=tariff["source"]["country"]))
        await session.merge(Country(code=tariff["target"]["country"]))
        await session.merge(PayoutMethod(code=tariff["payout_method"]))

    for code in TRANSFER_STATUSES:
        await session.merge(TransferStatus(code=code))
    for code in ATTEMPT_STATUSES:
        await session.merge(AttemptStatus(code=code))
    for code in LIMIT_PERIOD_TYPES:
        await session.merge(LimitPeriodType(code=code))

    for tariff in tariffs:
        fee = tariff["fee"]
        limits = tariff["amount_limits"]
        await session.merge(
            Direction(
                direction_id=tariff["direction_id"],
                source_country=tariff["source"]["country"],
                source_currency=tariff["source"]["currency"],
                target_country=tariff["target"]["country"],
                target_currency=tariff["target"]["currency"],
                payout_method=tariff["payout_method"],
                fee_percent=Decimal(str(fee["percent"])),
                fee_fixed=Decimal(str(fee["fixed"])),
                fee_min=Decimal(str(fee["min"])),
                fee_charged_in=fee["charged_in"],
                min_amount=Decimal(str(limits["min"])),
                max_amount=Decimal(str(limits["max"])),
                limit_currency=limits["currency"],
                min_margin_percent=Decimal(str(tariff["min_margin_percent"])),
                quote_ttl_sec=int(tariff["quote_ttl_sec"]),
            )
        )

    providers = json.loads((data_dir / "providers.json").read_text(encoding="utf-8"))
    for provider in providers:
        fee = provider["fee"]
        limits = provider["limits"]
        await session.merge(
            Provider(
                provider_id=provider["provider_id"],
                currency=provider["currency"],
                fee_percent=Decimal(str(fee["percent"])),
                fee_fixed=Decimal(str(fee["fixed"])),
                limit_min=Decimal(str(limits["min"])),
                limit_max=Decimal(str(limits["max"])),
                success_rate_24h=Decimal(str(provider["success_rate_24h"])),
                p95_latency_ms=int(provider["p95_latency_ms"]),
                enabled=bool(provider["enabled"]),
            )
        )
        for direction_id in provider["directions"]:
            await session.merge(
                ProviderDirection(provider_id=provider["provider_id"], direction_id=direction_id)
            )
        existing = (
            (
                await session.execute(
                    select(MaintenanceWindow).where(
                        MaintenanceWindow.provider_id == provider["provider_id"]
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in existing:
            await session.delete(row)
        for window in provider.get("maintenance_windows", []):
            session.add(
                MaintenanceWindow(
                    provider_id=provider["provider_id"],
                    from_at=parse_time(window["from"]),
                    to_at=parse_time(window["to"]),
                )
            )

    clients = json.loads((data_dir / "clients.json").read_text(encoding="utf-8"))
    for client in clients:
        await session.merge(
            Client(
                client_id=client["client_id"],
                daily_limit_amount=Decimal(str(client["daily_limit"]["amount"])),
                daily_limit_currency=client["daily_limit"]["currency"],
            )
        )
        for currency, amount in client["balances"].items():
            row = await session.get(Balance, (client["client_id"], currency))
            if row is None:
                session.add(
                    Balance(
                        client_id=client["client_id"],
                        currency=currency,
                        available=Decimal(str(amount)),
                        reserved=Decimal("0"),
                    )
                )
    await session.commit()


async def main() -> None:
    data_dir = Path(settings.data_dir)
    async with session_factory() as session:
        await seed(session, data_dir)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
