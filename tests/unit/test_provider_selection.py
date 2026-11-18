from decimal import Decimal

from pytest_check import check

from app.core.types import ProviderInfo, Window
from app.services.providers import (
    convert_amount,
    filter_availability,
    filter_direction,
    filter_limits,
    score_providers,
)


def provider(provider_id: str, **overrides) -> ProviderInfo:
    data = {
        "provider_id": provider_id,
        "currency": "USD",
        "fee_percent": Decimal("0.40"),
        "fee_fixed": Decimal("0.20"),
        "limit_min": Decimal("10"),
        "limit_max": Decimal("5000"),
        "success_rate": Decimal("0.97"),
        "p95_latency_ms": 1800,
        "enabled": True,
        "directions": frozenset({"RU-TH-BANK"}),
        "windows": (),
    }
    data.update(overrides)
    return ProviderInfo(**data)


RATES = {
    ("RUB", "USD"): Decimal("0.012"),
    ("RUB", "THB"): Decimal("0.42"),
    ("USD", "THB"): Decimal("35"),
    ("RUB", "KWD"): Decimal("0.0000037"),
    ("EUR", "KWD"): Decimal("0.33"),
    ("RUB", "EUR"): Decimal("0.011"),
}
MINOR = {"RUB": 2, "USD": 2, "THB": 2, "KWD": 3, "EUR": 2}


def test_filter_by_direction() -> None:
    alpha = provider("a")
    bravo = provider("b", directions=frozenset({"RU-JP-BANK"}))
    assert [p.provider_id for p in filter_direction([alpha, bravo], "RU-TH-BANK")] == ["a"]


def test_filter_availability_excludes_disabled_and_maintenance(now) -> None:
    inside = provider(
        "inside",
        windows=(
            Window(
                from_at=now.replace(year=2000, month=1, day=1),
                to_at=now.replace(year=2100, month=1, day=1),
            ),
        ),
    )
    disabled = provider("disabled", enabled=False)
    healthy = provider("healthy")
    kept = [p.provider_id for p in filter_availability([inside, disabled, healthy], now)]
    assert kept == ["healthy"]


def test_filter_limits_converts_to_provider_currency() -> None:
    cheap = provider("cheap", limit_min=Decimal("1"), limit_max=Decimal("5000"))
    tight = provider("tight", limit_min=Decimal("100000"), limit_max=Decimal("200000"))
    kept = filter_limits([cheap, tight], Decimal("4137"), "THB", RATES)
    assert [p.provider_id for p in kept] == ["cheap"]


def test_cross_rate_conversion() -> None:
    assert convert_amount(Decimal("1000"), "RUB", "KWD", RATES) == Decimal("0.0037")


def test_multi_hop_with_inverse_leg() -> None:
    rates = {
        ("RUB", "VND"): Decimal("300"),
        ("RUB", "EUR"): Decimal("0.011"),
    }
    converted = convert_amount(Decimal("909000"), "VND", "EUR", rates)
    assert converted is not None
    with check:
        assert converted.quantize(Decimal("0.01")) == Decimal("33.33")


def test_scoring_prefers_margin_then_success() -> None:
    rich = provider(
        "rich", fee_percent=Decimal("0.01"), fee_fixed=Decimal("0.01"), success_rate=Decimal("0.99")
    )
    poor = provider(
        "poor",
        fee_percent=Decimal("0.5"),
        fee_fixed=Decimal("0"),
        success_rate=Decimal("0.5"),
        p95_latency_ms=4000,
    )
    scored = score_providers(
        [poor, rich],
        client_fee_source=Decimal("150"),
        source_amount=Decimal("10000"),
        source_currency="RUB",
        target_amount=Decimal("4137"),
        target_currency="THB",
        rates=RATES,
        minor_units=MINOR,
        min_margin_percent=Decimal("0.5"),
    )
    assert [item.provider.provider_id for item in scored] == ["rich", "poor"]


def test_margin_floor_filters_unprofitable() -> None:
    greedy = provider("greedy", fee_percent=Decimal("50"), fee_fixed=Decimal("100"))
    scored = score_providers(
        [greedy],
        client_fee_source=Decimal("10"),
        source_amount=Decimal("10000"),
        source_currency="RUB",
        target_amount=Decimal("4137"),
        target_currency="THB",
        rates=RATES,
        minor_units=MINOR,
        min_margin_percent=Decimal("0.5"),
    )
    assert scored == []


def test_target_currency_zero_decimals_format() -> None:
    from app.core.money import format_money

    with check:
        assert format_money(Decimal("18658.4"), 0) == "18658"
    with check:
        assert format_money(Decimal("1.5"), 3) == "1.500"
