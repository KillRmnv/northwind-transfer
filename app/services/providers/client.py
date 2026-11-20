import httpx

from app.core.http_client import get_json
from app.core.types import (
    Accepted,
    CreateOutcome,
    Declined,
    PayoutState,
    ProbeOutcome,
    UnknownResult,
)


def _classify_create(status_code: int | None, payload: object) -> CreateOutcome:
    """Ответ мока в исход попытки: принять, отказать или разбираться дальше."""
    if status_code is None:
        return UnknownResult(reason="transport error")
    if status_code == 201 and isinstance(payload, dict) and payload.get("payout_id"):
        return Accepted(
            payout_id=str(payload["payout_id"]),
            status=str(payload.get("status", "PROCESSING")),
        )
    if status_code == 201:
        return UnknownResult(reason="http 201 without payout_id")
    if status_code == 422 and isinstance(payload, dict):
        return Declined(failure_code=str(payload.get("code", "DECLINED")))
    if status_code == 503:
        return Declined(failure_code="PROVIDER_UNAVAILABLE")
    if status_code is not None and 500 <= status_code < 600:
        return UnknownResult(reason=f"http {status_code}")
    if status_code == 409:
        return UnknownResult(reason="idempotency conflict at provider")
    if isinstance(payload, dict):
        return UnknownResult(reason=str(payload.get("code", f"http {status_code}")))
    return UnknownResult(reason=f"http {status_code}")


class ProviderClient:
    def __init__(self, base_url: str, timeout_sec: float, scenario: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec
        self._scenario = scenario

    def _client(self) -> httpx.AsyncClient:
        """HTTP-клиент с таймаутом провайдера."""
        return httpx.AsyncClient(timeout=self._timeout)

    async def create_payout(
        self,
        provider_id: str,
        reference: str,
        amount: str,
        currency: str,
        recipient: dict,
        callback_url: str,
        idempotency_key: str,
    ) -> CreateOutcome:
        """Создаёт выплату и классифицирует ответ мока в исход попытки."""
        headers = {"Idempotency-Key": idempotency_key}
        if self._scenario is not None:
            headers["X-Scenario"] = self._scenario
        body = {
            "reference": reference,
            "amount": amount,
            "currency": currency,
            "recipient": recipient,
            "callback_url": callback_url,
        }
        async with self._client() as client:
            status_code, payload = await get_json(
                client,
                "POST",
                f"{self._base_url}/{provider_id}/payouts",
                json=body,
                headers=headers,
            )
        return _classify_create(status_code, payload)

    async def get_payout(self, provider_id: str, payout_id: str) -> PayoutState | None:
        """Статус выплаты по payout_id; None — спросить иначе."""
        async with self._client() as client:
            status_code, payload = await get_json(
                client, "GET", f"{self._base_url}/{provider_id}/payouts/{payout_id}"
            )
        if status_code != 200 or not isinstance(payload, dict):
            return None
        return _parse_state(payload)

    async def find_payout(self, provider_id: str, reference: str) -> PayoutState | None:
        """Поиск выплаты по reference; None — записи нет."""
        outcome, state = await self.probe_payout(provider_id, reference)
        return state if outcome is ProbeOutcome.FOUND else None

    async def probe_payout(
        self, provider_id: str, reference: str
    ) -> tuple[ProbeOutcome, PayoutState | None]:
        """Зонд записи: найдена, точно нет или провайдер нечитаем."""
        async with self._client() as client:
            status_code, payload = await get_json(
                client,
                "GET",
                f"{self._base_url}/{provider_id}/payouts",
                params={"reference": reference},
            )
        if status_code is None:
            return ProbeOutcome.ERROR, None
        if status_code == 404:
            return ProbeOutcome.MISSING, None
        if status_code != 200 or not isinstance(payload, dict):
            return ProbeOutcome.ERROR, None
        return ProbeOutcome.FOUND, _parse_state(payload)


ALLOWED_PAYOUT_STATUSES = frozenset({"PROCESSING", "COMPLETED", "FAILED"})


async def confirm_payout(
    client: ProviderClient, provider_id: str, payout_id: str, reference: str
) -> PayoutState | None:
    """Подтверждение выплаты: прямое чтение, затем поиск по reference."""
    state = await client.get_payout(provider_id, payout_id)
    if state is None:
        state = await client.find_payout(provider_id, reference)
    if state is not None and state.reference and state.reference != reference:
        return None
    return state


def _parse_state(payload: dict) -> PayoutState:
    """Разбор статуса мока; неизвестный статус считается PROCESSING."""
    status = str(payload.get("status", "PROCESSING"))
    if status not in ALLOWED_PAYOUT_STATUSES:
        status = "PROCESSING"
    return PayoutState(
        payout_id=str(payload.get("payout_id", "")),
        reference=str(payload.get("reference", "")),
        status=status,
        failure_code=payload.get("failure_code"),
    )
