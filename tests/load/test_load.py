import asyncio
import time

from pytest_check import check


async def test_twenty_concurrent_quotes(api, freeze_rates, live_rates) -> None:
    freeze_rates(live_rates)
    payload = {
        "source_country": "RU",
        "source_currency": "RUB",
        "target_country": "TH",
        "target_currency": "THB",
        "payout_method": "BANK_ACCOUNT",
        "amount": "10000.00",
        "amount_type": "SOURCE",
    }

    async def one() -> int:
        response = await api.post("/quotes", json=payload)
        return response.status_code

    started = time.perf_counter()
    results = await asyncio.gather(*[one() for _ in range(20)])
    elapsed = time.perf_counter() - started
    with check:
        assert results.count(201) == 20
    with check:
        assert elapsed < 60


async def test_ten_concurrent_transfers_keep_invariant(
    api, freeze_rates, live_rates, monkeypatch
) -> None:
    from app.services import providers as providers_module
    from tests.integration.test_transfers_api import (
        accept_factory,
        complete_getter,
        no_find,
        patch_provider,
    )

    quote = None
    freeze_rates(live_rates)
    response = await api.post(
        "/quotes",
        json={
            "source_country": "RU",
            "source_currency": "RUB",
            "target_country": "TH",
            "target_currency": "THB",
            "payout_method": "BANK_ACCOUNT",
            "amount": "1000.00",
            "amount_type": "SOURCE",
        },
    )
    quote = response.json()
    patch_provider(
        monkeypatch,
        create=accept_factory("po-load"),
        get=complete_getter(),
        find=no_find,
    )

    async def one(index: int) -> int:
        reply = await api.post(
            "/transfers",
            json={
                "quote_id": quote["quote_id"],
                "client_id": "c_001",
                "recipient": {"name": "L", "account": str(index), "bank_code": "B"},
            },
            headers={"Idempotency-Key": f"load-{index}"},
        )
        return reply.status_code

    results = await asyncio.gather(*[one(i) for i in range(10)])
    balance = (await api.get("/clients/c_001/balance")).json()
    with check:
        assert results.count(201) == 10
    with check:
        assert balance["balances"]["RUB"]["available"] == "490000.00"
    with check:
        assert balance["balances"]["RUB"]["reserved"] == "0.00"
    _ = providers_module
