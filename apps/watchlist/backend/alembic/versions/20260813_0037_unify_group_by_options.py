"""Unify system Watchlist grouping on the canonical taxonomy.

Revision ID: 20260813_0037
Revises: 20260809_0036
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260813_0037"
down_revision = "20260809_0036"
branch_labels = None
depends_on = None


ALLOWED_GROUP_BYS = (
    "none",
    "instrument_type",
    "taxonomy",
    "data_freshness_status",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    view = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("default_group_by", sa.String()),
    )
    bind.execute(
        sa.update(view)
        .where(
            sa.or_(
                view.c.default_group_by.is_(None),
                view.c.default_group_by.not_in(ALLOWED_GROUP_BYS),
            )
        )
        .values(default_group_by="none")
    )
    bind.execute(
        sa.update(view)
        .where(view.c.kind == "system")
        .where(view.c.watchlist_view_id.like("%::fund-screening"))
        .values(default_group_by="taxonomy")
    )


def downgrade() -> None:
    view = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("default_group_by", sa.String()),
    )
    op.get_bind().execute(
        sa.update(view)
        .where(view.c.kind == "system")
        .where(view.c.watchlist_view_id.like("%::fund-screening"))
        .where(view.c.watchlist_view_id != "all-coverage::fund-screening")
        .values(default_group_by="attr.fund_taxonomy_level_1")
    )
