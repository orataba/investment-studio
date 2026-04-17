"""seed latest quote watchlist fields

Revision ID: f6d4c7f0a9b1
Revises: e7b5f5e4c11f
Create Date: 2026-04-16 01:10:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f6d4c7f0a9b1"
down_revision = "e7b5f5e4c11f"
branch_labels = None
depends_on = None


LATEST_QUOTE_FIELD_DEFINITIONS = [
    {
        "field_key": "latest_quote",
        "label": "Latest Quote",
        "description": "Most recent point from the active quote series used by the detail quote view.",
        "category_code": "general",
        "data_type": "number",
        "formatter_code": "decimal",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "bucket",
        "asset_scope_json": [],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["asset_chart_read_model"]},
        "source_domain": "read_model",
        "source_metric_code": "asset_chart_read_model.series.latest_quote",
        "default_width": 130,
        "default_visible": False,
    },
    {
        "field_key": "latest_quote_date",
        "label": "Quote Date",
        "description": "As-of date for the most recent active quote series point.",
        "category_code": "general",
        "data_type": "date",
        "formatter_code": "date",
        "sort_mode": "date",
        "filter_mode": "date_range",
        "group_mode": "bucket",
        "asset_scope_json": [],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["asset_chart_read_model"]},
        "source_domain": "read_model",
        "source_metric_code": "asset_chart_read_model.series.latest_quote_date",
        "default_width": 140,
        "default_visible": False,
    },
]


def upgrade() -> None:
    bind = op.get_bind()
    field_registry_table = sa.table(
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

    existing_field_keys = {
        row[0]
        for row in bind.execute(
            sa.select(field_registry_table.c.field_key).where(
                field_registry_table.c.field_key.in_(
                    [item["field_key"] for item in LATEST_QUOTE_FIELD_DEFINITIONS]
                )
            )
        )
    }
    missing_records = [
        definition
        for definition in LATEST_QUOTE_FIELD_DEFINITIONS
        if definition["field_key"] not in existing_field_keys
    ]
    if missing_records:
        op.bulk_insert(field_registry_table, missing_records)


def downgrade() -> None:
    bind = op.get_bind()
    field_registry_table = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
    )
    bind.execute(
        sa.delete(field_registry_table).where(
            field_registry_table.c.field_key.in_(
                [item["field_key"] for item in LATEST_QUOTE_FIELD_DEFINITIONS]
            )
        )
    )
