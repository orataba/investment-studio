"""replace fund taxonomy with the multi-asset instrument taxonomy

Revision ID: 20260813_0038
Revises: 20260813_0037
Create Date: 2026-08-13 16:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260813_0038"
down_revision = "20260813_0037"
branch_labels = None
depends_on = None


GENERIC_TAXONOMY_FIELD_KEYS = [
    *[f"attr.instrument_taxonomy_level_{level}" for level in range(1, 8)],
    "attr.instrument_taxonomy_leaf",
    "attr.instrument_taxonomy_path",
]
LEGACY_TAXONOMY_FIELD_KEYS = [
    "attr.fund_regime",
    *[f"attr.fund_taxonomy_level_{level}" for level in range(1, 7)],
    "attr.fund_taxonomy_leaf",
    "attr.fund_taxonomy_path",
]
LEGACY_TO_GENERIC_FIELD_KEY = {
    "attr.fund_regime": "attr.instrument_taxonomy_level_1",
    **{
        f"attr.fund_taxonomy_level_{level}": f"attr.instrument_taxonomy_level_{level + 1}"
        for level in range(1, 7)
    },
    "attr.fund_taxonomy_leaf": "attr.instrument_taxonomy_leaf",
    "attr.fund_taxonomy_path": "attr.instrument_taxonomy_path",
}
LEGACY_TO_GENERIC_ATTRIBUTE_KEY = {
    key.removeprefix("attr."): value.removeprefix("attr.")
    for key, value in LEGACY_TO_GENERIC_FIELD_KEY.items()
}
EQUITY_SECTORS = [
    ("equity-sector-energy", "能源"),
    ("equity-sector-materials", "原材料"),
    ("equity-sector-industrials", "工业"),
    ("equity-sector-consumer-discretionary", "可选消费"),
    ("equity-sector-consumer-staples", "日常消费"),
    ("equity-sector-health-care", "医疗保健"),
    ("equity-sector-financials", "金融"),
    ("equity-sector-information-technology", "信息技术"),
    ("equity-sector-communication-services", "通信服务"),
    ("equity-sector-utilities", "公用事业"),
    ("equity-sector-real-estate", "房地产"),
]
EQUITY_TAXONOMY_NODE_IDS = ["equity", *[node_id for node_id, _ in EQUITY_SECTORS]]


def _transform_json(value: object, *, reverse: bool = False) -> object:
    field_map = (
        {value: key for key, value in LEGACY_TO_GENERIC_FIELD_KEY.items()}
        if reverse
        else LEGACY_TO_GENERIC_FIELD_KEY
    )
    attribute_map = (
        {value: key for key, value in LEGACY_TO_GENERIC_ATTRIBUTE_KEY.items()}
        if reverse
        else LEGACY_TO_GENERIC_ATTRIBUTE_KEY
    )
    source_code = "instrument_taxonomy" if reverse else "fund_taxonomy"
    target_code = "fund_taxonomy" if reverse else "instrument_taxonomy"
    if isinstance(value, dict):
        return {
            field_map.get(str(key), attribute_map.get(str(key), str(key))): _transform_json(
                item,
                reverse=reverse,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_transform_json(item, reverse=reverse) for item in value]
    if value == source_code:
        return target_code
    if isinstance(value, str):
        return field_map.get(value, attribute_map.get(value, value))
    return value


def _rewrite_json_column(
    bind,
    table,
    *,
    key_columns: list,
    value_column,
    reverse: bool = False,
) -> None:
    for row in bind.execute(sa.select(*key_columns, value_column)).mappings():
        current = row[value_column.name]
        updated = _transform_json(current, reverse=reverse)
        if updated == current:
            continue
        predicate = sa.and_(
            *[column == row[column.name] for column in key_columns]
        )
        bind.execute(sa.update(table).where(predicate).values({value_column.name: updated}))


def _field_rows() -> list[dict[str, object]]:
    common = {
        "category_code": "product_taxonomy",
        "data_type": "string",
        "formatter_code": "text",
        "sort_mode": "alpha",
        "filter_mode": "multi_select",
        "group_mode": "discrete",
        "instrument_scope_json": ["fund", "etf", "equity", "index"],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["instrument_taxonomy_assignment"]},
        "source_domain": "taxonomy",
        "default_visible": False,
    }
    rows = [
        {
            **common,
            "field_key": f"attr.instrument_taxonomy_level_{level}",
            "label": f"分类层级 {level}",
            "description": f"Level {level} of the assigned cross-asset instrument taxonomy path.",
            "source_metric_code": f"instrument_taxonomy.derived.level_{level}",
            "default_width": 150,
        }
        for level in range(1, 8)
    ]
    rows.extend(
        [
            {
                **common,
                "field_key": "attr.instrument_taxonomy_leaf",
                "label": "分类叶子",
                "description": "Leaf label of the assigned cross-asset instrument taxonomy path.",
                "source_metric_code": "instrument_taxonomy.derived.leaf",
                "default_width": 180,
            },
            {
                **common,
                "field_key": "attr.instrument_taxonomy_path",
                "label": "分类路径",
                "description": "Full assigned cross-asset instrument taxonomy path.",
                "source_metric_code": "instrument_taxonomy.derived.path",
                "default_width": 280,
            },
        ]
    )
    return rows


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
    assignment_table = sa.table(
        "instrument_taxonomy_assignment",
        sa.column("instrument_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("node_id", sa.String()),
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
    definition_table = sa.table(
        "instrument_attribute_definition",
        sa.column("attribute_key", sa.String()),
        sa.column("applicability_json", sa.JSON()),
        sa.column("instrument_scope_json", sa.JSON()),
    )
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
        sa.column("default_group_by", sa.String()),
    )
    view_column_table = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_column_id", sa.Integer()),
        sa.column("field_key", sa.String()),
    )

    bind.execute(
        sa.update(node_table)
        .where(node_table.c.taxonomy_code == "fund_taxonomy")
        .values(taxonomy_code="instrument_taxonomy")
    )
    bind.execute(
        sa.update(assignment_table)
        .where(assignment_table.c.taxonomy_code == "fund_taxonomy")
        .values(taxonomy_code="instrument_taxonomy")
    )
    bind.execute(
        sa.update(definition_table)
        .where(definition_table.c.attribute_key == "coverage_status")
        .values(instrument_scope_json=["fund", "etf", "equity", "index"])
    )
    bind.execute(
        sa.update(field_table)
        .where(field_table.c.field_key == "attr.coverage_status")
        .values(instrument_scope_json=["fund", "etf", "equity", "index"])
    )

    bind.execute(
        sa.update(node_table)
        .where(node_table.c.node_id == "index")
        .values(display_order=4)
    )
    equity_exists = bind.execute(
        sa.select(node_table.c.node_id).where(node_table.c.node_id == "equity")
    ).first()
    if equity_exists is None:
        bind.execute(
            sa.insert(node_table).values(
                node_id="equity",
                taxonomy_code="instrument_taxonomy",
                instrument_type="equity",
                label="股票",
                parent_node_id=None,
                level_index=1,
                display_order=3,
                is_leaf=False,
                path_labels_json=["股票"],
                path_node_ids_json=["equity"],
            )
        )
    for display_order, (node_id, label) in enumerate(EQUITY_SECTORS, start=1):
        exists = bind.execute(
            sa.select(node_table.c.node_id).where(node_table.c.node_id == node_id)
        ).first()
        if exists is None:
            bind.execute(
                sa.insert(node_table).values(
                    node_id=node_id,
                    taxonomy_code="instrument_taxonomy",
                    instrument_type="equity",
                    label=label,
                    parent_node_id="equity",
                    level_index=2,
                    display_order=display_order,
                    is_leaf=True,
                    path_labels_json=["股票", label],
                    path_node_ids_json=["equity", node_id],
                )
            )

    existing_fields = {
        str(row[0])
        for row in bind.execute(
            sa.select(field_table.c.field_key).where(
                field_table.c.field_key.in_(GENERIC_TAXONOMY_FIELD_KEYS)
            )
        )
    }
    for row in _field_rows():
        if str(row["field_key"]) in existing_fields:
            bind.execute(
                sa.update(field_table)
                .where(field_table.c.field_key == row["field_key"])
                .values(**row)
            )
        else:
            bind.execute(sa.insert(field_table).values(**row))

    for old_key, new_key in LEGACY_TO_GENERIC_FIELD_KEY.items():
        bind.execute(
            sa.update(view_column_table)
            .where(view_column_table.c.field_key == old_key)
            .values(field_key=new_key)
        )
        bind.execute(
            sa.update(view_table)
            .where(view_table.c.default_group_by == old_key)
            .values(default_group_by="taxonomy")
        )

    _rewrite_json_column(
        bind,
        watchlist_row_table,
        key_columns=[watchlist_row_table.c.watchlist_id, watchlist_row_table.c.instrument_id],
        value_column=watchlist_row_table.c.attributes_json,
    )
    _rewrite_json_column(
        bind,
        summary_table,
        key_columns=[summary_table.c.instrument_id],
        value_column=summary_table.c.payload_json,
    )
    _rewrite_json_column(
        bind,
        definition_table,
        key_columns=[definition_table.c.attribute_key],
        value_column=definition_table.c.applicability_json,
    )
    for column in (
        view_table.c.default_sort_json,
        view_table.c.default_filters_json,
        view_table.c.default_advanced_filter_json,
    ):
        _rewrite_json_column(
            bind,
            view_table,
            key_columns=[view_table.c.watchlist_view_id],
            value_column=column,
        )

    bind.execute(
        sa.delete(field_table).where(
            field_table.c.field_key.in_(LEGACY_TAXONOMY_FIELD_KEYS)
        )
    )


def downgrade() -> None:
    from watchlist_migration_snapshots.watchlist_fields import FIELD_REGISTRY

    bind = op.get_bind()
    assignment_table = sa.table(
        "instrument_taxonomy_assignment",
        sa.column("instrument_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("node_id", sa.String()),
    )
    node_table = sa.table(
        "instrument_taxonomy_node",
        sa.column("node_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("display_order", sa.Integer()),
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
    definition_table = sa.table(
        "instrument_attribute_definition",
        sa.column("attribute_key", sa.String()),
        sa.column("applicability_json", sa.JSON()),
        sa.column("instrument_scope_json", sa.JSON()),
    )
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
        sa.column("default_group_by", sa.String()),
    )
    view_column_table = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_column_id", sa.Integer()),
        sa.column("field_key", sa.String()),
    )

    _rewrite_json_column(
        bind,
        watchlist_row_table,
        key_columns=[watchlist_row_table.c.watchlist_id, watchlist_row_table.c.instrument_id],
        value_column=watchlist_row_table.c.attributes_json,
        reverse=True,
    )
    _rewrite_json_column(
        bind,
        summary_table,
        key_columns=[summary_table.c.instrument_id],
        value_column=summary_table.c.payload_json,
        reverse=True,
    )
    _rewrite_json_column(
        bind,
        definition_table,
        key_columns=[definition_table.c.attribute_key],
        value_column=definition_table.c.applicability_json,
        reverse=True,
    )
    for column in (
        view_table.c.default_sort_json,
        view_table.c.default_filters_json,
        view_table.c.default_advanced_filter_json,
    ):
        _rewrite_json_column(
            bind,
            view_table,
            key_columns=[view_table.c.watchlist_view_id],
            value_column=column,
            reverse=True,
        )
    for old_key, new_key in LEGACY_TO_GENERIC_FIELD_KEY.items():
        bind.execute(
            sa.update(view_column_table)
            .where(view_column_table.c.field_key == new_key)
            .values(field_key=old_key)
        )

    bind.execute(
        sa.update(assignment_table)
        .where(assignment_table.c.node_id.in_(EQUITY_TAXONOMY_NODE_IDS))
        .values(node_id=None)
    )
    bind.execute(
        sa.delete(node_table).where(
            node_table.c.node_id.in_([node_id for node_id, _ in EQUITY_SECTORS])
        )
    )
    bind.execute(sa.delete(node_table).where(node_table.c.node_id == "equity"))
    bind.execute(
        sa.update(node_table)
        .where(node_table.c.node_id == "index")
        .values(display_order=3)
    )
    bind.execute(
        sa.delete(field_table).where(
            field_table.c.field_key.in_(GENERIC_TAXONOMY_FIELD_KEYS)
        )
    )
    bind.execute(
        sa.update(definition_table)
        .where(definition_table.c.attribute_key == "coverage_status")
        .values(instrument_scope_json=["fund", "etf"])
    )
    bind.execute(
        sa.update(field_table)
        .where(field_table.c.field_key == "attr.coverage_status")
        .values(instrument_scope_json=["fund", "etf"])
    )
    existing_fields = {
        str(row[0])
        for row in bind.execute(
            sa.select(field_table.c.field_key).where(
                field_table.c.field_key.in_(LEGACY_TAXONOMY_FIELD_KEYS)
            )
        )
    }
    for row in FIELD_REGISTRY:
        if str(row.get("field_key")) in LEGACY_TAXONOMY_FIELD_KEYS and str(row["field_key"]) not in existing_fields:
            bind.execute(sa.insert(field_table).values(**row))

    bind.execute(
        sa.update(node_table)
        .where(node_table.c.taxonomy_code == "instrument_taxonomy")
        .values(taxonomy_code="fund_taxonomy")
    )
    bind.execute(
        sa.update(assignment_table)
        .where(assignment_table.c.taxonomy_code == "instrument_taxonomy")
        .values(taxonomy_code="fund_taxonomy")
    )
