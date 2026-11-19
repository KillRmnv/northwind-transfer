from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now_utc
from app.core.settings import settings
from app.db.engine import get_session
from app.schemas.quotes import ErrorResponse
from app.services import webhooks as webhook_service

router = APIRouter(tags=["webhooks"])


@router.post(
    "/webhooks/{provider_id}",
    summary="Webhook провайдера",
    description="Принимает события COMPLETED/PROCESSING/FAILED. Требуется подпись тела секретом.",
    responses={
        200: {"description": "Событие принято (включая дубли и неизвестные reference)"},
        401: {
            "model": ErrorResponse,
            "description": "UNAUTHORIZED — неверная или отсутствующая подпись",
        },
        422: {
            "model": ErrorResponse,
            "description": "VALIDATION_ERROR — битое тело, payout_id или статус",
        },
        503: {
            "model": ErrorResponse,
            "description": "SERVICE_UNAVAILABLE — секрет вебхуков не настроен",
        },
    },
)
async def receive_webhook(
    provider_id: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    signature: str | None = Header(default=None, alias="X-Signature"),
) -> dict:
    body = await request.body()
    await webhook_service.handle_webhook(session, settings, provider_id, body, signature, now_utc())
    response.status_code = 200
    return {"status": "ok"}
