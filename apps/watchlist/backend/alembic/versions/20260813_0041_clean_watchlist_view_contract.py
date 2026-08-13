"""Remove legacy Watchlist fields and repair saved view references.

Revision ID: 20260813_0041
Revises: 20260813_0040
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260813_0041"
down_revision = "20260813_0040"
branch_labels = None
depends_on = None


RENAMED_FIELDS = {"asset_type": "instrument_type"}
REMOVED_FIELDS = {"asset_class", "instrument_class"}
LEGACY_FIELDS = set(RENAMED_FIELDS) | REMOVED_FIELDS


def _rewrite_filters(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    rewritten = {
        str(key): item
        for key, item in value.items()
        if str(key) not in LEGACY_FIELDS
    }
    if "instrument_type" not in rewritten and "asset_type" in value:
        rewritten["instrument_type"] = value["asset_type"]
    return rewritten


def _rewrite_sort(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    rewritten: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        field_key = str(item.get("field") or "").strip()
        if not field_key or field_key in REMOVED_FIELDS:
            continue
        field_key = RENAMED_FIELDS.get(field_key, field_key)
        if field_key in seen:
            continue
        seen.add(field_key)
        rewritten.append({**item, "field": field_key})
    return rewritten


def _rewrite_advanced_filter(node: object) -> dict[str, object] | None:
    if not isinstance(node, dict):
        return None
    if node.get("type") == "rule":
        field_key = str(node.get("field") or "").strip()
        if not field_key or field_key in REMOVED_FIELDS:
            return None
        return {**node, "field": RENAMED_FIELDS.get(field_key, field_key)}
    conditions = [
        rewritten
        for child in node.get("conditions") or []
        if (rewritten := _rewrite_advanced_filter(child)) is not None
    ]
    if not conditions:
        return None
    return {**node, "conditions": conditions}


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL lock_timeout = '30s'"))

    field_table = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
    )
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
        sa.column("default_group_by", sa.String()),
    )
    column_table = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_column_id", sa.Integer()),
        sa.column("watchlist_view_id", sa.String()),
        sa.column("field_key", sa.String()),
    )

    for row in bind.execute(sa.select(view_table)).mappings():
        advanced = _rewrite_advanced_filter(row["default_advanced_filter_json"])
        bind.execute(
            sa.update(view_table)
            .where(view_table.c.watchlist_view_id == row["watchlist_view_id"])
            .values(
                default_sort_json=_rewrite_sort(row["default_sort_json"]),
                default_filters_json=_rewrite_filters(row["default_filters_json"]),
                default_advanced_filter_json=advanced or {},
            )
        )

    columns = list(
        bind.execute(
            sa.select(
                column_table.c.watchlist_view_column_id,
                column_table.c.watchlist_view_id,
                column_table.c.field_key,
            )
        ).mappings()
    )
    canonical_views = {
        str(row["watchlist_view_id"])
        for row in columns
        if row["field_key"] == "instrument_type"
    }
    bind.execute(
        sa.delete(column_table).where(column_table.c.field_key.in_(REMOVED_FIELDS))
    )
    for row in columns:
        if row["field_key"] != "asset_type":
            continue
        if str(row["watchlist_view_id"]) in canonical_views:
            bind.execute(
                sa.delete(column_table).where(
                    column_table.c.watchlist_view_column_id
                    == row["watchlist_view_column_id"]
                )
            )
        else:
            bind.execute(
                sa.update(column_table)
                .where(
                    column_table.c.watchlist_view_column_id
                    == row["watchlist_view_column_id"]
                )
                .values(field_key="instrument_type")
            )

    bind.execute(
        sa.update(view_table)
        .where(view_table.c.watchlist_view_id == "all-coverage::overview")
        .values(default_group_by="instrument_type")
    )
    bind.execute(sa.delete(field_table).where(field_table.c.field_key.in_(LEGACY_FIELDS)))


def downgrade() -> None:
    raise RuntimeError(
        "20260813_0041 is a clean-cut legacy-field removal and cannot be downgraded."
    )
