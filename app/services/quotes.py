from datetime import datetime
from decimal import ROUND_CEILING, Decimal

from app.core.exceptions import (
    AmountAboveMaxError,
    AmountBelowMinError,
    RateUnavailableError,
    UnsupportedDirectionError,
)
from app.core.money import format_money, round_money
from app.core.types import QuoteResult, RateData, TariffData

__all__ = [
    "QuoteResult",
    "RateData",
    "TariffData",
]

RATE_DISPLAY_PLACES = 4
RATE_STALE_AFTER_HOURS = 6


def find_direction(
    tariffs: list[TariffData],
    source_country: str,
    source_currency: str,
    target_country: str,
    target_currency: str,
    payout_method: str,
) -> TariffData:
    for tariff in tariffs:
        if (
            tariff.source_country == source_country
            and tariff.source_currency == source_currency
            and tariff.target_country == target_country
            and tariff.target_currency == target_currency
            and tariff.payout_method == payout_method
        ):
            return tariff
    raise UnsupportedDirectionError(
        f"no direction {source_country}/{source_currency} -> "
        f"{target_country}/{target_currency} via {payout_method}"
    )


def resolve_rate(
    rates: dict[tuple[str, str], RateData],
    source_currency: str,
    target_currency: str,
    now: datetime,
    ttl_sec: int,
    minor_units: dict[str, int],
) -> Decimal:
    if source_currency == target_currency:
        return Decimal("1")
    limit_sec = min(ttl_sec, RATE_STALE_AFTER_HOURS * 3600)
    direct = rates.get((source_currency, target_currency))
    if direct is not None and _fresh(direct, now, limit_sec):
        return direct.rate
    inverse = rates.get((target_currency, source_currency))
    if inverse is not None and inverse.rate != 0 and _fresh(inverse, now, limit_sec):
        return Decimal("1") / inverse.rate
    best: tuple[Decimal, datetime] | None = None
    for (base, quote), first in rates.items():
        if base != source_currency or first.rate == 0:
            continue
        second = rates.get((quote, target_currency))
        if second is None:
            continue
        if not _fresh(first, now, limit_sec) or not _fresh(second, now, limit_sec):
            continue
        freshness = min(first.updated_at, second.updated_at)
        if best is None or freshness > best[1]:
            best = (first.rate * second.rate, freshness)
    if best is None:
        raise RateUnavailableError(f"no rate {source_currency} -> {target_currency}")
    return best[0]


def _fresh(data: RateData, now: datetime, limit_sec: float) -> bool:
    age_sec = (now - data.updated_at).total_seconds()
    return -60 <= age_sec <= limit_sec


def _quant(value: Decimal, currency: str, minor_units: dict[str, int]) -> Decimal:
    return round_money(value, minor_units[currency])


def calculate_source(
    amount: Decimal,
    tariff: TariffData,
    rate: Decimal,
    minor_units: dict[str, int],
) -> QuoteResult:
    src, tgt = tariff.source_currency, tariff.target_currency
    charged_in_source = tariff.fee_charged_in == "SOURCE"
    fee_currency = src if charged_in_source else tgt
    breakdown = [
        f"direction {tariff.direction_id}, mode SOURCE, rate {format_rate(rate)}",
    ]
    if charged_in_source:
        fee_raw = amount * tariff.fee_percent / Decimal("100") + tariff.fee_fixed
        fee = max(_quant(fee_raw, src, minor_units), _quant(tariff.fee_min, src, minor_units))
        breakdown.append(
            f"fee = max({format_money(amount, minor_units[src])} {src} × "
            f"{tariff.fee_percent}% + {format_money(tariff.fee_fixed, minor_units[src])} {src}, "
            f"min {format_money(tariff.fee_min, minor_units[src])} {src}) = "
            f"{format_money(fee, minor_units[src])} {src}"
        )
        to_convert = _quant(amount - fee, src, minor_units)
        target = _quant(to_convert * rate, tgt, minor_units)
        breakdown.append(
            f"to_convert = {format_money(amount, minor_units[src])} − "
            f"{format_money(fee, minor_units[src])} = "
            f"{format_money(to_convert, minor_units[src])} {src}"
        )
        breakdown.append(
            f"target = {format_money(to_convert, minor_units[src])} × {format_rate(rate)} = "
            f"{format_money(target, minor_units[tgt])} {tgt}"
        )
    else:
        gross = amount * rate
        fee_raw = gross * tariff.fee_percent / Decimal("100") + tariff.fee_fixed
        fee = max(_quant(fee_raw, tgt, minor_units), _quant(tariff.fee_min, tgt, minor_units))
        to_convert = _quant(amount, src, minor_units)
        target = _quant(gross - fee, tgt, minor_units)
        breakdown.append(
            f"gross = {format_money(amount, minor_units[src])} × {format_rate(rate)} = "
            f"{format_money(gross, minor_units[tgt])} {tgt}"
        )
        breakdown.append(
            f"fee = max(gross × {tariff.fee_percent}% + "
            f"{format_money(tariff.fee_fixed, minor_units[tgt])}, "
            f"min {format_money(tariff.fee_min, minor_units[tgt])}) = "
            f"{format_money(fee, minor_units[tgt])} {tgt}"
        )
        breakdown.append(f"target = gross − fee = {format_money(target, minor_units[tgt])} {tgt}")
    return QuoteResult(
        direction_id=tariff.direction_id,
        source_amount=_quant(amount, src, minor_units),
        source_currency=src,
        fee_amount=fee,
        fee_currency=fee_currency,
        amount_to_convert=to_convert,
        rate=rate,
        target_amount=target,
        target_currency=tgt,
        breakdown=breakdown,
    )


def calculate_target(
    amount: Decimal,
    tariff: TariffData,
    rate: Decimal,
    minor_units: dict[str, int],
) -> QuoteResult:
    src, tgt = tariff.source_currency, tariff.target_currency
    charged_in_source = tariff.fee_charged_in == "SOURCE"
    breakdown = [
        f"direction {tariff.direction_id}, mode TARGET, rate {format_rate(rate)}",
    ]
    if rate == 0:
        raise RateUnavailableError(f"rate {src} -> {tgt} is zero")
    quantum_src = Decimal("1") if minor_units[src] == 0 else Decimal("1." + "0" * minor_units[src])
    if charged_in_source:
        to_convert_exact = amount / rate
        fee_guess = to_convert_exact * tariff.fee_percent / Decimal("100") + tariff.fee_fixed
        fee_min_q = _quant(tariff.fee_min, src, minor_units)
        if _quant(fee_guess, src, minor_units) >= fee_min_q:
            source_raw = (to_convert_exact + tariff.fee_fixed) / (
                Decimal("1") - tariff.fee_percent / Decimal("100")
            )
        else:
            source_raw = to_convert_exact + tariff.fee_min
        source = source_raw.quantize(quantum_src, rounding=ROUND_CEILING)
        forward = calculate_source(source, tariff, rate, minor_units)
        return QuoteResult(
            direction_id=forward.direction_id,
            source_amount=forward.source_amount,
            source_currency=forward.source_currency,
            fee_amount=forward.fee_amount,
            fee_currency=forward.fee_currency,
            amount_to_convert=forward.amount_to_convert,
            rate=forward.rate,
            target_amount=forward.target_amount,
            target_currency=forward.target_currency,
            breakdown=breakdown + forward.breakdown,
        )
    gross_exact_fee = amount * tariff.fee_percent / Decimal("100") + tariff.fee_fixed
    fee_min_q = _quant(tariff.fee_min, tgt, minor_units)
    if _quant(gross_exact_fee, tgt, minor_units) >= fee_min_q:
        gross = (amount + tariff.fee_fixed) / (Decimal("1") - tariff.fee_percent / Decimal("100"))
    else:
        gross = amount + tariff.fee_min
    source_raw = gross / rate
    source = source_raw.quantize(quantum_src, rounding=ROUND_CEILING)
    forward = calculate_source(source, tariff, rate, minor_units)
    return QuoteResult(
        direction_id=forward.direction_id,
        source_amount=forward.source_amount,
        source_currency=forward.source_currency,
        fee_amount=forward.fee_amount,
        fee_currency=forward.fee_currency,
        amount_to_convert=forward.amount_to_convert,
        rate=forward.rate,
        target_amount=forward.target_amount,
        target_currency=forward.target_currency,
        breakdown=breakdown + forward.breakdown,
    )


def check_limits(
    amount_in_limit_currency: Decimal,
    tariff: TariffData,
    minor_units: dict[str, int],
) -> None:
    if amount_in_limit_currency < tariff.min_amount:
        min_formatted = format_money(tariff.min_amount, minor_units[tariff.limit_currency])
        raise AmountBelowMinError(
            f"amount below min {min_formatted} {tariff.limit_currency}",
            details={"min": min_formatted},
        )
    if amount_in_limit_currency > tariff.max_amount:
        max_formatted = format_money(tariff.max_amount, minor_units[tariff.limit_currency])
        raise AmountAboveMaxError(
            f"amount above max {max_formatted} {tariff.limit_currency}",
            details={"max": max_formatted},
        )


def amount_in_limit_currency(
    amount: Decimal,
    amount_currency: str,
    tariff: TariffData,
    rate: Decimal,
) -> Decimal:
    if amount_currency == tariff.limit_currency:
        return amount
    if (
        tariff.limit_currency == tariff.source_currency
        and amount_currency == tariff.target_currency
    ):
        return amount / rate if rate != 0 else amount
    if (
        tariff.limit_currency == tariff.target_currency
        and amount_currency == tariff.source_currency
    ):
        return amount * rate
    return amount


def format_rate(rate: Decimal) -> str:
    return f"{rate:.{RATE_DISPLAY_PLACES}f}"
