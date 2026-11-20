"""Normalization: direction-scoped limit uniqueness, drop dead category column,
foreign keys on history statuses, value checks, missing FK indexes."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0007_normalization"
down_revision: str | None = "0006_limit_scope_check"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_limit_client_scope_period", "limit_definitions", type_="unique")
    op.create_unique_constraint(
        "uq_limit_client_scope_period_dir",
        "limit_definitions",
        ["client_id", "scope", "period_type", "direction_id"],
    )
    op.drop_column("limit_definitions", "category")
    op.create_check_constraint(
        "ck_limit_direction_required",
        "limit_definitions",
        "scope <> 'direction' OR direction_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_directions_fee_charged_in",
        "directions",
        "fee_charged_in in ('SOURCE', 'TARGET')",
    )
    op.create_foreign_key(
        "fk_history_from_status",
        "transfer_status_history",
        "transfer_statuses",
        ["from_status"],
        ["code"],
    )
    op.create_foreign_key(
        "fk_history_to_status",
        "transfer_status_history",
        "transfer_statuses",
        ["to_status"],
        ["code"],
    )
    op.create_index("ix_payout_attempts_provider", "payout_attempts", ["provider_id"])
    op.create_index("ix_webhook_events_provider", "webhook_events", ["provider_id"])
    op.create_index("ix_limit_definitions_direction", "limit_definitions", ["direction_id"])


def downgrade() -> None:
    op.drop_constraint("uq_limit_client_scope_period_dir", "limit_definitions", type_="unique")
    op.create_unique_constraint(
        "uq_limit_client_scope_period",
        "limit_definitions",
        ["client_id", "scope", "period_type"],
    )
    op.add_column("limit_definitions", sa.Column("category", sa.String(64), nullable=True))
    op.drop_constraint("ck_limit_direction_required", "limit_definitions", type_="check")
    op.drop_constraint("ck_directions_fee_charged_in", "directions", type_="check")
    op.drop_constraint("fk_history_from_status", "transfer_status_history", type_="foreignkey")
    op.drop_constraint("fk_history_to_status", "transfer_status_history", type_="foreignkey")
    op.drop_index("ix_payout_attempts_provider", table_name="payout_attempts")
    op.drop_index("ix_webhook_events_provider", table_name="webhook_events")
    op.drop_index("ix_limit_definitions_direction", table_name="limit_definitions")
