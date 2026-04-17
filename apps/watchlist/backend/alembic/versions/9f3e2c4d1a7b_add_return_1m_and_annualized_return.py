"""add return_1m and annualized_return watchlist fields

Revision ID: 9f3e2c4d1a7b
Revises: c2d8f1b7a4e3
Create Date: 2026-04-16 21:55:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9f3e2c4d1a7b"
down_revision = "c2d8f1b7a4e3"
branch_labels = None
depends_on = None


NEW_FIELDS = [
    {
        "field_key": "return_1m",
        "label": "Total Return (1M)",
        "description": "1-month total return.",
        "category_code": "performance_risk",
        "data_type": "number",
        "formatter_code": "percent",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "none",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["performance_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "performance_snapshot.return_1m",
        "default_width": 150,
        "default_visible": True,
    },
    {
        "field_key": "annualized_return",
        "label": "Annualized Return",
        "description": "Since-inception annualized total return.",
        "category_code": "performance_risk",
        "data_type": "number",
        "formatter_code": "percent",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "none",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["performance_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "performance_snapshot.annualized_return",
        "default_width": 160,
        "default_visible": False,
    },
]


OVERVIEW_COLUMNS = [
    ("asset_name", 1, 320),
    ("price_chart_1m", 2, 140),
    ("latest_quote", 3, 130),
    ("latest_quote_date", 4, 140),
    ("return_1w", 5, 150),
    ("return_1m", 6, 150),
    ("return_ytd", 7, 150),
    ("ticker_or_isin", 8, 140),
    ("data_freshness_status", 9, 140),
]


PREVIOUS_OVERVIEW_COLUMNS = [
    ("asset_name", 1, 320),
    ("price_chart_1m", 2, 140),
    ("latest_quote", 3, 130),
    ("latest_quote_date", 4, 140),
    ("return_1w", 5, 150),
    ("return_ytd", 6, 150),
    ("ticker_or_isin", 7, 140),
    ("data_freshness_status", 8, 140),
]


def _refresh_overview_columns(bind, columns: list[tuple[str, int, int]]) -> None:
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
            for field_key, display_order, width in columns
        ],
    )


def upgrade() -> None:
    op.add_column(
        "watchlist_row_read_model",
        sa.Column("return_1m", sa.Numeric(precision=12, scale=6), nullable=True),
    )
    op.add_column(
        "watchlist_row_read_model",
        sa.Column("annualized_return", sa.Numeric(precision=12, scale=6), nullable=True),
    )

    bind = op.get_bind()
    field_table = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.String()),
        sa.column("category_code", sa.String()),
        sa.column("data_type", sa.String()),
        sa.column("formatter_code", sa.String()),
        sa.column("sort_mode", sa.String()),
        sa.column("filter_mode", sa.String()),
        sa.column("group_mode", sa.String()),
        sa.column("asset_scope_json", sa.JSON()),
        sa.column("product_scope_json", sa.JSON()),
        sa.column("availability_rule_json", sa.JSON()),
        sa.column("source_domain", sa.String()),
        sa.column("source_metric_code", sa.String()),
        sa.column("default_width", sa.Integer()),
        sa.column("default_visible", sa.Boolean()),
    )
    bind.execute(
        sa.delete(field_table).where(
            field_table.c.field_key.in_([item["field_key"] for item in NEW_FIELDS])
        )
    )
    op.bulk_insert(field_table, NEW_FIELDS)

    perf_table = sa.table(
        "performance_snapshot",
        sa.column("asset_id", sa.String()),
        sa.column("as_of_date", sa.Date()),
        sa.column("is_current", sa.Boolean()),
        sa.column("return_1m", sa.Numeric(precision=12, scale=6)),
        sa.column("annualized_return", sa.Numeric(precision=12, scale=6)),
    )
    row_table = sa.table(
        "watchlist_row_read_model",
        sa.column("asset_id", sa.String()),
        sa.column("return_1m", sa.Numeric(precision=12, scale=6)),
        sa.column("annualized_return", sa.Numeric(precision=12, scale=6)),
    )
    current_return_1m = (
        sa.select(perf_table.c.return_1m)
        .where(
            perf_table.c.asset_id == row_table.c.asset_id,
            perf_table.c.is_current.is_(True),
        )
        .order_by(perf_table.c.as_of_date.desc())
        .limit(1)
        .scalar_subquery()
    )
    current_annualized_return = (
        sa.select(perf_table.c.annualized_return)
        .where(
            perf_table.c.asset_id == row_table.c.asset_id,
            perf_table.c.is_current.is_(True),
        )
        .order_by(perf_table.c.as_of_date.desc())
        .limit(1)
        .scalar_subquery()
    )
    bind.execute(
        sa.update(row_table).values(
            return_1m=current_return_1m,
            annualized_return=current_annualized_return,
        )
    )

    _refresh_overview_columns(bind, OVERVIEW_COLUMNS)


def downgrade() -> None:
    bind = op.get_bind()
    field_table = sa.table("field_registry", sa.column("field_key", sa.String()))
    bind.execute(
        sa.delete(field_table).where(
            field_table.c.field_key.in_(["return_1m", "annualized_return"])
        )
    )

    _refresh_overview_columns(bind, PREVIOUS_OVERVIEW_COLUMNS)

    op.drop_column("watchlist_row_read_model", "annualized_return")
    op.drop_column("watchlist_row_read_model", "return_1m")
