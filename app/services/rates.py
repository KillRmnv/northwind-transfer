import time
from datetime import datetime
from decimal import Decimal

import httpx

from app.core.exceptions import RateUnavailableError
from app.core.http_client import get_json
from app.core.money import quantize_rate
from app.core.parse import parse_rate_entry
from app.core.types import RateData

CACHE_TTL_SEC = 60.0

_CACHE: dict[str, tuple[float, dict[tuple[str, str], RateData]]] = {}


def _parse_rates(payload: object) -> dict[tuple[str, str], RateData]:
    """Разбор сырых курсов мока в карту с метками времени."""
    if not isinstance(payload, list):
        raise RateUnavailableError("cannot fetch rates: invalid payload")
    rates: dict[tuple[str, str], RateData] = {}
    for entry in payload:
        parsed = parse_rate_entry(entry)
        if parsed is None:
            continue
        rates[(parsed["base"], parsed["quote"])] = RateData(
            rate=parsed["rate"],
            updated_at=parsed["updated_at"],
        )
    if not rates:
        raise RateUnavailableError("cannot fetch rates: no usable entries")
    return rates


class RatesClient:
    def __init__(self, base_url: str, timeout_sec: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec

    async def fetch(self) -> dict[tuple[str, str], RateData]:
        """Карта курсов мока с TTL-кэшем на базовый URL."""
        cached = _CACHE.get(self._base_url)
        if cached is not None and time.monotonic() - cached[0] < CACHE_TTL_SEC:
            return cached[1]
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            status_code, payload = await get_json(client, "GET", f"{self._base_url}/rates")
        if status_code is None:
            raise RateUnavailableError("cannot fetch rates: provider unreachable")
        if status_code != 200:
            raise RateUnavailableError(f"cannot fetch rates: http {status_code}")
        rates = _parse_rates(payload)
        _CACHE[self._base_url] = (time.monotonic(), rates)
        return rates


def clear_rates_cache() -> None:
    """Сброс кэша курсов: тесты и смена базового URL."""
    _CACHE.clear()


RATE_STALE_AFTER_HOURS = 6


def filter_fresh_rates(
    rates: dict[tuple[str, str], RateData], now: datetime
) -> dict[tuple[str, str], Decimal]:
    """Только свежие пары: протухшие отбрасываются, а не подменяются."""
    limit_sec = RATE_STALE_AFTER_HOURS * 3600
    return {
        pair: data.rate
        for pair, data in rates.items()
        if is_valid_rate(data.rate) and _fresh(data, now, limit_sec)
    }


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
        if not is_valid_rate(direct.rate):
            raise RateUnavailableError(f"invalid rate {source_currency} -> {target_currency}")
        if _fresh(direct, now, limit_sec):
            return quantize_rate(direct.rate)
        raise RateUnavailableError(f"rate {source_currency} -> {target_currency} is stale")
    inverse = rates.get((target_currency, source_currency))
    if inverse is not None:
        if not is_valid_rate(inverse.rate):
            raise RateUnavailableError(f"invalid rate {target_currency} -> {source_currency}")
        if _fresh(inverse, now, limit_sec):
            return quantize_rate(Decimal("1") / inverse.rate)
    best: tuple[Decimal, datetime] | None = None
    for (base, quote), first in rates.items():
        if base != source_currency or not is_valid_rate(first.rate):
            continue
        second = rates.get((quote, target_currency))
        if second is None or not is_valid_rate(second.rate):
            continue
        if not _fresh(first, now, limit_sec) or not _fresh(second, now, limit_sec):
            continue
        freshness = min(first.updated_at, second.updated_at)
        if best is None or freshness > best[1]:
            best = (first.rate * second.rate, freshness)
    if best is None:
        raise RateUnavailableError(f"no rate {source_currency} -> {target_currency}")
    return quantize_rate(best[0])


def is_valid_rate(rate: Decimal) -> bool:
    """Курс пригоден к расчёту: конечный и положительный."""
    return rate.is_finite() and rate > 0


def _fresh(data: RateData, now: datetime, limit_sec: int) -> bool:
    """Свежесть курса: в пределах лимита и без сильного ухода в будущее."""
    age_sec = (now - data.updated_at).total_seconds()
    return -60 <= age_sec <= limit_sec
