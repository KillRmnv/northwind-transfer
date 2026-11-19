"""Alembic async environment."""

import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

import app.models.tables  # noqa: F401
from alembic import context
from app.core.settings import settings
from app.models.base import Base

config = context.config


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_async_engine(settings.database_url)

    async def run() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(_migrate)
        await engine.dispose()

    def _migrate(connection) -> None:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_type=True,
            transaction_per_migration=True,
        )
        context.run_migrations()

    asyncio.run(run())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
