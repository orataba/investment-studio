"""Remove the generic fund holdings and exposure boundary.

Revision ID: 20260713_0027
Revises: 20260713_0026
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "20260713_0027"
down_revision = "20260713_0026"
branch_labels = None
depends_on = None


REMOVED_FIELD_KEYS = {
    "avg_credit_rating",
    "duration",
    "exposure_updated_at",
    "yield_to_worst",
}
REMOVED_SUMMARY_TABS = {"exposure", "portfolio"}
_DROP = object()


def _json_value(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _without_removed_sort_fields(value: object) -> object:
    source = _json_value(value)
    if not isinstance(source, list):
        return source
    return [
        item
        for item in source
        if not (
            isinstance(item, dict)
            and str(item.get("field") or "").strip() in REMOVED_FIELD_KEYS
        )
    ]


def _without_removed_filter_fields(value: object) -> object:
    source = _json_value(value)
    if not isinstance(source, dict):
        return source
    return {
        key: item
        for key, item in source.items()
        if str(key).strip() not in REMOVED_FIELD_KEYS
    }


def _without_removed_advanced_filter_fields(value: object) -> object:
    node = _json_value(value)
    if not isinstance(node, dict):
        return node
    if node.get("type") == "rule":
        if str(node.get("field") or "").strip() in REMOVED_FIELD_KEYS:
            return _DROP
        return node
    if node.get("type") != "group":
        return node

    conditions = node.get("conditions")
    if not isinstance(conditions, list):
        return node
    rewritten_conditions: list[object] = []
    for condition in conditions:
        rewritten = _without_removed_advanced_filter_fields(condition)
        if rewritten is not _DROP:
            rewritten_conditions.append(rewritten)
    return {**node, "conditions": rewritten_conditions}


def _without_removed_summary_sections(value: object) -> object:
    source = _json_value(value)
    if not isinstance(source, dict):
        return source

    payload = dict(source)
    tabs = payload.get("tabs")
    if isinstance(tabs, list):
        payload["tabs"] = [
            tab
            for tab in tabs
            if str(tab).strip().lower() not in REMOVED_SUMMARY_TABS
        ]

    key_stats = payload.get("key_stats")
    if isinstance(key_stats, list):
        payload["key_stats"] = [
            item
            for item in key_stats
            if not (
                isinstance(item, dict)
                and str(item.get("label") or "").strip().lower() == "holdings"
            )
        ]
    return payload


def upgrade() -> None:
    connection = op.get_bind()

    view_column = sa.table(
        "watchlist_view_column",
        sa.column("field_key", sa.String()),
    )
    connection.execute(
        sa.delete(view_column).where(view_column.c.field_key.in_(REMOVED_FIELD_KEYS))
    )

    view = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_group_by", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
    )
    for row in connection.execute(sa.select(view)).mappings():
        advanced_filter = _without_removed_advanced_filter_fields(
            row["default_advanced_filter_json"]
        )
        connection.execute(
            sa.update(view)
            .where(view.c.watchlist_view_id == row["watchlist_view_id"])
            .values(
                default_group_by=(
                    "none"
                    if str(row["default_group_by"] or "").strip()
                    in REMOVED_FIELD_KEYS
                    else row["default_group_by"]
                ),
                default_sort_json=_without_removed_sort_fields(
                    row["default_sort_json"]
                ),
                default_filters_json=_without_removed_filter_fields(
                    row["default_filters_json"]
                ),
                default_advanced_filter_json=(
                    {} if advanced_filter is _DROP else advanced_filter
                ),
            )
        )

    summary = sa.table(
        "instrument_summary_read_model",
        sa.column("instrument_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )
    for row in connection.execute(sa.select(summary)).mappings():
        connection.execute(
            sa.update(summary)
            .where(summary.c.instrument_id == row["instrument_id"])
            .values(
                payload_json=_without_removed_summary_sections(row["payload_json"])
            )
        )

    field_registry = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
    )
    connection.execute(
        sa.delete(field_registry).where(
            field_registry.c.field_key.in_(REMOVED_FIELD_KEYS)
        )
    )
    field_category = sa.table(
        "field_category",
        sa.column("category_code", sa.String()),
        sa.column("display_order", sa.Integer()),
    )
    connection.execute(
        sa.delete(field_category).where(field_category.c.category_code == "exposure")
    )
    connection.execute(
        sa.update(field_category)
        .where(field_category.c.category_code == "ratings_analysis")
        .values(display_order=8)
    )
    connection.execute(
        sa.update(field_category)
        .where(field_category.c.category_code == "monitoring")
        .values(display_order=9)
    )

    recalc_job = sa.table(
        "recalc_job",
        sa.column("job_type", sa.String()),
    )
    connection.execute(
        sa.delete(recalc_job).where(recalc_job.c.job_type == "exposure")
    )

    with op.batch_alter_table("watchlist_row_read_model") as batch_op:
        batch_op.drop_column("duration")
        batch_op.drop_column("yield_to_worst")
        batch_op.drop_column("avg_credit_rating")
        batch_op.drop_column("exposure_updated_at")

    op.drop_table("holding_position")
    op.drop_table("holding_snapshot")
    op.drop_table("exposure_analytics_snapshot")
    op.drop_table("instrument_exposure_holdings_read_model")
    op.drop_table("instrument_exposure_read_model")


def downgrade() -> None:
    raise RuntimeError(
        "20260713_0027 is intentionally irreversible: the generic holdings and "
        "exposure boundary is outside the product scope and is not restored."
    )
