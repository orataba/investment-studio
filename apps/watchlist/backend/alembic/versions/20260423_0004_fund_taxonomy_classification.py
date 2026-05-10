"""replace fixed fund category fields with hierarchical fund taxonomy

Revision ID: 20260423_0004
Revises: 20260422_0003
Create Date: 2026-04-23 00:04:00
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "20260423_0004"
down_revision = "20260422_0003"
branch_labels = None
depends_on = None


REMOVED_CLASSIFICATION_KEYS = {
    "fund_regime",
    "fund_category_l1",
    "fund_category_l2",
    "fund_category_l3",
}
FIELD_KEY_MAP = {
    "attr.fund_category_l1": "attr.fund_classification_level_1",
    "attr.fund_category_l2": "attr.fund_classification_level_2",
    "attr.fund_category_l3": "attr.fund_classification_level_3",
}
FUND_SCREENING_VIEW_COLUMNS = [
    ("instrument_name", 1, 320),
    ("attr.fund_regime", 2, 120),
    ("attr.fund_classification_level_1", 3, 150),
    ("attr.fund_classification_level_2", 4, 170),
    ("attr.fund_classification_level_3", 5, 180),
    ("attr.implementation_style", 6, 140),
    ("attr.style_profile", 7, 220),
    ("attr.manager_assessment", 8, 220),
    ("attr.volatility_bucket", 9, 120),
    ("attr.drawdown_control", 10, 120),
    ("attr.style_stability", 11, 120),
    ("attr.transparency_quality", 12, 120),
    ("data_freshness_status", 13, 140),
]


def _serialize_json(value: object) -> object:
    if isinstance(value, (dict, list)):
        return json.loads(json.dumps(value, ensure_ascii=False))
    return value


def _upsert_field_registry_rows(bind, field_registry_table, rows: list[dict[str, object]]) -> None:
    existing_keys = {
        str(item["field_key"])
        for item in bind.execute(sa.select(field_registry_table.c.field_key)).mappings()
    }
    for row in rows:
        payload = {key: _serialize_json(value) for key, value in row.items()}
        if str(payload["field_key"]) in existing_keys:
            bind.execute(
                sa.update(field_registry_table)
                .where(field_registry_table.c.field_key == payload["field_key"])
                .values(**payload)
            )
        else:
            bind.execute(sa.insert(field_registry_table).values(**payload))


def _upsert_field_category_rows(bind, field_category_table, rows: list[dict[str, object]]) -> None:
    existing_codes = {
        str(item["category_code"])
        for item in bind.execute(sa.select(field_category_table.c.category_code)).mappings()
    }
    for row in rows:
        payload = {key: _serialize_json(value) for key, value in row.items()}
        if str(payload["category_code"]) in existing_codes:
            bind.execute(
                sa.update(field_category_table)
                .where(field_category_table.c.category_code == payload["category_code"])
                .values(**payload)
            )
        else:
            bind.execute(sa.insert(field_category_table).values(**payload))


def _rewrite_sort_rules(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    rewritten: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        if not field:
            continue
        rewritten.append({**item, "field": FIELD_KEY_MAP.get(field, field)})
    return rewritten


def _rewrite_filters(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    rewritten: dict[str, object] = {}
    for key, field_value in value.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        rewritten[FIELD_KEY_MAP.get(normalized_key, normalized_key)] = field_value
    return rewritten


def _rewrite_advanced_filter(node: object) -> object:
    if not isinstance(node, dict):
        return node
    if node.get("type") == "rule":
        field = str(node.get("field") or "").strip()
        if field:
            return {**node, "field": FIELD_KEY_MAP.get(field, field)}
        return node
    if node.get("type") == "group":
        conditions = node.get("conditions")
        if isinstance(conditions, list):
            return {
                **node,
                "conditions": [_rewrite_advanced_filter(item) for item in conditions],
            }
    return node


def upgrade() -> None:
    from watchlist_app.reference_data.fund_taxonomy import fund_taxonomy_nodes
    from watchlist_app.reference_data.watchlist_fields import FIELD_CATEGORIES, FIELD_REGISTRY
    from watchlist_app.services.fund_taxonomy import (
        build_taxonomy_context,
        merge_taxonomy_attributes,
    )

    op.create_table(
        "instrument_taxonomy_node",
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("taxonomy_code", sa.String(), nullable=False),
        sa.Column("instrument_type", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("parent_node_id", sa.String(), nullable=True),
        sa.Column("level_index", sa.Integer(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("is_leaf", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("path_labels_json", sa.JSON(), nullable=False),
        sa.Column("path_node_ids_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_node_id"],
            ["instrument_taxonomy_node.node_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("node_id"),
    )
    op.create_index(
        "idx_instrument_taxonomy_node_taxonomy_parent_order",
        "instrument_taxonomy_node",
        ["taxonomy_code", "parent_node_id", "display_order"],
    )

    op.create_table(
        "instrument_taxonomy_assignment",
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("taxonomy_code", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_record_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument_detail.instrument_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["instrument_taxonomy_node.node_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("instrument_id", "taxonomy_code"),
    )
    op.create_index(
        "idx_instrument_taxonomy_assignment_taxonomy_node",
        "instrument_taxonomy_assignment",
        ["taxonomy_code", "node_id"],
    )

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
    value_table = sa.table(
        "instrument_attribute_value",
        sa.column("instrument_attribute_value_id", sa.Integer()),
        sa.column("instrument_id", sa.String()),
        sa.column("attribute_key", sa.String()),
        sa.column("value_json", sa.JSON()),
        sa.column("adopted_at", sa.DateTime(timezone=True)),
    )
    definition_table = sa.table(
        "instrument_attribute_definition",
        sa.column("attribute_key", sa.String()),
    )
    field_category_table = sa.table(
        "field_category",
        sa.column("category_code", sa.String()),
        sa.column("label", sa.String()),
        sa.column("parent_category_code", sa.String()),
        sa.column("display_order", sa.Integer()),
    )
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
        sa.column("instrument_scope_json", sa.JSON()),
        sa.column("product_scope_json", sa.JSON()),
        sa.column("availability_rule_json", sa.JSON()),
        sa.column("source_domain", sa.String()),
        sa.column("source_metric_code", sa.String()),
        sa.column("default_width", sa.Integer()),
        sa.column("default_visible", sa.Boolean()),
    )
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("default_group_by", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
    )
    view_column_table = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("field_key", sa.String()),
        sa.column("display_order", sa.Integer()),
        sa.column("width", sa.Integer()),
        sa.column("is_visible", sa.Boolean()),
    )
    watchlist_row_table = sa.table(
        "watchlist_row_read_model",
        sa.column("watchlist_id", sa.String()),
        sa.column("instrument_id", sa.String()),
        sa.column("attributes_json", sa.JSON()),
    )
    summary_table = sa.table(
        "instrument_summary_read_model",
        sa.column("instrument_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )

    nodes = fund_taxonomy_nodes()
    bind.execute(sa.insert(node_table), [{key: _serialize_json(value) for key, value in row.items()} for row in nodes])
    empty_taxonomy_context = build_taxonomy_context(None)

    bind.execute(
        sa.delete(value_table).where(
            value_table.c.attribute_key.in_(sorted(REMOVED_CLASSIFICATION_KEYS))
        )
    )
    bind.execute(
        sa.delete(definition_table).where(
            definition_table.c.attribute_key.in_(sorted(REMOVED_CLASSIFICATION_KEYS))
        )
    )
    bind.execute(
        sa.delete(field_registry_table).where(
            field_registry_table.c.field_key.in_(
                ["attr.fund_category_l1", "attr.fund_category_l2", "attr.fund_category_l3"]
            )
        )
    )

    taxonomy_field_rows = [
        row
        for row in FIELD_REGISTRY
        if str(row["field_key"]).startswith("attr.fund_")
        and (
            row["field_key"] == "attr.fund_regime"
            or row["field_key"].startswith("attr.fund_classification_")
        )
    ]
    _upsert_field_category_rows(bind, field_category_table, list(FIELD_CATEGORIES))
    _upsert_field_registry_rows(bind, field_registry_table, taxonomy_field_rows)

    for row in bind.execute(sa.select(view_table)).mappings():
        watchlist_view_id = str(row["watchlist_view_id"])
        rewritten_group_by = FIELD_KEY_MAP.get(
            str(row["default_group_by"] or "").strip(),
            row["default_group_by"],
        )
        rewritten_sort = _rewrite_sort_rules(row["default_sort_json"])
        rewritten_filters = _rewrite_filters(row["default_filters_json"])
        rewritten_advanced = _rewrite_advanced_filter(row["default_advanced_filter_json"])

        if str(row["name"] or "") == "基金分类筛选" and str(row["kind"] or "") == "system":
            rewritten_group_by = "attr.fund_classification_level_1"
            bind.execute(
                sa.delete(view_column_table).where(
                    view_column_table.c.watchlist_view_id == watchlist_view_id
                )
            )
            bind.execute(
                sa.insert(view_column_table),
                [
                    {
                        "watchlist_view_id": watchlist_view_id,
                        "field_key": field_key,
                        "display_order": display_order,
                        "width": width,
                        "is_visible": True,
                    }
                    for field_key, display_order, width in FUND_SCREENING_VIEW_COLUMNS
                ],
            )
        else:
            for column in bind.execute(
                sa.select(view_column_table).where(
                    view_column_table.c.watchlist_view_id == watchlist_view_id
                )
            ).mappings():
                next_field_key = FIELD_KEY_MAP.get(
                    str(column["field_key"]),
                    str(column["field_key"]),
                )
                if next_field_key == column["field_key"]:
                    continue
                bind.execute(
                    sa.update(view_column_table)
                    .where(
                        view_column_table.c.watchlist_view_id == watchlist_view_id,
                        view_column_table.c.field_key == column["field_key"],
                    )
                    .values(field_key=next_field_key)
                )

        bind.execute(
            sa.update(view_table)
            .where(view_table.c.watchlist_view_id == watchlist_view_id)
            .values(
                default_group_by=rewritten_group_by,
                default_sort_json=_serialize_json(rewritten_sort),
                default_filters_json=_serialize_json(rewritten_filters),
                default_advanced_filter_json=_serialize_json(rewritten_advanced),
            )
        )

    for row in bind.execute(sa.select(watchlist_row_table)).mappings():
        instrument_id = str(row["instrument_id"])
        next_attributes = merge_taxonomy_attributes(
            taxonomy_context=empty_taxonomy_context,
            instrument_attributes=row["attributes_json"] if isinstance(row["attributes_json"], dict) else {},
        )
        bind.execute(
            sa.update(watchlist_row_table)
            .where(
                watchlist_row_table.c.watchlist_id == row["watchlist_id"],
                watchlist_row_table.c.instrument_id == instrument_id,
            )
            .values(attributes_json=_serialize_json(next_attributes))
        )

    for row in bind.execute(sa.select(summary_table)).mappings():
        instrument_id = str(row["instrument_id"])
        payload = row["payload_json"] if isinstance(row["payload_json"], dict) else {}
        bind.execute(
            sa.update(summary_table)
            .where(summary_table.c.instrument_id == instrument_id)
            .values(
                payload_json=_serialize_json(
                    {
                        **payload,
                        "classification": empty_taxonomy_context,
                    }
                )
            )
        )


def downgrade() -> None:
    op.drop_index(
        "idx_instrument_taxonomy_assignment_taxonomy_node",
        table_name="instrument_taxonomy_assignment",
    )
    op.drop_table("instrument_taxonomy_assignment")
    op.drop_index(
        "idx_instrument_taxonomy_node_taxonomy_parent_order",
        table_name="instrument_taxonomy_node",
    )
    op.drop_table("instrument_taxonomy_node")
