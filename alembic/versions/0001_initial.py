"""Initial schema: Transfer Core 3NF reference and operational tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.models.guid import GUID

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

MONEY = sa.Numeric(20, 8)


def upgrade() -> None:
    op.create_table(
        "currencies",
        sa.Column("code", sa.String(3), primary_key=True),
        sa.Column("minor_units", sa.SmallInteger(), nullable=False),
    )
    op.create_table(
        "countries",
        sa.Column("code", sa.String(2), primary_key=True),
    )
    op.create_table(
        "payout_methods",
        sa.Column("code", sa.String(32), primary_key=True),
    )
    op.create_table(
        "transfer_statuses",
        sa.Column("code", sa.String(24), primary_key=True),
    )
    op.create_table(
        "attempt_statuses",
        sa.Column("code", sa.String(24), primary_key=True),
    )
    op.create_table(
        "limit_period_types",
        sa.Column("code", sa.String(16), primary_key=True),
    )
    op.create_table(
        "directions",
        sa.Column("direction_id", sa.String(32), primary_key=True),
        sa.Column("source_country", sa.String(2), sa.ForeignKey("countries.code"), nullable=False),
        sa.Column("source_currency", sa.String(3), sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("target_country", sa.String(2), sa.ForeignKey("countries.code"), nullable=False),
        sa.Column("target_currency", sa.String(3), sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("payout_method", sa.String(32), sa.ForeignKey("payout_methods.code"), nullable=False),
        sa.Column("fee_percent", sa.Numeric(8, 5), nullable=False),
        sa.Column("fee_fixed", MONEY, nullable=False),
        sa.Column("fee_min", MONEY, nullable=False),
        sa.Column("fee_charged_in", sa.String(8), nullable=False),
        sa.Column("min_amount", MONEY, nullable=False),
        sa.Column("max_amount", MONEY, nullable=False),
        sa.Column("limit_currency", sa.String(3), sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("min_margin_percent", sa.Numeric(8, 5), nullable=False),
        sa.Column("quote_ttl_sec", sa.Integer(), nullable=False),
    )
    op.create_table(
        "providers",
        sa.Column("provider_id", sa.String(32), primary_key=True),
        sa.Column("currency", sa.String(3), sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("fee_percent", sa.Numeric(8, 5), nullable=False),
        sa.Column("fee_fixed", MONEY, nullable=False),
        sa.Column("limit_min", MONEY, nullable=False),
        sa.Column("limit_max", MONEY, nullable=False),
        sa.Column("success_rate_24h", sa.Numeric(6, 5), nullable=False),
        sa.Column("p95_latency_ms", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "provider_directions",
        sa.Column("provider_id", sa.String(32), sa.ForeignKey("providers.provider_id"), primary_key=True),
        sa.Column("direction_id", sa.String(32), sa.ForeignKey("directions.direction_id"), primary_key=True),
    )
    op.create_index(
        "ix_provider_directions_direction", "provider_directions", ["direction_id"]
    )
    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider_id", sa.String(32), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("from_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("to_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_maintenance_windows_provider", "maintenance_windows", ["provider_id", "from_at", "to_at"]
    )
    op.create_table(
        "clients",
        sa.Column("client_id", sa.String(32), primary_key=True),
        sa.Column("daily_limit_amount", MONEY, nullable=False),
        sa.Column(
            "daily_limit_currency", sa.String(3), sa.ForeignKey("currencies.code"), nullable=False
        ),
    )
    op.create_table(
        "balances",
        sa.Column("client_id", sa.String(32), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("currency", sa.String(3), sa.ForeignKey("currencies.code"), primary_key=True),
        sa.Column("available", MONEY, nullable=False),
        sa.Column("reserved", MONEY, nullable=False, server_default="0"),
    )
    op.create_table(
        "quotes",
        sa.Column("quote_id", GUID(), primary_key=True),
        sa.Column("direction_id", sa.String(32), sa.ForeignKey("directions.direction_id"), nullable=False),
        sa.Column("source_amount", MONEY, nullable=False),
        sa.Column("source_currency", sa.String(3), sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("fee_amount", MONEY, nullable=False),
        sa.Column("fee_currency", sa.String(3), sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("amount_to_convert", MONEY, nullable=False),
        sa.Column("rate", sa.Numeric(20, 8), nullable=False),
        sa.Column("target_amount", MONEY, nullable=False),
        sa.Column("target_currency", sa.String(3), sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_quotes_direction", "quotes", ["direction_id"])
    op.create_table(
        "transfers",
        sa.Column("transfer_id", GUID(), primary_key=True),
        sa.Column("quote_id", GUID(), sa.ForeignKey("quotes.quote_id"), nullable=False),
        sa.Column("client_id", sa.String(32), sa.ForeignKey("clients.client_id"), nullable=False),
        sa.Column("status", sa.String(24), sa.ForeignKey("transfer_statuses.code"), nullable=False),
        sa.Column("source_amount", MONEY, nullable=False),
        sa.Column("source_currency", sa.String(3), sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("target_amount", MONEY, nullable=False),
        sa.Column("target_currency", sa.String(3), sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("recipient_name", sa.String(255), nullable=False),
        sa.Column("recipient_account", sa.String(64), nullable=False),
        sa.Column("recipient_bank", sa.String(32), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("idempotency_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_transfers_client_created", "transfers", ["client_id", "created_at"])
    op.create_index("ix_transfers_quote", "transfers", ["quote_id"])
    op.create_table(
        "payout_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("transfer_id", GUID(), sa.ForeignKey("transfers.transfer_id"), nullable=False),
        sa.Column("provider_id", sa.String(32), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("payout_id", sa.String(64), nullable=True),
        sa.Column("reference", sa.String(64), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("status", sa.String(24), sa.ForeignKey("attempt_statuses.code"), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_payout_attempts_transfer", "payout_attempts", ["transfer_id"])
    op.create_index("ix_payout_attempts_reference", "payout_attempts", ["reference"])
    op.create_table(
        "webhook_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("provider_id", sa.String(32), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("payout_id", sa.String(64), nullable=True),
        sa.Column("reference", sa.String(64), nullable=True),
        sa.Column("status", sa.String(24), sa.ForeignKey("attempt_statuses.code"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_webhook_events_reference", "webhook_events", ["reference"])
    op.create_table(
        "limit_definitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(32), sa.ForeignKey("clients.client_id"), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("direction_id", sa.String(32), sa.ForeignKey("directions.direction_id"), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("period_type", sa.String(16), sa.ForeignKey("limit_period_types.code"), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=True),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("weight", sa.Numeric(8, 5), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("client_id", "scope", "period_type", name="uq_limit_client_scope_period"),
    )
    op.create_index("ix_limit_definitions_client", "limit_definitions", ["client_id"])


def downgrade() -> None:
    for table in [
        "limit_definitions",
        "webhook_events",
        "payout_attempts",
        "transfers",
        "quotes",
        "balances",
        "clients",
        "maintenance_windows",
        "provider_directions",
        "providers",
        "directions",
        "limit_period_types",
        "attempt_statuses",
        "transfer_statuses",
        "payout_methods",
        "countries",
        "currencies",
    ]:
        op.drop_table(table)
