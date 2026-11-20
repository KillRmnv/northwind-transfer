"""Status history, quote breakdown, balance spent + checks, per-client idempotency."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.models.guid import GUID

revision: str = "0005_history_spent_breakdown"
down_revision: str | None = "0004_attempt_unique_no"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "transfer_status_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("transfer_id", GUID(), sa.ForeignKey("transfers.transfer_id"), nullable=False),
        sa.Column("from_status", sa.String(24), nullable=True),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_transfer_status_history_transfer", "transfer_status_history", ["transfer_id"]
    )
    op.add_column("quotes", sa.Column("breakdown", sa.JSON(), nullable=True))
    op.add_column(
        "balances", sa.Column("spent", sa.Numeric(20, 8), nullable=False, server_default="0")
    )
    op.create_check_constraint("ck_balances_available_non_negative", "balances", "available >= 0")
    op.create_check_constraint("ck_balances_reserved_non_negative", "balances", "reserved >= 0")
    op.create_check_constraint("ck_balances_spent_non_negative", "balances", "spent >= 0")
    op.drop_constraint("transfers_idempotency_key_key", "transfers", type_="unique")
    op.create_unique_constraint(
        "uq_transfers_client_key", "transfers", ["client_id", "idempotency_key"]
    )
    op.create_index("ix_transfers_updated", "transfers", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_transfers_updated", table_name="transfers")
    op.drop_constraint("uq_transfers_client_key", "transfers", type_="unique")
    op.create_unique_constraint("transfers_idempotency_key_key", "transfers", ["idempotency_key"])
    op.drop_constraint("ck_balances_spent_non_negative", "balances", type_="check")
    op.drop_constraint("ck_balances_reserved_non_negative", "balances", type_="check")
    op.drop_constraint("ck_balances_available_non_negative", "balances", type_="check")
    op.drop_column("balances", "spent")
    op.drop_column("quotes", "breakdown")
    op.drop_index("ix_transfer_status_history_transfer", table_name="transfer_status_history")
    op.drop_table("transfer_status_history")
