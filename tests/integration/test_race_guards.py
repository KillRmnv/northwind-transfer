import uuid
from datetime import datetime, timedelta, timezone

from pytest_check import check
from sqlalchemy import select, update

from app.models.tables import PayoutAttempt, Quote, WebhookEvent
from app.services import providers as providers_module
from tests.integration.test_transfers_api import (
    accept_factory,
    complete_getter,
    make_quote,
    no_find,
    patch_provider,
    post_transfer,
)
from tests.integration.test_webhooks_and_limits import deliver, event, processing_transfer


async def _attempt_count(session, transfer_hex: str) -> int:
    rows = (
        (
            await session.execute(
                select(PayoutAttempt).where(PayoutAttempt.reference == transfer_hex)
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


async def _expire_quote(session, quote_id: str) -> None:
    await session.execute(
        update(Quote)
        .where(Quote.quote_id == quote_id)
        .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    await session.commit()


async def test_replay_after_expiry_makes_no_new_payout(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)
    patch_provider(
        monkeypatch, create=accept_factory("po-exp"), get=complete_getter(), find=no_find
    )
    key = "key-exp-replay"
    first = await post_transfer(api, quote["quote_id"], key=key)
    assert first.status_code in (201, 202), first.text
    transfer_id = first.json()["transfer_id"]
    before = await _attempt_count(session, transfer_id.replace("-", ""))

    await _expire_quote(session, quote["quote_id"])
    second = await post_transfer(api, quote["quote_id"], key=key)
    with check:
        assert second.status_code in (200, 201, 202)
    with check:
        assert second.json()["transfer_id"] == transfer_id
    with check:
        assert await _attempt_count(session, transfer_id.replace("-", "")) == before


async def test_terminal_transfer_triggers_no_new_payout(
    api, freeze_rates, live_rates, monkeypatch, session
) -> None:
    quote = await make_quote(api, freeze_rates, live_rates)
    patch_provider(
        monkeypatch, create=accept_factory("po-term"), get=complete_getter(), find=no_find
    )
    key = "key-terminal-retry"
    first = await post_transfer(api, quote["quote_id"], key=key)
    assert first.json()["status"] == "COMPLETED"
    transfer_id = first.json()["transfer_id"]
    before = await _attempt_count(session, transfer_id.replace("-", ""))

    calls: list = []

    async def counting_create(self, *args, **kwargs):
        calls.append(1)
        return providers_module.Accepted(payout_id="po-late", status="PROCESSING")

    monkeypatch.setattr(providers_module.ProviderClient, "create_payout", counting_create)
    second = await post_transfer(api, quote["quote_id"], key=key)
    with check:
        assert second.json()["transfer_id"] == transfer_id
    with check:
        assert calls == []
    with check:
        assert second.json()["status"] == "COMPLETED"
    with check:
        assert await _attempt_count(session, transfer_id.replace("-", "")) == before


async def test_unresolved_attempt_is_retried_not_stuck(
    api, freeze_rates, live_rates, monkeypatch, session
) -> None:

    quote = await make_quote(api, freeze_rates, live_rates)

    async def flaky_create(self, *args, **kwargs):
        from app.services.providers import UnknownResult

        return UnknownResult(reason="boom")

    patch_provider(monkeypatch, create=flaky_create, find=no_find)

    async def error_probe(self, provider_id, reference):
        return ("error", None)

    monkeypatch.setattr(providers_module.ProviderClient, "probe_payout", error_probe)
    key = "key-unresolved"
    first = await post_transfer(api, quote["quote_id"], key=key)
    with check:
        assert first.status_code == 202
    transfer_id = first.json()["transfer_id"]
    before = await _attempt_count(session, transfer_id.replace("-", ""))
    with check:
        assert before >= 1

    patch_provider(
        monkeypatch, create=accept_factory("po-retry"), get=complete_getter(), find=no_find
    )
    second = await post_transfer(api, quote["quote_id"], key=key)
    with check:
        assert second.json()["transfer_id"] == transfer_id
    with check:
        assert second.json()["status"] == "COMPLETED"
    attempts_after = await _attempt_count(session, transfer_id.replace("-", ""))
    with check:
        assert attempts_after == before
    from app.models.tables import PayoutAttempt as _PA

    rows = (
        (await session.execute(select(_PA).where(_PA.reference == transfer_id.replace("-", ""))))
        .scalars()
        .all()
    )
    with check:
        assert all(r.payout_id for r in rows)


async def test_duplicate_webhook_event_single_row(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    reference = await processing_transfer(api, freeze_rates, live_rates, monkeypatch)
    raw = event(reference, "COMPLETED", "ev-conc-dup", "2026-11-05T12:00:00Z")
    first = await deliver(api, raw)
    second = await deliver(api, raw)
    with check:
        assert first.status_code == 200
    with check:
        assert second.status_code == 200
    rows = (
        (await session.execute(select(WebhookEvent).where(WebhookEvent.event_id == "ev-conc-dup")))
        .scalars()
        .all()
    )
    with check:
        assert len(rows) == 1


async def test_expired_quote_without_live_payout_fails_and_releases(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    from app.core.settings import settings as app_settings
    from app.services.transfers import run_attempts

    quote = await make_quote(api, freeze_rates, live_rates)

    async def transport_down(self, *args, **kwargs):
        from app.services.providers import UnknownResult

        return UnknownResult(reason="down")

    patch_provider(monkeypatch, create=transport_down, get=no_find, find=no_find)
    key = "key-expire-fail"
    created = await post_transfer(api, quote["quote_id"], key=key)
    transfer_id = created.json()["transfer_id"]
    await _expire_quote(session, quote["quote_id"])

    status = await run_attempts(
        session, app_settings, uuid.UUID(transfer_id), datetime.now(timezone.utc)
    )
    with check:
        assert status == "FAILED"
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["reserved"] == "0.00"


async def test_concurrent_duplicate_transfers_single_row(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    import asyncio

    quote = await make_quote(api, freeze_rates, live_rates)
    patch_provider(
        monkeypatch, create=accept_factory("po-race2"), get=complete_getter(), find=no_find
    )
    results = await asyncio.gather(
        *[post_transfer(api, quote["quote_id"], key="key-race-dup") for _ in range(2)]
    )
    with check:
        assert all(r.status_code in (200, 201, 202) for r in results)
    ids = {r.json()["transfer_id"] for r in results}
    with check:
        assert len(ids) == 1


async def test_settle_from_payout_reports_actual_status() -> None:
    from types import SimpleNamespace

    from app.services.transfer_machine import PAYOUT_SETTLEMENT, TransferStateMachine

    with check:
        assert PAYOUT_SETTLEMENT["COMPLETED"] == "complete"
    with check:
        assert PAYOUT_SETTLEMENT["FAILED"] == "retry"
    with check:
        assert PAYOUT_SETTLEMENT.get("BOGUS", "wait") == "wait"

    terminal = SimpleNamespace(status="COMPLETED", transfer_id="tid")
    machine = TransferStateMachine(session=None, transfer=terminal)  # type: ignore[arg-type]
    with check:
        assert await machine.complete(__import__("datetime").datetime.now()) is False
    with check:
        assert await machine.fail(__import__("datetime").datetime.now()) is False
    with check:
        assert await machine.start(__import__("datetime").datetime.now()) is False
