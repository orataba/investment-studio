"""seed qualitative research framework

Revision ID: 20260501_0016
Revises: 20260501_0015
Create Date: 2026-05-01 18:40:00
"""

from __future__ import annotations

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "20260501_0016"
down_revision = "20260501_0015"
branch_labels = None
depends_on = None


CREATED_AT = datetime(2026, 5, 1, 18, 40, tzinfo=UTC)
NEW_ATTRIBUTE_KEYS = {
    "research_evidence_level",
    "investment_edge_quality",
    "process_repeatability",
    "decision_discipline",
    "style_drift_risk",
    "risk_management_quality",
    "liquidity_terms_fit",
    "fee_value_assessment",
    "alignment_quality",
    "portfolio_role",
}


def _tables() -> tuple[sa.TableClause, sa.TableClause]:
    definition_table = sa.table(
        "instrument_attribute_definition",
        sa.column("attribute_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.String()),
        sa.column("data_type", sa.String()),
        sa.column("domain_code", sa.String()),
        sa.column("group_code", sa.String()),
        sa.column("display_order", sa.Integer()),
        sa.column("options_json", sa.JSON()),
        sa.column("instrument_scope_json", sa.JSON()),
        sa.column("applicability_json", sa.JSON()),
        sa.column("rubric_json", sa.JSON()),
        sa.column("is_groupable", sa.Boolean()),
        sa.column("is_filterable", sa.Boolean()),
        sa.column("is_view_column", sa.Boolean()),
        sa.column("default_visible", sa.Boolean()),
        sa.column("required_for_monitoring", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
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
        sa.column("instrument_scope_json", sa.JSON()),
        sa.column("product_scope_json", sa.JSON()),
        sa.column("availability_rule_json", sa.JSON()),
        sa.column("source_domain", sa.String()),
        sa.column("source_metric_code", sa.String()),
        sa.column("default_width", sa.Integer()),
        sa.column("default_visible", sa.Boolean()),
    )
    return definition_table, field_table


def _definition_payload(definition: dict[str, object]) -> dict[str, object]:
    return {
        "label": definition["label"],
        "description": definition.get("description"),
        "data_type": definition["data_type"],
        "domain_code": definition["domain_code"],
        "group_code": definition["group_code"],
        "display_order": definition.get("display_order", 999),
        "options_json": definition.get("options", []),
        "instrument_scope_json": definition.get("instrument_scope_json", ["fund"]),
        "applicability_json": definition.get("applicability_json", {}),
        "rubric_json": definition.get("rubric_json", {}),
        "is_groupable": definition.get("is_groupable", True),
        "is_filterable": definition.get("is_filterable", True),
        "is_view_column": definition.get("is_view_column", True),
        "default_visible": definition.get("default_visible", False),
        "required_for_monitoring": definition.get("required_for_monitoring", False),
    }


def upgrade() -> None:
    from watchlist_app.reference_data.watchlist_fields import (
        INSTRUMENT_ATTRIBUTE_DEFINITIONS,
        build_attribute_field_definition,
    )

    bind = op.get_bind()
    definition_table, field_table = _tables()
    existing_attribute_keys = {
        str(row["attribute_key"])
        for row in bind.execute(
            sa.select(definition_table.c.attribute_key)
        ).mappings()
    }
    existing_field_keys = {
        str(row["field_key"])
        for row in bind.execute(sa.select(field_table.c.field_key)).mappings()
    }

    for definition in INSTRUMENT_ATTRIBUTE_DEFINITIONS:
        attribute_key = str(definition["attribute_key"])
        payload = _definition_payload(definition)
        if attribute_key in existing_attribute_keys:
            bind.execute(
                sa.update(definition_table)
                .where(definition_table.c.attribute_key == attribute_key)
                .values(**payload)
            )
        else:
            bind.execute(
                sa.insert(definition_table).values(
                    attribute_key=attribute_key,
                    created_at=CREATED_AT,
                    **payload,
                )
            )

        if not definition.get("is_view_column", True):
            continue
        field_payload = build_attribute_field_definition(definition)
        field_key = str(field_payload["field_key"])
        if field_key in existing_field_keys:
            bind.execute(
                sa.update(field_table)
                .where(field_table.c.field_key == field_key)
                .values(**field_payload)
            )
        else:
            bind.execute(sa.insert(field_table).values(**field_payload))


def downgrade() -> None:
    bind = op.get_bind()
    definition_table, field_table = _tables()
    bind.execute(
        sa.delete(field_table).where(
            field_table.c.field_key.in_([f"attr.{key}" for key in sorted(NEW_ATTRIBUTE_KEYS)])
        )
    )
    bind.execute(
        sa.delete(definition_table).where(
            definition_table.c.attribute_key.in_(sorted(NEW_ATTRIBUTE_KEYS))
        )
    )
