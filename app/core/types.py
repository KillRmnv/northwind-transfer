from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class TariffData:
    direction_id: str
    source_country: str
    source_currency: str
    target_country: str
    target_currency: str
    payout_method: str
    fee_percent: Decimal
    fee_fixed: Decimal
    fee_min: Decimal
    fee_charged_in: str
    min_amount: Decimal
    max_amount: Decimal
    limit_currency: str
    min_margin_percent: Decimal
    quote_ttl_sec: int


@dataclass(frozen=True)
class RateData:
    rate: Decimal
    updated_at: datetime


@dataclass(frozen=True)
class QuoteResult:
    direction_id: str
    source_amount: Decimal
    source_currency: str
    fee_amount: Decimal
    fee_currency: str
    amount_to_convert: Decimal
    rate: Decimal
    target_amount: Decimal
    target_currency: str
    breakdown: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Window:
    from_at: datetime
    to_at: datetime


@dataclass(frozen=True)
class ProviderInfo:
    provider_id: str
    currency: str
    fee_percent: Decimal
    fee_fixed: Decimal
    limit_min: Decimal
    limit_max: Decimal
    success_rate: Decimal
    p95_latency_ms: int
    enabled: bool
    directions: frozenset = field(default_factory=frozenset)
    windows: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class ScoredProvider:
    provider: ProviderInfo
    score: Decimal
    provider_fee_source: Decimal


@dataclass(frozen=True)
class Accepted:
    payout_id: str
    status: str


@dataclass(frozen=True)
class Declined:
    failure_code: str


@dataclass(frozen=True)
class ServerError:
    status_code: int


@dataclass(frozen=True)
class UnknownResult:
    reason: str


@dataclass(frozen=True)
class PayoutState:
    payout_id: str
    reference: str
    status: str
    failure_code: str | None


CreateOutcome = Accepted | Declined | ServerError | UnknownResult
