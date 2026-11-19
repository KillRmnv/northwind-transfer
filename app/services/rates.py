from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.core.exceptions import RateUnavailableError
from app.core.http_client import get_json
from app.services.quotes import RateData


class RatesClient:
    def __init__(self, base_url: str, timeout_sec: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec

    async def fetch(self) -> dict[tuple[str, str], RateData]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            status_code, payload = await get_json(client, "GET", f"{self._base_url}/rates")
        if status_code is None:
            raise RateUnavailableError("cannot fetch rates: provider unreachable")
        if status_code != 200 or not isinstance(payload, list):
            raise RateUnavailableError(f"cannot fetch rates: http {status_code}")
        rates: dict[tuple[str, str], RateData] = {}
        for entry in payload:
            if not isinstance(entry, dict):
                raise RateUnavailableError("cannot fetch rates: invalid payload")
            updated_at = datetime.fromisoformat(str(entry["updated_at"]).replace("Z", "+00:00"))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            rates[(entry["base"], entry["quote"])] = RateData(
                rate=Decimal(str(entry["rate"])),
                updated_at=updated_at,
            )
        return rates
