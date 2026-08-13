"""Restore the universal instrument-type Group By field.

Revision ID: 20260813_0040
Revises: 20260813_0039
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260813_0040"
down_revision = "20260813_0039"
branch_labels = None
depends_on = None


INSTRUMENT_TYPE_FIELD = {
    "field_key": "instrument_type",
    "label": "Instrument Type",
    "description": "Top-level instrument type resolved from shared identity.",
    "category_code": "general",
    "data_type": "string",
    "formatter_code": "text",
    "sort_mode": "alpha",
    "filter_mode": "multi_select",
    "group_mode": "discrete",
    "instrument_scope_json": [],
    "product_scope_json": [],
    "availability_rule_json": {"requires": ["watchlist_row_read_model"]},
    "source_domain": "read_model",
    "source_metric_code": "watchlist_row_read_model.instrument_type",
    "default_width": 140,
    "default_visible": False,
}


def upgrade() -> None:
    bind = op.get_bind()
    field = sa.table(
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
    exists = bind.execute(
        sa.select(field.c.field_key).where(field.c.field_key == "instrument_type")
    ).first()
    if exists is None:
        bind.execute(sa.insert(field).values(**INSTRUMENT_TYPE_FIELD))
    else:
        bind.execute(
            sa.update(field)
            .where(field.c.field_key == "instrument_type")
            .values(**INSTRUMENT_TYPE_FIELD)
        )


def downgrade() -> None:
    # The field existed in the original schema; this migration only repairs drift.
    pass
