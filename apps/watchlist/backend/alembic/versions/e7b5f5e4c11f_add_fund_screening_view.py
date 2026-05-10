"""add fund screening view

Revision ID: e7b5f5e4c11f
Revises: d0f6c3a2b9ef
Create Date: 2026-04-16 00:30:00.000000

"""

from __future__ import annotations

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e7b5f5e4c11f"
down_revision = "d0f6c3a2b9ef"
branch_labels = None
depends_on = None


VIEW_ID = "fund-screening"
VIEW_NAME = "基金筛选"
VIEW_DESCRIPTION = "按分类、研究标签和监控判断快速筛选基金。"
VIEW_COLUMNS = [
    ("instrument_name", 1, 320),
    ("attr.strategy_family", 2, 140),
    ("attr.strategy_subtype", 3, 220),
    ("attr.implementation_style", 4, 140),
    ("attr.volatility_bucket", 5, 120),
    ("attr.drawdown_control", 6, 120),
    ("attr.equity_correlation_bucket", 7, 140),
    ("attr.preferred_regime", 8, 220),
    ("attr.weak_regime", 9, 220),
    ("attr.style_stability", 10, 120),
    ("attr.transparency_quality", 11, 120),
    ("data_freshness_status", 12, 140),
]


def upgrade() -> None:
    bind = op.get_bind()
    watchlist_table = sa.table(
        "watchlist",
        sa.column("watchlist_id", sa.String()),
    )
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("watchlist_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
        sa.column("default_group_by", sa.String()),
        sa.column("density", sa.String()),
        sa.column("is_default", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    column_table = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("field_key", sa.String()),
        sa.column("display_order", sa.Integer()),
        sa.column("width", sa.Integer()),
        sa.column("is_visible", sa.Boolean()),
        sa.column("pin_side", sa.String()),
    )

    watchlist_ids = [row[0] for row in bind.execute(sa.select(watchlist_table.c.watchlist_id))]
    if not watchlist_ids:
        return

    scoped_view_ids = [f"{watchlist_id}::{VIEW_ID}" for watchlist_id in watchlist_ids]
    existing_view_ids = {
        row[0]
        for row in bind.execute(
            sa.select(view_table.c.watchlist_view_id).where(
                view_table.c.watchlist_view_id.in_(scoped_view_ids)
            )
        )
    }
    missing_watchlist_ids = [
        watchlist_id
        for watchlist_id in watchlist_ids
        if f"{watchlist_id}::{VIEW_ID}" not in existing_view_ids
    ]
    if not missing_watchlist_ids:
        return

    created_at = datetime(2026, 4, 16, 0, 30, tzinfo=UTC)
    op.bulk_insert(
        view_table,
        [
            {
                "watchlist_view_id": f"{watchlist_id}::{VIEW_ID}",
                "watchlist_id": watchlist_id,
                "name": VIEW_NAME,
                "description": VIEW_DESCRIPTION,
                "kind": "system",
                "default_sort_json": [],
                "default_filters_json": {"instrument_type": ["fund"]},
                "default_advanced_filter_json": {},
                "default_group_by": "attr.strategy_family",
                "density": "standard",
                "is_default": False,
                "created_at": created_at,
            }
            for watchlist_id in missing_watchlist_ids
        ],
    )
    op.bulk_insert(
        column_table,
        [
            {
                "watchlist_view_id": f"{watchlist_id}::{VIEW_ID}",
                "field_key": field_key,
                "display_order": display_order,
                "width": width,
                "is_visible": True,
                "pin_side": None,
            }
            for watchlist_id in missing_watchlist_ids
            for field_key, display_order, width in VIEW_COLUMNS
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
    )
    column_table = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
    )

    watchlist_view_ids = [
        row[0]
        for row in bind.execute(
            sa.select(view_table.c.watchlist_view_id).where(
                view_table.c.watchlist_view_id.like(f"%::{VIEW_ID}")
            )
        )
    ]
    if not watchlist_view_ids:
        return

    bind.execute(
        sa.delete(column_table).where(column_table.c.watchlist_view_id.in_(watchlist_view_ids))
    )
    bind.execute(
        sa.delete(view_table).where(view_table.c.watchlist_view_id.in_(watchlist_view_ids))
    )
