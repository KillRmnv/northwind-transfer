import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.exception_handlers import (
    domain_error_handler,
    http_exception_handler,
    pydantic_validation_handler,
    request_validation_handler,
    unhandled_error_handler,
)
from app.api.v1.quotes import router as quotes_router
from app.api.v1.transfers import router as transfers_router
from app.api.v1.webhooks import router as webhooks_router
from app.core.clock import now_utc
from app.core.exceptions import DomainError
from app.core.logging import configure_logging, new_request_id, request_id_var
from app.core.settings import settings
from app.db.engine import session_factory
from app.repositories import transfers as transfer_repo

logger = logging.getLogger("transfer-core.reconcile")
access_logger = logging.getLogger("transfer-core.http")


async def _reconcile_pass(list_open_transfers, reconcile_transfer) -> None:
    async with session_factory() as session:
        pending = await list_open_transfers(session)
    semaphore = asyncio.Semaphore(8)

    async def run_one(transfer_id, sem=semaphore) -> dict:
        async with sem, session_factory() as session:
            transfer = await transfer_repo.get_transfer(session, transfer_id)
            if transfer is None:
                return {}
            stats = await reconcile_transfer(session, settings, transfer, now_utc())
            await session.commit()
            return stats

    batches = await asyncio.gather(
        *[run_one(item.transfer_id) for item in pending], return_exceptions=True
    )
    total = {"checked": 0, "completed": 0, "failed": 0, "still_open": 0}
    for batch in batches:
        if isinstance(batch, dict):
            for key in total:
                total[key] += batch.get(key, 0)
    logger.info("reconcile pass", extra={"status": total})


async def reconcile_loop() -> None:
    from app.services.reconcile import (
        list_open_transfers,
        reconcile_transfer,
        release_reconcile_lock,
        try_reconcile_lock,
    )

    while True:
        try:
            async with session_factory() as lock_session:
                if not await try_reconcile_lock(lock_session):
                    logger.info("reconcile pass skipped: lock held by another runner")
                    await lock_session.commit()
                    continue
                await lock_session.commit()
                try:
                    await _reconcile_pass(list_open_transfers, reconcile_transfer)
                finally:
                    await release_reconcile_lock(lock_session)
                    await lock_session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("reconcile pass failed: %s", exc)
        await asyncio.sleep(timedelta(seconds=settings.reconcile_interval_sec).total_seconds())


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
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
    app.add_exception_handler(PydanticValidationError, pydantic_validation_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        request_id = new_request_id()
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        except Exception:
            access_logger.exception(
                "request failed",
                extra={"method": request.method, "path": request.url.path},
            )
            request_id_var.reset(token)
            raise
        access_logger.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
            },
        )
        request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response

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
