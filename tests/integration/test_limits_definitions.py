from decimal import Decimal

from pytest_check import check

from app.models.tables import LimitDefinition


async def seed_limit(session, client_id: str, **values):
    definition = LimitDefinition(
        client_id=client_id,
        scope=values.get("scope", "client"),
        direction_id=values.get("direction_id"),
        category=values.get("category"),
        period_type=values.get("period_type", "DAILY"),
        window_days=values.get("window_days"),
        amount=values.get("amount", Decimal("5000")),
        currency=values.get("currency", "RUB"),
        weight=values.get("weight", Decimal("1")),
        is_active=values.get("is_active", True),
    )
    session.add(definition)
    await session.commit()
    return definition


async def make_quote(api, freeze_rates, live_rates, amount="4000.00"):
    from tests.integration.test_transfers_api import make_quote as mq

    return await mq(api, freeze_rates, live_rates, amount=amount)


async def test_definition_with_direct_rate_pair_does_not_500(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    from tests.integration.test_transfers_api import (
        accept_factory,
        complete_getter,
        no_find,
        patch_provider,
        post_transfer,
    )

    await seed_limit(session, "c_001", amount=Decimal("100000"), currency="THB")
    patch_provider(monkeypatch, create=accept_factory("po-l1"), get=complete_getter(), find=no_find)
    quote = await make_quote(api, freeze_rates, live_rates)
    response = await post_transfer(api, quote["quote_id"], key="lim-direct")
    with check:
        assert response.status_code in (201, 202)
    with check:
        assert response.json().get("error", {}).get("code") != "INTERNAL_ERROR"


async def test_hourly_limit_blocks_second_transfer(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    from tests.integration.test_transfers_api import (
        accept_factory,
        complete_getter,
        no_find,
        patch_provider,
        post_transfer,
    )

    await seed_limit(session, "c_001", period_type="HOURLY", amount=Decimal("5000"), currency="RUB")
    patch_provider(monkeypatch, create=accept_factory("po-h"), get=complete_getter(), find=no_find)
    quote = await make_quote(api, freeze_rates, live_rates, amount="4000.00")
    first = await post_transfer(api, quote["quote_id"], key="hour-1")
    with check:
        assert first.status_code in (201, 202)
    quote2 = await make_quote(api, freeze_rates, live_rates, amount="4000.00")
    second = await post_transfer(api, quote2["quote_id"], key="hour-2")
    with check:
        assert second.status_code == 409
    with check:
        assert second.json()["error"]["code"] == "DAILY_LIMIT_EXCEEDED"


async def test_window_limit_counts_only_window(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    from tests.integration.test_transfers_api import (
        accept_factory,
        complete_getter,
        no_find,
        patch_provider,
        post_transfer,
    )

    await seed_limit(
        session,
        "c_001",
        period_type="WINDOW_N",
        window_days=7,
        amount=Decimal("5000"),
        currency="RUB",
    )
    patch_provider(monkeypatch, create=accept_factory("po-w"), get=complete_getter(), find=no_find)
    quote = await make_quote(api, freeze_rates, live_rates, amount="4000.00")
    first = await post_transfer(api, quote["quote_id"], key="win-1")
    with check:
        assert first.status_code in (201, 202)
    quote2 = await make_quote(api, freeze_rates, live_rates, amount="4000.00")
    second = await post_transfer(api, quote2["quote_id"], key="win-2")
    with check:
        assert second.status_code == 409


async def test_negative_weight_cannot_bypass_limit(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    from tests.integration.test_transfers_api import (
        accept_factory,
        complete_getter,
        no_find,
        patch_provider,
        post_transfer,
    )

    await seed_limit(
        session,
        "c_001",
        period_type="DAILY",
        amount=Decimal("1000"),
        currency="RUB",
        weight=Decimal("-5"),
    )
    patch_provider(
        monkeypatch, create=accept_factory("po-neg"), get=complete_getter(), find=no_find
    )
    quote = await make_quote(api, freeze_rates, live_rates, amount="4000.00")
    response = await post_transfer(api, quote["quote_id"], key="neg-weight")
    with check:
        assert response.status_code == 409


async def test_window_with_null_days_does_not_limit(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    from tests.integration.test_transfers_api import (
        accept_factory,
        complete_getter,
        no_find,
        patch_provider,
        post_transfer,
    )

    await seed_limit(
        session,
        "c_001",
        period_type="WINDOW_N",
        window_days=None,
        amount=Decimal("1"),
        currency="RUB",
    )
    patch_provider(
        monkeypatch, create=accept_factory("po-null"), get=complete_getter(), find=no_find
    )
    quote = await make_quote(api, freeze_rates, live_rates, amount="4000.00")
    response = await post_transfer(api, quote["quote_id"], key="null-window")
    with check:
        assert response.status_code in (201, 202)


async def test_weight_scales_usage(api, session, freeze_rates, live_rates, monkeypatch) -> None:
    from tests.integration.test_transfers_api import (
        accept_factory,
        complete_getter,
        no_find,
        patch_provider,
        post_transfer,
    )

    await seed_limit(
        session,
        "c_001",
        period_type="DAILY",
        amount=Decimal("5000"),
        currency="RUB",
        weight=Decimal("2"),
    )
    patch_provider(
        monkeypatch, create=accept_factory("po-scale"), get=complete_getter(), find=no_find
    )
    quote = await make_quote(api, freeze_rates, live_rates, amount="3000.00")
    response = await post_transfer(api, quote["quote_id"], key="scale-1")
    with check:
        assert response.status_code == 409


async def test_unconvertible_limit_currency_fails_closed(
    api, session, freeze_rates, live_rates, monkeypatch
) -> None:
    from tests.integration.test_transfers_api import (
        accept_factory,
        complete_getter,
        no_find,
        patch_provider,
        post_transfer,
    )

    await seed_limit(session, "c_001", amount=Decimal("100000"), currency="KZT")
    patch_provider(
        monkeypatch, create=accept_factory("po-kzt"), get=complete_getter(), find=no_find
    )
    quote = await make_quote(api, freeze_rates, live_rates)
    response = await post_transfer(api, quote["quote_id"], key="key-kzt")
    with check:
        assert response.status_code == 422
    with check:
        assert response.json()["error"]["code"] == "RATE_UNAVAILABLE"


async def test_unknown_limit_scope_rejected_at_write(session) -> None:
    import pytest
    from sqlalchemy.exc import IntegrityError

    await seed_limit(session, "c_001", scope="direction", direction_id="RU-TH-BANK")
    with pytest.raises(IntegrityError):
        await seed_limit(session, "c_001", scope="category", category="food")
    await session.rollback()
