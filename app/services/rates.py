import time

import httpx

from app.core.exceptions import RateUnavailableError
from app.core.http_client import get_json
from app.core.parse import parse_rate_entry
from app.services.quotes import RateData

CACHE_TTL_SEC = 60.0

_CACHE: dict[str, tuple[float, dict[tuple[str, str], RateData]]] = {}


def _parse_rates(payload: object) -> dict[tuple[str, str], RateData]:
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
    _CACHE.clear()
