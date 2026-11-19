import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pytest_check import check

from app.core import exceptions
from app.core.money import format_money
from app.services import quotes as service
from app.services.quotes import RateData, TariffData

DATA = Path("data")


def load_tariffs() -> list[TariffData]:
    out = []
    for entry in json.loads((DATA / "tariffs.json").read_text(encoding="utf-8")):
        out.append(
            TariffData(
                direction_id=entry["direction_id"],
                source_country=entry["source"]["country"],
                source_currency=entry["source"]["currency"],
                target_country=entry["target"]["country"],
                target_currency=entry["target"]["currency"],
                payout_method=entry["payout_method"],
                fee_percent=Decimal(entry["fee"]["percent"]),
                fee_fixed=Decimal(entry["fee"]["fixed"]),
                fee_min=Decimal(entry["fee"]["min"]),
                fee_charged_in=entry["fee"]["charged_in"],
                min_amount=Decimal(entry["amount_limits"]["min"]),
                max_amount=Decimal(entry["amount_limits"]["max"]),
                limit_currency=entry["amount_limits"]["currency"],
                min_margin_percent=Decimal(entry["min_margin_percent"]),
                quote_ttl_sec=entry["quote_ttl_sec"],
            )
        )
    return out


def load_minor() -> dict[str, int]:
    return {
        entry["code"]: entry["minor_units"]
        for entry in json.loads((DATA / "currencies.json").read_text(encoding="utf-8"))
    }


def load_rates(now: datetime) -> dict[tuple[str, str], RateData]:
    out = {}
    for entry in json.loads((DATA / "rates.json").read_text(encoding="utf-8")):
        out[(entry["base"], entry["quote"])] = RateData(
            rate=Decimal(entry["rate"]),
            updated_at=datetime.fromisoformat(entry["updated_at"].replace("Z", "+00:00")),
        )
    return out


TARIFFS = load_tariffs()
MINOR = load_minor()


def tariff(direction_id: str) -> TariffData:
    return next(t for t in TARIFFS if t.direction_id == direction_id)


def check_result(result: service.QuoteResult, expected: dict, minor: dict[str, int]) -> None:
    with check:
        assert result.direction_id == expected["direction_id"]
    with check:
        assert (
            format_money(result.source_amount, minor[result.source_currency])
            == expected["source_amount"]["amount"]
        )
    with check:
        assert result.source_currency == expected["source_amount"]["currency"]
    with check:
        assert (
            format_money(result.fee_amount, minor[result.fee_currency]) == expected["fee"]["amount"]
        )
    with check:
        assert result.fee_currency == expected["fee"]["currency"]
    with check:
        assert (
            format_money(result.amount_to_convert, minor[result.source_currency])
            == expected["amount_to_convert"]["amount"]
        )
    with check:
        assert f"{result.rate:.4f}" == expected["rate"]
    with check:
        assert (
            format_money(result.target_amount, minor[result.target_currency])
            == expected["target_amount"]["amount"]
        )
    with check:
        assert result.target_currency == expected["target_amount"]["currency"]


POSITIVE_CASES = [
    "01_source_percent_fee.json",
    "02_source_min_fee.json",
    "03_target_mode.json",
    "07_percent_plus_fixed.json",
    "08_zero_decimal_target.json",
    "09_three_decimal_source.json",
    "10_fee_charged_in_target.json",
]


@pytest.mark.parametrize("filename", POSITIVE_CASES)
def test_examples(filename: str, now: datetime) -> None:
    case = json.loads((Path("examples") / filename).read_text(encoding="utf-8"))
    request, expected = case["request"], case["response"]["body"]
    direction = service.find_direction(
        TARIFFS,
        request["source_country"],
        request["source_currency"],
        request["target_country"],
        request["target_currency"],
        request["payout_method"],
    )
    rate = service.resolve_rate(
        load_rates(now),
        request["source_currency"],
        request["target_currency"],
        now,
        10**9,
        MINOR,
    )
    amount = Decimal(request["amount"])
    if request["amount_type"] == "SOURCE":
        result = service.calculate_source(amount, direction, rate, MINOR)
    else:
        result = service.calculate_target(amount, direction, rate, MINOR)
    check_result(result, expected, MINOR)


def test_target_source_roundtrip(now: datetime) -> None:
    direction = tariff("RU-TH-BANK")
    rates = load_rates(now)
    rate = service.resolve_rate(rates, "RUB", "THB", now, 10**9, MINOR)
    first = service.calculate_source(Decimal("10000.00"), direction, rate, MINOR)
    there = service.calculate_target(first.target_amount, direction, rate, MINOR)
    with check:
        assert there.source_amount == first.source_amount
    with check:
        assert there.fee_amount == first.fee_amount
    with check:
        assert there.target_amount == first.target_amount


@pytest.mark.parametrize("source_amount", ["1000.00", "300000.00"])
def test_target_source_roundtrip_at_limits(source_amount: str, now: datetime) -> None:
    direction = tariff("RU-TH-BANK")
    rates = load_rates(now)
    rate = service.resolve_rate(rates, "RUB", "THB", now, 10**9, MINOR)
    first = service.calculate_source(Decimal(source_amount), direction, rate, MINOR)
    there = service.calculate_target(first.target_amount, direction, rate, MINOR)
    with check:
        assert there.target_amount >= first.target_amount
    with check:
        assert there.source_amount >= first.source_amount


@pytest.mark.parametrize(
    ("amount", "code"),
    [
        ("500.00", "AMOUNT_BELOW_MIN"),
        ("500000.00", "AMOUNT_ABOVE_MAX"),
    ],
)
def test_amount_limits(amount: str, code: str, now: datetime) -> None:
    direction = tariff("RU-TH-BANK")
    rates = load_rates(now)
    rate = service.resolve_rate(rates, "RUB", "THB", now, 10**9, MINOR)
    with pytest.raises(getattr(exceptions, _code_to_error(code))) as exc_info:
        service.check_limits(
            service.amount_in_limit_currency(Decimal(amount), "RUB", direction, rate),
            direction,
            MINOR,
        )
    assert exc_info.value.code == code


def _code_to_error(code: str) -> str:
    return {
        "AMOUNT_BELOW_MIN": "AmountBelowMinError",
        "AMOUNT_ABOVE_MAX": "AmountAboveMaxError",
    }[code]


def test_error_codes_mapping() -> None:
    mapping = {
        "ValidationError": "VALIDATION_ERROR",
        "UnsupportedDirectionError": "UNSUPPORTED_DIRECTION",
        "AmountBelowMinError": "AMOUNT_BELOW_MIN",
        "AmountAboveMaxError": "AMOUNT_ABOVE_MAX",
        "RateUnavailableError": "RATE_UNAVAILABLE",
        "QuoteNotFoundError": "QUOTE_NOT_FOUND",
        "QuoteExpiredError": "QUOTE_EXPIRED",
        "InsufficientFundsError": "INSUFFICIENT_FUNDS",
        "DailyLimitExceededError": "DAILY_LIMIT_EXCEEDED",
        "IdempotencyConflictError": "IDEMPOTENCY_CONFLICT",
    }
    for name, code in mapping.items():
        cls = getattr(exceptions, name)
        with check:
            assert cls.code == code
        with check:
            assert issubclass(cls, exceptions.DomainError)


def test_unsupported_direction() -> None:
    with pytest.raises(exceptions.UnsupportedDirectionError) as exc_info:
        service.find_direction(TARIFFS, "RU", "RUB", "TH", "THB", "CRYPTO")
    assert exc_info.value.code == "UNSUPPORTED_DIRECTION"


def test_rate_unavailable(now: datetime) -> None:
    with pytest.raises(exceptions.RateUnavailableError) as exc_info:
        service.resolve_rate(load_rates(now), "KZT", "THB", now, 10**9, MINOR)
    assert exc_info.value.code == "RATE_UNAVAILABLE"


def test_stale_rate_rejected(now: datetime) -> None:
    rates = load_rates(now)
    with pytest.raises(exceptions.RateUnavailableError):
        service.resolve_rate(rates, "RUB", "AED", now, 10**9, MINOR)


def test_minor_units_formatting() -> None:
    with check:
        assert format_money(Decimal("18658"), 0) == "18658"
    with check:
        assert format_money(Decimal("5573.75"), 2) == "5573.75"
    with check:
        assert format_money(Decimal("1"), 3) == "1.000"
