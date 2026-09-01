"""separate fund unit and cumulative NAV watchlist values

Revision ID: 20260901_0050
Revises: 20260824_0049
Create Date: 2026-09-01 14:10:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260901_0050"
down_revision = "20260824_0049"
branch_labels = None
depends_on = None


CUMULATIVE_NAV_FIELDS = [
    {
        "field_key": "latest_cumulative_nav",
        "label": "Cumulative NAV",
        "description": (
            "Most recent dividend-reinvested cumulative NAV for a public or "
            "private fund; blank for other asset types."
        ),
        "category_code": "general",
        "data_type": "number",
        "formatter_code": "decimal",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "bucket",
        "instrument_scope_json": [],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["instrument_chart_read_model"]},
        "source_domain": "read_model",
        "source_metric_code": "instrument_chart_read_model.latest_values.total_return.value",
        "default_width": 140,
        "default_visible": False,
    },
    {
        "field_key": "latest_cumulative_nav_date",
        "label": "Cumulative NAV Date",
        "description": (
            "As-of date for the most recent dividend-reinvested cumulative NAV; "
            "blank for other asset types."
        ),
        "category_code": "general",
        "data_type": "date",
        "formatter_code": "date",
        "sort_mode": "date",
        "filter_mode": "date_range",
        "group_mode": "bucket",
        "instrument_scope_json": [],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["instrument_chart_read_model"]},
        "source_domain": "read_model",
        "source_metric_code": "instrument_chart_read_model.latest_values.total_return.date",
        "default_width": 150,
        "default_visible": False,
    },
]


def _field_registry_table() -> sa.TableClause:
    return sa.table(
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
        sa.column("instrument_scope_json", sa.JSON()),
        sa.column("product_scope_json", sa.JSON()),
        sa.column("availability_rule_json", sa.JSON()),
        sa.column("source_domain", sa.String()),
        sa.column("source_metric_code", sa.String()),
        sa.column("default_width", sa.Integer()),
        sa.column("default_visible", sa.Boolean()),
    )


def _fund_overview_view_ids(bind: sa.Connection) -> list[str]:
    view = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("watchlist_id", sa.String()),
    )
    return [
        str(row[0])
        for row in bind.execute(
            sa.select(view.c.watchlist_view_id).where(
                view.c.watchlist_id.in_(["all-public-funds", "all-private-funds"]),
                view.c.watchlist_view_id.like("%::overview"),
            )
        )
    ]


def upgrade() -> None:
    bind = op.get_bind()
    field_registry = _field_registry_table()
    bind.execute(
        sa.update(field_registry)
        .where(field_registry.c.field_key == "latest_quote")
        .values(
            label="Latest Value",
            description=(
                "Most recent valuation-role value: Unit NAV for funds, market price "
                "for equities and ETFs, or index level for indexes."
            ),
            source_metric_code="instrument_chart_read_model.latest_values.valuation.value",
        )
    )
    bind.execute(
        sa.update(field_registry)
        .where(field_registry.c.field_key == "latest_quote_date")
        .values(
            label="Value Date",
            description="As-of date for the most recent valuation-role value.",
            source_metric_code="instrument_chart_read_model.latest_values.valuation.date",
        )
    )
    existing = {
        str(row[0])
        for row in bind.execute(
            sa.select(field_registry.c.field_key).where(
                field_registry.c.field_key.in_(
                    [field["field_key"] for field in CUMULATIVE_NAV_FIELDS]
                )
            )
        )
    }
    missing = [
        field for field in CUMULATIVE_NAV_FIELDS if field["field_key"] not in existing
    ]
    if missing:
        op.bulk_insert(field_registry, missing)

    view_column = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("field_key", sa.String()),
        sa.column("display_order", sa.Integer()),
        sa.column("width", sa.Integer()),
        sa.column("is_visible", sa.Boolean()),
        sa.column("pin_side", sa.String()),
    )
    for view_id in _fund_overview_view_ids(bind):
        already_present = bind.execute(
            sa.select(view_column.c.field_key).where(
                view_column.c.watchlist_view_id == view_id,
                view_column.c.field_key == "latest_cumulative_nav",
            )
        ).first()
        if already_present is not None:
            continue
        bind.execute(
            sa.update(view_column)
            .where(
                view_column.c.watchlist_view_id == view_id,
                view_column.c.display_order >= 5,
            )
            .values(display_order=view_column.c.display_order + 1)
        )
        bind.execute(
            sa.insert(view_column).values(
                watchlist_view_id=view_id,
                field_key="latest_cumulative_nav",
                display_order=5,
                width=140,
                is_visible=True,
                pin_side=None,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    view_column = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("field_key", sa.String()),
        sa.column("display_order", sa.Integer()),
    )
    for view_id in _fund_overview_view_ids(bind):
        bind.execute(
            sa.delete(view_column).where(
                view_column.c.watchlist_view_id == view_id,
                view_column.c.field_key == "latest_cumulative_nav",
            )
        )
        bind.execute(
            sa.update(view_column)
            .where(
                view_column.c.watchlist_view_id == view_id,
                view_column.c.display_order > 5,
            )
            .values(display_order=view_column.c.display_order - 1)
        )

    field_registry = _field_registry_table()
    bind.execute(
        sa.delete(field_registry).where(
            field_registry.c.field_key.in_(
                [field["field_key"] for field in CUMULATIVE_NAV_FIELDS]
            )
        )
    )
    bind.execute(
        sa.update(field_registry)
        .where(field_registry.c.field_key == "latest_quote")
        .values(
            label="Latest Quote",
            description="Most recent point from the active quote series used by the detail quote view.",
            source_metric_code="instrument_chart_read_model.series.latest_quote",
        )
    )
    bind.execute(
        sa.update(field_registry)
        .where(field_registry.c.field_key == "latest_quote_date")
        .values(
            label="Quote Date",
            description="As-of date for the most recent active quote series point.",
            source_metric_code="instrument_chart_read_model.series.latest_quote_date",
        )
    )
