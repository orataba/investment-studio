"""Replace price chart fields with boundary-aware cumulative return charts.

Revision ID: 20260716_0026
Revises: 20260712_0025
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from alembic import op
import sqlalchemy as sa


revision = "20260716_0026"
down_revision = "20260712_0025"
branch_labels = None
depends_on = None


OLD_FIELD_KEY = "price_chart_1m"
NEW_FIELDS = (
    (
        "return_chart_1d",
        "1d",
        "Return 1D",
        "Cumulative return from the last valid close on or before the one-day "
        "boundary to the latest close.",
    ),
    (
        "return_chart_1w",
        "1w",
        "Return 1W",
        "Cumulative return from the last valid close on or before the one-week "
        "boundary to the latest close.",
    ),
    (
        "return_chart_1m",
        "1m",
        "Return 1M",
        "Cumulative return from the last valid close on or before the one-month "
        "boundary to the latest close.",
    ),
    (
        "return_chart_1y",
        "1y",
        "Return 1Y",
        "Cumulative return from the last valid close on or before the one-year "
        "boundary to the latest close.",
    ),
)


def _replace_json_key(value: Any, old: str, new: str) -> Any:
    if isinstance(value, list):
        return [_replace_json_key(item, old, new) for item in value]
    if isinstance(value, dict):
        return {
            (new if key == old else key): _replace_json_key(item, old, new)
            for key, item in value.items()
        }
    return new if value == old else value


def _field_payload(
    field_key: str,
    window: str,
    label: str,
    description: str,
) -> dict[str, object]:
    return {
        "field_key": field_key,
        "label": label,
        "description": description,
        "category_code": "performance_risk",
        "data_type": "sparkline",
        "formatter_code": "sparkline",
        "sort_mode": "none",
        "filter_mode": "none",
        "group_mode": "none",
        "instrument_scope_json": ["fund", "etf", "index"],
        "product_scope_json": [],
        "availability_rule_json": {
            "requires": ["instrument_chart_read_model"]
        },
        "source_domain": "read_model",
        "source_metric_code": (
            f"instrument_chart_read_model.normalized_return.{window}"
        ),
        "default_width": 140,
        "default_visible": False,
    }


def upgrade() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    field_table = sa.Table("field_registry", metadata, autoload_with=connection)
    column_table = sa.Table(
        "watchlist_view_column", metadata, autoload_with=connection
    )
    view_table = sa.Table("watchlist_view", metadata, autoload_with=connection)

    for row in NEW_FIELDS:
        payload = _field_payload(*row)
        existing = connection.scalar(
            sa.select(sa.literal(True))
            .select_from(field_table)
            .where(field_table.c.field_key == payload["field_key"])
            .limit(1)
        )
        if existing:
            connection.execute(
                sa.update(field_table)
                .where(field_table.c.field_key == payload["field_key"])
                .values(**{key: value for key, value in payload.items() if key != "field_key"})
            )
        else:
            connection.execute(sa.insert(field_table).values(**payload))

    views_with_new_column = sa.select(column_table.c.watchlist_view_id).where(
        column_table.c.field_key == "return_chart_1m"
    )
    connection.execute(
        sa.delete(column_table).where(
            column_table.c.field_key == OLD_FIELD_KEY,
            column_table.c.watchlist_view_id.in_(views_with_new_column),
        )
    )
    connection.execute(
        sa.update(column_table)
        .where(column_table.c.field_key == OLD_FIELD_KEY)
        .values(field_key="return_chart_1m")
    )

    json_columns = (
        "default_sort_json",
        "default_filters_json",
        "default_advanced_filter_json",
    )
    for row in connection.execute(sa.select(view_table)).mappings():
        updates: dict[str, object] = {}
        for column_name in json_columns:
            current = deepcopy(row[column_name])
            replaced = _replace_json_key(current, OLD_FIELD_KEY, "return_chart_1m")
            if replaced != current:
                updates[column_name] = replaced
        if updates:
            connection.execute(
                sa.update(view_table)
                .where(
                    view_table.c.watchlist_view_id == row["watchlist_view_id"]
                )
                .values(**updates)
            )

    connection.execute(
        sa.delete(field_table).where(field_table.c.field_key == OLD_FIELD_KEY)
    )


def downgrade() -> None:
    raise RuntimeError(
        "20260716_0026 is intentionally irreversible: price-chart fields had "
        "ambiguous date and return semantics."
    )
