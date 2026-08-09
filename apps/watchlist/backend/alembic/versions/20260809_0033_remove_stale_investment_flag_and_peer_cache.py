"""Remove the manual invested flag and stale persisted peer calculations.

Revision ID: 20260809_0033
Revises: 20260809_0032

Actual ownership is not a Watchlist research attribute; it must come from
portfolio positions.  Peer statistics are live cross-sectional calculations
and must not remain cached inside per-instrument Watchlist rows.
"""

from __future__ import annotations

from typing import Any

from alembic import op
import sqlalchemy as sa


revision = "20260809_0033"
down_revision = "20260809_0032"
branch_labels = None
depends_on = None


REMOVED_ATTRIBUTE = "is_invested"
REMOVED_FIELD = "attr.is_invested"


def _strip_advanced_filter(node: object) -> object | None:
    if not isinstance(node, dict):
        return node
    if str(node.get("field_key") or node.get("field") or "") == REMOVED_FIELD:
        return None
    cleaned: dict[str, Any] = dict(node)
    for key in ("children", "conditions", "rules"):
        value = cleaned.get(key)
        if isinstance(value, list):
            cleaned[key] = [
                child
                for item in value
                if (child := _strip_advanced_filter(item)) is not None
            ]
    return cleaned


def _clean_saved_views(bind) -> None:
    view = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
        sa.column("default_group_by", sa.String()),
    )
    rows = bind.execute(sa.select(view)).mappings().all()
    for row in rows:
        bind.execute(
            sa.update(view)
            .where(view.c.watchlist_view_id == row["watchlist_view_id"])
            .values(
                default_sort_json=[
                    rule
                    for rule in row["default_sort_json"] or []
                    if not isinstance(rule, dict)
                    or str(rule.get("field_key") or rule.get("field") or "")
                    != REMOVED_FIELD
                ],
                default_filters_json={
                    key: value
                    for key, value in (row["default_filters_json"] or {}).items()
                    if key != REMOVED_FIELD
                },
                default_advanced_filter_json=(
                    _strip_advanced_filter(row["default_advanced_filter_json"] or {}) or {}
                ),
                default_group_by=(
                    "none" if row["default_group_by"] == REMOVED_FIELD else row["default_group_by"]
                ),
            )
        )


def _clean_read_models(bind) -> None:
    row_model = sa.table(
        "watchlist_row_read_model",
        sa.column("watchlist_id", sa.String()),
        sa.column("instrument_id", sa.String()),
        sa.column("attributes_json", sa.JSON()),
    )
    for row in bind.execute(sa.select(row_model)).mappings().all():
        attributes = {
            key: value
            for key, value in (row["attributes_json"] or {}).items()
            if key != REMOVED_ATTRIBUTE and not str(key).startswith("peer_")
        }
        bind.execute(
            sa.update(row_model)
            .where(
                row_model.c.watchlist_id == row["watchlist_id"],
                row_model.c.instrument_id == row["instrument_id"],
            )
            .values(attributes_json=attributes)
        )

    summary = sa.table(
        "instrument_summary_read_model",
        sa.column("instrument_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )
    for row in bind.execute(sa.select(summary)).mappings().all():
        payload = dict(row["payload_json"] or {})
        instrument_attributes = dict(payload.get("instrument_attributes") or {})
        instrument_attributes.pop(REMOVED_ATTRIBUTE, None)
        payload["instrument_attributes"] = instrument_attributes
        bind.execute(
            sa.update(summary)
            .where(summary.c.instrument_id == row["instrument_id"])
            .values(payload_json=payload)
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    _clean_saved_views(bind)
    _clean_read_models(bind)

    view_column = sa.table("watchlist_view_column", sa.column("field_key", sa.String()))
    attribute_value = sa.table(
        "instrument_attribute_value", sa.column("attribute_key", sa.String())
    )
    attribute_definition = sa.table(
        "instrument_attribute_definition", sa.column("attribute_key", sa.String())
    )
    field_registry = sa.table("field_registry", sa.column("field_key", sa.String()))
    bind.execute(sa.delete(view_column).where(view_column.c.field_key == REMOVED_FIELD))
    bind.execute(
        sa.delete(attribute_value).where(attribute_value.c.attribute_key == REMOVED_ATTRIBUTE)
    )
    bind.execute(
        sa.delete(attribute_definition).where(
            attribute_definition.c.attribute_key == REMOVED_ATTRIBUTE
        )
    )
    bind.execute(sa.delete(field_registry).where(field_registry.c.field_key == REMOVED_FIELD))


def downgrade() -> None:
    raise RuntimeError(
        "The manual invested flag was not an ownership fact and stale peer caches are invalid. "
        "Restore a pre-migration database backup instead of recreating them."
    )
