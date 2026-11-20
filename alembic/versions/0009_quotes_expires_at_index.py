"""Index for retention purge of expired quotes."""

from __future__ import annotations

from alembic import op

revision: str = "0009_quotes_expires_at_index"
down_revision: str | None = "0008_hot_path_indexes"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_index("ix_quotes_expires_at", "quotes", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_quotes_expires_at", table_name="quotes")
