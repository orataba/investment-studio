"""add status to default overview

Revision ID: 20260501_0015
Revises: 20260501_0014
Create Date: 2026-05-01 17:25:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260501_0015"
down_revision = "20260501_0014"
branch_labels = None
depends_on = None


OVERVIEW_COLUMNS = [
    ("asset_name", 1, 320),
    ("attr.coverage_status", 2, 110),
    ("price_chart_1m", 3, 140),
    ("latest_quote", 4, 130),
    ("latest_quote_date", 5, 140),
    ("return_1w", 6, 150),
    ("return_mtd", 7, 120),
    ("return_ytd", 8, 150),
    ("attr.current_drawdown", 9, 120),
    ("attr.peer_overall_percentile", 10, 120),
    ("data_freshness_status", 11, 140),
]

PREVIOUS_OVERVIEW_COLUMNS = [
    ("asset_name", 1, 320),
    ("price_chart_1m", 2, 140),
    ("latest_quote", 3, 130),
    ("latest_quote_date", 4, 140),
    ("return_1w", 5, 150),
    ("return_mtd", 6, 120),
    ("return_ytd", 7, 150),
    ("attr.current_drawdown", 8, 120),
    ("attr.peer_overall_percentile", 9, 120),
    ("data_freshness_status", 10, 140),
]

STATUS_OPTIONS = ["Watch", "Proposed", "Invested", "Paused", "Exited"]
PREVIOUS_STATUS_OPTIONS = ["Invested", "Focus", "Watch", "Archived"]
STATUS_DESCRIPTION = (
    "Research lifecycle status such as watch, proposed, invested, paused, or exited."
)
PREVIOUS_STATUS_DESCRIPTION = "Research status such as invested, focus, watch, or archived."


def _tables() -> tuple[sa.TableClause, sa.TableClause, sa.TableClause, sa.TableClause]:
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
        sa.column("description", sa.String()),
        sa.column("default_width", sa.Integer()),
    )
    attribute_table = sa.table(
        "instrument_attribute_definition",
        sa.column("attribute_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.String()),
        sa.column("options_json", sa.JSON()),
    )
    return view_table, column_table, field_table, attribute_table


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
    view_table, column_table, field_table, attribute_table = _tables()
    bind.execute(
        sa.update(attribute_table)
        .where(attribute_table.c.attribute_key == "coverage_status")
        .values(
            label="Status",
            description=STATUS_DESCRIPTION,
            options_json=STATUS_OPTIONS,
        )
    )
    bind.execute(
        sa.update(field_table)
        .where(field_table.c.field_key == "attr.coverage_status")
        .values(
            label="Status",
            description=STATUS_DESCRIPTION,
            default_width=110,
        )
    )
    _replace_overview_columns(bind, view_table, column_table, OVERVIEW_COLUMNS)


def downgrade() -> None:
    bind = op.get_bind()
    view_table, column_table, field_table, attribute_table = _tables()
    _replace_overview_columns(bind, view_table, column_table, PREVIOUS_OVERVIEW_COLUMNS)
    bind.execute(
        sa.update(attribute_table)
        .where(attribute_table.c.attribute_key == "coverage_status")
        .values(
            label="Coverage Status",
            description=PREVIOUS_STATUS_DESCRIPTION,
            options_json=PREVIOUS_STATUS_OPTIONS,
        )
    )
    bind.execute(
        sa.update(field_table)
        .where(field_table.c.field_key == "attr.coverage_status")
        .values(
            label="Coverage Status",
            description=PREVIOUS_STATUS_DESCRIPTION,
            default_width=160,
        )
    )
