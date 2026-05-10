"""refresh overview watchlist columns

Revision ID: c2d8f1b7a4e3
Revises: f6d4c7f0a9b1
Create Date: 2026-04-16 02:10:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c2d8f1b7a4e3"
down_revision = "f6d4c7f0a9b1"
branch_labels = None
depends_on = None


OVERVIEW_COLUMNS = [
    ("instrument_name", 1, 320),
    ("price_chart_1m", 2, 140),
    ("latest_quote", 3, 130),
    ("latest_quote_date", 4, 140),
    ("return_1w", 5, 150),
    ("return_ytd", 6, 150),
    ("ticker_or_isin", 7, 140),
    ("data_freshness_status", 8, 140),
]


def upgrade() -> None:
    bind = op.get_bind()
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("kind", sa.String()),
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

    overview_view_ids = [
        row[0]
        for row in bind.execute(
            sa.select(view_table.c.watchlist_view_id).where(
                view_table.c.watchlist_view_id.like("%::overview"),
                view_table.c.kind == "system",
            )
        )
    ]
    if not overview_view_ids:
        return

    bind.execute(
        sa.delete(column_table).where(column_table.c.watchlist_view_id.in_(overview_view_ids))
    )
    op.bulk_insert(
        column_table,
        [
            {
                "watchlist_view_id": view_id,
                "field_key": field_key,
                "display_order": display_order,
                "width": width,
                "is_visible": True,
                "pin_side": None,
            }
            for view_id in overview_view_ids
            for field_key, display_order, width in OVERVIEW_COLUMNS
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    column_table = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("field_key", sa.String()),
    )
    overview_view_ids = [
        row[0]
        for row in bind.execute(
            sa.text(
                "select watchlist_view_id from watchlist_view where kind = 'system' and watchlist_view_id like :pattern"
            ),
            {"pattern": "%::overview"},
        )
    ]
    if not overview_view_ids:
        return

    bind.execute(
        sa.delete(column_table).where(column_table.c.watchlist_view_id.in_(overview_view_ids))
    )
    op.bulk_insert(
        column_table,
        [
            {
                "watchlist_view_id": view_id,
                "field_key": field_key,
                "display_order": display_order,
                "width": width,
                "is_visible": True,
                "pin_side": None,
            }
            for view_id in overview_view_ids
            for field_key, display_order, width in (
                ("instrument_name", 1, 320),
                ("instrument_type", 2, 140),
                ("data_freshness_status", 3, 140),
            )
        ],
    )
