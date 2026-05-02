"""remove legacy category_name read model field

Revision ID: 20260501_0012
Revises: 20260430_0011
Create Date: 2026-05-01 10:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260501_0012"
down_revision = "20260430_0011"
branch_labels = None
depends_on = None


def _tables() -> tuple[sa.TableClause, sa.TableClause, sa.TableClause]:
    view_table = sa.table(
        "watchlist_view",
        sa.column("default_group_by", sa.String()),
    )
    column_table = sa.table(
        "watchlist_view_column",
        sa.column("field_key", sa.String()),
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
    return view_table, column_table, field_table


def upgrade() -> None:
    bind = op.get_bind()
    view_table, column_table, field_table = _tables()
    bind.execute(
        sa.delete(column_table).where(column_table.c.field_key == "category_name")
    )
    bind.execute(
        sa.update(view_table)
        .where(view_table.c.default_group_by == "category_name")
        .values(default_group_by="none")
    )
    bind.execute(
        sa.delete(field_table).where(field_table.c.field_key == "category_name")
    )
    with op.batch_alter_table("watchlist_row_read_model") as batch_op:
        batch_op.drop_column("category_name")


def downgrade() -> None:
    _, _, field_table = _tables()
    with op.batch_alter_table("watchlist_row_read_model") as batch_op:
        batch_op.add_column(sa.Column("category_name", sa.String(), nullable=True))
    op.bulk_insert(
        field_table,
        [
            {
                "field_key": "category_name",
                "label": "Peer Category",
                "description": "Legacy external peer group/category.",
                "category_code": "basics",
                "data_type": "string",
                "formatter_code": "text",
                "sort_mode": "alpha",
                "filter_mode": "multi_select",
                "group_mode": "discrete",
                "asset_scope_json": ["fund"],
                "product_scope_json": ["mutual_fund", "cef", "etf"],
                "availability_rule_json": {"requires": ["watchlist_row_read_model"]},
                "source_domain": "read_model",
                "source_metric_code": "watchlist_row_read_model.category_name",
                "default_width": 240,
                "default_visible": False,
            }
        ],
    )
