from decimal import Decimal

from app.core.exceptions import ValidationError
from app.core.money import format_money, format_rate
from app.core.types import QuoteResult
from app.models.tables import Quote, Transfer
from app.schemas.balances import BalanceResponse, CurrencyBalance
from app.schemas.quotes import Money, QuoteResponse
from app.schemas.transfers import Attempt, HistoryEntry, TransferResponse


def units_for(minor_units: dict[str, int], currency: str) -> int:
    if currency not in minor_units:
        raise ValidationError(f"unknown currency {currency}")
    return minor_units[currency]


def money(amount: Decimal, currency: str, minor_units: dict[str, int]) -> Money:
    return Money(amount=format_money(amount, units_for(minor_units, currency)), currency=currency)


def quote_to_response(
    quote_id: str,
    result: QuoteResult,
    minor_units: dict[str, int],
    expires_at,
    breakdown: list[str] | None = None,
) -> QuoteResponse:
    return QuoteResponse(
        quote_id=quote_id,
        direction_id=result.direction_id,
        source_amount=money(result.source_amount, result.source_currency, minor_units),
        fee=money(result.fee_amount, result.fee_currency, minor_units),
        amount_to_convert=money(result.amount_to_convert, result.source_currency, minor_units),
        rate=format_rate(result.rate),
        target_amount=money(result.target_amount, result.target_currency, minor_units),
        breakdown=result.breakdown if breakdown is None else breakdown,
        expires_at=expires_at,
    )


def quote_row_to_response(quote: Quote, minor_units: dict[str, int]) -> QuoteResponse:
    return QuoteResponse(
        quote_id=str(quote.quote_id),
        direction_id=quote.direction_id,
        source_amount=money(quote.source_amount, quote.source_currency, minor_units),
        fee=money(quote.fee_amount, quote.fee_currency, minor_units),
        amount_to_convert=money(quote.amount_to_convert, quote.source_currency, minor_units),
        rate=format_rate(Decimal(quote.rate)),
        target_amount=money(quote.target_amount, quote.target_currency, minor_units),
        breakdown=list(quote.breakdown or []),
        expires_at=quote.expires_at,
    )


def transfer_to_response(
    transfer: Transfer,
    attempts: list,
    history: list[tuple[str, object, str]],
    minor_units: dict[str, int],
) -> TransferResponse:
    provider_id = next((a.provider_id for a in reversed(attempts) if a.payout_id), None)
    return TransferResponse(
        transfer_id=str(transfer.transfer_id),
        quote_id=str(transfer.quote_id),
        client_id=transfer.client_id,
        status=transfer.status,
        source_amount=money(
            transfer.source_amount, transfer.source_currency, minor_units
        ).model_dump(),
        target_amount=money(
            transfer.target_amount, transfer.target_currency, minor_units
        ).model_dump(),
        provider_id=provider_id,
        attempts=[
            Attempt(
                provider_id=a.provider_id,
                payout_id=a.payout_id,
                status=a.status,
                failure_code=a.failure_code,
                attempt_no=a.attempt_no,
                created_at=a.created_at,
            )
            for a in attempts
        ],
        history=[
            HistoryEntry(status=status, at=moment, actor=actor) for status, moment, actor in history
        ],
        created_at=transfer.created_at,
        updated_at=transfer.updated_at,
    )


def balances_to_response(
    client_id: str, rows: dict, minor_units: dict[str, int]
) -> BalanceResponse:
    return BalanceResponse(
        client_id=client_id,
        balances={
            currency: CurrencyBalance(
                currency=currency,
                available=format_money(row.available, units_for(minor_units, currency)),
                reserved=format_money(row.reserved, units_for(minor_units, currency)),
                spent=format_money(row.spent, units_for(minor_units, currency)),
            )
            for currency, row in rows.items()
        },
    )
