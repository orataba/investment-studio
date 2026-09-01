"""Retire the duplicate Holdings Portfolio Total view.

Revision ID: 20260902_0057
Revises: 20260824_0056
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260902_0057"
down_revision: str | None = "20260824_0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table_view_store = sa.table(
        "portfolio_table_view_store",
        sa.column("view_scope", sa.String()),
    )
    op.get_bind().execute(
        sa.delete(table_view_store).where(
            table_view_store.c.view_scope == "holdings_total"
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "The retired Holdings Portfolio Total view state cannot be reconstructed. "
        "Restore the pre-migration database backup instead of downgrading."
    )
