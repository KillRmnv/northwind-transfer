"""Unique constraint for maintenance windows to make seed idempotent."""

from __future__ import annotations

from alembic import op

revision: str = "0003_maintenance_window_unique"
down_revision: str | None = "0002_funds_settled"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        delete from maintenance_windows a using maintenance_windows b
        where a.id > b.id
          and a.provider_id = b.provider_id
          and a.from_at = b.from_at
          and a.to_at = b.to_at
        """
    )
    op.create_unique_constraint(
        "uq_maintenance_window",
        "maintenance_windows",
        ["provider_id", "from_at", "to_at"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_maintenance_window", "maintenance_windows", type_="unique")
