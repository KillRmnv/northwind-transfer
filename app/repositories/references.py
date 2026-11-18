from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import ProviderInfo, TariffData, Window
from app.models.tables import Currency, Direction, MaintenanceWindow, Provider, ProviderDirection


async def load_minor_units(session: AsyncSession) -> dict[str, int]:
    rows = (await session.execute(select(Currency.code, Currency.minor_units))).all()
    return {code: int(minor) for code, minor in rows}


async def load_providers(session: AsyncSession) -> list[ProviderInfo]:
    rows = (await session.execute(select(Provider))).scalars().all()
    links = (await session.execute(select(ProviderDirection))).scalars().all()
    windows = (await session.execute(select(MaintenanceWindow))).scalars().all()
    by_provider: dict[str, set[str]] = {}
    for link in links:
        by_provider.setdefault(link.provider_id, set()).add(link.direction_id)
    windows_by_provider: dict[str, list[Window]] = {}
    for window in windows:
        windows_by_provider.setdefault(window.provider_id, []).append(
            Window(from_at=window.from_at, to_at=window.to_at)
        )
    return [
        ProviderInfo(
            provider_id=row.provider_id,
            currency=row.currency,
            fee_percent=Decimal(row.fee_percent),
            fee_fixed=Decimal(row.fee_fixed),
            limit_min=Decimal(row.limit_min),
            limit_max=Decimal(row.limit_max),
            success_rate=Decimal(row.success_rate_24h),
            p95_latency_ms=row.p95_latency_ms,
            enabled=bool(row.enabled),
            directions=frozenset(by_provider.get(row.provider_id, set())),
            windows=tuple(windows_by_provider.get(row.provider_id, [])),
        )
        for row in rows
    ]


async def load_tariffs(session: AsyncSession) -> list[TariffData]:
    rows = (await session.execute(select(Direction))).scalars().all()
    return [
        TariffData(
            direction_id=row.direction_id,
            source_country=row.source_country,
            source_currency=row.source_currency,
            target_country=row.target_country,
            target_currency=row.target_currency,
            payout_method=row.payout_method,
            fee_percent=Decimal(row.fee_percent),
            fee_fixed=Decimal(row.fee_fixed),
            fee_min=Decimal(row.fee_min),
            fee_charged_in=row.fee_charged_in,
            min_amount=Decimal(row.min_amount),
            max_amount=Decimal(row.max_amount),
            limit_currency=row.limit_currency,
            min_margin_percent=Decimal(row.min_margin_percent),
            quote_ttl_sec=row.quote_ttl_sec,
        )
        for row in rows
    ]
