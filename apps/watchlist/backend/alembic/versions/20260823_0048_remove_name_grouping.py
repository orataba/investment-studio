"""Remove meaningless single-instrument grouping by name.

Revision ID: 20260823_0048
Revises: 20260823_0047
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260823_0048"
down_revision = "20260823_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    field_registry = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("group_mode", sa.String()),
    )
    watchlist_view = sa.table(
        "watchlist_view",
        sa.column("default_group_by", sa.String()),
    )
    bind.execute(
        sa.update(field_registry)
        .where(field_registry.c.field_key == "instrument_name")
        .values(group_mode="none")
    )
    bind.execute(
        sa.update(watchlist_view)
        .where(watchlist_view.c.default_group_by == "instrument_name")
        .values(default_group_by="none")
    )


def downgrade() -> None:
    bind = op.get_bind()
    field_registry = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("group_mode", sa.String()),
    )
    bind.execute(
        sa.update(field_registry)
        .where(field_registry.c.field_key == "instrument_name")
        .values(group_mode="discrete")
    )
