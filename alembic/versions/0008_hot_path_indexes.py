"""Hot-path indexes: composite payout lookup, partial open-transfers queue,
covering index for limit window aggregations."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0008_hot_path_indexes"
down_revision: str | None = "0007_normalization"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

OPEN_STATUSES = "status IN ('PENDING', 'PROCESSING', 'NEEDS_REVIEW')"


def upgrade() -> None:
    op.drop_index("ix_payout_attempts_provider", table_name="payout_attempts")
    op.create_index(
        "ix_payout_attempts_provider_payout",
        "payout_attempts",
        ["provider_id", "payout_id"],
    )
    op.create_index(
        "ix_transfers_open_updated",
        "transfers",
        ["updated_at"],
        postgresql_where=sa.text(OPEN_STATUSES),
    )
    op.drop_index("ix_transfers_client_created", table_name="transfers")
    op.create_index(
        "ix_transfers_client_created",
        "transfers",
        ["client_id", "created_at"],
        postgresql_include=["status", "source_amount", "source_currency", "quote_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_payout_attempts_provider_payout", table_name="payout_attempts")
    op.create_index("ix_payout_attempts_provider", "payout_attempts", ["provider_id"])
    op.drop_index("ix_transfers_open_updated", table_name="transfers")
    op.drop_index("ix_transfers_client_created", table_name="transfers")
    op.create_index("ix_transfers_client_created", "transfers", ["client_id", "created_at"])
