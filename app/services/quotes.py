from datetime import datetime
from decimal import Decimal

from app.core.exceptions import (
    AmountAboveMaxError,
    AmountBelowMinError,
    RateUnavailableError,
    UnsupportedDirectionError,
)
from app.core.money import (
    ceil_to_minor,
    format_money,
    format_rate,
    quantize_rate,
    quantum,
    round_money,
)
from app.core.types import Money, QuoteResult, RateData, TariffData

__all__ = [
    "QuoteResult",
    "RateData",
    "TariffData",
]

RATE_STALE_AFTER_HOURS = 6


def filter_fresh_rates(
    rates: dict[tuple[str, str], RateData], now: datetime
) -> dict[tuple[str, str], Decimal]:
    """Только свежие пары: протухшие отбрасываются, а не подменяются."""
    limit_sec = RATE_STALE_AFTER_HOURS * 3600
    return {
        pair: data.rate
        for pair, data in rates.items()
        if _valid_rate(data.rate) and _fresh(data, now, limit_sec)
    }


def find_direction(
    tariffs: list[TariffData],
    source_country: str,
    source_currency: str,
    target_country: str,
    target_currency: str,
    payout_method: str,
) -> TariffData:
    """Направление по странам, валютам и способу выплаты."""
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


def find_tariff(tariffs: list[TariffData], direction_id: str) -> TariffData:
    """Тариф по id направления."""
    for tariff in tariffs:
        if tariff.direction_id == direction_id:
            return tariff
    raise UnsupportedDirectionError(f"unknown direction {direction_id}")


def resolve_rate(
    rates: dict[tuple[str, str], RateData],
    source_currency: str,
    target_currency: str,
    now: datetime,
    ttl_sec: int,
    minor_units: dict[str, int],
) -> Decimal:
    """Курс: прямой, обратный или кросс; протухший — RATE_UNAVAILABLE."""
    if source_currency == target_currency:
        return Decimal("1")
    limit_sec = min(ttl_sec, RATE_STALE_AFTER_HOURS * 3600)
    direct = rates.get((source_currency, target_currency))
    if direct is not None:
        if not _valid_rate(direct.rate):
            raise RateUnavailableError(f"invalid rate {source_currency} -> {target_currency}")
        if _fresh(direct, now, limit_sec):
            return quantize_rate(direct.rate)
        raise RateUnavailableError(f"rate {source_currency} -> {target_currency} is stale")
    inverse = rates.get((target_currency, source_currency))
    if inverse is not None:
        if not _valid_rate(inverse.rate):
            raise RateUnavailableError(f"invalid rate {target_currency} -> {source_currency}")
        if _fresh(inverse, now, limit_sec):
            return quantize_rate(Decimal("1") / inverse.rate)
    best: tuple[Decimal, datetime] | None = None
    for (base, quote), first in rates.items():
        if base != source_currency or not _valid_rate(first.rate):
            continue
        second = rates.get((quote, target_currency))
        if second is None or not _valid_rate(second.rate):
            continue
        if not _fresh(first, now, limit_sec) or not _fresh(second, now, limit_sec):
            continue
        freshness = min(first.updated_at, second.updated_at)
        if best is None or freshness > best[1]:
            best = (first.rate * second.rate, freshness)
    if best is None:
        raise RateUnavailableError(f"no rate {source_currency} -> {target_currency}")
    return quantize_rate(best[0])


def _valid_rate(rate: Decimal) -> bool:
    """Курс пригоден к расчёту: конечный и положительный."""
    return rate.is_finite() and rate > 0


def _fresh(data: RateData, now: datetime, limit_sec: int) -> bool:
    """Свежесть курса: в пределах лимита и без сильного ухода в будущее."""
    age_sec = (now - data.updated_at).total_seconds()
    return -60 <= age_sec <= limit_sec


def _quant(value: Decimal, currency: str, minor_units: dict[str, int]) -> Decimal:
    """Округление суммы по minor_units валюты."""
    return round_money(value, minor_units[currency])


class Breakdown:
    """Трассировка расчёта: шаги строкой для аудита в ответе."""

    def __init__(self) -> None:
        self._lines: list[str] = []

    def add(self, text: str) -> "Breakdown":
        """Шаг трассировки; возвращает себя для цепочки."""
        self._lines.append(text)
        return self

    def extend(self, lines: list[str]) -> "Breakdown":
        """Чужие шаги в конец; возвращает себя для цепочки."""
        self._lines.extend(lines)
        return self

    def build(self) -> list[str]:
        """Готовая трассировка копией: билдер можно переиспользовать."""
        return list(self._lines)


def calculate_source(
    amount: Decimal,
    tariff: TariffData,
    rate: Decimal,
    minor_units: dict[str, int],
) -> QuoteResult:
    """Расчёт от суммы отправки: комиссия, конвертация, получение."""
    src, tgt = tariff.source_currency, tariff.target_currency
    charged_in_source = tariff.fee_charged_in == "SOURCE"
    fee_currency = src if charged_in_source else tgt
    trace = Breakdown().add(
        f"direction {tariff.direction_id}, mode SOURCE, rate {format_rate(rate)}"
    )
    if charged_in_source:
        fee_raw = amount * tariff.fee_percent / Decimal("100") + tariff.fee_fixed
        fee = max(_quant(fee_raw, src, minor_units), _quant(tariff.fee_min, src, minor_units))
        trace.add(
            f"fee = max({format_money(amount, minor_units[src])} {src} × "
            f"{tariff.fee_percent}% + {format_money(tariff.fee_fixed, minor_units[src])} {src}, "
            f"min {format_money(tariff.fee_min, minor_units[src])} {src}) = "
            f"{format_money(fee, minor_units[src])} {src}"
        )
        to_convert = _quant(amount - fee, src, minor_units)
        target = _quant(to_convert * rate, tgt, minor_units)
        trace.add(
            f"to_convert = {format_money(amount, minor_units[src])} − "
            f"{format_money(fee, minor_units[src])} = "
            f"{format_money(to_convert, minor_units[src])} {src}"
        )
        trace.add(
            f"target = {format_money(to_convert, minor_units[src])} × {format_rate(rate)} = "
            f"{format_money(target, minor_units[tgt])} {tgt}"
        )
    else:
        gross = amount * rate
        fee_raw = gross * tariff.fee_percent / Decimal("100") + tariff.fee_fixed
        fee = max(_quant(fee_raw, tgt, minor_units), _quant(tariff.fee_min, tgt, minor_units))
        to_convert = _quant(amount, src, minor_units)
        target = _quant(gross - fee, tgt, minor_units)
        trace.add(
            f"gross = {format_money(amount, minor_units[src])} × {format_rate(rate)} = "
            f"{format_money(gross, minor_units[tgt])} {tgt}"
        )
        trace.add(
            f"fee = max(gross × {tariff.fee_percent}% + "
            f"{format_money(tariff.fee_fixed, minor_units[tgt])}, "
            f"min {format_money(tariff.fee_min, minor_units[tgt])}) = "
            f"{format_money(fee, minor_units[tgt])} {tgt}"
        )
        trace.add(f"target = gross − fee = {format_money(target, minor_units[tgt])} {tgt}")
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
        breakdown=trace.build(),
    )


def _converge_forward(
    source: Decimal,
    tariff: TariffData,
    rate: Decimal,
    minor_units: dict[str, int],
    target_amount: Decimal,
    trace: Breakdown,
) -> QuoteResult:
    """Дотягивание суммы прямым пересчётом до целевой, максимум 10 шагов."""
    forward = calculate_source(source, tariff, rate, minor_units)
    for _ in range(10):
        if forward.target_amount >= target_amount:
            break
        source = ceil_to_minor(
            Decimal(forward.source_amount) + quantum(minor_units[tariff.source_currency]),
            minor_units[tariff.source_currency],
        )
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
        breakdown=trace.extend(forward.breakdown).build(),
    )


def calculate_target(
    amount: Decimal,
    tariff: TariffData,
    rate: Decimal,
    minor_units: dict[str, int],
) -> QuoteResult:
    """Инверсия: подбор суммы отправки под целевой результат."""
    src, tgt = tariff.source_currency, tariff.target_currency
    charged_in_source = tariff.fee_charged_in == "SOURCE"
    trace = Breakdown().add(
        f"direction {tariff.direction_id}, mode TARGET, rate {format_rate(rate)}"
    )
    if not _valid_rate(rate):
        raise RateUnavailableError(f"invalid rate {src} -> {tgt}")
    if charged_in_source:
        to_convert_exact = amount / rate
        percent_candidate = ceil_to_minor(
            (to_convert_exact + tariff.fee_fixed)
            / (Decimal("1") - tariff.fee_percent / Decimal("100")),
            minor_units[src],
        )
        raw_fee = percent_candidate * tariff.fee_percent / Decimal("100") + tariff.fee_fixed
        source = percent_candidate
        if raw_fee < tariff.fee_min:
            source = ceil_to_minor(to_convert_exact + tariff.fee_min, minor_units[src])
        return _converge_forward(source, tariff, rate, minor_units, amount, trace)
    percent_gross = (amount + tariff.fee_fixed) / (
        Decimal("1") - tariff.fee_percent / Decimal("100")
    )
    percent_source = ceil_to_minor(percent_gross / rate, minor_units[src])
    raw_gross_fee = percent_gross * tariff.fee_percent / Decimal("100") + tariff.fee_fixed
    source = percent_source
    if raw_gross_fee < tariff.fee_min:
        source = ceil_to_minor((amount + tariff.fee_min) / rate, minor_units[src])
    return _converge_forward(source, tariff, rate, minor_units, amount, trace)


def check_limits(
    amount_in_limit_currency: Decimal,
    tariff: TariffData,
    minor_units: dict[str, int],
) -> None:
    """Сумма в пределах min/max направления в валюте лимита."""
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
    amount: Money,
    tariff: TariffData,
    rate: Decimal,
) -> Decimal | None:
    """Сумма в валюте лимита; None — пересчитать нечем."""
    if amount.currency == tariff.limit_currency:
        return amount.amount
    if not _valid_rate(rate):
        return None
    if (
        tariff.limit_currency == tariff.source_currency
        and amount.currency == tariff.target_currency
    ):
        return amount.amount / rate
    if (
        tariff.limit_currency == tariff.target_currency
        and amount.currency == tariff.source_currency
    ):
        return amount.amount * rate
    return None
