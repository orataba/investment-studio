"""Remove the dimensionally invalid automatic investment rating.

Revision ID: 20260809_0031
Revises: 20260728_0030

The retired score averaged returns, Sharpe ratios, and yields as if they shared
one unit.  That output is not an investment fact and must not survive as a
filter, grouping key, read model, or historical snapshot.  Manual research
ratings remain in ``instrument_manual_profile.research_payload_json``.
"""

from __future__ import annotations

from typing import Any

from alembic import op
import sqlalchemy as sa


revision = "20260809_0031"
down_revision = "20260728_0030"
branch_labels = None
depends_on = None


REMOVED_FIELDS = {"overall_rating", "analyst_stance"}


def _strip_advanced_filter(node: object) -> object | None:
    if not isinstance(node, dict):
        return node
    if str(node.get("field_key") or node.get("field") or "") in REMOVED_FIELDS:
        return None
    cleaned: dict[str, Any] = dict(node)
    for key in ("children", "conditions", "rules"):
        value = cleaned.get(key)
        if isinstance(value, list):
            children = [
                child
                for item in value
                if (child := _strip_advanced_filter(item)) is not None
            ]
            cleaned[key] = children
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
    rows = bind.execute(
        sa.select(
            view.c.watchlist_view_id,
            view.c.default_sort_json,
            view.c.default_filters_json,
            view.c.default_advanced_filter_json,
            view.c.default_group_by,
        )
    ).mappings()
    for row in rows:
        sort_rules = [
            rule
            for rule in (row["default_sort_json"] or [])
            if not isinstance(rule, dict)
            or str(rule.get("field_key") or rule.get("field") or "")
            not in REMOVED_FIELDS
        ]
        filters = {
            key: value
            for key, value in (row["default_filters_json"] or {}).items()
            if key not in REMOVED_FIELDS
        }
        advanced = _strip_advanced_filter(row["default_advanced_filter_json"] or {})
        bind.execute(
            sa.update(view)
            .where(view.c.watchlist_view_id == row["watchlist_view_id"])
            .values(
                default_sort_json=sort_rules,
                default_filters_json=filters,
                default_advanced_filter_json=advanced or {},
                default_group_by=(
                    "none"
                    if row["default_group_by"] in REMOVED_FIELDS
                    else row["default_group_by"]
                ),
            )
        )


def _clean_summary_payloads(bind) -> None:
    summary = sa.table(
        "instrument_summary_read_model",
        sa.column("instrument_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )
    rows = bind.execute(
        sa.select(summary.c.instrument_id, summary.c.payload_json)
    ).mappings()
    for row in rows:
        payload = dict(row["payload_json"] or {})
        for key in ("rating_as_of", *REMOVED_FIELDS):
            payload.pop(key, None)
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
    _clean_summary_payloads(bind)

    view_column = sa.table(
        "watchlist_view_column",
        sa.column("field_key", sa.String()),
    )
    field_registry = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
    )
    field_category = sa.table(
        "field_category",
        sa.column("category_code", sa.String()),
    )
    bind.execute(
        sa.delete(view_column).where(view_column.c.field_key.in_(REMOVED_FIELDS))
    )
    bind.execute(
        sa.delete(field_registry).where(field_registry.c.field_key.in_(REMOVED_FIELDS))
    )
    bind.execute(
        sa.delete(field_category).where(
            field_category.c.category_code == "ratings_analysis"
        )
    )

    op.drop_table("instrument_rating_read_model")
    op.drop_table("instrument_score_snapshot")
    with op.batch_alter_table("watchlist_row_read_model") as batch_op:
        batch_op.drop_column("overall_rating")
        batch_op.drop_column("analyst_stance")


def downgrade() -> None:
    raise RuntimeError(
        "Automatic ratings were removed because their methodology was invalid. "
        "Restore a pre-migration database backup instead of recreating them."
    )
