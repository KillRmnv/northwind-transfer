import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.exception_handlers import (
    domain_error_handler,
    request_validation_handler,
    unhandled_error_handler,
)
from app.api.v1.quotes import router as quotes_router
from app.api.v1.transfers import router as transfers_router
from app.api.v1.webhooks import router as webhooks_router
from app.core.clock import now_utc
from app.core.exceptions import DomainError
from app.core.settings import settings
from app.db.engine import session_factory

logger = logging.getLogger("transfer-core.reconcile")


async def reconcile_loop() -> None:
    from app.services.reconcile import reconcile_once

    while True:
        try:
            async with session_factory() as session:
                await reconcile_once(session, settings, now_utc())
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("reconcile pass failed: %s", exc)
        await asyncio.sleep(settings.reconcile_interval_sec)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if settings.env != "test":
        task = asyncio.create_task(reconcile_loop())
    yield
    if task is not None:
        task.cancel()


def create_app() -> FastAPI:
    docs_url = "/docs" if settings.is_dev else None
    openapi_url = "/openapi.json" if settings.is_dev else None
    app = FastAPI(
        title="Transfer Core", docs_url=docs_url, openapi_url=openapi_url, lifespan=lifespan
    )
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(quotes_router, prefix="/api/v1")
    app.include_router(transfers_router, prefix="/api/v1")
    app.include_router(webhooks_router, prefix="/api/v1")
    app.include_router(quotes_router)
    app.include_router(transfers_router)
    app.include_router(webhooks_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
