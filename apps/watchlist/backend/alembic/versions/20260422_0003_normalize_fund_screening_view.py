"""normalize fund screening view naming

Revision ID: 20260422_0003
Revises: 20260422_0002
Create Date: 2026-04-22 12:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260422_0003"
down_revision = "20260422_0002"
branch_labels = None
depends_on = None


LEGACY_VIEW_ID = "private-fund-screening"
LEGACY_VIEW_NAME = "私募分类筛选"
LEGACY_VIEW_DESCRIPTION = "先按分类树缩小私募基金池，再叠加研究标签和监控判断。"
LEGACY_DEFAULT_FILTERS = {"instrument_type": ["fund"], "attr.fund_regime": ["私募"]}

FUND_VIEW_ID = "fund-screening"
FUND_VIEW_NAME = "基金分类筛选"
FUND_VIEW_DESCRIPTION = "先按分类树缩小基金池，再叠加研究标签和监控判断。"
FUND_DEFAULT_FILTERS = {"instrument_type": ["fund"]}
FUND_VIEW_COLUMNS = [
    ("instrument_name", 1, 320),
    ("attr.fund_regime", 2, 120),
    ("attr.fund_category_l1", 3, 150),
    ("attr.fund_category_l2", 4, 170),
    ("attr.fund_category_l3", 5, 180),
    ("attr.implementation_style", 6, 140),
    ("attr.style_profile", 7, 220),
    ("attr.manager_assessment", 8, 220),
    ("attr.volatility_bucket", 9, 120),
    ("attr.drawdown_control", 10, 120),
    ("attr.style_stability", 11, 120),
    ("attr.transparency_quality", 12, 120),
    ("data_freshness_status", 13, 140),
]


def _replace_local_view_id(scoped_view_id: str, *, source: str, target: str) -> str:
    prefix = f"::{source}"
    if scoped_view_id.endswith(prefix):
        return f"{scoped_view_id[:-len(prefix)]}::{target}"
    return scoped_view_id


def _normalize_view(
    *,
    bind,
    view_table,
    column_table,
    source_local_view_id: str,
    target_local_view_id: str,
    target_name: str,
    target_description: str,
    target_filters: dict[str, object],
) -> None:
    candidate_rows = list(
        bind.execute(
            sa.select(
                view_table.c.watchlist_view_id,
                view_table.c.watchlist_id,
                view_table.c.kind,
                view_table.c.density,
                view_table.c.is_default,
                view_table.c.created_at,
            ).where(
                sa.or_(
                    view_table.c.watchlist_view_id.like(f"%::{source_local_view_id}"),
                    view_table.c.watchlist_view_id.like(f"%::{target_local_view_id}"),
                )
            )
        ).mappings()
    )
    for row in candidate_rows:
        current_view_id = str(row["watchlist_view_id"])
        target_view_id = _replace_local_view_id(
            current_view_id,
            source=source_local_view_id,
            target=target_local_view_id,
        )

        existing_target = bind.execute(
            sa.select(view_table.c.watchlist_view_id).where(
                view_table.c.watchlist_view_id == target_view_id
            )
        ).first()

        values = {
            "watchlist_id": row["watchlist_id"],
            "name": target_name,
            "description": target_description,
            "kind": row["kind"],
            "default_sort_json": [],
            "default_filters_json": target_filters,
            "default_advanced_filter_json": {},
            "default_group_by": "attr.fund_category_l1",
            "density": row["density"],
            "is_default": row["is_default"],
            "created_at": row["created_at"],
        }

        if existing_target is None:
            bind.execute(
                sa.insert(view_table).values(
                    watchlist_view_id=target_view_id,
                    **values,
                )
            )
        else:
            bind.execute(
                sa.update(view_table)
                .where(view_table.c.watchlist_view_id == target_view_id)
                .values(**values)
            )

        bind.execute(
            sa.delete(column_table).where(column_table.c.watchlist_view_id == target_view_id)
        )
        op.bulk_insert(
            column_table,
            [
                {
                    "watchlist_view_id": target_view_id,
                    "field_key": field_key,
                    "display_order": display_order,
                    "width": width,
                    "is_visible": True,
                    "pin_side": None,
                }
                for field_key, display_order, width in FUND_VIEW_COLUMNS
            ],
        )

        if current_view_id != target_view_id:
            bind.execute(
                sa.delete(column_table).where(column_table.c.watchlist_view_id == current_view_id)
            )
            bind.execute(
                sa.delete(view_table).where(view_table.c.watchlist_view_id == current_view_id)
            )


def upgrade() -> None:
    bind = op.get_bind()
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("watchlist_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
        sa.column("default_group_by", sa.String()),
        sa.column("density", sa.String()),
        sa.column("is_default", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
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

    _normalize_view(
        bind=bind,
        view_table=view_table,
        column_table=column_table,
        source_local_view_id=LEGACY_VIEW_ID,
        target_local_view_id=FUND_VIEW_ID,
        target_name=FUND_VIEW_NAME,
        target_description=FUND_VIEW_DESCRIPTION,
        target_filters=FUND_DEFAULT_FILTERS,
    )


def downgrade() -> None:
    bind = op.get_bind()
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("watchlist_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
        sa.column("default_group_by", sa.String()),
        sa.column("density", sa.String()),
        sa.column("is_default", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
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

    _normalize_view(
        bind=bind,
        view_table=view_table,
        column_table=column_table,
        source_local_view_id=FUND_VIEW_ID,
        target_local_view_id=LEGACY_VIEW_ID,
        target_name=LEGACY_VIEW_NAME,
        target_description=LEGACY_VIEW_DESCRIPTION,
        target_filters=LEGACY_DEFAULT_FILTERS,
    )
