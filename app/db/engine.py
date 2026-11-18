from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.settings import Settings, settings


def create_engine(settings_obj: Settings):
    kwargs: dict = {
        "pool_pre_ping": settings_obj.db_pool_pre_ping,
    }
    if settings_obj.db_profile == "sqlite":
        kwargs["poolclass"] = NullPool
    else:
        kwargs.update(
            {
                "pool_size": settings_obj.db_pool_size,
                "max_overflow": settings_obj.db_max_overflow,
                "pool_timeout": settings_obj.db_pool_timeout,
            }
        )
    return create_async_engine(settings_obj.database_url, **kwargs)


engine = create_engine(settings)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
