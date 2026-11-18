import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from pytest_check import check

from app.api.v1 import transfers as transfers_route
from app.core.settings import settings as app_settings
from app.services import providers as providers_module


async def make_quote(api, freeze_rates, live_rates, amount="10000.00", amount_type="SOURCE"):
    freeze_rates(live_rates)
    response = await api.post(
        "/quotes",
        json={
            "source_country": "RU",
            "source_currency": "RUB",
            "target_country": "TH",
            "target_currency": "THB",
            "payout_method": "BANK_ACCOUNT",
            "amount": amount,
            "amount_type": amount_type,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def post_transfer(api, quote_id, client_id="c_001", key="key-1"):
    return await api.post(
        "/transfers",
        json={
            "quote_id": quote_id,
            "client_id": client_id,
            "recipient": {"name": "Somchai", "account": "123", "bank_code": "BKKB"},
        },
        headers={"Idempotency-Key": key},
    )


def patch_provider(monkeypatch, create=None, get=None, find=None):
    if create is not None:
        monkeypatch.setattr(providers_module.ProviderClient, "create_payout", create)
    if get is not None:
        monkeypatch.setattr(providers_module.ProviderClient, "get_payout", get)
    if find is not None:
        monkeypatch.setattr(providers_module.ProviderClient, "find_payout", find)


def accept_factory(payout_id="po_test_1"):
    async def fake_create(self, *args, **kwargs):
        return providers_module.Accepted(payout_id=payout_id, status="PROCESSING")

    return fake_create


def complete_getter():
    async def fake_get(self, provider_id, payout_id):
        return providers_module.PayoutState(
            payout_id=payout_id, reference="", status="COMPLETED", failure_code=None
        )

    return fake_get


async def no_find(self, provider_id, reference):
    return None


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def test_transfer_completed_sync(api, freeze_rates, live_rates, monkeypatch) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)
    patch_provider(monkeypatch, create=accept_factory("po_1"), get=complete_getter(), find=no_find)

    response = await post_transfer(api, quote["quote_id"])
    with check:
        assert response.status_code == 201
    body = response.json()
    with check:
        assert body["status"] == "COMPLETED"
    with check:
        assert len(body["attempts"]) == 1
    with check:
        assert len(body["history"]) >= 2

    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["available"] == "490000.00"
    with check:
        assert balance["balances"]["RUB"]["reserved"] == "0.00"


async def test_transfer_fallback_to_next_provider(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)
    calls = []

    async def fake_create(self, provider_id, *args, **kwargs):
        calls.append(provider_id)
        if len(calls) == 1:
            return providers_module.Declined(failure_code="RECIPIENT_ACCOUNT_INVALID")
        return providers_module.Accepted(payout_id="po_2", status="PROCESSING")

    patch_provider(monkeypatch, create=fake_create, get=complete_getter(), find=no_find)

    response = await post_transfer(api, quote["quote_id"], key="key-fb")
    with check:
        assert response.status_code == 201
    body = response.json()
    with check:
        assert body["status"] == "COMPLETED"
    with check:
        assert len(body["attempts"]) == 2
    with check:
        assert body["attempts"][0]["status"] == "FAILED"


async def test_transfer_all_decline_releases_reserve(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)

    async def fake_create(self, *args, **kwargs):
        return providers_module.Declined(failure_code="RECIPIENT_ACCOUNT_INVALID")

    patch_provider(monkeypatch, create=fake_create)

    response = await post_transfer(api, quote["quote_id"], key="key-decline")
    with check:
        assert response.status_code in (201, 202)
    with check:
        assert response.json()["status"] == "FAILED"
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["available"] == "500000.00"
    with check:
        assert balance["balances"]["RUB"]["reserved"] == "0.00"


async def test_unknown_result_adopts_existing_payout(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)

    async def fake_create(self, *args, **kwargs):
        return providers_module.UnknownResult(reason="transport error")

    async def fake_find(self, provider_id, reference):
        return providers_module.PayoutState(
            payout_id="po_x", reference=reference, status="PROCESSING", failure_code=None
        )

    patch_provider(monkeypatch, create=fake_create, get=complete_getter(), find=fake_find)

    response = await post_transfer(api, quote["quote_id"], key="key-unknown")
    with check:
        assert response.status_code == 201
    with check:
        assert response.json()["status"] == "COMPLETED"


async def test_idempotency_same_key_returns_same_transfer(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)
    patch_provider(
        monkeypatch, create=accept_factory("po_idem"), get=complete_getter(), find=no_find
    )

    first = await post_transfer(api, quote["quote_id"], key="key-same")
    second = await post_transfer(api, quote["quote_id"], key="key-same")
    with check:
        assert first.json()["transfer_id"] == second.json()["transfer_id"]
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["available"] == "490000.00"


async def test_idempotency_different_body_conflicts(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)
    patch_provider(monkeypatch, create=accept_factory("po_c"), get=complete_getter(), find=no_find)

    await post_transfer(api, quote["quote_id"], key="key-conflict")
    quote2 = await make_quote(api, freeze_rates, live_rates, amount="20000.00")
    response = await post_transfer(api, quote2["quote_id"], key="key-conflict")
    with check:
        assert response.status_code == 422
    with check:
        assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_double_submit_returns_one_transfer(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)
    patch_provider(
        monkeypatch, create=accept_factory("po_race"), get=complete_getter(), find=no_find
    )

    first = await post_transfer(api, quote["quote_id"], key="key-race")
    second = await post_transfer(api, quote["quote_id"], key="key-race")
    with check:
        assert first.json()["transfer_id"] == second.json()["transfer_id"]
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["available"] == "490000.00"


async def test_webhook_bad_signature_rejected(api) -> None:
    raw = json.dumps(
        {
            "event_id": "ev_bad",
            "payout_id": "po_bad",
            "reference": "nope",
            "status": "COMPLETED",
            "occurred_at": "2026-11-05T12:00:00Z",
        }
    ).encode()
    response = await api.post(
        "/webhooks/prov_alpha", content=raw, headers={"X-Signature": sign("other", raw)}
    )
    with check:
        assert response.status_code in (401, 422)


async def test_webhook_unknown_reference_accepted(api) -> None:
    raw = json.dumps(
        {
            "event_id": "ev-unknown-1",
            "payout_id": "po_unknown",
            "reference": "unknown-ref",
            "status": "COMPLETED",
            "occurred_at": "2026-11-05T12:00:00Z",
        }
    ).encode()
    response = await api.post(
        "/webhooks/prov_alpha",
        content=raw,
        headers={"X-Signature": sign(app_settings.provider_webhook_secret, raw)},
    )
    with check:
        assert response.status_code == 200


async def test_webhook_completes_transfer(api, freeze_rates, live_rates, monkeypatch) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)

    async def fake_create(self, *args, **kwargs):
        return providers_module.Accepted(payout_id="po_wh", status="PROCESSING")

    async def fake_get(self, provider_id, payout_id):
        return providers_module.PayoutState(
            payout_id=payout_id, reference="", status="PROCESSING", failure_code=None
        )

    patch_provider(monkeypatch, create=fake_create, get=fake_get, find=no_find)

    created = await post_transfer(api, quote["quote_id"], key="key-wh")
    with check:
        assert created.status_code == 202
    patch_provider(monkeypatch, get=complete_getter())
    transfer_id = created.json()["transfer_id"]
    raw = json.dumps(
        {
            "event_id": "ev-wh-1",
            "payout_id": "po_wh",
            "reference": transfer_id.replace("-", ""),
            "status": "COMPLETED",
            "occurred_at": "2026-11-05T12:00:00Z",
        }
    ).encode()
    response = await api.post(
        "/webhooks/prov_alpha",
        content=raw,
        headers={"X-Signature": sign(app_settings.provider_webhook_secret, raw)},
    )
    with check:
        assert response.status_code == 200
    got = (await api.get(f"/transfers/{transfer_id}")).json()
    with check:
        assert got["status"] == "COMPLETED"


async def test_expired_quote_rejected(api, freeze_rates, live_rates, monkeypatch) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)
    real_now = datetime.now(timezone.utc)
    monkeypatch.setattr(transfers_route, "now_utc", lambda: real_now + timedelta(seconds=100000))
    response = await post_transfer(api, quote["quote_id"], key="key-exp")
    with check:
        assert response.status_code == 410
    with check:
        assert response.json()["error"]["code"] == "QUOTE_EXPIRED"


async def test_insufficient_funds(api, freeze_rates, live_rates) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)
    response = await post_transfer(api, quote["quote_id"], client_id="c_007", key="key-funds")
    with check:
        assert response.status_code == 409
    with check:
        assert response.json()["error"]["code"] == "INSUFFICIENT_FUNDS"


async def test_daily_limit_exceeded(api, freeze_rates, live_rates, monkeypatch) -> None:
    patch_provider(monkeypatch, create=accept_factory("po_dl"), get=complete_getter(), find=no_find)
    quote = await make_quote(api, freeze_rates, live_rates, amount="200000.00")
    first = await post_transfer(api, quote["quote_id"], key="key-dl-1")
    with check:
        assert first.status_code in (201, 202)
    quote2 = await make_quote(api, freeze_rates, live_rates, amount="200000.00")
    second = await post_transfer(api, quote2["quote_id"], key="key-dl-2")
    with check:
        assert second.status_code == 409
    with check:
        assert second.json()["error"]["code"] == "DAILY_LIMIT_EXCEEDED"
