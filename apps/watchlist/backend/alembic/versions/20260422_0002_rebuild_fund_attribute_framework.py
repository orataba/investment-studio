"""rebuild fund attribute framework into classification/research/monitoring

Revision ID: 20260422_0002
Revises: 20260421_0001
Create Date: 2026-04-22 00:02:00
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from alembic import op
import sqlalchemy as sa


revision = "20260422_0002"
down_revision = "20260421_0001"
branch_labels = None
depends_on = None


REFERENCE_CREATED_AT = datetime(2026, 4, 22, 0, 2, tzinfo=UTC)
MIGRATION_SOURCE_RECORD_ID = "migration/fund-framework-v3"
PRIVATE_SCREENING_VIEW_ID = "private-fund-screening"
PRIVATE_SCREENING_VIEW_NAME = "私募分类筛选"
PRIVATE_SCREENING_VIEW_DESCRIPTION = "先按分类树缩小私募基金池，再叠加研究标签和监控判断。"
PRIVATE_SCREENING_VIEW_COLUMNS = [
    ("asset_name", 1, 320),
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
LEGACY_STRATEGY_KEYS = {"strategy_family", "strategy_subtype"}
FIELD_KEY_MAP = {
    "attr.strategy_family": "attr.fund_category_l1",
    "attr.strategy_subtype": "attr.fund_category_l2",
}


def _normalize_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _first_match(options: list[str], candidates: list[str]) -> str | None:
    normalized = {item.strip() for item in candidates if item.strip()}
    for option in options:
        if option in normalized:
            return option
    return None


def _derive_category_l1(regime: str | None, family: str | None, subtypes: list[str]) -> str | None:
    if "指数增强" in subtypes or _first_match(["宽基指数", "债券指数", "行业主题指数"], subtypes):
        return "指数工具"
    if family == "主动权益":
        return "主动权益" if regime == "公募" else "股票"
    if family == "被动指数":
        return "指数工具"
    if family == "股票对冲":
        return "股票"
    if family == "CTA":
        return "商品"
    if family == "套利":
        return "相对价值"
    if family == "多策略":
        return "多资产"
    if family == "债券策略":
        return "固定收益" if regime == "公募" else "债券"
    if family in {"宏观", "期权/波动率"}:
        return family
    if family == "FOF/MOM":
        return "FOF/MOM"
    return None


def _derive_category_l2(regime: str | None, family: str | None, subtypes: list[str]) -> str | None:
    if "指数增强" in subtypes:
        return "指数增强"
    if "市场中性" in subtypes or "统计套利" in subtypes:
        return "市场中性"
    if "量化选股" in subtypes:
        return "量化选股"
    if "股票多头" in subtypes:
        return "主动股票" if regime == "公募" else "主观选股"
    if "宽基指数" in subtypes:
        return "宽基指数"
    if "债券指数" in subtypes:
        return "债券指数"
    if "行业主题指数" in subtypes:
        return "行业主题指数"
    if "信用债" in subtypes:
        return "信用债"
    if "可转债策略" in subtypes:
        return "可转债"
    if "宏观择时" in subtypes or family == "宏观":
        return "宏观择时"
    if family == "股票对冲":
        return "股票多空"
    if family == "CTA" or _first_match(["商品CTA", "金融CTA", "趋势跟踪", "短周期CTA"], subtypes):
        return "CTA"
    if family == "套利" or _first_match(
        ["跨期套利", "跨品种套利", "期权套利", "可转债套利"],
        subtypes,
    ):
        return "套利"
    if family == "多策略" or "复合多策略" in subtypes:
        return "多策略"
    if family == "债券策略":
        return "信用债" if regime == "公募" else "债券增强"
    if family == "主动权益":
        return "主动股票" if regime == "公募" else "主观选股"
    if family == "FOF/MOM":
        return "公募FOF" if regime == "公募" else "私募FOF"
    if family == "期权/波动率" or "期权套利" in subtypes:
        return "期权策略"
    return None


def _derive_category_l3(subtypes: list[str], category_l2: str | None) -> str | None:
    direct_map = {
        "统计套利": "统计套利",
        "跨期套利": "跨期套利",
        "跨品种套利": "跨品种套利",
        "商品CTA": "商品CTA",
        "金融CTA": "金融CTA",
        "可转债套利": "可转债套利",
        "信用债": "信用下沉",
        "债券指数": "综合债指数",
        "宽基指数": "宽基权益指数",
        "复合多策略": "复合多策略",
        "股票多头": "股票主观多头",
        "量化选股": "股票量化多因子",
        "QDII": "海外债券",
    }
    for subtype in subtypes:
        if subtype in direct_map:
            return direct_map[subtype]
    fallback_map = {
        "主观选股": "股票主观多头",
        "量化选股": "股票量化多因子",
        "CTA": "商品CTA",
        "多策略": "复合多策略",
        "债券指数": "综合债指数",
        "宽基指数": "宽基权益指数",
        "债券增强": "信用下沉",
    }
    if category_l2 is not None:
        return fallback_map.get(category_l2)
    return None


def _derive_classification(values: dict[str, object]) -> dict[str, str]:
    regime = str(values.get("fund_regime") or "").strip() or None
    family = str(values.get("strategy_family") or "").strip() or None
    subtypes = _normalize_list(values.get("strategy_subtype"))
    category_l1 = _derive_category_l1(regime, family, subtypes)
    category_l2 = _derive_category_l2(regime, family, subtypes)
    category_l3 = _derive_category_l3(subtypes, category_l2)
    derived: dict[str, str] = {}
    if category_l1:
        derived["fund_category_l1"] = category_l1
    if category_l2:
        derived["fund_category_l2"] = category_l2
    if category_l3:
        derived["fund_category_l3"] = category_l3
    return derived


def _map_filter_values(field_key: str, values: object) -> list[object]:
    if not isinstance(values, list):
        return []
    if field_key == "attr.strategy_family":
        mapping = {
            "主动权益": "主动权益",
            "被动指数": "指数工具",
            "股票对冲": "股票",
            "CTA": "商品",
            "套利": "相对价值",
            "多策略": "多资产",
            "债券策略": "债券",
            "宏观": "宏观",
            "FOF/MOM": "FOF/MOM",
            "期权/波动率": "期权/波动率",
        }
        return [mapping[item] for item in values if str(item).strip() in mapping]
    if field_key == "attr.strategy_subtype":
        mapping = {
            "市场中性": "市场中性",
            "指数增强": "指数增强",
            "股票多头": "主动股票",
            "量化选股": "量化选股",
            "宽基指数": "宽基指数",
            "债券指数": "债券指数",
            "行业主题指数": "行业主题指数",
            "商品CTA": "CTA",
            "金融CTA": "CTA",
            "趋势跟踪": "CTA",
            "短周期CTA": "CTA",
            "跨期套利": "套利",
            "跨品种套利": "套利",
            "统计套利": "市场中性",
            "期权套利": "期权策略",
            "可转债套利": "套利",
            "可转债策略": "可转债",
            "宏观择时": "宏观择时",
            "信用债": "信用债",
            "复合多策略": "多策略",
            "QDII": "海外权益",
        }
        return [mapping[item] for item in values if str(item).strip() in mapping]
    return [item for item in values if str(item).strip()]


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
        rewritten.append(
            {
                **item,
                "field": FIELD_KEY_MAP.get(field, field),
            }
        )
    return rewritten


def _rewrite_filters(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    rewritten: dict[str, object] = {}
    for field_key, field_value in value.items():
        normalized_key = str(field_key).strip()
        if not normalized_key:
            continue
        target_key = FIELD_KEY_MAP.get(normalized_key, normalized_key)
        mapped_value = _map_filter_values(normalized_key, field_value)
        if isinstance(field_value, list) and not mapped_value:
            continue
        rewritten[target_key] = mapped_value if isinstance(field_value, list) else field_value
    return rewritten


def _rewrite_advanced_filter(node: object) -> object:
    if not isinstance(node, dict):
        return node
    if node.get("type") == "rule":
        field = str(node.get("field") or "").strip()
        if not field:
            return node
        return {**node, "field": FIELD_KEY_MAP.get(field, field)}
    if node.get("type") == "group":
        conditions = node.get("conditions")
        if isinstance(conditions, list):
            return {
                **node,
                "conditions": [_rewrite_advanced_filter(item) for item in conditions],
            }
    return node


def _load_framework_snapshot() -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    from watchlist_app.reference_data.watchlist_fields import (
        FIELD_CATEGORIES,
        INSTRUMENT_ATTRIBUTE_DEFINITIONS,
        build_attribute_field_definition,
    )

    attribute_definitions = [dict(item) for item in INSTRUMENT_ATTRIBUTE_DEFINITIONS]
    field_rows = [
        build_attribute_field_definition(item)
        for item in attribute_definitions
        if item.get("is_view_column", True)
    ]
    return [dict(item) for item in FIELD_CATEGORIES], attribute_definitions, field_rows


def _load_latest_values(bind, value_table) -> dict[str, dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    rows = bind.execute(
        sa.select(
            value_table.c.asset_id,
            value_table.c.attribute_key,
            value_table.c.value_json,
        ).order_by(
            value_table.c.asset_id,
            value_table.c.attribute_key,
            value_table.c.adopted_at.desc(),
            value_table.c.instrument_attribute_value_id.desc(),
        )
    ).mappings()
    for row in rows:
        asset_id = str(row["asset_id"])
        attribute_key = str(row["attribute_key"])
        if asset_id not in latest:
            latest[asset_id] = {}
        if attribute_key not in latest[asset_id]:
            latest[asset_id][attribute_key] = row["value_json"]
    return latest


def _upsert_field_categories(bind, field_category_table, categories: list[dict[str, object]]) -> None:
    existing = {
        str(row["category_code"])
        for row in bind.execute(
            sa.select(field_category_table.c.category_code)
        ).mappings()
    }
    for category in categories:
        payload = {
            "category_code": category["category_code"],
            "label": category["label"],
            "display_order": category["display_order"],
            "parent_category_code": category.get("parent_category_code"),
        }
        if payload["category_code"] in existing:
            bind.execute(
                sa.update(field_category_table)
                .where(field_category_table.c.category_code == payload["category_code"])
                .values(**payload)
            )
        else:
            bind.execute(sa.insert(field_category_table).values(**payload))


def upgrade() -> None:
    with op.batch_alter_table("instrument_attribute_definition") as batch:
        batch.add_column(sa.Column("domain_code", sa.String(), nullable=True))
        batch.add_column(sa.Column("group_code", sa.String(), nullable=True))
        batch.add_column(sa.Column("display_order", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("asset_scope_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("applicability_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("rubric_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("required_for_monitoring", sa.Boolean(), nullable=True))

    bind = op.get_bind()
    categories_snapshot, definition_snapshot, seeded_field_rows = _load_framework_snapshot()
    field_category_table = sa.table(
        "field_category",
        sa.column("category_code", sa.String()),
        sa.column("label", sa.String()),
        sa.column("parent_category_code", sa.String()),
        sa.column("display_order", sa.Integer()),
    )
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
        sa.column("asset_scope_json", sa.JSON()),
        sa.column("applicability_json", sa.JSON()),
        sa.column("rubric_json", sa.JSON()),
        sa.column("is_groupable", sa.Boolean()),
        sa.column("is_filterable", sa.Boolean()),
        sa.column("is_view_column", sa.Boolean()),
        sa.column("default_visible", sa.Boolean()),
        sa.column("required_for_monitoring", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    value_table = sa.table(
        "instrument_attribute_value",
        sa.column("instrument_attribute_value_id", sa.Integer()),
        sa.column("asset_id", sa.String()),
        sa.column("attribute_key", sa.String()),
        sa.column("value_json", sa.JSON()),
        sa.column("effective_from", sa.Date()),
        sa.column("adopted_at", sa.DateTime(timezone=True)),
        sa.column("source_record_id", sa.String()),
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
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("default_group_by", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
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
    asset_table = sa.table("asset_detail", sa.column("asset_id", sa.String()))
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

    _upsert_field_categories(bind, field_category_table, categories_snapshot)

    existing_definition_rows = list(
        bind.execute(
            sa.select(
                definition_table.c.attribute_key,
                definition_table.c.label,
                definition_table.c.description,
                definition_table.c.data_type,
                definition_table.c.options_json,
                definition_table.c.is_groupable,
                definition_table.c.is_filterable,
                definition_table.c.is_view_column,
                definition_table.c.default_visible,
                definition_table.c.created_at,
            )
        ).mappings()
    )
    existing_by_key = {
        str(row["attribute_key"]): row for row in existing_definition_rows
    }
    seeded_keys = {str(item["attribute_key"]) for item in definition_snapshot}

    field_seed_source: list[dict[str, object]] = []
    for definition in definition_snapshot:
        attribute_key = str(definition["attribute_key"])
        payload = {
            "label": definition["label"],
            "description": definition.get("description"),
            "data_type": definition["data_type"],
            "domain_code": definition["domain_code"],
            "group_code": definition["group_code"],
            "display_order": definition["display_order"],
            "options_json": definition.get("options", []),
            "asset_scope_json": definition.get("asset_scope_json", ["fund"]),
            "applicability_json": definition.get("applicability_json", {}),
            "rubric_json": definition.get("rubric_json", {}),
            "is_groupable": definition.get("is_groupable", True),
            "is_filterable": definition.get("is_filterable", True),
            "is_view_column": definition.get("is_view_column", True),
            "default_visible": definition.get("default_visible", False),
            "required_for_monitoring": definition.get("required_for_monitoring", False),
        }
        if attribute_key in existing_by_key:
            bind.execute(
                sa.update(definition_table)
                .where(definition_table.c.attribute_key == attribute_key)
                .values(**payload)
            )
        else:
            bind.execute(
                sa.insert(definition_table).values(
                    attribute_key=attribute_key,
                    created_at=REFERENCE_CREATED_AT,
                    **payload,
                )
            )
        field_seed_source.append(dict(definition))

    custom_index = 0
    for row in existing_definition_rows:
        attribute_key = str(row["attribute_key"])
        if attribute_key in seeded_keys or attribute_key in LEGACY_STRATEGY_KEYS:
            continue
        custom_payload = {
            "attribute_key": attribute_key,
            "label": row["label"],
            "description": row["description"],
            "data_type": row["data_type"],
            "options": row["options_json"] or [],
            "domain_code": "research",
            "group_code": "custom",
            "display_order": 9000 + custom_index,
            "asset_scope_json": ["fund"],
            "applicability_json": {},
            "rubric_json": {
                "summary": "Legacy custom attribute migrated into the research domain."
            },
            "is_groupable": bool(row["is_groupable"]),
            "is_filterable": bool(row["is_filterable"]),
            "is_view_column": bool(row["is_view_column"]),
            "default_visible": bool(row["default_visible"]),
            "required_for_monitoring": False,
        }
        bind.execute(
            sa.update(definition_table)
            .where(definition_table.c.attribute_key == attribute_key)
            .values(
                domain_code="research",
                group_code="custom",
                display_order=9000 + custom_index,
                asset_scope_json=["fund"],
                applicability_json={},
                rubric_json=custom_payload["rubric_json"],
                required_for_monitoring=False,
            )
        )
        field_seed_source.append(custom_payload)
        custom_index += 1

    bind.execute(
        sa.delete(field_registry_table).where(field_registry_table.c.field_key.like("attr.%"))
    )
    field_rows = []
    from watchlist_app.reference_data.watchlist_fields import build_attribute_field_definition

    for definition in field_seed_source:
        if definition.get("is_view_column", True):
            field_rows.append(build_attribute_field_definition(definition))
    if field_rows:
        op.bulk_insert(field_registry_table, field_rows)

    latest_values = _load_latest_values(bind, value_table)
    asset_ids = {
        str(row["asset_id"])
        for row in bind.execute(sa.select(asset_table.c.asset_id)).mappings()
    }.union(latest_values.keys())
    for asset_id in sorted(asset_ids):
        current_values = latest_values.get(asset_id, {})
        derived_values = _derive_classification(current_values)
        pending_values: dict[str, object] = {}

        for key in ("fund_category_l1", "fund_category_l2", "fund_category_l3"):
            candidate = derived_values.get(key)
            if candidate is not None and current_values.get(key) != candidate:
                pending_values[key] = candidate

        for attribute_key, value_json in pending_values.items():
            bind.execute(
                sa.insert(value_table).values(
                    asset_id=asset_id,
                    attribute_key=attribute_key,
                    value_json=value_json,
                    effective_from=None,
                    adopted_at=REFERENCE_CREATED_AT,
                    source_record_id=MIGRATION_SOURCE_RECORD_ID,
                )
            )

    bind.execute(
        sa.delete(value_table).where(value_table.c.attribute_key.in_(sorted(LEGACY_STRATEGY_KEYS)))
    )
    bind.execute(
        sa.delete(definition_table).where(
            definition_table.c.attribute_key.in_(sorted(LEGACY_STRATEGY_KEYS))
        )
    )

    rewritten_views = list(
        bind.execute(
            sa.select(
                view_table.c.watchlist_view_id,
                view_table.c.name,
                view_table.c.description,
                view_table.c.default_group_by,
                view_table.c.default_sort_json,
                view_table.c.default_filters_json,
                view_table.c.default_advanced_filter_json,
            )
        ).mappings()
    )
    for row in rewritten_views:
        view_id = str(row["watchlist_view_id"])
        next_name = row["name"]
        next_description = row["description"]
        next_group_by = FIELD_KEY_MAP.get(
            str(row["default_group_by"] or "").strip(),
            row["default_group_by"],
        )
        next_filters = _rewrite_filters(row["default_filters_json"])
        next_sort = _rewrite_sort_rules(row["default_sort_json"])
        next_advanced = _rewrite_advanced_filter(row["default_advanced_filter_json"])
        if view_id.endswith(f"::{PRIVATE_SCREENING_VIEW_ID}"):
            next_name = PRIVATE_SCREENING_VIEW_NAME
            next_description = PRIVATE_SCREENING_VIEW_DESCRIPTION
            next_group_by = "attr.fund_category_l1"
            next_filters = {"asset_type": ["fund"], "attr.fund_regime": ["私募"]}
            next_sort = []
            next_advanced = {}
        bind.execute(
            sa.update(view_table)
            .where(view_table.c.watchlist_view_id == view_id)
            .values(
                name=next_name,
                description=next_description,
                default_group_by=next_group_by,
                default_sort_json=next_sort,
                default_filters_json=next_filters,
                default_advanced_filter_json=next_advanced,
            )
        )

    view_ids = [
        str(row["watchlist_view_id"])
        for row in bind.execute(sa.select(view_table.c.watchlist_view_id)).mappings()
    ]
    for view_id in view_ids:
        existing_columns = list(
            bind.execute(
                sa.select(
                    column_table.c.field_key,
                    column_table.c.width,
                    column_table.c.is_visible,
                    column_table.c.pin_side,
                )
                .where(column_table.c.watchlist_view_id == view_id)
                .order_by(column_table.c.display_order)
            ).mappings()
        )
        bind.execute(
            sa.delete(column_table).where(column_table.c.watchlist_view_id == view_id)
        )
        if view_id.endswith(f"::{PRIVATE_SCREENING_VIEW_ID}"):
            op.bulk_insert(
                column_table,
                [
                    {
                        "watchlist_view_id": view_id,
                        "field_key": field_key,
                        "display_order": display_order,
                        "width": width,
                        "is_visible": True,
                        "pin_side": None,
                    }
                    for field_key, display_order, width in PRIVATE_SCREENING_VIEW_COLUMNS
                ],
            )
            continue
        rewritten_columns: list[dict[str, object]] = []
        seen_field_keys: set[str] = set()
        for index, column in enumerate(existing_columns, start=1):
            field_key = FIELD_KEY_MAP.get(str(column["field_key"]), str(column["field_key"]))
            if field_key in seen_field_keys:
                continue
            seen_field_keys.add(field_key)
            rewritten_columns.append(
                {
                    "watchlist_view_id": view_id,
                    "field_key": field_key,
                    "display_order": index,
                    "width": column["width"],
                    "is_visible": bool(column["is_visible"]),
                    "pin_side": column["pin_side"],
                }
            )
        if rewritten_columns:
            op.bulk_insert(column_table, rewritten_columns)

    latest_values = _load_latest_values(bind, value_table)
    watchlist_rows = list(
        bind.execute(
            sa.select(watchlist_row_table.c.watchlist_id, watchlist_row_table.c.asset_id)
        ).mappings()
    )
    for row in watchlist_rows:
        bind.execute(
            sa.update(watchlist_row_table)
            .where(
                sa.and_(
                    watchlist_row_table.c.watchlist_id == row["watchlist_id"],
                    watchlist_row_table.c.asset_id == row["asset_id"],
                )
            )
            .values(attributes_json=latest_values.get(str(row["asset_id"]), {}))
        )

    summary_rows = list(
        bind.execute(
            sa.select(summary_table.c.asset_id, summary_table.c.payload_json)
        ).mappings()
    )
    for row in summary_rows:
        payload_json = row["payload_json"] if isinstance(row["payload_json"], dict) else {}
        next_payload = dict(payload_json)
        next_payload["instrument_attributes"] = latest_values.get(str(row["asset_id"]), {})
        bind.execute(
            sa.update(summary_table)
            .where(summary_table.c.asset_id == row["asset_id"])
            .values(payload_json=next_payload)
        )

    bind.execute(
        sa.delete(field_category_table).where(
            field_category_table.c.category_code == "custom_attributes"
        )
    )

    with op.batch_alter_table("instrument_attribute_definition") as batch:
        batch.alter_column("domain_code", existing_type=sa.String(), nullable=False)
        batch.alter_column("group_code", existing_type=sa.String(), nullable=False)
        batch.alter_column("display_order", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("asset_scope_json", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("applicability_json", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("rubric_json", existing_type=sa.JSON(), nullable=False)
        batch.alter_column(
            "required_for_monitoring",
            existing_type=sa.Boolean(),
            nullable=False,
        )


def downgrade() -> None:
    # This migration intentionally performs a one-way cleanup of the legacy tag model.
    pass
