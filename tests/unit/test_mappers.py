from datetime import datetime, timezone
from decimal import Decimal

from pytest_check import check

from app.api import mappers
from app.core.types import QuoteResult
from app.schemas.quotes import Money

MINOR = {"RUB": 2, "THB": 2, "JPY": 0, "KWD": 3}


def test_quote_to_response_formats_by_minor_units() -> None:
    result = QuoteResult(
        direction_id="RU-TH-BANK",
        source_amount=Decimal("10000.00"),
        source_currency="RUB",
        fee_amount=Decimal("150.00"),
        fee_currency="RUB",
        amount_to_convert=Decimal("9850.00"),
        rate=Decimal("0.42000000"),
        target_amount=Decimal("4137.00"),
        target_currency="THB",
        breakdown=["step"],
    )
    response = mappers.quote_to_response(
        "q-1", result, MINOR, datetime(2026, 11, 5, tzinfo=timezone.utc)
    )
    with check:
        assert response.source_amount == Money(amount="10000.00", currency="RUB")
    with check:
        assert response.target_amount == Money(amount="4137.00", currency="THB")
    with check:
        assert response.rate == "0.42000000"
    with check:
        assert response.breakdown == ["step"]


def test_quote_to_response_keeps_zero_decimals() -> None:
    result = QuoteResult(
        direction_id="RU-JP-BANK",
        source_amount=Decimal("10000.00"),
        source_currency="RUB",
        fee_amount=Decimal("180.00"),
        fee_currency="RUB",
        amount_to_convert=Decimal("9820.00"),
        rate=Decimal("1.9"),
        target_amount=Decimal("18658"),
        target_currency="JPY",
    )
    response = mappers.quote_to_response(
        "q-2", result, MINOR, datetime(2026, 11, 5, tzinfo=timezone.utc)
    )
    assert response.target_amount.amount == "18658"


def test_money_formats_three_decimals() -> None:
    assert mappers.money(Decimal("1"), "KWD", MINOR).amount == "1.000"


def test_balances_to_response_keeps_available_and_reserved() -> None:
    class Row:
        available = Decimal("490000.00")
        reserved = Decimal("10000.00")
        spent = Decimal("0")

    response = mappers.balances_to_response("c_001", {"RUB": Row()}, MINOR)
    with check:
        assert response.balances["RUB"].available == "490000.00"
    with check:
        assert response.balances["RUB"].reserved == "10000.00"
