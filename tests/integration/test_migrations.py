import os
import subprocess
import sys

import asyncpg
import pytest
from pytest_check import check

DB_NAME = os.environ.get("TEST_MIGRATIONS_DB", "transfer_test_migrations")


async def admin_connect():
    return await asyncpg.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5433")),
        user=os.environ.get("DB_USER", "transfer"),
        password=os.environ.get("DB_PASSWORD", "transfer"),
        database="postgres",
    )


@pytest.fixture()
async def migrated_database():
    admin = await admin_connect()
    await admin.execute(f'drop database if exists "{DB_NAME}"')
    await admin.execute(f'create database "{DB_NAME}"')
    await admin.close()
    env = {
        **os.environ,
        "DB_NAME": DB_NAME,
        "DB_HOST": os.environ.get("DB_HOST", "localhost"),
        "DB_PORT": os.environ.get("DB_PORT", "5433"),
        "DB_USER": os.environ.get("DB_USER", "transfer"),
        "DB_PASSWORD": os.environ.get("DB_PASSWORD", "transfer"),
        "DB_PROFILE": "postgres",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    connection = await asyncpg.connect(
        host=env["DB_HOST"],
        port=int(env["DB_PORT"]),
        user=env["DB_USER"],
        password=env["DB_PASSWORD"],
        database=DB_NAME,
    )
    yield connection
    await connection.close()
    admin = await admin_connect()
    await admin.execute(f'drop database if exists "{DB_NAME}" with (force)')
    await admin.close()


async def test_migration_creates_every_model_table(migrated_database) -> None:
    import importlib

    from app.models.base import Base

    importlib.import_module("app.models.tables")
    rows = await migrated_database.fetch(
        "select tablename from pg_tables where schemaname = 'public'"
    )
    actual = {row["tablename"] for row in rows} - {"alembic_version"}
    expected = set(Base.metadata.tables)
    with check:
        assert actual == expected
    with check:
        assert "transfers" in actual
    with check:
        assert "webhook_events" in actual


async def test_migration_creates_expected_indexes(migrated_database) -> None:
    rows = await migrated_database.fetch(
        "select indexname from pg_indexes where schemaname = 'public'"
    )
    indexes = {row["indexname"] for row in rows}
    required = {
        "ix_provider_directions_direction",
        "ix_maintenance_windows_provider",
        "ix_quotes_direction",
        "ix_transfers_client_created",
        "ix_transfers_quote",
        "ix_payout_attempts_transfer",
        "ix_payout_attempts_reference",
        "ix_webhook_events_reference",
        "ix_limit_definitions_client",
    }
    with check:
        assert required <= indexes


async def test_migration_enforces_idempotency_and_event_uniqueness(migrated_database) -> None:
    constraints = await migrated_database.fetch(
        """
        select conname, contype from pg_constraint
        where conrelid in ('transfers'::regclass, 'webhook_events'::regclass)
        """
    )
    names = {row["conname"] for row in constraints}
    with check:
        assert any("idempotency" in name or "uq_transfers_client_key" in name for name in names)
    with check:
        assert any("webhook_events_pkey" in name for name in names)


KNOWN_TABLES = (
    "currencies",
    "countries",
    "payout_methods",
    "transfer_statuses",
    "attempt_statuses",
    "limit_period_types",
    "directions",
    "providers",
    "provider_directions",
    "maintenance_windows",
    "clients",
    "balances",
)


def _count_table_sql(table: str) -> str:
    if table not in KNOWN_TABLES:
        raise ValueError(f"unknown table {table}")
    return f"select count(*) from {table}"  # noqa: S608 - table validated against KNOWN_TABLES above


async def test_seed_is_idempotent(migrated_database) -> None:
    import importlib
    from pathlib import Path

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.db import seed as seed_module

    importlib.import_module("app.models.tables")
    url = (
        "postgresql+asyncpg://"
        f"{os.environ.get('DB_USER', 'transfer')}:{os.environ.get('DB_PASSWORD', 'transfer')}"
        f"@{os.environ.get('DB_HOST', 'localhost')}:{os.environ.get('DB_PORT', '5433')}"
        f"/{DB_NAME}"
    )
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await seed_module.seed(session, Path("data"))
        counts = {}
        for table in (
            "currencies",
            "countries",
            "payout_methods",
            "transfer_statuses",
            "attempt_statuses",
            "limit_period_types",
            "directions",
            "providers",
            "provider_directions",
            "maintenance_windows",
            "clients",
            "balances",
        ):
            counts[table] = (await session.execute(text(_count_table_sql(table)))).scalar_one()
        balances_before = (
            await session.execute(
                text("select client_id, currency, available from balances order by 1, 2")
            )
        ).all()
        await seed_module.seed(session, Path("data"))
        for table, expected in counts.items():
            actual = (await session.execute(text(_count_table_sql(table)))).scalar_one()
            with check:
                assert actual == expected, table
        balances_after = (
            await session.execute(
                text("select client_id, currency, available from balances order by 1, 2")
            )
        ).all()
        with check:
            assert balances_after == balances_before
    await engine.dispose()
    with check:
        assert counts["currencies"] == 10
    with check:
        assert counts["providers"] == 7


async def test_postgres_memory_settings_applied(session) -> None:
    import pytest
    from sqlalchemy import text

    if session.bind.dialect.name != "postgresql":
        pytest.skip("requires PostgreSQL")
    for name in ("shared_buffers", "work_mem", "max_connections"):
        value = (await session.execute(text(f"SHOW {name}"))).scalar_one()
        with check:
            assert value, name
