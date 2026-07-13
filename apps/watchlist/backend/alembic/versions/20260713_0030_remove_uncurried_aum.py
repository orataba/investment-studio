"""Remove the uncurried AUM read-model surface.

Revision ID: 20260713_0030
Revises: 20260713_0029
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "20260713_0030"
down_revision = "20260713_0029"
branch_labels = None
depends_on = None


REMOVED_FIELD_KEY = "aum"
_DROP = object()


def _json_value(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as error:
            raise RuntimeError("Persisted watchlist view JSON is invalid.") from error
    return value


def _without_removed_sort_field(value: object) -> list[object]:
    source = _json_value(value)
    if not isinstance(source, list):
        raise RuntimeError("watchlist_view.default_sort_json must be a JSON array.")
    return [
        item
        for item in source
        if not (
            isinstance(item, dict)
            and str(item.get("field") or "").strip() == REMOVED_FIELD_KEY
        )
    ]


def _without_removed_filter_field(value: object) -> dict[str, object]:
    source = _json_value(value)
    if not isinstance(source, dict):
        raise RuntimeError("watchlist_view.default_filters_json must be a JSON object.")
    return {
        str(key): item
        for key, item in source.items()
        if str(key).strip() != REMOVED_FIELD_KEY
    }


def _without_removed_advanced_filter_field(value: object) -> object:
    node = _json_value(value)
    if not isinstance(node, dict):
        raise RuntimeError(
            "watchlist_view.default_advanced_filter_json must be a JSON object."
        )
    if node.get("type") == "rule":
        if str(node.get("field") or "").strip() == REMOVED_FIELD_KEY:
            return _DROP
        return node
    if node.get("type") != "group":
        return node

    conditions = node.get("conditions")
    if not isinstance(conditions, list):
        raise RuntimeError("Advanced-filter groups must contain a conditions array.")
    rewritten_conditions: list[object] = []
    for condition in conditions:
        rewritten = _without_removed_advanced_filter_field(condition)
        if rewritten is not _DROP:
            rewritten_conditions.append(rewritten)
    return {**node, "conditions": rewritten_conditions}


def _require_table(connection, table_name: str) -> None:
    if not sa.inspect(connection).has_table(table_name):
        raise RuntimeError(
            f"Required table {table_name!r} is missing; refusing partial AUM cleanup."
        )


def upgrade() -> None:
    connection = op.get_bind()
    for table_name in (
        "watchlist_row_read_model",
        "field_registry",
        "watchlist_view",
        "watchlist_view_column",
    ):
        _require_table(connection, table_name)

    row_columns = {
        str(column["name"])
        for column in sa.inspect(connection).get_columns("watchlist_row_read_model")
    }
    if REMOVED_FIELD_KEY not in row_columns:
        raise RuntimeError(
            "watchlist_row_read_model.aum is missing; refusing a partial AUM cleanup."
        )
    nonnull_aum_count = int(
        connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM watchlist_row_read_model WHERE aum IS NOT NULL"
            )
        )
        or 0
    )
    if nonnull_aum_count:
        raise RuntimeError(
            "watchlist_row_read_model.aum contains non-null values. Migrate them into "
            "an explicit amount/currency/as-of/source fact model before dropping AUM."
        )
    field_count = int(
        connection.scalar(
            sa.text("SELECT COUNT(*) FROM field_registry WHERE field_key = 'aum'")
        )
        or 0
    )
    if field_count != 1:
        raise RuntimeError(
            "field_registry must contain exactly one AUM row; refusing a partial cleanup."
        )

    view = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_group_by", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
    )
    rewritten_views: list[tuple[str, dict[str, object]]] = []
    for row in connection.execute(sa.select(view)).mappings():
        advanced_filter = _without_removed_advanced_filter_field(
            row["default_advanced_filter_json"]
        )
        rewritten_views.append(
            (
                str(row["watchlist_view_id"]),
                {
                    "default_group_by": (
                        "none"
                        if str(row["default_group_by"] or "").strip()
                        == REMOVED_FIELD_KEY
                        else row["default_group_by"]
                    ),
                    "default_sort_json": _without_removed_sort_field(
                        row["default_sort_json"]
                    ),
                    "default_filters_json": _without_removed_filter_field(
                        row["default_filters_json"]
                    ),
                    "default_advanced_filter_json": (
                        {} if advanced_filter is _DROP else advanced_filter
                    ),
                },
            )
        )

    view_column = sa.table(
        "watchlist_view_column",
        sa.column("field_key", sa.String()),
    )
    connection.execute(
        sa.delete(view_column).where(view_column.c.field_key == REMOVED_FIELD_KEY)
    )
    for view_id, values in rewritten_views:
        connection.execute(
            sa.update(view)
            .where(view.c.watchlist_view_id == view_id)
            .values(**values)
        )

    field_registry = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
    )
    delete_result = connection.execute(
        sa.delete(field_registry).where(
            field_registry.c.field_key == REMOVED_FIELD_KEY
        )
    )
    if delete_result.rowcount != 1:
        raise RuntimeError("AUM field metadata deletion did not affect exactly one row.")

    with op.batch_alter_table("watchlist_row_read_model") as batch_op:
        batch_op.drop_column(REMOVED_FIELD_KEY)


def downgrade() -> None:
    raise RuntimeError(
        "20260713_0030 is intentionally irreversible: an amount without currency, "
        "as-of date, and source lineage is not restored. Restore a pre-migration "
        "backup if rollback is required."
    )
