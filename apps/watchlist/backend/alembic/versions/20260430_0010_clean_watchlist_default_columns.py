"""remove taxonomy hierarchy columns from default watchlist views

Revision ID: 20260430_0010
Revises: 20260425_0009
Create Date: 2026-04-30 11:05:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260430_0010"
down_revision = "20260425_0009"
branch_labels = None
depends_on = None


REMOVED_DEFAULT_COLUMN_KEYS = [
    "attr.fund_regime",
    "attr.fund_taxonomy_level_1",
    "attr.fund_taxonomy_level_2",
    "attr.fund_taxonomy_level_3",
    "attr.fund_taxonomy_level_4",
    "attr.fund_taxonomy_level_5",
    "attr.fund_taxonomy_level_6",
]

PREVIOUS_OVERVIEW_COLUMNS = [
    ("instrument_name", 1, 320),
    ("attr.fund_regime", 2, 120),
    ("attr.fund_taxonomy_level_1", 3, 150),
    ("attr.fund_taxonomy_level_2", 4, 170),
    ("price_chart_1m", 5, 140),
    ("latest_quote", 6, 130),
    ("latest_quote_date", 7, 140),
    ("return_1w", 8, 150),
    ("return_1m", 9, 150),
    ("return_ytd", 10, 150),
    ("ticker_or_isin", 11, 140),
    ("data_freshness_status", 12, 140),
]

PREVIOUS_FUND_SCREENING_COLUMNS = [
    ("instrument_name", 1, 320),
    ("attr.fund_regime", 2, 120),
    ("attr.fund_taxonomy_level_1", 3, 150),
    ("attr.fund_taxonomy_level_2", 4, 170),
    ("attr.fund_taxonomy_level_3", 5, 180),
    ("attr.implementation_style", 6, 140),
    ("attr.style_profile", 7, 220),
    ("attr.manager_assessment", 8, 220),
    ("attr.volatility_bucket", 9, 120),
    ("attr.drawdown_control", 10, 120),
    ("attr.style_stability", 11, 120),
    ("attr.transparency_quality", 12, 120),
    ("data_freshness_status", 13, 140),
]


def _tables() -> tuple[sa.TableClause, sa.TableClause]:
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
    return view_table, column_table


def _system_view_ids(bind, view_table: sa.TableClause) -> list[str]:
    return [
        str(row["watchlist_view_id"])
        for row in bind.execute(
            sa.select(view_table.c.watchlist_view_id).where(view_table.c.kind == "system")
        ).mappings()
    ]


def _resequence_columns(bind, column_table: sa.TableClause, view_ids: list[str]) -> None:
    for view_id in view_ids:
        rows = list(
            bind.execute(
                sa.select(column_table.c.field_key, column_table.c.display_order)
                .where(column_table.c.watchlist_view_id == view_id)
                .order_by(column_table.c.display_order, column_table.c.field_key)
            ).mappings()
        )
        for display_order, row in enumerate(rows, start=1):
            if row["display_order"] == display_order:
                continue
            bind.execute(
                sa.update(column_table)
                .where(
                    column_table.c.watchlist_view_id == view_id,
                    column_table.c.field_key == row["field_key"],
                )
                .values(display_order=display_order)
            )


def _replace_view_columns(
    bind,
    view_table: sa.TableClause,
    column_table: sa.TableClause,
    view_suffix: str,
    columns: list[tuple[str, int, int]],
) -> None:
    view_ids = [
        str(row["watchlist_view_id"])
        for row in bind.execute(
            sa.select(view_table.c.watchlist_view_id).where(
                view_table.c.kind == "system",
                view_table.c.watchlist_view_id.like(f"%::{view_suffix}"),
            )
        ).mappings()
    ]
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
    view_table, column_table = _tables()
    view_ids = _system_view_ids(bind, view_table)
    if not view_ids:
        return

    bind.execute(
        sa.delete(column_table).where(
            column_table.c.watchlist_view_id.in_(view_ids),
            column_table.c.field_key.in_(REMOVED_DEFAULT_COLUMN_KEYS),
        )
    )
    _resequence_columns(bind, column_table, view_ids)


def downgrade() -> None:
    bind = op.get_bind()
    view_table, column_table = _tables()
    _replace_view_columns(bind, view_table, column_table, "overview", PREVIOUS_OVERVIEW_COLUMNS)
    _replace_view_columns(
        bind,
        view_table,
        column_table,
        "fund-screening",
        PREVIOUS_FUND_SCREENING_COLUMNS,
    )
