"""Check constraint on limit_definitions.scope values."""

from __future__ import annotations

from alembic import op

revision: str = "0006_limit_scope_check"
down_revision: str | None = "0005_history_spent_breakdown"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("delete from limit_definitions where scope not in ('client', 'direction')")
    op.create_check_constraint(
        "ck_limit_definitions_scope",
        "limit_definitions",
        "scope in ('client', 'direction')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_limit_definitions_scope", "limit_definitions", type_="check")
