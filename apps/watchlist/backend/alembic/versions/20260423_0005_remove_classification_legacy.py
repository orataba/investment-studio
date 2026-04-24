"""remove residual classification naming from fund taxonomy framework

Revision ID: 20260423_0005
Revises: 20260423_0004
Create Date: 2026-04-23 00:05:00
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "20260423_0005"
down_revision = "20260423_0004"
branch_labels = None
depends_on = None


OLD_TAXONOMY_CODE = "fund_classification"
NEW_TAXONOMY_CODE = "fund_taxonomy"
OLD_CATEGORY_CODE = "product_classification"
NEW_CATEGORY_CODE = "product_taxonomy"

FIELD_KEY_MAP = {
    "attr.fund_classification_level_1": "attr.fund_taxonomy_level_1",
    "attr.fund_classification_level_2": "attr.fund_taxonomy_level_2",
    "attr.fund_classification_level_3": "attr.fund_taxonomy_level_3",
    "attr.fund_classification_level_4": "attr.fund_taxonomy_level_4",
    "attr.fund_classification_level_5": "attr.fund_taxonomy_level_5",
    "attr.fund_classification_level_6": "attr.fund_taxonomy_level_6",
    "attr.fund_classification_leaf": "attr.fund_taxonomy_leaf",
    "attr.fund_classification_path": "attr.fund_taxonomy_path",
}
DERIVED_KEY_MAP = {
    "fund_classification_level_1": "fund_taxonomy_level_1",
    "fund_classification_level_2": "fund_taxonomy_level_2",
    "fund_classification_level_3": "fund_taxonomy_level_3",
    "fund_classification_level_4": "fund_taxonomy_level_4",
    "fund_classification_level_5": "fund_taxonomy_level_5",
    "fund_classification_level_6": "fund_taxonomy_level_6",
    "fund_classification_leaf": "fund_taxonomy_leaf",
    "fund_classification_path": "fund_taxonomy_path",
}
TAB_MAP = {
    "summary": "overview",
    "chart": "quote",
    "portfolio": "exposure",
}


def _serialize_json(value: object) -> object:
    if isinstance(value, (dict, list)):
        return json.loads(json.dumps(value, ensure_ascii=False))
    return value


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


def _rewrite_sort_rules(value: object, mapping: dict[str, str]) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    rewritten: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        if not field:
            continue
        rewritten.append({**item, "field": mapping.get(field, field)})
    return rewritten


def _rewrite_filters(value: object, mapping: dict[str, str]) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    rewritten: dict[str, object] = {}
    for key, field_value in value.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        rewritten[mapping.get(normalized_key, normalized_key)] = field_value
    return rewritten


def _rewrite_advanced_filter(node: object, mapping: dict[str, str]) -> object:
    if not isinstance(node, dict):
        return node
    if node.get("type") == "rule":
        field = str(node.get("field") or "").strip()
        if field:
            return {**node, "field": mapping.get(field, field)}
        return node
    if node.get("type") == "group":
        conditions = node.get("conditions")
        if isinstance(conditions, list):
            return {
                **node,
                "conditions": [_rewrite_advanced_filter(item, mapping) for item in conditions],
            }
    return node


def _rewrite_attribute_payload(value: object, mapping: dict[str, str]) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    rewritten: dict[str, object] = {}
    for key, item in value.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        rewritten[mapping.get(normalized_key, normalized_key)] = item
    return rewritten


def _rewrite_taxonomy_payload(payload: object, mapping: dict[str, str]) -> dict[str, object] | object:
    if not isinstance(payload, dict):
        return payload
    rewritten = dict(payload)
    if str(rewritten.get("taxonomy_code") or "").strip() == OLD_TAXONOMY_CODE:
        rewritten["taxonomy_code"] = NEW_TAXONOMY_CODE
    derived_values = rewritten.get("derived_values")
    if isinstance(derived_values, dict):
        rewritten["derived_values"] = {
            mapping.get(str(key), str(key)): value
            for key, value in derived_values.items()
            if str(key).strip()
        }
    return rewritten


def _rewrite_tabs(value: object, mapping: dict[str, str]) -> list[str]:
    if not isinstance(value, list):
        return ["overview"]
    rewritten: list[str] = []
    seen: set[str] = set()
    for item in value:
        tab = mapping.get(str(item or "").strip(), str(item or "").strip())
        if not tab or tab in seen:
            continue
        seen.add(tab)
        rewritten.append(tab)
    return rewritten or ["overview"]


def _rewrite_summary_payload(
    payload: object,
    *,
    derived_key_map: dict[str, str],
    tab_map: dict[str, str],
) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    rewritten = dict(payload)
    legacy_taxonomy = rewritten.pop("classification", None)
    taxonomy_payload = rewritten.get("taxonomy", legacy_taxonomy)
    if taxonomy_payload is not None:
        rewritten["taxonomy"] = _rewrite_taxonomy_payload(
            taxonomy_payload,
            derived_key_map,
        )
    rewritten["tabs"] = _rewrite_tabs(rewritten.get("tabs"), tab_map)
    return rewritten


def upgrade() -> None:
    from watchlist_app.reference_data.watchlist_fields import FIELD_CATEGORIES, FIELD_REGISTRY

    bind = op.get_bind()
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
        sa.column("asset_scope_json", sa.JSON()),
        sa.column("product_scope_json", sa.JSON()),
        sa.column("availability_rule_json", sa.JSON()),
        sa.column("source_domain", sa.String()),
        sa.column("source_metric_code", sa.String()),
        sa.column("default_width", sa.Integer()),
        sa.column("default_visible", sa.Boolean()),
    )
    definition_table = sa.table(
        "instrument_attribute_definition",
        sa.column("attribute_key", sa.String()),
        sa.column("domain_code", sa.String()),
        sa.column("group_code", sa.String()),
    )
    node_table = sa.table(
        "instrument_taxonomy_node",
        sa.column("taxonomy_code", sa.String()),
    )
    assignment_table = sa.table(
        "instrument_taxonomy_assignment",
        sa.column("taxonomy_code", sa.String()),
    )
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_group_by", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
    )
    view_column_table = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("field_key", sa.String()),
    )
    watchlist_row_table = sa.table(
        "watchlist_row_read_model",
        sa.column("watchlist_id", sa.String()),
        sa.column("asset_id", sa.String()),
        sa.column("attributes_json", sa.JSON()),
    )
    summary_table = sa.table(
        "asset_summary_read_model",
        sa.column("asset_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )

    relevant_categories = [
        row
        for row in FIELD_CATEGORIES
        if row["category_code"] in {"basics", NEW_CATEGORY_CODE}
    ]
    _upsert_field_category_rows(bind, field_category_table, relevant_categories)

    relevant_field_rows = [
        row
        for row in FIELD_REGISTRY
        if row["field_key"] == "attr.fund_vehicle"
        or row["field_key"] == "attr.fund_regime"
        or str(row["field_key"]).startswith("attr.fund_taxonomy_")
    ]
    _upsert_field_registry_rows(bind, field_registry_table, relevant_field_rows)
    bind.execute(
        sa.update(field_registry_table)
        .where(field_registry_table.c.category_code == OLD_CATEGORY_CODE)
        .values(category_code=NEW_CATEGORY_CODE)
    )

    bind.execute(
        sa.update(definition_table)
        .where(definition_table.c.domain_code == "classification")
        .values(domain_code="overview")
    )
    bind.execute(
        sa.update(definition_table)
        .where(definition_table.c.group_code == "taxonomy_identity")
        .values(group_code="overview_identity")
    )
    bind.execute(
        sa.update(node_table)
        .where(node_table.c.taxonomy_code == OLD_TAXONOMY_CODE)
        .values(taxonomy_code=NEW_TAXONOMY_CODE)
    )
    bind.execute(
        sa.update(assignment_table)
        .where(assignment_table.c.taxonomy_code == OLD_TAXONOMY_CODE)
        .values(taxonomy_code=NEW_TAXONOMY_CODE)
    )

    for row in bind.execute(sa.select(view_column_table)).mappings():
        next_field_key = FIELD_KEY_MAP.get(str(row["field_key"]), str(row["field_key"]))
        if next_field_key == row["field_key"]:
            continue
        bind.execute(
            sa.update(view_column_table)
            .where(
                view_column_table.c.watchlist_view_id == row["watchlist_view_id"],
                view_column_table.c.field_key == row["field_key"],
            )
            .values(field_key=next_field_key)
        )

    for row in bind.execute(sa.select(view_table)).mappings():
        bind.execute(
            sa.update(view_table)
            .where(view_table.c.watchlist_view_id == row["watchlist_view_id"])
            .values(
                default_group_by=FIELD_KEY_MAP.get(
                    str(row["default_group_by"] or "").strip(),
                    row["default_group_by"],
                ),
                default_sort_json=_serialize_json(
                    _rewrite_sort_rules(row["default_sort_json"], FIELD_KEY_MAP)
                ),
                default_filters_json=_serialize_json(
                    _rewrite_filters(row["default_filters_json"], FIELD_KEY_MAP)
                ),
                default_advanced_filter_json=_serialize_json(
                    _rewrite_advanced_filter(
                        row["default_advanced_filter_json"],
                        FIELD_KEY_MAP,
                    )
                ),
            )
        )

    for row in bind.execute(sa.select(watchlist_row_table)).mappings():
        bind.execute(
            sa.update(watchlist_row_table)
            .where(
                watchlist_row_table.c.watchlist_id == row["watchlist_id"],
                watchlist_row_table.c.asset_id == row["asset_id"],
            )
            .values(
                attributes_json=_serialize_json(
                    _rewrite_attribute_payload(row["attributes_json"], DERIVED_KEY_MAP)
                )
            )
        )

    for row in bind.execute(sa.select(summary_table)).mappings():
        bind.execute(
            sa.update(summary_table)
            .where(summary_table.c.asset_id == row["asset_id"])
            .values(
                payload_json=_serialize_json(
                    _rewrite_summary_payload(
                        row["payload_json"],
                        derived_key_map=DERIVED_KEY_MAP,
                        tab_map=TAB_MAP,
                    )
                )
            )
        )

    bind.execute(
        sa.delete(field_registry_table).where(
            field_registry_table.c.field_key.in_(sorted(FIELD_KEY_MAP.keys()))
        )
    )
    bind.execute(
        sa.delete(field_category_table).where(
            field_category_table.c.category_code == OLD_CATEGORY_CODE
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    reverse_field_key_map = {value: key for key, value in FIELD_KEY_MAP.items()}
    reverse_derived_key_map = {value: key for key, value in DERIVED_KEY_MAP.items()}
    reverse_tab_map = {value: key for key, value in TAB_MAP.items()}

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
    )
    definition_table = sa.table(
        "instrument_attribute_definition",
        sa.column("domain_code", sa.String()),
        sa.column("group_code", sa.String()),
    )
    node_table = sa.table(
        "instrument_taxonomy_node",
        sa.column("taxonomy_code", sa.String()),
    )
    assignment_table = sa.table(
        "instrument_taxonomy_assignment",
        sa.column("taxonomy_code", sa.String()),
    )
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_group_by", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
    )
    view_column_table = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("field_key", sa.String()),
    )
    watchlist_row_table = sa.table(
        "watchlist_row_read_model",
        sa.column("watchlist_id", sa.String()),
        sa.column("asset_id", sa.String()),
        sa.column("attributes_json", sa.JSON()),
    )
    summary_table = sa.table(
        "asset_summary_read_model",
        sa.column("asset_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )

    bind.execute(
        sa.insert(field_category_table).values(
            category_code=OLD_CATEGORY_CODE,
            label="Product Classification",
            parent_category_code=None,
            display_order=3,
        )
    )

    bind.execute(
        sa.update(definition_table)
        .where(definition_table.c.domain_code == "overview")
        .values(domain_code="classification")
    )
    bind.execute(
        sa.update(definition_table)
        .where(definition_table.c.group_code == "overview_identity")
        .values(group_code="taxonomy_identity")
    )
    bind.execute(
        sa.update(node_table)
        .where(node_table.c.taxonomy_code == NEW_TAXONOMY_CODE)
        .values(taxonomy_code=OLD_TAXONOMY_CODE)
    )
    bind.execute(
        sa.update(assignment_table)
        .where(assignment_table.c.taxonomy_code == NEW_TAXONOMY_CODE)
        .values(taxonomy_code=OLD_TAXONOMY_CODE)
    )

    for row in bind.execute(sa.select(view_column_table)).mappings():
        next_field_key = reverse_field_key_map.get(str(row["field_key"]), str(row["field_key"]))
        if next_field_key == row["field_key"]:
            continue
        bind.execute(
            sa.update(view_column_table)
            .where(
                view_column_table.c.watchlist_view_id == row["watchlist_view_id"],
                view_column_table.c.field_key == row["field_key"],
            )
            .values(field_key=next_field_key)
        )

    for row in bind.execute(sa.select(view_table)).mappings():
        bind.execute(
            sa.update(view_table)
            .where(view_table.c.watchlist_view_id == row["watchlist_view_id"])
            .values(
                default_group_by=reverse_field_key_map.get(
                    str(row["default_group_by"] or "").strip(),
                    row["default_group_by"],
                ),
                default_sort_json=_serialize_json(
                    _rewrite_sort_rules(row["default_sort_json"], reverse_field_key_map)
                ),
                default_filters_json=_serialize_json(
                    _rewrite_filters(row["default_filters_json"], reverse_field_key_map)
                ),
                default_advanced_filter_json=_serialize_json(
                    _rewrite_advanced_filter(
                        row["default_advanced_filter_json"],
                        reverse_field_key_map,
                    )
                ),
            )
        )

    for row in bind.execute(sa.select(watchlist_row_table)).mappings():
        bind.execute(
            sa.update(watchlist_row_table)
            .where(
                watchlist_row_table.c.watchlist_id == row["watchlist_id"],
                watchlist_row_table.c.asset_id == row["asset_id"],
            )
            .values(
                attributes_json=_serialize_json(
                    _rewrite_attribute_payload(row["attributes_json"], reverse_derived_key_map)
                )
            )
        )

    for row in bind.execute(sa.select(summary_table)).mappings():
        payload = _rewrite_summary_payload(
            row["payload_json"],
            derived_key_map=reverse_derived_key_map,
            tab_map=reverse_tab_map,
        )
        taxonomy_payload = payload.get("taxonomy")
        if isinstance(taxonomy_payload, dict):
            payload["classification"] = taxonomy_payload
            payload.pop("taxonomy", None)
        bind.execute(
            sa.update(summary_table)
            .where(summary_table.c.asset_id == row["asset_id"])
            .values(payload_json=_serialize_json(payload))
        )

    bind.execute(
        sa.delete(field_category_table).where(
            field_category_table.c.category_code == NEW_CATEGORY_CODE
        )
    )
    bind.execute(
        sa.delete(field_registry_table).where(
            field_registry_table.c.field_key.in_(sorted(reverse_field_key_map.keys()))
        )
    )
