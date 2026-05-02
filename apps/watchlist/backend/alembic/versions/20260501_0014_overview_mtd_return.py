"""add MTD return to default overview

Revision ID: 20260501_0014
Revises: 20260501_0013
Create Date: 2026-05-01 16:10:00
"""

from __future__ import annotations

from datetime import date

from alembic import op
import sqlalchemy as sa


revision = "20260501_0014"
down_revision = "20260501_0013"
branch_labels = None
depends_on = None


OVERVIEW_COLUMNS = [
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

PREVIOUS_OVERVIEW_COLUMNS = [
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

RETURN_MTD_FIELD = {
    "field_key": "return_mtd",
    "label": "MTD",
    "description": "Month-to-date total return.",
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
    "source_metric_code": "performance_snapshot.return_mtd",
    "default_width": 120,
    "default_visible": True,
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
    performance_table = sa.table(
        "performance_snapshot",
        sa.column("snapshot_id", sa.String()),
        sa.column("asset_id", sa.String()),
        sa.column("as_of_date", sa.Date()),
        sa.column("is_current", sa.Boolean()),
        sa.column("return_ytd", sa.Numeric(12, 6)),
        sa.column("return_mtd", sa.Numeric(12, 6)),
        sa.column("return_1m", sa.Numeric(12, 6)),
    )
    row_table = sa.table(
        "watchlist_row_read_model",
        sa.column("watchlist_id", sa.String()),
        sa.column("asset_id", sa.String()),
        sa.column("return_ytd", sa.Numeric(12, 6)),
        sa.column("return_mtd", sa.Numeric(12, 6)),
    )
    chart_table = sa.table(
        "asset_chart_read_model",
        sa.column("asset_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )
    return view_table, column_table, field_table, performance_table, row_table, chart_table


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


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value[:10]:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _chart_points(payload: object) -> list[tuple[date, float]]:
    if not isinstance(payload, dict):
        return []
    series = payload.get("series")
    if not isinstance(series, list):
        return []
    points = next(
        (
            item.get("points")
            for item in series
            if isinstance(item, dict) and isinstance(item.get("points"), list)
        ),
        None,
    )
    if not points:
        return []
    parsed_points: list[tuple[date, float]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        point_date = _parse_date(point.get("date"))
        value = point.get("value")
        if point_date is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        parsed_points.append((point_date, float(value)))
    return sorted(parsed_points)


def _value_at_or_before(points: list[tuple[date, float]], target: date) -> tuple[date, float] | None:
    candidates = [point for point in points if point[0] <= target]
    return candidates[-1] if candidates else None


def _value_before(points: list[tuple[date, float]], target: date) -> tuple[date, float] | None:
    candidates = [point for point in points if point[0] < target]
    return candidates[-1] if candidates else None


def _period_return(points: list[tuple[date, float]], as_of_date: date, period_start: date) -> float | None:
    latest = _value_at_or_before(points, as_of_date)
    base = _value_before(points, period_start)
    if latest is None or base is None or base[1] <= 0:
        return None
    return (latest[1] / base[1] - 1) * 100


def _backfill_calendar_returns(
    bind,
    performance_table: sa.TableClause,
    row_table: sa.TableClause,
    chart_table: sa.TableClause,
) -> None:
    chart_points_by_asset = {
        str(row["asset_id"]): _chart_points(row["payload_json"])
        for row in bind.execute(
            sa.select(chart_table.c.asset_id, chart_table.c.payload_json)
        ).mappings()
    }

    current_returns_by_asset: dict[str, dict[str, object]] = {}
    for row in bind.execute(
        sa.select(
            performance_table.c.snapshot_id,
            performance_table.c.asset_id,
            performance_table.c.as_of_date,
            performance_table.c.is_current,
        )
    ).mappings():
        asset_id = str(row["asset_id"])
        as_of_date = _parse_date(row["as_of_date"])
        points = chart_points_by_asset.get(asset_id, [])
        if as_of_date is None or not points:
            continue
        values = {
            "return_ytd": _period_return(points, as_of_date, date(as_of_date.year, 1, 1)),
            "return_mtd": _period_return(
                points,
                as_of_date,
                date(as_of_date.year, as_of_date.month, 1),
            ),
        }
        bind.execute(
            sa.update(performance_table)
            .where(performance_table.c.snapshot_id == row["snapshot_id"])
            .values(**values)
        )
        if row["is_current"]:
            current_returns_by_asset[asset_id] = values

    if not current_returns_by_asset:
        return
    for row in bind.execute(
        sa.select(row_table.c.watchlist_id, row_table.c.asset_id).where(
            row_table.c.asset_id.in_(sorted(current_returns_by_asset))
        )
    ).mappings():
        bind.execute(
            sa.update(row_table)
            .where(
                row_table.c.watchlist_id == row["watchlist_id"],
                row_table.c.asset_id == row["asset_id"],
            )
            .values(**current_returns_by_asset[str(row["asset_id"])])
        )


def upgrade() -> None:
    op.add_column(
        "performance_snapshot",
        sa.Column("return_mtd", sa.Numeric(precision=12, scale=6), nullable=True),
    )
    op.add_column(
        "watchlist_row_read_model",
        sa.Column("return_mtd", sa.Numeric(precision=12, scale=6), nullable=True),
    )

    bind = op.get_bind()
    view_table, column_table, field_table, performance_table, row_table, chart_table = _tables()
    bind.execute(sa.delete(field_table).where(field_table.c.field_key == "return_mtd"))
    op.bulk_insert(field_table, [RETURN_MTD_FIELD])
    bind.execute(
        sa.update(field_table)
        .where(field_table.c.field_key == "return_ytd")
        .values(label="YTD")
    )
    _replace_overview_columns(bind, view_table, column_table, OVERVIEW_COLUMNS)
    _backfill_calendar_returns(bind, performance_table, row_table, chart_table)


def downgrade() -> None:
    bind = op.get_bind()
    view_table, column_table, field_table, _, _, _ = _tables()
    _replace_overview_columns(bind, view_table, column_table, PREVIOUS_OVERVIEW_COLUMNS)
    bind.execute(
        sa.update(field_table)
        .where(field_table.c.field_key == "return_ytd")
        .values(label="Total Return (YTD)")
    )
    bind.execute(sa.delete(field_table).where(field_table.c.field_key == "return_mtd"))
    with op.batch_alter_table("watchlist_row_read_model") as batch_op:
        batch_op.drop_column("return_mtd")
    with op.batch_alter_table("performance_snapshot") as batch_op:
        batch_op.drop_column("return_mtd")
