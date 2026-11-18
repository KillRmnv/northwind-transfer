from datetime import datetime
from decimal import Decimal

import httpx

from app.core.types import (
    Accepted,
    CreateOutcome,
    Declined,
    PayoutState,
    ProviderInfo,
    ScoredProvider,
    UnknownResult,
)

W_MARGIN = 100.0
W_SUCCESS = 50.0
W_LATENCY_MS = 1000.0


def filter_direction(providers: list[ProviderInfo], direction_id: str) -> list[ProviderInfo]:
    return [p for p in providers if direction_id in p.directions]


def in_maintenance(provider: ProviderInfo, now: datetime) -> bool:
    return any(window.from_at <= now < window.to_at for window in provider.windows)


def filter_availability(providers: list[ProviderInfo], now: datetime) -> list[ProviderInfo]:
    return [p for p in providers if p.enabled and not in_maintenance(p, now)]


def convert_amount(
    amount: Decimal,
    source_currency: str,
    target_currency: str,
    rates: dict[tuple[str, str], Decimal],
    max_hops: int = 3,
) -> Decimal | None:
    if source_currency == target_currency:
        return amount
    graph: dict[str, list[tuple[str, Decimal]]] = {}
    for (base, quote), rate in rates.items():
        if rate == 0:
            continue
        graph.setdefault(base, []).append((quote, rate))
        graph.setdefault(quote, []).append((base, Decimal("1") / rate))
    frontier: list[tuple[str, Decimal, int]] = [(source_currency, Decimal("1"), 0)]
    seen = {source_currency}
    while frontier:
        currency, factor, hops = frontier.pop(0)
        if hops >= max_hops:
            continue
        for neighbour, rate in graph.get(currency, []):
            next_factor = factor * rate
            if neighbour == target_currency:
                return amount * next_factor
            if neighbour in seen:
                continue
            seen.add(neighbour)
            frontier.append((neighbour, next_factor, hops + 1))
    return None


def provider_fee_in_source(
    provider: ProviderInfo,
    target_amount: Decimal,
    target_currency: str,
    source_currency: str,
    rates: dict[tuple[str, str], Decimal],
    minor_units: dict[str, int],
) -> Decimal | None:
    from app.core.money import round_money

    amount_in_provider = convert_amount(target_amount, target_currency, provider.currency, rates)
    if amount_in_provider is None:
        return None
    fee_in_provider = (
        amount_in_provider * provider.fee_percent / Decimal("100") + provider.fee_fixed
    )
    converted = convert_amount(fee_in_provider, provider.currency, source_currency, rates)
    if converted is None:
        return None
    return round_money(converted, minor_units[source_currency])


def filter_limits(
    providers: list[ProviderInfo],
    target_amount: Decimal,
    target_currency: str,
    rates: dict[tuple[str, str], Decimal],
) -> list[ProviderInfo]:
    kept = []
    for provider in providers:
        converted = convert_amount(target_amount, target_currency, provider.currency, rates)
        if converted is None:
            continue
        if provider.limit_min <= converted <= provider.limit_max:
            kept.append(provider)
    return kept


def score_providers(
    providers: list[ProviderInfo],
    client_fee_source: Decimal,
    source_amount: Decimal,
    source_currency: str,
    target_amount: Decimal,
    target_currency: str,
    rates: dict[tuple[str, str], Decimal],
    minor_units: dict[str, int],
    min_margin_percent: Decimal,
) -> list[ScoredProvider]:
    scored = []
    for provider in providers:
        fee_source = provider_fee_in_source(
            provider, target_amount, target_currency, source_currency, rates, minor_units
        )
        if fee_source is None or source_amount == 0:
            continue
        margin = (client_fee_source - fee_source) / source_amount
        if margin < min_margin_percent / Decimal("100"):
            continue
        score = (
            Decimal(str(W_MARGIN)) * margin
            + Decimal(str(W_SUCCESS)) * provider.success_rate
            - Decimal(provider.p95_latency_ms) / Decimal(str(W_LATENCY_MS))
        )
        scored.append(
            ScoredProvider(provider=provider, score=score, provider_fee_source=fee_source)
        )
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored


class ProviderClient:
    def __init__(self, base_url: str, timeout_sec: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec

    def _client(self) -> httpx.AsyncClient:
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
        scenario: str | None = None,
    ) -> CreateOutcome:
        headers = {"Idempotency-Key": idempotency_key}
        if scenario is not None:
            headers["X-Scenario"] = scenario
        body = {
            "reference": reference,
            "amount": amount,
            "currency": currency,
            "recipient": recipient,
            "callback_url": callback_url,
        }
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url}/{provider_id}/payouts", json=body, headers=headers
                )
        except (httpx.TransportError, httpx.TimeoutException):
            return UnknownResult(reason="transport error")
        if response.status_code == 201:
            payload = response.json()
            return Accepted(
                payout_id=payload["payout_id"], status=payload.get("status", "PROCESSING")
            )
        if response.status_code == 422:
            payload = response.json()
            return Declined(failure_code=str(payload.get("code", "DECLINED")))
        if response.status_code == 503:
            return Declined(failure_code="PROVIDER_UNAVAILABLE")
        if 500 <= response.status_code < 600:
            return UnknownResult(reason=f"http {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            return UnknownResult(reason=f"http {response.status_code}")
        if response.status_code == 409:
            return UnknownResult(reason="idempotency conflict at provider")
        return UnknownResult(reason=str(payload.get("code", f"http {response.status_code}")))

    async def get_payout(self, provider_id: str, payout_id: str) -> PayoutState | None:
        try:
            async with self._client() as client:
                response = await client.get(f"{self._base_url}/{provider_id}/payouts/{payout_id}")
        except (httpx.TransportError, httpx.TimeoutException):
            return None
        if response.status_code != 200:
            return None
        return _parse_state(response.json())

    async def find_payout(self, provider_id: str, reference: str) -> PayoutState | None:
        outcome, state = await self.probe_payout(provider_id, reference)
        return state if outcome == "found" else None

    async def probe_payout(
        self, provider_id: str, reference: str
    ) -> tuple[str, PayoutState | None]:
        try:
            async with self._client() as client:
                response = await client.get(
                    f"{self._base_url}/{provider_id}/payouts",
                    params={"reference": reference},
                )
        except (httpx.TransportError, httpx.TimeoutException):
            return "error", None
        if response.status_code == 404:
            return "missing", None
        if response.status_code != 200:
            return "error", None
        return "found", _parse_state(response.json())


def _parse_state(payload: dict) -> PayoutState:
    return PayoutState(
        payout_id=str(payload.get("payout_id", "")),
        reference=str(payload.get("reference", "")),
        status=str(payload.get("status", "PROCESSING")),
        failure_code=payload.get("failure_code"),
    )
