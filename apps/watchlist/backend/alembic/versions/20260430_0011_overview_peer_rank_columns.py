"""add peer rank to default overview columns

Revision ID: 20260430_0011
Revises: 20260430_0010
Create Date: 2026-04-30 23:35:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260430_0011"
down_revision = "20260430_0010"
branch_labels = None
depends_on = None


OVERVIEW_COLUMNS = [
    ("instrument_name", 1, 320),
    ("price_chart_1m", 2, 140),
    ("latest_quote", 3, 130),
    ("latest_quote_date", 4, 140),
    ("return_1w", 5, 150),
    ("return_1m", 6, 150),
    ("return_ytd", 7, 150),
    ("attr.peer_overall_percentile", 8, 120),
    ("data_freshness_status", 9, 140),
]

PREVIOUS_OVERVIEW_COLUMNS = [
    ("instrument_name", 1, 320),
    ("price_chart_1m", 2, 140),
    ("latest_quote", 3, 130),
    ("latest_quote_date", 4, 140),
    ("return_1w", 5, 150),
    ("return_1m", 6, 150),
    ("return_ytd", 7, 150),
    ("ticker_or_isin", 8, 140),
    ("data_freshness_status", 9, 140),
]


def _tables() -> tuple[sa.TableClause, sa.TableClause, sa.TableClause]:
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
    field_table = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("default_width", sa.Integer()),
    )
    return view_table, column_table, field_table


def _system_overview_view_ids(bind, view_table: sa.TableClause) -> list[str]:
    return [
        str(row["watchlist_view_id"])
        for row in bind.execute(
            sa.select(view_table.c.watchlist_view_id).where(
                view_table.c.kind == "system",
                view_table.c.watchlist_view_id.like("%::overview"),
            )
        ).mappings()
    ]


def _replace_overview_columns(
    bind,
    view_table: sa.TableClause,
    column_table: sa.TableClause,
    columns: list[tuple[str, int, int]],
) -> None:
    view_ids = _system_overview_view_ids(bind, view_table)
    if not view_ids:
        return

    bind.execute(sa.delete(column_table).where(column_table.c.watchlist_view_id.in_(view_ids)))
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
            for view_id in view_ids
            for field_key, display_order, width in columns
        ],
    )


def upgrade() -> None:
    bind = op.get_bind()
    view_table, column_table, field_table = _tables()
    bind.execute(
        sa.update(field_table)
        .where(field_table.c.field_key == "attr.peer_overall_percentile")
        .values(label="同类排名", default_width=120)
    )
    _replace_overview_columns(bind, view_table, column_table, OVERVIEW_COLUMNS)


def downgrade() -> None:
    bind = op.get_bind()
    view_table, column_table, field_table = _tables()
    bind.execute(
        sa.update(field_table)
        .where(field_table.c.field_key == "attr.peer_overall_percentile")
        .values(label="Peer Overall Percentile", default_width=170)
    )
    _replace_overview_columns(bind, view_table, column_table, PREVIOUS_OVERVIEW_COLUMNS)
