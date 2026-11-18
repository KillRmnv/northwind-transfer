from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pytest_check import check

from app.core import exceptions
from app.services import quotes as service

RATES = {
    ("RUB", "THB"): Decimal("0.42"),
    ("RUB", "VND"): Decimal("300"),
    ("RUB", "JPY"): Decimal("1.9"),
    ("KWD", "THB"): Decimal("113.75"),
    ("KWD", "JPY"): Decimal("515"),
}
MINOR = {"RUB": 2, "THB": 2, "VND": 0, "JPY": 0, "KWD": 3}


def tariff(**overrides) -> service.TariffData:
    data = {
        "direction_id": "RU-TH-BANK",
        "source_country": "RU",
        "source_currency": "RUB",
        "target_country": "TH",
        "target_currency": "THB",
        "payout_method": "BANK_ACCOUNT",
        "fee_percent": Decimal("1.50"),
        "fee_fixed": Decimal("0"),
        "fee_min": Decimal("50"),
        "fee_charged_in": "SOURCE",
        "min_amount": Decimal("1000"),
        "max_amount": Decimal("300000"),
        "limit_currency": "RUB",
        "min_margin_percent": Decimal("0.5"),
        "quote_ttl_sec": 900,
    }
    data.update(overrides)
    return service.TariffData(**data)


def test_target_never_underpays_when_percent_fee_above_min() -> None:
    card = tariff(
        direction_id="RU-TH-CARD",
        payout_method="CARD",
        fee_percent=Decimal("2.00"),
        fee_fixed=Decimal("30.00"),
        fee_min=Decimal("80.00"),
    )
    result = service.calculate_target(Decimal("1020.00"), card, RATES[("RUB", "THB")], MINOR)
    assert result.target_amount >= Decimal("1020.00")


def test_target_never_underpays_zero_decimal_currency() -> None:
    vn = tariff(
        direction_id="RU-VN-BANK",
        target_country="VN",
        target_currency="VND",
        fee_percent=Decimal("1.20"),
        fee_fixed=Decimal("5000"),
        fee_min=Decimal("30000"),
        fee_charged_in="TARGET",
        min_amount=Decimal("1000"),
        max_amount=Decimal("200000"),
    )
    result = service.calculate_target(Decimal("2070000"), vn, Decimal("300"), MINOR)
    assert result.target_amount >= Decimal("2070000")


def test_target_never_underpays_three_decimal_source() -> None:
    kw = tariff(
        direction_id="KW-JP-BANK",
        source_country="KW",
        source_currency="KWD",
        target_country="JP",
        target_currency="JPY",
        fee_percent=Decimal("1.50"),
        fee_fixed=Decimal("0"),
        fee_min=Decimal("300"),
        fee_charged_in="TARGET",
        min_amount=Decimal("20.000"),
        max_amount=Decimal("3000.000"),
        limit_currency="KWD",
    )
    result = service.calculate_target(Decimal("19890"), kw, RATES[("KWD", "JPY")], MINOR)
    assert result.target_amount >= Decimal("19890")


def test_target_roundtrip_for_all_directions_matches_examples() -> None:
    so = service.calculate_source(Decimal("10000.00"), tariff(), RATES[("RUB", "THB")], MINOR)
    back = service.calculate_target(so.target_amount, tariff(), RATES[("RUB", "THB")], MINOR)
    with check:
        assert back.source_amount == so.source_amount
    with check:
        assert back.target_amount == so.target_amount
    with check:
        assert back.fee_amount == so.fee_amount


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-1"), Decimal("NaN")])
def test_invalid_rates_rejected(bad: Decimal) -> None:
    now = datetime.now(timezone.utc)
    rates = {("RUB", "THB"): service.RateData(rate=bad, updated_at=now)}
    with pytest.raises(exceptions.RateUnavailableError):
        service.resolve_rate(rates, "RUB", "THB", now, 900, MINOR)


def test_stale_direct_rate_does_not_fall_back_to_cross() -> None:
    now = datetime.now(timezone.utc)
    old = datetime.fromisoformat("2020-01-01T00:00:00+00:00")
    rates = {
        ("RUB", "PHP"): service.RateData(rate=Decimal("0.70"), updated_at=old),
        ("RUB", "USD"): service.RateData(rate=Decimal("0.012"), updated_at=now),
        ("USD", "PHP"): service.RateData(rate=Decimal("58"), updated_at=now),
    }
    with pytest.raises(exceptions.RateUnavailableError):
        service.resolve_rate(rates, "RUB", "PHP", now, 900, MINOR)
