import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

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
LIMIT_PERIOD_TYPES = ["MINUTES", "HOURLY", "DAILY", "WINDOW_N"]


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_json(data_dir: Path, name: str) -> Any:
    return json.loads((data_dir / f"{name}.json").read_text(encoding="utf-8"))


async def seed_currencies(session: AsyncSession, data_dir: Path) -> None:
    for entry in load_json(data_dir, "currencies"):
        await session.merge(Currency(code=entry["code"], minor_units=entry["minor_units"]))


async def seed_reference_data(session: AsyncSession, data_dir: Path) -> None:
    tariffs = load_json(data_dir, "tariffs")
    countries = {tariff["source"]["country"] for tariff in tariffs}
    countries.update(tariff["target"]["country"] for tariff in tariffs)
    for code in sorted(countries):
        await session.merge(Country(code=code))
    for code in sorted({tariff["payout_method"] for tariff in tariffs}):
        await session.merge(PayoutMethod(code=code))
    for code in TRANSFER_STATUSES:
        await session.merge(TransferStatus(code=code))
    for code in ATTEMPT_STATUSES:
        await session.merge(AttemptStatus(code=code))
    for code in LIMIT_PERIOD_TYPES:
        await session.merge(LimitPeriodType(code=code))


def build_direction(tariff: dict) -> Direction:
    fee = tariff["fee"]
    limits = tariff["amount_limits"]
    return Direction(
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


async def seed_directions(session: AsyncSession, data_dir: Path) -> None:
    for tariff in load_json(data_dir, "tariffs"):
        await session.merge(build_direction(tariff))


def build_provider(entry: dict) -> Provider:
    fee = entry["fee"]
    limits = entry["limits"]
    return Provider(
        provider_id=entry["provider_id"],
        currency=entry["currency"],
        fee_percent=Decimal(str(fee["percent"])),
        fee_fixed=Decimal(str(fee["fixed"])),
        limit_min=Decimal(str(limits["min"])),
        limit_max=Decimal(str(limits["max"])),
        success_rate_24h=Decimal(str(entry["success_rate_24h"])),
        p95_latency_ms=int(entry["p95_latency_ms"]),
        enabled=bool(entry["enabled"]),
    )


async def seed_provider_windows(session: AsyncSession, provider_id: str, windows: list) -> None:
    existing = (
        (
            await session.execute(
                select(MaintenanceWindow).where(MaintenanceWindow.provider_id == provider_id)
            )
        )
        .scalars()
        .all()
    )
    for row in existing:
        await session.delete(row)
    for window in windows:
        session.add(
            MaintenanceWindow(
                provider_id=provider_id,
                from_at=parse_time(window["from"]),
                to_at=parse_time(window["to"]),
            )
        )


async def seed_providers(session: AsyncSession, data_dir: Path) -> None:
    for entry in load_json(data_dir, "providers"):
        await session.merge(build_provider(entry))
        for direction_id in entry["directions"]:
            await session.merge(
                ProviderDirection(provider_id=entry["provider_id"], direction_id=direction_id)
            )
        await seed_provider_windows(
            session, entry["provider_id"], entry.get("maintenance_windows", [])
        )


async def seed_client_balances(session: AsyncSession, client: dict) -> None:
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


async def seed_clients(session: AsyncSession, data_dir: Path) -> None:
    for client in load_json(data_dir, "clients"):
        await session.merge(
            Client(
                client_id=client["client_id"],
                daily_limit_amount=Decimal(str(client["daily_limit"]["amount"])),
                daily_limit_currency=client["daily_limit"]["currency"],
            )
        )
        await seed_client_balances(session, client)


async def seed(session: AsyncSession, data_dir: Path) -> None:
    await seed_currencies(session, data_dir)
    await seed_reference_data(session, data_dir)
    await seed_directions(session, data_dir)
    await seed_providers(session, data_dir)
    await seed_clients(session, data_dir)
    await session.commit()


async def main() -> None:
    data_dir = Path(settings.data_dir)
    async with session_factory() as session:
        await seed(session, data_dir)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
