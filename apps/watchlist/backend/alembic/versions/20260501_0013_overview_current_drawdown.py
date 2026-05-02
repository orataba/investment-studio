"""add current drawdown to default overview

Revision ID: 20260501_0013
Revises: 20260501_0012
Create Date: 2026-05-01 15:35:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260501_0013"
down_revision = "20260501_0012"
branch_labels = None
depends_on = None


OVERVIEW_COLUMNS = [
    ("asset_name", 1, 320),
    ("price_chart_1m", 2, 140),
    ("latest_quote", 3, 130),
    ("latest_quote_date", 4, 140),
    ("return_1w", 5, 150),
    ("return_1m", 6, 150),
    ("return_ytd", 7, 150),
    ("attr.current_drawdown", 8, 120),
    ("attr.peer_overall_percentile", 9, 120),
    ("data_freshness_status", 10, 140),
]

PREVIOUS_OVERVIEW_COLUMNS = [
    ("asset_name", 1, 320),
    ("price_chart_1m", 2, 140),
    ("latest_quote", 3, 130),
    ("latest_quote_date", 4, 140),
    ("return_1w", 5, 150),
    ("return_1m", 6, 150),
    ("return_ytd", 7, 150),
    ("attr.peer_overall_percentile", 8, 120),
    ("data_freshness_status", 9, 140),
]

CURRENT_DRAWDOWN_FIELD = {
    "field_key": "attr.current_drawdown",
    "label": "当前回撤",
    "description": "Current drawdown from the latest NAV relative to the prior high watermark.",
    "category_code": "performance_risk",
    "data_type": "number",
    "formatter_code": "percent",
    "sort_mode": "numeric",
    "filter_mode": "range",
    "group_mode": "none",
    "asset_scope_json": ["fund"],
    "product_scope_json": ["mutual_fund", "cef", "etf"],
    "availability_rule_json": {"requires": ["asset_risk_read_model"]},
    "source_domain": "read_model",
    "source_metric_code": "watchlist_row_read_model.attributes.current_drawdown",
    "default_width": 120,
    "default_visible": False,
}


def _tables() -> tuple[
    sa.TableClause,
    sa.TableClause,
    sa.TableClause,
    sa.TableClause,
    sa.TableClause,
    sa.TableClause,
]:
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
    row_table = sa.table(
        "watchlist_row_read_model",
        sa.column("asset_id", sa.String()),
        sa.column("attributes_json", sa.JSON()),
    )
    chart_table = sa.table(
        "asset_chart_read_model",
        sa.column("asset_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )
    risk_table = sa.table(
        "asset_risk_read_model",
        sa.column("asset_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )
    return view_table, column_table, field_table, row_table, chart_table, risk_table


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


def _current_drawdown_from_chart_payload(payload: object) -> float | None:
    if not isinstance(payload, dict):
        return None
    series = payload.get("series")
    if not isinstance(series, list) or not series:
        return None
    points = next(
        (
            item.get("points")
            for item in series
            if isinstance(item, dict) and isinstance(item.get("points"), list)
        ),
        None,
    )
    if not points:
        return None
    peak: float | None = None
    current: float | None = None
    for point in points:
        if not isinstance(point, dict):
            continue
        value = point.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            continue
        numeric = float(value)
        peak = numeric if peak is None else max(peak, numeric)
        current = (numeric / peak - 1) * 100 if peak else None
    return current


def _backfill_current_drawdown(
    bind,
    row_table: sa.TableClause,
    chart_table: sa.TableClause,
    risk_table: sa.TableClause,
) -> None:
    values_by_asset = {
        str(row["asset_id"]): _current_drawdown_from_chart_payload(row["payload_json"])
        for row in bind.execute(
            sa.select(chart_table.c.asset_id, chart_table.c.payload_json)
        ).mappings()
    }
    values_by_asset = {
        asset_id: value
        for asset_id, value in values_by_asset.items()
        if value is not None
    }
    if not values_by_asset:
        return

    for row in bind.execute(
        sa.select(row_table.c.asset_id, row_table.c.attributes_json).where(
            row_table.c.asset_id.in_(sorted(values_by_asset))
        )
    ).mappings():
        attributes = row["attributes_json"] if isinstance(row["attributes_json"], dict) else {}
        next_attributes = {**attributes, "current_drawdown": values_by_asset[str(row["asset_id"])]}
        bind.execute(
            sa.update(row_table)
            .where(row_table.c.asset_id == row["asset_id"])
            .values(attributes_json=next_attributes)
        )

    for row in bind.execute(
        sa.select(risk_table.c.asset_id, risk_table.c.payload_json).where(
            risk_table.c.asset_id.in_(sorted(values_by_asset))
        )
    ).mappings():
        payload = row["payload_json"] if isinstance(row["payload_json"], dict) else {}
        next_payload = {**payload, "current_drawdown": values_by_asset[str(row["asset_id"])]}
        bind.execute(
            sa.update(risk_table)
            .where(risk_table.c.asset_id == row["asset_id"])
            .values(payload_json=next_payload)
        )


def upgrade() -> None:
    bind = op.get_bind()
    view_table, column_table, field_table, row_table, chart_table, risk_table = _tables()
    bind.execute(
        sa.delete(field_table).where(
            field_table.c.field_key == CURRENT_DRAWDOWN_FIELD["field_key"]
        )
    )
    op.bulk_insert(field_table, [CURRENT_DRAWDOWN_FIELD])
    _replace_overview_columns(bind, view_table, column_table, OVERVIEW_COLUMNS)
    _backfill_current_drawdown(bind, row_table, chart_table, risk_table)


def downgrade() -> None:
    bind = op.get_bind()
    view_table, column_table, field_table, _, _, _ = _tables()
    _replace_overview_columns(bind, view_table, column_table, PREVIOUS_OVERVIEW_COLUMNS)
    bind.execute(
        sa.delete(field_table).where(
            field_table.c.field_key == CURRENT_DRAWDOWN_FIELD["field_key"]
        )
    )
