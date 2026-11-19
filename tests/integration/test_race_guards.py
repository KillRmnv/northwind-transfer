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


async def test_expired_quote_with_confirmed_payout_completes(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    from app.core.settings import settings as app_settings
    from app.services.transfers import run_attempts

    quote = await make_quote(api, freeze_rates, live_rates)

    async def transport_down(self, *args, **kwargs):
        return providers_module.UnknownResult(reason="down")

    async def found_probe(self, provider_id, reference):
        return (
            "found",
            providers_module.PayoutState(
                payout_id="po-found", reference=reference, status="COMPLETED", failure_code=None
            ),
        )

    patch_provider(monkeypatch, create=transport_down, get=complete_getter(), probe=found_probe)
    key = "key-expire-found"
    created = await post_transfer(api, quote["quote_id"], key=key)
    transfer_id = created.json()["transfer_id"]
    await _expire_quote(session, quote["quote_id"])

    status = await run_attempts(
        session, app_settings, uuid.UUID(transfer_id), datetime.now(timezone.utc)
    )
    with check:
        assert status == "COMPLETED"
    got = (await api.get(f"/transfers/{transfer_id}")).json()
    with check:
        assert got["status"] == "COMPLETED"
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["reserved"] == "0.00"


async def test_expired_quote_with_young_pending_keeps_reserve_for_review(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    from app.core.settings import settings as app_settings
    from app.services.transfers import run_attempts

    quote = await make_quote(api, freeze_rates, live_rates)

    async def transport_down(self, *args, **kwargs):
        return providers_module.UnknownResult(reason="down")

    async def error_probe(self, provider_id, reference):
        return ("error", None)

    patch_provider(monkeypatch, create=transport_down, probe=error_probe)
    key = "key-expire-review"
    created = await post_transfer(api, quote["quote_id"], key=key)
    transfer_id = created.json()["transfer_id"]
    await _expire_quote(session, quote["quote_id"])

    status = await run_attempts(
        session, app_settings, uuid.UUID(transfer_id), datetime.now(timezone.utc)
    )
    with check:
        assert status == "NEEDS_REVIEW"
    got = (await api.get(f"/transfers/{transfer_id}")).json()
    with check:
        assert got["status"] == "NEEDS_REVIEW"
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["reserved"] == "10000.00"


async def test_young_pending_attempt_blocks_next_provider(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    from app.core.settings import settings as app_settings
    from app.services.transfers import run_attempts

    quote = await make_quote(api, freeze_rates, live_rates)

    async def transport_down(self, *args, **kwargs):
        return providers_module.UnknownResult(reason="down")

    async def error_probe(self, provider_id, reference):
        return ("error", None)

    async def missing_probe(self, provider_id, reference):
        return ("missing", None)

    patch_provider(monkeypatch, create=transport_down, probe=error_probe)
    created = await post_transfer(api, quote["quote_id"], key="young-key")
    with check:
        assert created.status_code == 202
    with check:
        assert created.json()["status"] == "NEEDS_REVIEW"
    transfer_id = created.json()["transfer_id"]

    calls: list = []

    async def counting_create(self, provider_id, *args, **kwargs):
        calls.append(provider_id)
        return providers_module.Accepted(payout_id="po-late", status="PROCESSING")

    monkeypatch.setattr(providers_module.ProviderClient, "create_payout", counting_create)
    monkeypatch.setattr(providers_module.ProviderClient, "probe_payout", missing_probe)
    status = await run_attempts(
        session, app_settings, uuid.UUID(transfer_id), datetime.now(timezone.utc)
    )
    with check:
        assert status == "NEEDS_REVIEW"
    with check:
        assert calls == []
    with check:
        assert await _attempt_count(session, transfer_id.replace("-", "")) == 1


async def test_persistent_5xx_reaches_terminal_failed(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    from app.services import transfers as transfers_module

    quote = await make_quote(api, freeze_rates, live_rates)

    async def always_missing(*args, **kwargs):
        return None

    async def missing_probe(self, provider_id, reference):
        return ("missing", None)

    monkeypatch.setattr(transfers_module, "create_with_recovery", always_missing)
    patch_provider(monkeypatch, probe=missing_probe)
    response = await post_transfer(api, quote["quote_id"], key="key-5xx-all")
    body = response.json()
    with check:
        assert response.status_code == 200
    with check:
        assert body["status"] == "FAILED"
    with check:
        assert len(body["attempts"]) >= 2
    with check:
        assert all(a["status"] == "FAILED" for a in body["attempts"])
    with check:
        assert all(a["failure_code"] == "PROVIDER_UNREACHABLE" for a in body["attempts"])
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["reserved"] == "0.00"


async def test_needs_review_counts_toward_daily_limit(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    first_quote = await make_quote(api, freeze_rates, live_rates, amount="200000.00")
    second_quote = await make_quote(api, freeze_rates, live_rates, amount="200000.00")

    async def transport_down(self, *args, **kwargs):
        return providers_module.UnknownResult(reason="down")

    async def error_probe(self, provider_id, reference):
        return ("error", None)

    patch_provider(monkeypatch, create=transport_down, probe=error_probe)
    first = await post_transfer(api, first_quote["quote_id"], key="nr-1")
    with check:
        assert first.status_code == 202
    with check:
        assert first.json()["status"] == "NEEDS_REVIEW"
    second = await post_transfer(api, second_quote["quote_id"], key="nr-2")
    with check:
        assert second.status_code == 409
    with check:
        assert second.json()["error"]["code"] == "DAILY_LIMIT_EXCEEDED"


async def test_reconcile_lock_single_runner(session) -> None:
    import pytest
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.services.reconcile import release_reconcile_lock, try_reconcile_lock

    if session.bind is None or session.bind.dialect.name != "postgresql":
        pytest.skip("advisory locks require postgres")
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    async with factory() as other:
        with check:
            assert await try_reconcile_lock(session) is True
        with check:
            assert await try_reconcile_lock(other) is False
        await release_reconcile_lock(session)
        with check:
            assert await try_reconcile_lock(other) is True
        await release_reconcile_lock(other)
    await session.commit()


async def test_processing_transition_uses_confirmation_time(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    import asyncio
    from datetime import datetime as datetime_module

    quote = await make_quote(api, freeze_rates, live_rates)

    async def slow_accept(self, *args, **kwargs):
        await asyncio.sleep(1.5)
        return providers_module.Accepted(payout_id="po-slow", status="PROCESSING")

    patch_provider(monkeypatch, create=slow_accept, get=complete_getter(), find=no_find)
    created = await post_transfer(api, quote["quote_id"], key="slow-key")
    with check:
        assert created.status_code == 201
    body = created.json()
    created_at = datetime_module.fromisoformat(body["created_at"])
    proc = next(h for h in body["history"] if h["status"] == "PROCESSING")
    at = datetime_module.fromisoformat(proc["at"])
    with check:
        assert (at - created_at).total_seconds() >= 1.0


async def test_history_from_status_uses_locked_row(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.clock import now_utc
    from app.repositories import transfers as transfer_repo
    from app.services import transfers as transfers_module
    from app.services.transfer_machine import TransferStateMachine

    quote = await make_quote(api, freeze_rates, live_rates)

    async def immediate_unknown(*args, **kwargs):
        return providers_module.UnknownResult(reason="down")

    monkeypatch.setattr(transfers_module, "create_with_recovery", immediate_unknown)
    created = await post_transfer(api, quote["quote_id"], key="stale-key")
    with check:
        assert created.json()["status"] == "NEEDS_REVIEW"
    transfer_id = uuid.UUID(created.json()["transfer_id"])
    stale = await transfer_repo.get_transfer(session, transfer_id)
    assert stale is not None
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    async with factory() as other:
        fresh = await transfer_repo.get_transfer(other, transfer_id, lock=True)
        assert fresh is not None
        moved = await TransferStateMachine(other, fresh).start(now_utc(), "reconcile")
        with check:
            assert moved is True
        await other.commit()
    done = await TransferStateMachine(session, stale).complete(now_utc(), "service")
    with check:
        assert done is True
    await session.commit()
    history = await transfer_repo.history_for_transfer(session, transfer_id)
    with check:
        assert (history[-1].from_status, history[-1].to_status) == ("PROCESSING", "COMPLETED")


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
