"""add index root to fund taxonomy

Revision ID: 20260602_0020
Revises: 20260509_0019
Create Date: 2026-06-02 16:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260602_0020"
down_revision = "20260509_0019"
branch_labels = None
depends_on = None


TAXONOMY_FIELD_KEYS = [
    "attr.fund_regime",
    "attr.fund_taxonomy_level_1",
    "attr.fund_taxonomy_level_2",
    "attr.fund_taxonomy_level_3",
    "attr.fund_taxonomy_level_4",
    "attr.fund_taxonomy_level_5",
    "attr.fund_taxonomy_level_6",
    "attr.fund_taxonomy_leaf",
    "attr.fund_taxonomy_path",
]
INDEX_COMPATIBLE_FIELD_KEYS = [*TAXONOMY_FIELD_KEYS, "price_chart_1m"]
LOCAL_DETAIL_VIEW_FILTERS = {"instrument_type": ["fund", "index"]}
FUND_ONLY_VIEW_FILTERS = {"instrument_type": ["fund"]}
PRODUCT_SCREENING_VIEW_NAME = "产品分类筛选"
PRODUCT_SCREENING_VIEW_DESCRIPTION = "先按分类树缩小产品池，再叠加研究标签和监控判断。"
FUND_SCREENING_VIEW_NAME = "基金分类筛选"
FUND_SCREENING_VIEW_DESCRIPTION = "先按分类树缩小基金池，再叠加研究标签和监控判断。"


def upgrade() -> None:
    bind = op.get_bind()
    node_table = sa.table(
        "instrument_taxonomy_node",
        sa.column("node_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("instrument_type", sa.String()),
        sa.column("label", sa.String()),
        sa.column("parent_node_id", sa.String()),
        sa.column("level_index", sa.Integer()),
        sa.column("display_order", sa.Integer()),
        sa.column("is_leaf", sa.Boolean()),
        sa.column("path_labels_json", sa.JSON()),
        sa.column("path_node_ids_json", sa.JSON()),
    )
    field_table = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("instrument_scope_json", sa.JSON()),
    )
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("default_filters_json", sa.JSON()),
    )

    exists = bind.execute(
        sa.select(node_table.c.node_id).where(node_table.c.node_id == "index")
    ).first()
    if exists is None:
        bind.execute(
            sa.insert(node_table).values(
                node_id="index",
                taxonomy_code="fund_taxonomy",
                instrument_type="index",
                label="指数",
                parent_node_id=None,
                level_index=1,
                display_order=3,
                is_leaf=True,
                path_labels_json=["指数"],
                path_node_ids_json=["index"],
            )
        )

    bind.execute(
        sa.update(field_table)
        .where(field_table.c.field_key.in_(INDEX_COMPATIBLE_FIELD_KEYS))
        .values(instrument_scope_json=["fund", "index"])
    )
    bind.execute(
        sa.update(view_table)
        .where(view_table.c.kind == "system")
        .where(view_table.c.watchlist_view_id.like("%::fund-screening"))
        .values(
            name=PRODUCT_SCREENING_VIEW_NAME,
            description=PRODUCT_SCREENING_VIEW_DESCRIPTION,
            default_filters_json=LOCAL_DETAIL_VIEW_FILTERS,
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    node_table = sa.table(
        "instrument_taxonomy_node",
        sa.column("node_id", sa.String()),
    )
    field_table = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("instrument_scope_json", sa.JSON()),
    )
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("default_filters_json", sa.JSON()),
    )
    assignment_table = sa.table(
        "instrument_taxonomy_assignment",
        sa.column("node_id", sa.String()),
    )

    bind.execute(
        sa.update(assignment_table)
        .where(assignment_table.c.node_id == "index")
        .values(node_id=None)
    )
    bind.execute(sa.delete(node_table).where(node_table.c.node_id == "index"))
    bind.execute(
        sa.update(field_table)
        .where(field_table.c.field_key.in_(INDEX_COMPATIBLE_FIELD_KEYS))
        .values(instrument_scope_json=["fund"])
    )
    bind.execute(
        sa.update(view_table)
        .where(view_table.c.kind == "system")
        .where(view_table.c.watchlist_view_id.like("%::fund-screening"))
        .values(
            name=FUND_SCREENING_VIEW_NAME,
            description=FUND_SCREENING_VIEW_DESCRIPTION,
            default_filters_json=FUND_ONLY_VIEW_FILTERS,
        )
    )
