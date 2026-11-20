from datetime import datetime
from decimal import Decimal

import pytest
from pytest_check import check

from app.services.quotes import RateData


def quote_payload(**overrides) -> dict:
    body = {
        "source_country": "RU",
        "source_currency": "RUB",
        "target_country": "TH",
        "target_currency": "THB",
        "payout_method": "BANK_ACCOUNT",
        "amount": "10000.00",
        "amount_type": "SOURCE",
    }
    body.update(overrides)
    return body


async def test_create_and_read_quote(api, freeze_rates, live_rates) -> None:
    freeze_rates(live_rates)
    response = await api.post("/quotes", json=quote_payload())
    with check:
        assert response.status_code == 201
    body = response.json()
    with check:
        assert body["direction_id"] == "RU-TH-BANK"
    with check:
        assert body["source_amount"] == {"amount": "10000.00", "currency": "RUB"}
    with check:
        assert body["fee"] == {"amount": "150.00", "currency": "RUB"}
    with check:
        assert body["amount_to_convert"] == {"amount": "9850.00", "currency": "RUB"}
    with check:
        assert body["rate"] == "0.42000000"
    with check:
        assert body["target_amount"] == {"amount": "4137.00", "currency": "THB"}
    with check:
        assert isinstance(body["breakdown"], list) and body["breakdown"]
    with check:
        assert body["quote_id"] in response.headers.get("location", "")

    got = await api.get(f"/quotes/{body['quote_id']}")
    with check:
        assert got.status_code == 200
    with check:
        assert got.json()["quote_id"] == body["quote_id"]


async def test_versioned_and_root_paths_match(api, freeze_rates, live_rates) -> None:
    freeze_rates(live_rates)
    first = await api.post("/api/v1/quotes", json=quote_payload())
    second = await api.post("/quotes", json=quote_payload())
    with check:
        assert first.status_code == 201
    with check:
        assert second.status_code == 201
    got = await api.get(f"/api/v1/quotes/{first.json()['quote_id']}")
    with check:
        assert got.status_code == 200


@pytest.mark.parametrize(
    ("payload", "code", "status"),
    [
        (quote_payload(amount="500.00"), "AMOUNT_BELOW_MIN", 400),
        (quote_payload(amount="500000.00"), "AMOUNT_ABOVE_MAX", 400),
        (quote_payload(payout_method="CRYPTO"), "UNSUPPORTED_DIRECTION", 400),
        (quote_payload(amount="10.0"), "VALIDATION_ERROR", 422),
        (quote_payload(amount="-5.00"), "VALIDATION_ERROR", 422),
        (quote_payload(source_currency="XX"), "VALIDATION_ERROR", 422),
    ],
)
async def test_quote_errors(
    api, freeze_rates, live_rates, payload: dict, code: str, status: int
) -> None:
    freeze_rates(live_rates)
    response = await api.post("/quotes", json=payload)
    with check:
        assert response.status_code == status
    with check:
        assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    with check:
        assert body["error"]["code"] == code
    with check:
        assert body["status"] == status


async def test_rate_unavailable(api, freeze_rates) -> None:
    freeze_rates({})
    response = await api.post(
        "/quotes",
        json=quote_payload(
            source_country="KZ",
            source_currency="KZT",
            target_country="TH",
            target_currency="THB",
        ),
    )
    with check:
        assert response.status_code == 422
    with check:
        assert response.json()["error"]["code"] == "RATE_UNAVAILABLE"


async def test_stale_rate_unavailable(api, freeze_rates, now: datetime) -> None:
    rates = {
        ("RUB", "THB"): RateData(
            rate=Decimal("0.42000000"),
            updated_at=datetime.fromisoformat("2026-10-01T00:00:00+00:00"),
        )
    }
    freeze_rates(rates)
    response = await api.post("/quotes", json=quote_payload())
    with check:
        assert response.status_code == 422
    with check:
        assert response.json()["error"]["code"] == "RATE_UNAVAILABLE"


async def test_quote_not_found(api) -> None:
    response = await api.get("/quotes/123e4567-e89b-12d3-a456-426614174000")
    with check:
        assert response.status_code == 404
    with check:
        assert response.json()["error"]["code"] == "QUOTE_NOT_FOUND"


async def test_request_id_header_present(api) -> None:
    response = await api.get("/health")
    with check:
        assert response.status_code == 200
    with check:
        assert len(response.headers["x-request-id"]) == 16


async def test_target_mode_obeys_source_max(api, freeze_rates, live_rates) -> None:
    freeze_rates(live_rates)
    response = await api.post(
        "/quotes",
        json=quote_payload(amount="126000.00", amount_type="TARGET"),
    )
    with check:
        assert response.status_code == 400
    with check:
        assert response.json()["error"]["code"] == "AMOUNT_ABOVE_MAX"


async def test_expired_quote_get_returns_410(api, freeze_rates, live_rates, monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone

    from app.api.v1 import quotes as quotes_route

    freeze_rates(live_rates)
    created = await api.post("/quotes", json=quote_payload())
    quote_id = created.json()["quote_id"]
    future = datetime.now(timezone.utc) + timedelta(seconds=100000)
    monkeypatch.setattr(quotes_route, "now_utc", lambda: future)
    response = await api.get(f"/quotes/{quote_id}")
    with check:
        assert response.status_code == 410
    with check:
        assert response.json()["error"]["code"] == "QUOTE_EXPIRED"


async def test_expired_quotes_purged_only_when_unreferenced(session) -> None:
    import uuid
    from datetime import datetime, timedelta, timezone

    from app.models.tables import Quote, Transfer
    from app.repositories.quotes import get_quote, purge_expired_quotes

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=60)

    def quote_row(**overrides) -> Quote:
        base = {
            "quote_id": uuid.uuid4(),
            "direction_id": "RU-TH-BANK",
            "source_amount": Decimal("10000.00"),
            "source_currency": "RUB",
            "fee_amount": Decimal("150.00"),
            "fee_currency": "RUB",
            "amount_to_convert": Decimal("9850.00"),
            "rate": Decimal("0.42"),
            "target_amount": Decimal("4137.00"),
            "target_currency": "THB",
            "breakdown": [],
            "expires_at": old,
            "created_at": old,
        }
        base.update(overrides)
        return Quote(**base)

    orphan = quote_row()
    fresh = quote_row(quote_id=uuid.uuid4(), expires_at=now + timedelta(hours=1))
    linked = quote_row()
    session.add_all([orphan, fresh, linked])
    await session.flush()
    session.add(
        Transfer(
            transfer_id=uuid.uuid4(),
            quote_id=linked.quote_id,
            client_id="c_001",
            status="PENDING",
            source_amount=Decimal("10000.00"),
            source_currency="RUB",
            target_amount=Decimal("4137.00"),
            target_currency="THB",
            recipient_name="Somchai",
            recipient_account="123",
            recipient_bank="BKKB",
            idempotency_key="key-purge-guard",
            idempotency_hash="x",
            created_at=old,
            updated_at=old,
        )
    )
    await session.commit()

    purged = await purge_expired_quotes(session, now, 30)

    with check:
        assert purged == 1
    with check:
        assert await get_quote(session, orphan.quote_id) is None
    with check:
        assert await get_quote(session, fresh.quote_id) is not None
    with check:
        assert await get_quote(session, linked.quote_id) is not None
