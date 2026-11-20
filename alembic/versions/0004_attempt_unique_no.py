"""Unique constraint on payout attempt numbering per transfer."""

from __future__ import annotations

from alembic import op

revision: str = "0004_attempt_unique_no"
down_revision: str | None = "0003_maintenance_window_unique"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        delete from payout_attempts a using payout_attempts b
        where a.id > b.id
          and a.transfer_id = b.transfer_id
          and a.attempt_no = b.attempt_no
        """
    )
    op.create_unique_constraint(
        "uq_attempt_transfer_no", "payout_attempts", ["transfer_id", "attempt_no"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_attempt_transfer_no", "payout_attempts", type_="unique")
