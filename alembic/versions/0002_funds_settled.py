"""Add funds_settled guard column."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0002_funds_settled"
down_revision: str | None = "0001_initial"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "transfers",
        sa.Column("funds_settled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("transfers", "funds_settled")
