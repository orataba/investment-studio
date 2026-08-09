"""Canonicalize the Watchlist primary display field.

Revision ID: 20260809_0036
Revises: 20260809_0035

Some upgraded databases retained the historical ``asset_name`` seed while a
fresh database used ``instrument_name``.  Reconcile the persisted field and
saved views so the frontend has one field identity and no runtime alias.
"""

from __future__ import annotations

from typing import Any

from alembic import op
import sqlalchemy as sa


revision = "20260809_0036"
down_revision = "20260809_0035"
branch_labels = None
depends_on = None


LEGACY_FIELD = "asset_name"
CANONICAL_FIELD = "instrument_name"
CANONICAL_FIELD_VALUES = {
    "label": "Name",
    "description": (
        "Canonical instrument display name from the watchlist row read model."
    ),
    "category_code": "general",
    "data_type": "string",
    "formatter_code": "text",
    "sort_mode": "alpha",
    "filter_mode": "text",
    "group_mode": "discrete",
    "instrument_scope_json": [],
    "product_scope_json": [],
    "availability_rule_json": {"requires": ["watchlist_row_read_model"]},
    "source_domain": "read_model",
    "source_metric_code": "watchlist_row_read_model.instrument_name",
    "default_width": 320,
    "default_visible": True,
}


def _replace_field_identity(value: object) -> object:
    if isinstance(value, str):
        return CANONICAL_FIELD if value == LEGACY_FIELD else value
    if isinstance(value, list):
        return [_replace_field_identity(item) for item in value]
    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = CANONICAL_FIELD if raw_key == LEGACY_FIELD else raw_key
        if key in cleaned:
            raise RuntimeError(
                "Saved Watchlist configuration contains both asset_name and "
                "instrument_name; resolve the duplicate field before migration."
            )
        cleaned[key] = _replace_field_identity(raw_value)
    return cleaned


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL lock_timeout = '30s'"))

    field_registry = sa.table(
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
    view_column = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("field_key", sa.String()),
    )
    view = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
        sa.column("default_group_by", sa.String()),
    )

    field_counts = dict(
        bind.execute(
            sa.select(field_registry.c.field_key, sa.func.count())
            .where(field_registry.c.field_key.in_((LEGACY_FIELD, CANONICAL_FIELD)))
            .group_by(field_registry.c.field_key)
        ).all()
    )
    legacy_exists = int(field_counts.get(LEGACY_FIELD, 0)) == 1
    canonical_exists = int(field_counts.get(CANONICAL_FIELD, 0)) == 1
    if legacy_exists == canonical_exists:
        raise RuntimeError(
            "Expected exactly one Watchlist primary display field: asset_name "
            "or instrument_name."
        )

    duplicate_view = bind.execute(
        sa.select(view_column.c.watchlist_view_id)
        .where(view_column.c.field_key.in_((LEGACY_FIELD, CANONICAL_FIELD)))
        .group_by(view_column.c.watchlist_view_id)
        .having(sa.func.count() > 1)
        .limit(1)
    ).first()
    if duplicate_view is not None:
        raise RuntimeError(
            "Saved Watchlist view contains both asset_name and instrument_name: "
            f"{duplicate_view[0]!r}."
        )

    cleaned_views: list[dict[str, object]] = []
    for row in bind.execute(sa.select(view)).mappings():
        cleaned_views.append(
            {
                "watchlist_view_id": row["watchlist_view_id"],
                "default_sort_json": _replace_field_identity(
                    row["default_sort_json"] or []
                ),
                "default_filters_json": _replace_field_identity(
                    row["default_filters_json"] or {}
                ),
                "default_advanced_filter_json": _replace_field_identity(
                    row["default_advanced_filter_json"] or {}
                ),
                "default_group_by": (
                    CANONICAL_FIELD
                    if row["default_group_by"] == LEGACY_FIELD
                    else row["default_group_by"]
                ),
            }
        )

    source_field = LEGACY_FIELD if legacy_exists else CANONICAL_FIELD
    bind.execute(
        sa.update(field_registry)
        .where(field_registry.c.field_key == source_field)
        .values(field_key=CANONICAL_FIELD, **CANONICAL_FIELD_VALUES)
    )
    bind.execute(
        sa.update(view_column)
        .where(view_column.c.field_key == LEGACY_FIELD)
        .values(field_key=CANONICAL_FIELD)
    )
    for cleaned in cleaned_views:
        bind.execute(
            sa.update(view)
            .where(view.c.watchlist_view_id == cleaned["watchlist_view_id"])
            .values(
                default_sort_json=cleaned["default_sort_json"],
                default_filters_json=cleaned["default_filters_json"],
                default_advanced_filter_json=cleaned[
                    "default_advanced_filter_json"
                ],
                default_group_by=cleaned["default_group_by"],
            )
        )


def downgrade() -> None:
    raise RuntimeError(
        "instrument_name is the canonical Watchlist field identity and is not "
        "downgradable to asset_name."
    )
