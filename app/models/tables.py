import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.guid import GUID

MONEY = Numeric(20, 8)


class Currency(Base):
    __tablename__ = "currencies"
    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    minor_units: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class Country(Base):
    __tablename__ = "countries"
    code: Mapped[str] = mapped_column(String(2), primary_key=True)


class PayoutMethod(Base):
    __tablename__ = "payout_methods"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)


class TransferStatus(Base):
    __tablename__ = "transfer_statuses"
    code: Mapped[str] = mapped_column(String(24), primary_key=True)


class AttemptStatus(Base):
    __tablename__ = "attempt_statuses"
    code: Mapped[str] = mapped_column(String(24), primary_key=True)


class LimitPeriodType(Base):
    __tablename__ = "limit_period_types"
    code: Mapped[str] = mapped_column(String(16), primary_key=True)


class Direction(Base):
    __tablename__ = "directions"
    direction_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_country: Mapped[str] = mapped_column(ForeignKey("countries.code"), nullable=False)
    source_currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    target_country: Mapped[str] = mapped_column(ForeignKey("countries.code"), nullable=False)
    target_currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    payout_method: Mapped[str] = mapped_column(ForeignKey("payout_methods.code"), nullable=False)
    fee_percent: Mapped[object] = mapped_column(Numeric(8, 5), nullable=False)
    fee_fixed: Mapped[object] = mapped_column(MONEY, nullable=False)
    fee_min: Mapped[object] = mapped_column(MONEY, nullable=False)
    fee_charged_in: Mapped[str] = mapped_column(String(8), nullable=False)
    min_amount: Mapped[object] = mapped_column(MONEY, nullable=False)
    max_amount: Mapped[object] = mapped_column(MONEY, nullable=False)
    limit_currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    min_margin_percent: Mapped[object] = mapped_column(Numeric(8, 5), nullable=False)
    quote_ttl_sec: Mapped[int] = mapped_column(Integer, nullable=False)


class Provider(Base):
    __tablename__ = "providers"
    provider_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    fee_percent: Mapped[object] = mapped_column(Numeric(8, 5), nullable=False)
    fee_fixed: Mapped[object] = mapped_column(MONEY, nullable=False)
    limit_min: Mapped[object] = mapped_column(MONEY, nullable=False)
    limit_max: Mapped[object] = mapped_column(MONEY, nullable=False)
    success_rate_24h: Mapped[object] = mapped_column(Numeric(6, 5), nullable=False)
    p95_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ProviderDirection(Base):
    __tablename__ = "provider_directions"
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.provider_id"), primary_key=True)
    direction_id: Mapped[str] = mapped_column(
        ForeignKey("directions.direction_id"), primary_key=True
    )


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.provider_id"), nullable=False)
    from_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    to_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Client(Base):
    __tablename__ = "clients"
    client_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    daily_limit_amount: Mapped[object] = mapped_column(MONEY, nullable=False)
    daily_limit_currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)


class Balance(Base):
    __tablename__ = "balances"
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.client_id"), primary_key=True)
    currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), primary_key=True)
    available: Mapped[object] = mapped_column(MONEY, nullable=False)
    reserved: Mapped[object] = mapped_column(MONEY, nullable=False, default=0)


class Quote(Base):
    __tablename__ = "quotes"
    quote_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    direction_id: Mapped[str] = mapped_column(ForeignKey("directions.direction_id"), nullable=False)
    source_amount: Mapped[object] = mapped_column(MONEY, nullable=False)
    source_currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    fee_amount: Mapped[object] = mapped_column(MONEY, nullable=False)
    fee_currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    amount_to_convert: Mapped[object] = mapped_column(MONEY, nullable=False)
    rate: Mapped[object] = mapped_column(Numeric(20, 8), nullable=False)
    target_amount: Mapped[object] = mapped_column(MONEY, nullable=False)
    target_currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Transfer(Base):
    __tablename__ = "transfers"
    transfer_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.quote_id"), nullable=False)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.client_id"), nullable=False)
    status: Mapped[str] = mapped_column(ForeignKey("transfer_statuses.code"), nullable=False)
    source_amount: Mapped[object] = mapped_column(MONEY, nullable=False)
    source_currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    target_amount: Mapped[object] = mapped_column(MONEY, nullable=False)
    target_currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    recipient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_account: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient_bank: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    funds_settled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PayoutAttempt(Base):
    __tablename__ = "payout_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transfer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transfers.transfer_id"), nullable=False
    )
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.provider_id"), nullable=False)
    payout_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reference: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[object] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    status: Mapped[str] = mapped_column(ForeignKey("attempt_statuses.code"), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.provider_id"), nullable=False)
    payout_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(ForeignKey("attempt_statuses.code"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LimitDefinition(Base):
    __tablename__ = "limit_definitions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.client_id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    direction_id: Mapped[str | None] = mapped_column(
        ForeignKey("directions.direction_id"), nullable=True
    )
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    period_type: Mapped[str] = mapped_column(ForeignKey("limit_period_types.code"), nullable=False)
    window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount: Mapped[object] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    weight: Mapped[object] = mapped_column(Numeric(8, 5), nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("client_id", "scope", "period_type", name="uq_limit_client_scope_period"),
    )
