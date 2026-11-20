from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.mappers import quote_row_to_response, quote_to_response
from app.core.clock import as_utc, now_utc
from app.core.exceptions import (
    QuoteExpiredError,
    QuoteNotFoundError,
    RateUnavailableError,
    ValidationError,
)
from app.core.money import amount_string_is_valid, ensure_storable
from app.core.parse import parse_decimal
from app.core.settings import settings
from app.core.types import Money
from app.db.engine import get_session
from app.repositories.quotes import get_quote as load_quote
from app.repositories.quotes import save_quote
from app.repositories.references import load_minor_units, load_tariffs
from app.schemas.quotes import ErrorResponse, QuoteRequest, QuoteResponse
from app.services.quotes import (
    amount_in_limit_currency,
    calculate_source,
    calculate_target,
    check_limits,
    find_direction,
    resolve_rate,
)
from app.services.rates import RatesClient

router = APIRouter(tags=["quotes"])


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
    source_country = request.source_country.strip().upper()
    source_currency = request.source_currency.strip().upper()
    target_country = request.target_country.strip().upper()
    target_currency = request.target_currency.strip().upper()
    payout_method = request.payout_method.strip()
    now = now_utc()
    minor_units = await load_minor_units(session)
    if source_currency not in minor_units or target_currency not in minor_units:
        raise ValidationError(f"unknown currency {source_currency} or {target_currency}")
    tariffs = await load_tariffs(session)
    tariff = find_direction(
        tariffs,
        source_country,
        source_currency,
        target_country,
        target_currency,
        payout_method,
    )
    amount_currency = source_currency if request.amount_type == "SOURCE" else target_currency
    if not amount_string_is_valid(request.amount, minor_units[amount_currency]):
        raise ValidationError(
            f"amount must be a string with {minor_units[amount_currency]} decimals"
        )
    amount = parse_decimal(request.amount, "amount")
    if amount <= 0:
        raise ValidationError("amount must be positive")
    rates = await RatesClient(
        settings.mock_provider_url, settings.mock_provider_timeout_sec
    ).fetch()
    rate = resolve_rate(
        rates, source_currency, target_currency, now, tariff.quote_ttl_sec, minor_units
    )
    if request.amount_type == "SOURCE":
        result = calculate_source(amount, tariff, rate, minor_units)
    else:
        result = calculate_target(amount, tariff, rate, minor_units)
    ensure_storable(
        result.source_amount,
        result.fee_amount,
        result.amount_to_convert,
        result.target_amount,
        field="quote amount",
    )
    limit_amount = amount_in_limit_currency(
        Money(result.source_amount, tariff.source_currency), tariff, rate
    )
    if limit_amount is None:
        raise RateUnavailableError(
            f"cannot convert {tariff.source_currency} to limit currency {tariff.limit_currency}"
        )
    check_limits(limit_amount, tariff, minor_units)
    expires_at = now + timedelta(seconds=tariff.quote_ttl_sec)
    quote_id = await save_quote(session, result, expires_at, now)
    response.headers["Location"] = f"/quotes/{quote_id}"
    return quote_to_response(str(quote_id), result, minor_units, expires_at)


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
    if as_utc(row.expires_at) <= now_utc():
        raise QuoteExpiredError(str(quote_id))
    minor_units = await load_minor_units(session)
    return quote_row_to_response(row, minor_units)
