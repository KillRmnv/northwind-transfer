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
    monkeypatch.setattr(providers_module.ProviderClient, "get_payout", complete_getter())
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
    monkeypatch.setattr(providers_module.ProviderClient, "get_payout", complete_getter())
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


async def test_daily_limit_in_third_currency(api, freeze_rates, live_rates, monkeypatch) -> None:
    patch_provider(
        monkeypatch, create=accept_factory("po-lim"), get=complete_getter(), find=no_find
    )
    first_quote = await make_quote(api, freeze_rates, live_rates, amount="300000.00")
    first = await post_transfer(api, first_quote["quote_id"], client_id="c_003", key="lim-1")
    with check:
        assert first.status_code in (201, 202)
    second_quote = await make_quote(api, freeze_rates, live_rates, amount="300000.00")
    second = await post_transfer(api, second_quote["quote_id"], client_id="c_003", key="lim-2")
    with check:
        assert second.status_code == 409
    with check:
        assert second.json()["error"]["code"] == "DAILY_LIMIT_EXCEEDED"


async def test_repeat_key_after_quote_expiry_returns_same_transfer(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    from datetime import datetime, timedelta, timezone

    from app.api.v1 import transfers as transfers_route

    quote = await make_quote(api, freeze_rates, live_rates)
    patch_provider(
        monkeypatch, create=accept_factory("po-exp"), get=complete_getter(), find=no_find
    )
    first = await post_transfer(api, quote["quote_id"], key="exp-key")
    assert first.status_code in (201, 202)
    future = datetime.now(timezone.utc) + timedelta(seconds=100000)
    monkeypatch.setattr(transfers_route, "now_utc", lambda: future)
    second = await post_transfer(api, quote["quote_id"], key="exp-key")
    with check:
        assert second.status_code in (201, 202)
    with check:
        assert second.json()["transfer_id"] == first.json()["transfer_id"]


async def test_foreign_payout_id_does_not_complete(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    first_quote = await make_quote(api, freeze_rates, live_rates)
    second_quote = await make_quote(api, freeze_rates, live_rates, amount="2000.00")

    async def processing_get(self, provider_id, payout_id):
        return providers_module.PayoutState(
            payout_id=payout_id, reference="", status="PROCESSING", failure_code=None
        )

    patch_provider(monkeypatch, create=accept_factory("po-a"), get=processing_get, find=no_find)
    first = await post_transfer(api, first_quote["quote_id"], key="foreign-1")
    second = await post_transfer(api, second_quote["quote_id"], key="foreign-2")
    with check:
        assert first.status_code == 202
    with check:
        assert second.status_code == 202

    async def fake_get(self, provider_id, payout_id):
        return providers_module.PayoutState(
            payout_id=payout_id, reference="other-reference", status="COMPLETED", failure_code=None
        )

    patch_provider(monkeypatch, get=fake_get)
    raw = event(
        first.json()["transfer_id"].replace("-", ""),
        "COMPLETED",
        "ev-foreign",
        "2026-11-05T12:00:00Z",
    )
    payload = json.loads(raw)
    payload["payout_id"] = "po-foreign"
    raw = json.dumps(payload).encode()
    response = await api.post(
        "/webhooks/prov_alpha",
        content=raw,
        headers={"X-Signature": sign(app_settings.provider_webhook_secret, raw)},
    )
    with check:
        assert response.status_code == 200
    status = (await api.get(f"/transfers/{first.json()['transfer_id']}")).json()["status"]
    with check:
        assert status == "PROCESSING"


async def test_empty_webhook_secret_fails_closed(api, monkeypatch) -> None:
    monkeypatch.setattr(app_settings, "provider_webhook_secret", "")
    raw = event("ffffffffffffffff", "COMPLETED", "ev-nosecret", "2026-11-05T12:00:00Z")
    response = await api.post("/webhooks/prov_alpha", content=raw)
    with check:
        assert response.status_code == 503


async def test_async_failed_falls_back_without_reconcile(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)
    calls = []

    async def fake_create(self, provider_id, *args, **kwargs):
        calls.append(provider_id)
        return providers_module.Accepted(payout_id=f"po-{len(calls)}", status="PROCESSING")

    async def fake_get(self, provider_id, payout_id):
        if len(calls) == 1:
            return providers_module.PayoutState(
                payout_id=payout_id, reference="", status="FAILED", failure_code="ACCOUNT_CLOSED"
            )
        return providers_module.PayoutState(
            payout_id=payout_id, reference="", status="COMPLETED", failure_code=None
        )

    patch_provider(monkeypatch, create=fake_create, get=fake_get, find=no_find)
    response = await post_transfer(api, quote["quote_id"], key="async-failed")
    with check:
        assert response.status_code == 201
    body = response.json()
    with check:
        assert body["status"] == "COMPLETED"
    with check:
        assert len(body["attempts"]) == 2
    with check:
        assert body["attempts"][0]["status"] == "FAILED"


async def test_missing_signature_returns_401(api) -> None:
    raw = event("ffffffffffffffff", "COMPLETED", "ev-nosig", "2026-11-05T12:00:00Z")
    response = await api.post("/webhooks/prov_alpha", content=raw)
    with check:
        assert response.status_code == 401
    with check:
        assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_concurrent_same_key_one_transfer(api, freeze_rates, live_rates, monkeypatch) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)
    patch_provider(
        monkeypatch, create=accept_factory("po-conc"), get=complete_getter(), find=no_find
    )

    async def submit():
        return await post_transfer(api, quote["quote_id"], key="conc-key")

    results = await asyncio.gather(*[submit() for _ in range(5)])
    ids = {r.json().get("transfer_id") for r in results if r.status_code < 500}
    with check:
        assert len(ids) == 1
    with check:
        assert all(r.status_code in (201, 202) for r in results)
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["available"] == "490000.00"


async def test_reconcile_completes_lost_webhook(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    from app.core.clock import now_utc
    from app.core.settings import settings as app_settings
    from app.services.reconcile import reconcile_once

    quote = await make_quote(api, freeze_rates, live_rates)

    async def processing_get(self, provider_id, payout_id):
        return providers_module.PayoutState(
            payout_id=payout_id, reference="", status="PROCESSING", failure_code=None
        )

    patch_provider(monkeypatch, create=accept_factory("po-lost"), get=processing_get, find=no_find)
    created = await post_transfer(api, quote["quote_id"], key="lost-key")
    with check:
        assert created.status_code == 202

    patch_provider(monkeypatch, get=complete_getter())
    await session.commit()
    stats = await reconcile_once(session, app_settings, now_utc())
    await session.commit()
    with check:
        assert stats["completed"] >= 1
    got = (await api.get(f"/transfers/{created.json()['transfer_id']}")).json()
    with check:
        assert got["status"] == "COMPLETED"
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["reserved"] == "0.00"


async def test_rejected_webhook_event_is_audited(api, session) -> None:
    from sqlalchemy import select

    from app.models.tables import WebhookEvent as WebhookEventRow

    raw = event("ffffffffffffffff", "COMPLETED", "ev-audit-1", "2026-11-05T12:00:00Z")
    response = await api.post(
        "/webhooks/prov_alpha",
        content=raw,
        headers={"X-Signature": sign(app_settings.provider_webhook_secret, raw)},
    )
    assert response.status_code == 200
    await session.commit()
    rows = (await session.execute(select(WebhookEventRow))).scalars().all()
    with check:
        assert any(row.event_id == "ev-audit-1" for row in rows)


async def test_webhook_rejects_bad_payout_id(api) -> None:
    raw = event("ffffffffffffffff", "COMPLETED", "ev-bad-id", "2026-11-05T12:00:00Z")
    payload = json.loads(raw)
    payload["payout_id"] = "po_x/../../admin/ledger"
    raw = json.dumps(payload).encode()
    response = await api.post(
        "/webhooks/prov_alpha",
        content=raw,
        headers={"X-Signature": sign(app_settings.provider_webhook_secret, raw)},
    )
    with check:
        assert response.status_code == 422


async def test_unknown_webhook_status_rejected(api) -> None:
    raw = event("ffffffffffffffff", "SUCCESS", "ev-status", "2026-11-05T12:00:00Z")
    response = await api.post(
        "/webhooks/prov_alpha",
        content=raw,
        headers={"X-Signature": sign(app_settings.provider_webhook_secret, raw)},
    )
    with check:
        assert response.status_code == 422
    with check:
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_webhook_completed_updates_attempt_status(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)

    async def processing_get(self, provider_id, payout_id):
        return providers_module.PayoutState(
            payout_id=payout_id, reference="", status="PROCESSING", failure_code=None
        )

    patch_provider(monkeypatch, create=accept_factory("po-att"), get=processing_get, find=no_find)
    created = await post_transfer(api, quote["quote_id"], key="att-key")
    with check:
        assert created.status_code == 202
    patch_provider(monkeypatch, get=complete_getter())
    transfer_id = created.json()["transfer_id"]
    raw = event(transfer_id.replace("-", ""), "COMPLETED", "ev-att", "2026-11-05T12:00:00Z")
    payload = json.loads(raw)
    payload["payout_id"] = "po-att"
    raw = json.dumps(payload).encode()
    await api.post(
        "/webhooks/prov_alpha",
        content=raw,
        headers={"X-Signature": sign(app_settings.provider_webhook_secret, raw)},
    )
    body = (await api.get(f"/transfers/{transfer_id}")).json()
    with check:
        assert body["status"] == "COMPLETED"
    with check:
        assert body["attempts"][-1]["status"] == "COMPLETED"


async def test_settled_transfer_cannot_be_reset_to_processing(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    from sqlalchemy import text

    from app.repositories import transfers as transfer_repo

    quote = await make_quote(api, freeze_rates, live_rates)
    patch_provider(
        monkeypatch, create=accept_factory("po-reset"), get=complete_getter(), find=no_find
    )
    created = await post_transfer(api, quote["quote_id"], key="reset-key")
    transfer_id = created.json()["transfer_id"]
    await session.execute(text("update transfers set funds_settled = true, status = 'COMPLETED'"))
    await session.commit()
    import uuid as uuid_module

    from app.core.clock import now_utc

    updated = await transfer_repo.set_status_if_unsettled(
        session, uuid_module.UUID(transfer_id), "PROCESSING", now_utc()
    )
    with check:
        assert updated is False
    body = (await api.get(f"/transfers/{transfer_id}")).json()
    with check:
        assert body["status"] == "COMPLETED"
