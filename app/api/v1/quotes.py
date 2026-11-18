from datetime import timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now_utc
from app.core.exceptions import (
    QuoteNotFoundError,
    RateUnavailableError,
    ValidationError,
)
from app.core.money import amount_string_is_valid, format_money
from app.core.settings import settings
from app.db.engine import get_session
from app.repositories.quotes import get_quote as load_quote
from app.repositories.quotes import save_quote
from app.repositories.references import load_minor_units, load_tariffs
from app.schemas.quotes import ErrorResponse, Money, QuoteRequest, QuoteResponse
from app.services.quotes import (
    amount_in_limit_currency,
    calculate_source,
    calculate_target,
    check_limits,
    find_direction,
    format_rate,
    resolve_rate,
)
from app.services.rates import RatesClient

router = APIRouter(tags=["quotes"])


def _money(amount: Decimal, currency: str, minor_units: dict[str, int]) -> Money:
    return Money(amount=format_money(amount, minor_units[currency]), currency=currency)


@router.post(
    "/quotes",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать котировку",
    responses={
        201: {"description": "Котировка создана"},
        400: {
            "model": ErrorResponse,
            "description": "UNSUPPORTED_DIRECTION, AMOUNT_BELOW_MIN, AMOUNT_ABOVE_MAX",
        },
        404: {"model": ErrorResponse, "description": "QUOTE_NOT_FOUND"},
        410: {"model": ErrorResponse, "description": "QUOTE_EXPIRED"},
        422: {"model": ErrorResponse, "description": "VALIDATION_ERROR, RATE_UNAVAILABLE"},
    },
)
async def create_quote(
    request: QuoteRequest, response: Response, session: AsyncSession = Depends(get_session)
) -> QuoteResponse:
    source_currency = request.source_currency.upper()
    target_currency = request.target_currency.upper()
    now = now_utc()
    minor_units = await load_minor_units(session)
    if source_currency not in minor_units or target_currency not in minor_units:
        raise ValidationError(f"unknown currency {source_currency} or {target_currency}")
    tariffs = await load_tariffs(session)
    tariff = find_direction(
        tariffs,
        request.source_country.upper(),
        source_currency,
        request.target_country.upper(),
        target_currency,
        request.payout_method,
    )
    amount_currency = source_currency if request.amount_type == "SOURCE" else target_currency
    if not amount_string_is_valid(request.amount, minor_units[amount_currency]):
        raise ValidationError(
            f"amount must be a string with {minor_units[amount_currency]} decimals"
        )
    try:
        amount = Decimal(request.amount)
    except InvalidOperation as exc:
        raise ValidationError("amount is not a number") from exc
    if amount <= 0:
        raise ValidationError("amount must be positive")
    try:
        rates = await RatesClient(
            settings.mock_provider_url, settings.mock_provider_timeout_sec
        ).fetch()
    except httpx.HTTPError as exc:
        raise RateUnavailableError(f"cannot fetch rates: {exc}") from exc
    rate = resolve_rate(
        rates, source_currency, target_currency, now, tariff.quote_ttl_sec, minor_units
    )
    check_limits(
        amount_in_limit_currency(amount, amount_currency, tariff, rate), tariff, minor_units
    )
    if request.amount_type == "SOURCE":
        result = calculate_source(amount, tariff, rate, minor_units)
    else:
        result = calculate_target(amount, tariff, rate, minor_units)
    expires_at = now + timedelta(seconds=tariff.quote_ttl_sec)
    quote_id = await save_quote(session, result, expires_at, now)
    response.headers["Location"] = f"/quotes/{quote_id}"
    return QuoteResponse(
        quote_id=str(quote_id),
        direction_id=result.direction_id,
        source_amount=_money(result.source_amount, result.source_currency, minor_units),
        fee=_money(result.fee_amount, result.fee_currency, minor_units),
        amount_to_convert=_money(result.amount_to_convert, result.source_currency, minor_units),
        rate=format_rate(result.rate),
        target_amount=_money(result.target_amount, result.target_currency, minor_units),
        breakdown=result.breakdown,
        expires_at=expires_at,
    )


@router.get(
    "/quotes/{quote_id}",
    response_model=QuoteResponse,
    summary="Получить котировку",
    responses={
        200: {"description": "Котировка"},
        404: {"model": ErrorResponse, "description": "QUOTE_NOT_FOUND"},
    },
)
async def read_quote(quote_id: UUID, session: AsyncSession = Depends(get_session)) -> QuoteResponse:
    row = await load_quote(session, quote_id)
    if row is None:
        raise QuoteNotFoundError(str(quote_id))
    minor_units = await load_minor_units(session)
    return QuoteResponse(
        quote_id=str(row.quote_id),
        direction_id=row.direction_id,
        source_amount=_money(row.source_amount, row.source_currency, minor_units),
        fee=_money(row.fee_amount, row.fee_currency, minor_units),
        amount_to_convert=_money(row.amount_to_convert, row.source_currency, minor_units),
        rate=format_rate(row.rate),
        target_amount=_money(row.target_amount, row.target_currency, minor_units),
        breakdown=[],
        expires_at=row.expires_at,
    )
