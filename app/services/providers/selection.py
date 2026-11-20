from datetime import datetime
from decimal import Decimal

from app.core.exceptions import RateUnavailableError
from app.core.types import Money, ProviderInfo, ScoredProvider

W_MARGIN = Decimal("100")
W_SUCCESS = Decimal("50")
W_LATENCY_MS = Decimal("1000")


def filter_direction(providers: list[ProviderInfo], direction_id: str) -> list[ProviderInfo]:
    """Оставляет провайдеров, обслуживающих направление."""
    return [p for p in providers if direction_id in p.directions]


def in_maintenance(provider: ProviderInfo, now: datetime) -> bool:
    """Провайдер внутри окна обслуживания."""
    return any(window.from_at <= now < window.to_at for window in provider.windows)


def filter_availability(providers: list[ProviderInfo], now: datetime) -> list[ProviderInfo]:
    """Только включённые и вне обслуживания."""
    return [p for p in providers if p.enabled and not in_maintenance(p, now)]


def convert_amount(
    amount: Decimal,
    source_currency: str,
    target_currency: str,
    rates: dict[tuple[str, str], Decimal],
    max_hops: int = 3,
) -> Decimal | None:
    """Конвертация поиском пути по графу курсов, до трёх рёбер."""
    if source_currency == target_currency:
        return amount
    graph: dict[str, list[tuple[str, Decimal]]] = {}
    for (base, quote), rate in rates.items():
        if not rate.is_finite() or rate <= 0:
            continue
        graph.setdefault(base, []).append((quote, rate))
        graph.setdefault(quote, []).append((base, Decimal("1") / rate))
    frontier: list[tuple[str, Decimal, int]] = [(source_currency, Decimal("1"), 0)]
    seen = {source_currency}
    while frontier:
        currency, factor, hops = frontier.pop(0)
        if hops >= max_hops:
            continue
        for neighbour, rate in graph.get(currency, []):
            next_factor = factor * rate
            if neighbour == target_currency:
                return amount * next_factor
            if neighbour in seen:
                continue
            seen.add(neighbour)
            frontier.append((neighbour, next_factor, hops + 1))
    return None


def convert_money(
    source: Money, target_currency: str, rates: dict[tuple[str, str], Decimal]
) -> Decimal | None:
    """То же для Money: сумма или None, если путь не строится."""
    return convert_amount(source.amount, source.currency, target_currency, rates)


def convert_or_raise(
    source: Money, target_currency: str, rates: dict[tuple[str, str], Decimal]
) -> Decimal:
    """Конвертация или RATE_UNAVAILABLE: молчаливого пропуска нет."""
    converted = convert_money(source, target_currency, rates)
    if converted is None:
        raise RateUnavailableError(f"cannot convert {source.currency} to {target_currency}")
    return converted


def provider_fee_in_source(
    provider: ProviderInfo,
    target: Money,
    source_currency: str,
    rates: dict[tuple[str, str], Decimal],
) -> Money | None:
    """Комиссия провайдера в валюте источника для сравнения с комиссией клиента."""

    amount_in_provider = convert_amount(target.amount, target.currency, provider.currency, rates)
    if amount_in_provider is None:
        return None
    fee_in_provider = (
        amount_in_provider * provider.fee_percent / Decimal("100") + provider.fee_fixed
    )
    converted = convert_amount(fee_in_provider, provider.currency, source_currency, rates)
    if converted is None:
        return None
    return Money(converted, source_currency)


def filter_limits(
    providers: list[ProviderInfo],
    target: Money,
    rates: dict[tuple[str, str], Decimal],
) -> list[ProviderInfo]:
    """Отсекает провайдеров, чья сумма вне его min/max."""
    kept = []
    for provider in providers:
        converted = convert_amount(target.amount, target.currency, provider.currency, rates)
        if converted is None:
            continue
        if provider.limit_min <= converted <= provider.limit_max:
            kept.append(provider)
    return kept


def score_providers(
    providers: list[ProviderInfo],
    *,
    client_fee: Money,
    source: Money,
    target: Money,
    rates: dict[tuple[str, str], Decimal],
    min_margin_percent: Decimal,
) -> list[ScoredProvider]:
    """Взвешенный выбор: маржа, надёжность, штраф за задержку."""
    scored = []
    for provider in providers:
        fee_source = provider_fee_in_source(provider, target, source.currency, rates)
        if fee_source is None or source.amount == 0:
            continue
        margin = (client_fee.amount - fee_source.amount) / source.amount
        if margin < min_margin_percent / Decimal("100"):
            continue
        score = (
            W_MARGIN * margin
            + W_SUCCESS * provider.success_rate
            - Decimal(provider.p95_latency_ms) / W_LATENCY_MS
        )
        scored.append(
            ScoredProvider(provider=provider, score=score, provider_fee_source=fee_source.amount)
        )
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored
