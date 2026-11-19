from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.mappers import balances_to_response, transfer_to_response
from app.core.clock import now_utc
from app.core.exceptions import TransferNotFoundError, ValidationError
from app.core.settings import settings
from app.db.engine import get_session
from app.models.tables import Client
from app.repositories import balances as balance_repo
from app.repositories import transfers as transfer_repo
from app.repositories.references import load_minor_units
from app.schemas.balances import BalanceResponse
from app.schemas.quotes import ErrorResponse
from app.schemas.transfers import TransferRequest, TransferResponse
from app.services import transfers as transfer_service

router = APIRouter(tags=["transfers"])


async def build_history(session: AsyncSession, transfer) -> list[tuple]:
    items = [("PENDING", transfer.created_at, "client")]
    attempts = await transfer_repo.latest_attempts(session, transfer.transfer_id)
    for attempt in attempts:
        items.append((attempt.status, attempt.created_at, f"provider:{attempt.provider_id}"))
    events = await transfer_repo.webhook_events_for_transfer(session, transfer.transfer_id.hex)
    for event in events:
        items.append((event.status, event.received_at, f"provider:{event.provider_id}"))
    items.append((transfer.status, transfer.updated_at, "service"))
    return items


def require_idempotency_key(value: str | None) -> str:
    key = (value or "").strip()
    if not key or len(key) > 128:
        raise ValidationError("Idempotency-Key must be 1..128 non-blank characters")
    return key


async def load_transfer_view(session: AsyncSession, transfer_id: UUID) -> TransferResponse:
    transfer = await transfer_repo.get_transfer(session, transfer_id)
    if transfer is None:
        raise TransferNotFoundError(str(transfer_id))
    minor_units = await load_minor_units(session)
    history = await build_history(session, transfer)
    attempts = await transfer_repo.latest_attempts(session, transfer.transfer_id)
    return transfer_to_response(transfer, attempts, history, minor_units)


def respond_with_outcome(response: Response, transfer_id: UUID, final_status: str) -> None:
    if final_status == "COMPLETED":
        response.status_code = status.HTTP_201_CREATED
    else:
        response.status_code = status.HTTP_202_ACCEPTED
    response.headers["Location"] = f"/transfers/{transfer_id}"


@router.post(
    "/transfers",
    response_model=TransferResponse,
    summary="Создать перевод",
    responses={
        201: {"description": "Перевод завершён синхронно"},
        202: {"description": "Перевод принят, итог позже"},
        404: {"model": ErrorResponse, "description": "QUOTE_NOT_FOUND"},
        409: {"model": ErrorResponse, "description": "INSUFFICIENT_FUNDS, DAILY_LIMIT_EXCEEDED"},
        410: {"model": ErrorResponse, "description": "QUOTE_EXPIRED"},
        422: {"model": ErrorResponse, "description": "VALIDATION_ERROR, IDEMPOTENCY_CONFLICT"},
    },
)
async def create_transfer(
    request: TransferRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TransferResponse:
    key = require_idempotency_key(idempotency_key)
    now = now_utc()
    transfer, _ = await transfer_service.create_transfer(
        session,
        settings,
        request.quote_id,
        request.client_id,
        request.recipient.model_dump(),
        key,
        now,
    )
    final_status = await transfer_service.run_attempts(session, settings, transfer.transfer_id, now)
    respond_with_outcome(response, transfer.transfer_id, final_status)
    return await load_transfer_view(session, transfer.transfer_id)


@router.get(
    "/transfers/{transfer_id}",
    response_model=TransferResponse,
    summary="Статус перевода",
    responses={
        200: {"description": "Перевод"},
        404: {"model": ErrorResponse, "description": "TRANSFER_NOT_FOUND"},
    },
)
async def read_transfer(
    transfer_id: UUID, session: AsyncSession = Depends(get_session)
) -> TransferResponse:
    return await load_transfer_view(session, transfer_id)


@router.get(
    "/clients/{client_id}/balance",
    response_model=BalanceResponse,
    summary="Баланс клиента",
)
async def read_balance(
    client_id: str, session: AsyncSession = Depends(get_session)
) -> BalanceResponse:
    client = await session.get(Client, client_id)
    if client is None:
        raise ValidationError(f"unknown client {client_id}")
    rows = await balance_repo.get_balances(session, client_id)
    minor_units = await load_minor_units(session)
    return balances_to_response(client_id, rows, minor_units)
