import asyncio
import json
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pytest_check import check

from app.core.clock import now_utc
from app.core.settings import settings as app_settings
from app.services import providers as providers_module
from tests.integration.test_transfers_api import make_quote, post_transfer


def mock_base() -> str:
    return app_settings.mock_provider_url


@pytest.fixture()
def live_mock(monkeypatch):
    try:
        with urllib.request.urlopen(  # noqa: S310 - mock URL from test settings
            mock_base() + "/health", timeout=2
        ) as response:
            json.loads(response.read().decode())
    except Exception:
        pytest.skip("mock-provider is not reachable")
    monkeypatch.setattr(app_settings, "public_base_url", "http://127.0.0.1:9")
    return mock_base()


@pytest.fixture()
def mock_scenario(monkeypatch):
    real_init = providers_module.ProviderClient.__init__

    def _set(forced: str) -> None:
        def init_with_scenario(self, base_url, timeout_sec, **kwargs):
            real_init(self, base_url, timeout_sec, scenario=forced)

        monkeypatch.setattr(providers_module.ProviderClient, "__init__", init_with_scenario)

    return _set


def ledger_entries(provider_id: str) -> list:
    with urllib.request.urlopen(  # noqa: S310 - mock URL from test settings
        f"{mock_base()}/{provider_id}/admin/ledger", timeout=10
    ) as response:
        return json.loads(response.read().decode()).get("entries", [])


async def settle_via_reconcile(api, session, transfer_id: str, deadline_sec: int = 150) -> dict:
    from app.services.reconcile import reconcile_once

    deadline = datetime.now(timezone.utc) + timedelta(seconds=deadline_sec)
    while True:
        await session.commit()
        await reconcile_once(session, app_settings, now_utc())
        await session.commit()
        got = (await api.get(f"/transfers/{transfer_id}")).json()
        if got["status"] in ("COMPLETED", "FAILED"):
            return got
        assert datetime.now(timezone.utc) < deadline, "transfer did not settle"
        await asyncio.sleep(5)


async def test_ok_scenario_completes_sync(
    api, session, freeze_rates, live_rates, live_mock, mock_scenario
) -> None:
    freeze_rates(live_rates)
    mock_scenario("ok")
    quote = await make_quote(api, freeze_rates, live_rates)
    response = await post_transfer(api, quote["quote_id"], key=f"ct-ok-{uuid.uuid4().hex[:12]}")
    body = response.json()
    with check:
        assert response.status_code == 201
    with check:
        assert body["status"] == "COMPLETED"
    attempt = next(a for a in body["attempts"] if a["payout_id"])
    entries = [
        e for e in ledger_entries(body["provider_id"]) if e["payout_id"] == attempt["payout_id"]
    ]
    with check:
        assert len(entries) == 1
    with check:
        assert entries[0]["status"] == "COMPLETED"
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["reserved"] == "0.00"


async def test_decline_scenario_fails_terminal(
    api, session, freeze_rates, live_rates, live_mock, mock_scenario
) -> None:
    freeze_rates(live_rates)
    mock_scenario("decline")
    quote = await make_quote(api, freeze_rates, live_rates)
    response = await post_transfer(api, quote["quote_id"], key=f"ct-dec-{uuid.uuid4().hex[:12]}")
    body = response.json()
    with check:
        assert response.status_code == 200
    with check:
        assert body["status"] == "FAILED"
    with check:
        assert len(body["attempts"]) >= 1
    with check:
        assert all(a["status"] == "FAILED" for a in body["attempts"])
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["reserved"] == "0.00"


async def test_error_5xx_scenario_fails_terminal(
    api, session, freeze_rates, live_rates, live_mock, mock_scenario
) -> None:
    freeze_rates(live_rates)
    mock_scenario("error_5xx")
    quote = await make_quote(api, freeze_rates, live_rates)
    response = await post_transfer(api, quote["quote_id"], key=f"ct-5xx-{uuid.uuid4().hex[:12]}")
    with check:
        assert response.status_code in (200, 202)
    transfer_id = response.json()["transfer_id"]
    got = await settle_via_reconcile(api, session, transfer_id)
    with check:
        assert got["status"] == "FAILED"
    with check:
        assert all(a["status"] == "FAILED" for a in got["attempts"])
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["reserved"] == "0.00"


async def test_slow_scenario_settles(
    api, session, freeze_rates, live_rates, live_mock, mock_scenario
) -> None:
    freeze_rates(live_rates)
    mock_scenario("slow")
    quote = await make_quote(api, freeze_rates, live_rates)
    response = await post_transfer(api, quote["quote_id"], key=f"ct-slow-{uuid.uuid4().hex[:12]}")
    with check:
        assert response.status_code in (201, 202)
    transfer_id = response.json()["transfer_id"]
    got = await settle_via_reconcile(api, session, transfer_id)
    with check:
        assert got["status"] == "COMPLETED"
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert balance["balances"]["RUB"]["reserved"] == "0.00"
