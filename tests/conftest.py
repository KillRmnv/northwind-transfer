import json
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

os.environ["ENV"] = "test"

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import seed as seed_module
from app.main import app
from app.models.base import Base


def database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    if os.environ.get("DB_PROFILE") == "sqlite":
        return "sqlite+aiosqlite:///var/test.sqlite3"
    return (
        "postgresql+asyncpg://"
        f"{os.environ.get('DB_USER', 'transfer')}:{os.environ.get('DB_PASSWORD', 'transfer')}"
        f"@{os.environ.get('DB_HOST', 'localhost')}:{os.environ.get('DB_PORT', '5433')}"
        "/transfer_test"
    )


async def ensure_test_database() -> None:
    if os.environ.get("DB_PROFILE") == "sqlite" or os.environ.get("TEST_DATABASE_URL"):
        return
    import asyncpg

    connection = await asyncpg.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5433")),
        user=os.environ.get("DB_USER", "transfer"),
        password=os.environ.get("DB_PASSWORD", "transfer"),
        database="postgres",
    )
    try:
        exists = await connection.fetchval(
            "select 1 from pg_database where datname = 'transfer_test'"
        )
        if not exists:
            await connection.execute("create database transfer_test")
    finally:
        await connection.close()


@pytest.fixture()
async def engine() -> AsyncIterator:
    await ensure_test_database()
    eng = create_async_engine(database_url(), poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest.fixture()
async def session(engine) -> AsyncIterator[AsyncSession]:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as current:
        await seed_module.seed(current, Path("data"))
        yield current
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


@pytest.fixture()
async def api(session: AsyncSession) -> AsyncIterator:
    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db.engine import get_session

    await session.commit()
    factory = async_sessionmaker(session.bind, class_=AsyncSession, expire_on_commit=False)

    async def override() -> AsyncIterator[AsyncSession]:
        async with factory() as request_session:
            yield request_session

    app.dependency_overrides[get_session] = override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def now() -> datetime:
    return datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def freeze_rates():
    from app.services import rates as rates_module

    original = rates_module.RatesClient.fetch

    def _freeze(rates: dict) -> None:
        async def fake_fetch(self) -> dict:
            return rates

        rates_module.RatesClient.fetch = fake_fetch

    yield _freeze
    rates_module.RatesClient.fetch = original


@pytest.fixture()
def live_rates() -> dict:
    from app.services.quotes import RateData

    current = datetime.now(timezone.utc)
    data = json.loads((Path("data") / "rates.json").read_text(encoding="utf-8"))
    return {
        (entry["base"], entry["quote"]): RateData(rate=Decimal(entry["rate"]), updated_at=current)
        for entry in data
    }
