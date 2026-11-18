import httpx
import pytest
import respx
from pytest_check import check

from app.core.types import Accepted, Declined, UnknownResult
from app.services.providers import ProviderClient

BASE = "http://mock-provider:9000"


def client() -> ProviderClient:
    return ProviderClient(BASE, timeout_sec=2.0)


async def create(**kwargs):
    params = {
        "provider_id": "prov_alpha",
        "reference": "ref-1",
        "amount": "4137.00",
        "currency": "THB",
        "recipient": {"name": "Somchai", "account": "123", "bank_code": "BKKB"},
        "callback_url": "http://api:8080/webhooks/prov_alpha",
        "idempotency_key": "ref-1:prov_alpha",
    }
    params.update(kwargs)
    return await client().create_payout(**params)


@respx.mock
async def test_create_payout_accepted() -> None:
    respx.post(f"{BASE}/prov_alpha/payouts").mock(
        return_value=httpx.Response(201, json={"payout_id": "po_1", "status": "PROCESSING"})
    )
    outcome = await create()
    with check:
        assert isinstance(outcome, Accepted)
    with check:
        assert outcome.payout_id == "po_1"


@respx.mock
async def test_create_payout_declined() -> None:
    respx.post(f"{BASE}/prov_alpha/payouts").mock(
        return_value=httpx.Response(422, json={"code": "RECIPIENT_ACCOUNT_INVALID"})
    )
    outcome = await create()
    with check:
        assert isinstance(outcome, Declined)
    with check:
        assert outcome.failure_code == "RECIPIENT_ACCOUNT_INVALID"


@respx.mock
async def test_create_payout_5xx_is_unknown() -> None:
    respx.post(f"{BASE}/prov_alpha/payouts").mock(return_value=httpx.Response(500))
    outcome = await create()
    with check:
        assert isinstance(outcome, UnknownResult)
    with check:
        assert "500" in outcome.reason


@respx.mock
async def test_create_payout_gateway_timeout_is_unknown() -> None:
    respx.post(f"{BASE}/prov_alpha/payouts").mock(return_value=httpx.Response(504))
    outcome = await create()
    assert isinstance(outcome, UnknownResult)


@respx.mock
async def test_create_payout_transport_error_is_unknown() -> None:
    respx.post(f"{BASE}/prov_alpha/payouts").mock(side_effect=httpx.ConnectError("boom"))
    outcome = await create()
    with check:
        assert isinstance(outcome, UnknownResult)
    with check:
        assert outcome.reason == "transport error"


@respx.mock
async def test_create_payout_timeout_is_unknown() -> None:
    respx.post(f"{BASE}/prov_alpha/payouts").mock(side_effect=httpx.ReadTimeout("slow"))
    outcome = await create()
    assert isinstance(outcome, UnknownResult)


@respx.mock
async def test_create_payout_409_is_unknown() -> None:
    respx.post(f"{BASE}/prov_alpha/payouts").mock(return_value=httpx.Response(409))
    outcome = await create()
    assert isinstance(outcome, UnknownResult)


@respx.mock
async def test_create_payout_sends_header_and_currency_format() -> None:
    route = respx.post(f"{BASE}/prov_alpha/payouts").mock(
        return_value=httpx.Response(201, json={"payout_id": "po_2", "status": "PROCESSING"})
    )
    await create()
    request = route.calls[0].request
    with check:
        assert request.headers["Idempotency-Key"] == "ref-1:prov_alpha"
    with check:
        assert b'"currency":"THB"' in request.content.replace(b" ", b"")


@respx.mock
async def test_probe_payout_distinguishes_404_and_error() -> None:
    respx.get(f"{BASE}/prov_alpha/payouts").mock(return_value=httpx.Response(404))
    outcome, state = await client().probe_payout("prov_alpha", "ref-1")
    with check:
        assert outcome == "missing"
    with check:
        assert state is None


@respx.mock
async def test_probe_payout_error_on_5xx() -> None:
    respx.get(f"{BASE}/prov_alpha/payouts").mock(return_value=httpx.Response(503))
    outcome, _ = await client().probe_payout("prov_alpha", "ref-1")
    assert outcome == "error"


@respx.mock
async def test_probe_payout_found() -> None:
    respx.get(f"{BASE}/prov_alpha/payouts").mock(
        return_value=httpx.Response(
            200,
            json={
                "payout_id": "po_3",
                "reference": "ref-1",
                "status": "COMPLETED",
                "failure_code": None,
            },
        )
    )
    outcome, state = await client().probe_payout("prov_alpha", "ref-1")
    with check:
        assert outcome == "found"
    with check:
        assert state is not None and state.status == "COMPLETED"


@respx.mock
async def test_get_payout_404_returns_none() -> None:
    respx.get(f"{BASE}/prov_alpha/payouts/po_x").mock(return_value=httpx.Response(404))
    assert await client().get_payout("prov_alpha", "po_x") is None


@respx.mock
async def test_get_payout_parse_failure_code() -> None:
    respx.get(f"{BASE}/prov_alpha/payouts/po_x").mock(
        return_value=httpx.Response(
            200,
            json={
                "payout_id": "po_x",
                "reference": "ref",
                "status": "FAILED",
                "failure_code": "ACCOUNT_CLOSED",
            },
        )
    )
    state = await client().get_payout("prov_alpha", "po_x")
    assert state is not None and state.failure_code == "ACCOUNT_CLOSED"


def test_unknown_outcome_types_are_distinct() -> None:
    with check:
        assert not isinstance(Accepted(payout_id="x", status="PROCESSING"), Declined)
    with pytest.raises(TypeError):
        UnknownResult()
