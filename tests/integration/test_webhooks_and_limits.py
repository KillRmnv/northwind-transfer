import asyncio
import json
import uuid

from pytest_check import check

from app.core.settings import settings as app_settings
from app.services import providers as providers_module
from tests.integration.test_transfers_api import (
    accept_factory,
    complete_getter,
    make_quote,
    no_find,
    patch_provider,
    post_transfer,
    sign,
)


def event(reference: str, status: str, event_id: str, occurred_at: str) -> bytes:
    return json.dumps(
        {
            "event_id": event_id,
            "payout_id": "po_wh",
            "reference": reference,
            "status": status,
            "occurred_at": occurred_at,
        }
    ).encode()


async def deliver(api, raw: bytes):
    return await api.post(
        "/webhooks/prov_alpha",
        content=raw,
        headers={"X-Signature": sign(app_settings.provider_webhook_secret, raw)},
    )


async def processing_transfer(api, freeze_rates, live_rates, monkeypatch) -> str:
    quote = await make_quote(api, freeze_rates, live_rates)

    async def fake_get(self, provider_id, payout_id):
        return providers_module.PayoutState(
            payout_id=payout_id, reference="", status="PROCESSING", failure_code=None
        )

    patch_provider(monkeypatch, create=accept_factory("po_wh"), get=fake_get, find=no_find)
    created = await post_transfer(api, quote["quote_id"], key=f"key-{uuid.uuid4().hex}")
    assert created.status_code == 202, created.text
    return created.json()["transfer_id"].replace("-", "")


async def test_duplicate_event_is_idempotent(api, freeze_rates, live_rates, monkeypatch) -> None:
    reference = await processing_transfer(api, freeze_rates, live_rates, monkeypatch)
    raw = event(reference, "COMPLETED", "ev-dup", "2026-11-05T12:00:00Z")
    first = await deliver(api, raw)
    second = await deliver(api, raw)
    with check:
        assert first.status_code == 200
    with check:
        assert second.status_code == 200

    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["available"] == "490000.00"


async def test_late_processing_does_not_override_terminal(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    reference = await processing_transfer(api, freeze_rates, live_rates, monkeypatch)
    completed = event(reference, "COMPLETED", "ev-c", "2026-11-05T12:00:00Z")
    await deliver(api, completed)
    late = event(reference, "PROCESSING", "ev-late", "2026-11-05T11:00:00Z")
    response = await deliver(api, late)
    with check:
        assert response.status_code == 200
    status = (await api.get(f"/transfers/{uuid.UUID(reference)}")).json()["status"]
    with check:
        assert status == "COMPLETED"


async def test_unknown_reference_logged_but_accepted(api) -> None:
    raw = event("ffffffffffffffff", "COMPLETED", "ev-unk", "2026-11-05T12:00:00Z")
    response = await deliver(api, raw)
    with check:
        assert response.status_code == 200
    with check:
        assert response.json() == {"status": "ok"}


async def test_parallel_requests_share_pool(api, freeze_rates, live_rates) -> None:
    freeze_rates(live_rates)
    payload = {
        "source_country": "RU",
        "source_currency": "RUB",
        "target_country": "TH",
        "target_currency": "THB",
        "payout_method": "BANK_ACCOUNT",
        "amount": "2000.00",
        "amount_type": "SOURCE",
    }

    async def one_request() -> int:
        response = await api.post("/quotes", json=payload)
        return response.status_code

    results = await asyncio.gather(*[one_request() for _ in range(50)])
    with check:
        assert set(results) == {201}


async def test_no_double_reserve_under_concurrency(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    quote = await make_quote(api, freeze_rates, live_rates, amount="20000.00")
    patch_provider(
        monkeypatch, create=accept_factory("po-par"), get=complete_getter(), find=no_find
    )

    async def submit(key: str):
        return await post_transfer(api, quote["quote_id"], client_id="c_002", key=key)

    results = await asyncio.gather(*[submit(f"p-{i}") for i in range(5)])
    statuses = [r.status_code for r in results]
    with check:
        assert statuses.count(201) == 2
    with check:
        assert statuses.count(409) == 3
    balance = (await api.get("/clients/c_002/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["available"] == "10000.00"
    with check:
        assert balance["balances"]["RUB"]["reserved"] == "0.00"
