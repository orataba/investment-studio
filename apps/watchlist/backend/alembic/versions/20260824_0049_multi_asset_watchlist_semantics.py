"""Make Watchlist fields and saved views asset-appropriate.

Revision ID: 20260824_0049
Revises: 20260823_0048
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "20260824_0049"
down_revision = "20260823_0048"
branch_labels = None
depends_on = None


FUND_TYPE_SCOPE = ["public_fund", "private_fund"]
FUND_TYPES = set(FUND_TYPE_SCOPE)
ACTIVE_RESEARCH_STATUS = {"coverage_status": ["Proposed", "Invested", "Paused"]}
REMOVED_ATTRIBUTES = {
    "volatility_bucket",
    "drawdown_control",
    "style_stability",
}
REMOVED_FIELDS = {f"attr.{attribute_key}" for attribute_key in REMOVED_ATTRIBUTES}
FUND_ONLY_ATTRIBUTES = {
    "fund_vehicle",
    "investment_edge_quality",
    "implementation_style",
    "process_repeatability",
    "decision_discipline",
    "trading_universe",
    "alpha_source",
    "style_profile",
    "manager_assessment",
    "team_stability_assessment",
    "portfolio_construction",
    "style_drift_risk",
    "risk_management_quality",
    "capacity_bucket",
    "liquidity_terms_fit",
    "fee_value_assessment",
    "alignment_quality",
    "historical_delivery",
    "equity_correlation_bucket",
    "preferred_regime",
    "weak_regime",
    "transparency_quality",
}
NON_GROUPABLE_ATTRIBUTES = {
    "research_evidence_level",
    "portfolio_role",
    *(FUND_ONLY_ATTRIBUTES - {"fund_vehicle"}),
    "equity_business_quality",
    "equity_valuation_view",
    "equity_financial_quality",
    "equity_governance_quality",
    "etf_index_fit",
    "etf_tracking_quality",
    "etf_liquidity_quality",
    "etf_structure_quality",
    "index_methodology_quality",
    "index_representation_quality",
    "index_investability_quality",
    "index_governance_quality",
}
ACTIVE_REQUIRED_ATTRIBUTES = {
    "thesis_status",
    "research_evidence_level",
    "investment_edge_quality",
    "process_repeatability",
    "team_stability_assessment",
    "risk_management_quality",
    "liquidity_terms_fit",
    "fee_value_assessment",
    "alignment_quality",
    "historical_delivery",
    "transparency_quality",
    "equity_business_quality",
    "equity_valuation_view",
    "equity_financial_quality",
    "equity_governance_quality",
    "etf_index_fit",
    "etf_tracking_quality",
    "etf_liquidity_quality",
    "etf_structure_quality",
    "index_methodology_quality",
    "index_representation_quality",
    "index_investability_quality",
    "index_governance_quality",
}
SPECIAL_GROUP_FIELDS = {
    "none",
    "taxonomy",
    "data_freshness_status",
}


def _json_value(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _rewrite_view_value(
    value: object,
    *,
    unavailable_fields: set[str],
) -> object | None:
    value = _json_value(value)
    if isinstance(value, str):
        if value in unavailable_fields:
            return None
        return "metric_as_of_date" if value == "last_nav_date" else value
    if isinstance(value, list):
        rewritten = [
            _rewrite_view_value(item, unavailable_fields=unavailable_fields)
            for item in value
        ]
        return [item for item in rewritten if item is not None]
    if not isinstance(value, dict):
        return value
    field_key = str(value.get("field") or "")
    if field_key in unavailable_fields:
        return None
    rewritten: dict[str, object] = {}
    for raw_key, item in value.items():
        key = str(raw_key)
        if key in unavailable_fields:
            continue
        next_key = "metric_as_of_date" if key == "last_nav_date" else key
        cleaned = _rewrite_view_value(item, unavailable_fields=unavailable_fields)
        if cleaned is not None:
            rewritten[next_key] = cleaned
    return rewritten


def _clean_attributes(
    value: object,
    *,
    instrument_type: str,
) -> dict[str, object]:
    parsed = _json_value(value)
    if not isinstance(parsed, dict):
        return {}
    cleaned = dict(parsed)
    for attribute_key in REMOVED_ATTRIBUTES:
        cleaned.pop(attribute_key, None)
        cleaned.pop(f"attr.{attribute_key}", None)
    if instrument_type not in FUND_TYPES:
        for attribute_key in FUND_ONLY_ATTRIBUTES:
            cleaned.pop(attribute_key, None)
            cleaned.pop(f"attr.{attribute_key}", None)
    return cleaned


def _rewrite_summary_payload(
    value: object,
    *,
    instrument_type: str,
) -> dict[str, object]:
    parsed = _json_value(value)
    if not isinstance(parsed, dict):
        return {}
    rewritten = dict(parsed)
    if "fund_name" in rewritten and "instrument_name" not in rewritten:
        rewritten["instrument_name"] = rewritten["fund_name"]
    rewritten.pop("fund_name", None)
    if "nav_snapshot" in rewritten and "series_snapshot" not in rewritten:
        rewritten["series_snapshot"] = rewritten["nav_snapshot"]
    rewritten.pop("nav_snapshot", None)
    rewritten["instrument_attributes"] = _clean_attributes(
        rewritten.get("instrument_attributes"),
        instrument_type=instrument_type,
    )
    return rewritten


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL lock_timeout = '30s'"))

    category = sa.table(
        "field_category",
        sa.column("category_code", sa.String()),
        sa.column("label", sa.String()),
        sa.column("parent_category_code", sa.String()),
        sa.column("display_order", sa.Integer()),
    )
    field = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.String()),
        sa.column("category_code", sa.String()),
        sa.column("group_mode", sa.String()),
        sa.column("instrument_scope_json", sa.JSON()),
        sa.column("product_scope_json", sa.JSON()),
        sa.column("default_visible", sa.Boolean()),
    )
    definition = sa.table(
        "instrument_attribute_definition",
        sa.column("attribute_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.String()),
        sa.column("options_json", sa.JSON()),
        sa.column("instrument_scope_json", sa.JSON()),
        sa.column("applicability_json", sa.JSON()),
        sa.column("rubric_json", sa.JSON()),
        sa.column("is_groupable", sa.Boolean()),
        sa.column("required_for_monitoring", sa.Boolean()),
    )
    attribute_value = sa.table(
        "instrument_attribute_value",
        sa.column("instrument_attribute_value_id", sa.Integer()),
        sa.column("instrument_id", sa.String()),
        sa.column("attribute_key", sa.String()),
    )
    detail = sa.table(
        "instrument_detail",
        sa.column("instrument_id", sa.String()),
        sa.column("instrument_type", sa.String()),
    )
    watchlist_item = sa.table(
        "watchlist_item",
        sa.column("watchlist_id", sa.String()),
        sa.column("instrument_id", sa.String()),
    )
    view = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("watchlist_id", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
        sa.column("default_group_by", sa.String()),
    )
    view_column = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("field_key", sa.String()),
    )
    watchlist_row = sa.table(
        "watchlist_row_read_model",
        sa.column("watchlist_id", sa.String()),
        sa.column("instrument_id", sa.String()),
        sa.column("instrument_type", sa.String()),
        sa.column("attributes_json", sa.JSON()),
    )
    summary = sa.table(
        "instrument_summary_read_model",
        sa.column("instrument_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )

    bind.execute(
        sa.update(category)
        .where(category.c.category_code == "research_framework")
        .values(label="Investment Research")
    )
    old_taxonomy_category = bind.execute(
        sa.select(category).where(category.c.category_code == "product_taxonomy")
    ).mappings().first()
    new_taxonomy_category = bind.execute(
        sa.select(category.c.category_code).where(
            category.c.category_code == "instrument_taxonomy"
        )
    ).first()
    if new_taxonomy_category is None:
        bind.execute(
            sa.insert(category).values(
                category_code="instrument_taxonomy",
                label="Instrument Taxonomy",
                parent_category_code=(
                    old_taxonomy_category["parent_category_code"]
                    if old_taxonomy_category is not None
                    else None
                ),
                display_order=(
                    old_taxonomy_category["display_order"]
                    if old_taxonomy_category is not None
                    else 3
                ),
            )
        )
    else:
        bind.execute(
            sa.update(category)
            .where(category.c.category_code == "instrument_taxonomy")
            .values(label="Instrument Taxonomy")
        )
    bind.execute(
        sa.update(field)
        .where(field.c.category_code == "product_taxonomy")
        .values(category_code="instrument_taxonomy")
    )
    bind.execute(
        sa.update(category)
        .where(category.c.parent_category_code == "product_taxonomy")
        .values(parent_category_code="instrument_taxonomy")
    )
    bind.execute(
        sa.delete(category).where(category.c.category_code == "product_taxonomy")
    )

    bind.execute(
        sa.update(view_column)
        .where(view_column.c.field_key == "last_nav_date")
        .values(field_key="metric_as_of_date")
    )
    old_metric_field = bind.execute(
        sa.select(field.c.field_key).where(field.c.field_key == "last_nav_date")
    ).first()
    new_metric_field = bind.execute(
        sa.select(field.c.field_key).where(field.c.field_key == "metric_as_of_date")
    ).first()
    if old_metric_field is not None and new_metric_field is None:
        bind.execute(
            sa.update(field)
            .where(field.c.field_key == "last_nav_date")
            .values(field_key="metric_as_of_date")
        )
    elif old_metric_field is not None:
        bind.execute(sa.delete(field).where(field.c.field_key == "last_nav_date"))
    bind.execute(
        sa.update(field)
        .where(field.c.field_key == "metric_as_of_date")
        .values(
            instrument_scope_json=[
                "public_fund",
                "private_fund",
                "etf",
                "equity",
                "index",
            ],
            product_scope_json=[],
        )
    )

    bind.execute(
        sa.delete(view_column).where(view_column.c.field_key.in_(REMOVED_FIELDS))
    )
    bind.execute(
        sa.delete(attribute_value).where(
            attribute_value.c.attribute_key.in_(REMOVED_ATTRIBUTES)
        )
    )
    bind.execute(
        sa.delete(field).where(field.c.field_key.in_(REMOVED_FIELDS))
    )
    bind.execute(
        sa.delete(definition).where(
            definition.c.attribute_key.in_(REMOVED_ATTRIBUTES)
        )
    )

    invalid_fund_attribute_value_ids = sa.select(
        attribute_value.c.instrument_attribute_value_id
    ).select_from(
        attribute_value.join(
            detail,
            detail.c.instrument_id == attribute_value.c.instrument_id,
        )
    ).where(
        attribute_value.c.attribute_key.in_(FUND_ONLY_ATTRIBUTES),
        detail.c.instrument_type.not_in(FUND_TYPES),
    )
    bind.execute(
        sa.delete(attribute_value).where(
            attribute_value.c.instrument_attribute_value_id.in_(
                invalid_fund_attribute_value_ids
            )
        )
    )

    bind.execute(
        sa.update(definition)
        .where(definition.c.attribute_key.in_(NON_GROUPABLE_ATTRIBUTES))
        .values(is_groupable=False)
    )
    bind.execute(
        sa.update(field)
        .where(
            field.c.field_key.in_(
                {f"attr.{attribute_key}" for attribute_key in NON_GROUPABLE_ATTRIBUTES}
            )
        )
        .values(group_mode="none")
    )
    bind.execute(
        sa.update(definition)
        .where(definition.c.attribute_key.in_(FUND_ONLY_ATTRIBUTES))
        .values(instrument_scope_json=FUND_TYPE_SCOPE)
    )
    bind.execute(
        sa.update(field)
        .where(
            field.c.field_key.in_(
                {f"attr.{attribute_key}" for attribute_key in FUND_ONLY_ATTRIBUTES}
            )
        )
        .values(instrument_scope_json=FUND_TYPE_SCOPE, product_scope_json=[])
    )
    bind.execute(
        sa.update(definition)
        .where(definition.c.attribute_key.in_(ACTIVE_REQUIRED_ATTRIBUTES))
        .values(
            applicability_json=ACTIVE_RESEARCH_STATUS,
            required_for_monitoring=True,
        )
    )
    bind.execute(
        sa.update(definition)
        .where(definition.c.attribute_key == "coverage_status")
        .values(label="Investment Status", required_for_monitoring=True)
    )
    bind.execute(
        sa.update(field)
        .where(field.c.field_key == "attr.coverage_status")
        .values(label="Investment Status")
    )
    bind.execute(
        sa.update(definition)
        .where(definition.c.attribute_key == "fund_vehicle")
        .values(
            description="场外开放式、LOF、私募契约型等基金载体形态，属于基本情况而非策略分类。",
            options_json=[
                "LOF/场内开放式",
                "场外开放式",
                "封闭式",
                "集合资管计划",
                "契约型私募基金",
                "公司型私募基金",
                "其他",
            ],
        )
    )
    bind.execute(
        sa.update(definition)
        .where(definition.c.attribute_key == "primary_geographic_exposure")
        .values(
            description="底层资产的主要地域，而不是产品注册地、交易所或上市地点；用于跨市场筛选和同类比较。"
        )
    )
    bind.execute(
        sa.update(field)
        .where(field.c.field_key == "attr.primary_geographic_exposure")
        .values(
            description="底层资产的主要地域，而不是产品注册地、交易所或上市地点；用于跨市场筛选和同类比较。"
        )
    )
    bind.execute(
        sa.update(definition)
        .where(definition.c.attribute_key == "focus_bucket")
        .values(
            rubric_json={
                "summary": "用于排研究资源优先级，不代表标的好坏或是否持仓。",
                "standard": "Tier 1 代表近期最需要研究/复核；Tier 3 代表低频观察。",
            }
        )
    )
    bind.execute(
        sa.update(definition)
        .where(definition.c.attribute_key == "research_evidence_level")
        .values(
            rubric_json={
                "summary": "衡量标签、rating 和 Research View 是否有足够材料支撑。",
                "standard": "完整证据需要覆盖与资产类型相匹配的一手材料、量化或估值验证、关键反证与失效条件；轶事证据不能支撑高 conviction。",
                "evidence": [
                    "primary_sources",
                    "research_notes",
                    "quantitative_or_valuation_work",
                    "invalidation_evidence",
                ],
            }
        )
    )
    bind.execute(
        sa.update(definition)
        .where(definition.c.attribute_key == "portfolio_role")
        .values(
            rubric_json={
                "summary": "把单一标的研究结论翻译成组合用途。",
                "standard": "组合角色必须与相关性、风险贡献、容量、交易或申赎约束及当前投资结论一致；仅观察不能与已建议配置的高 conviction 结论并存。",
                "evidence": [
                    "correlation",
                    "risk_contribution",
                    "portfolio_gap",
                    "research_notes",
                ],
            }
        )
    )
    bind.execute(
        sa.update(field)
        .where(field.c.field_key == "attr.fund_vehicle")
        .values(
            description="场外开放式、LOF、私募契约型等基金载体形态，属于基本情况而非策略分类。"
        )
    )

    bind.execute(
        sa.update(field)
        .where(field.c.field_key == "management_firm_name")
        .values(
            label="Manager / Issuer",
            description="Current fund manager, advisor, or ETF issuer.",
        )
    )
    bind.execute(
        sa.update(field)
        .where(field.c.field_key.in_(("attr.peer_group", "avg_credit_rating")))
        .values(group_mode="none")
    )
    bind.execute(
        sa.update(field)
        .where(field.c.field_key.in_(("duration", "avg_credit_rating")))
        .values(default_visible=False)
    )

    for row in bind.execute(
        sa.select(
            watchlist_row.c.watchlist_id,
            watchlist_row.c.instrument_id,
            watchlist_row.c.instrument_type,
            watchlist_row.c.attributes_json,
        )
    ).mappings():
        cleaned = _clean_attributes(
            row["attributes_json"],
            instrument_type=str(row["instrument_type"]),
        )
        if cleaned != _json_value(row["attributes_json"]):
            bind.execute(
                sa.update(watchlist_row)
                .where(
                    watchlist_row.c.watchlist_id == row["watchlist_id"],
                    watchlist_row.c.instrument_id == row["instrument_id"],
                )
                .values(attributes_json=cleaned)
            )

    instrument_types = dict(
        bind.execute(
            sa.select(detail.c.instrument_id, detail.c.instrument_type)
        ).all()
    )
    for row in bind.execute(
        sa.select(summary.c.instrument_id, summary.c.payload_json)
    ).mappings():
        rewritten = _rewrite_summary_payload(
            row["payload_json"],
            instrument_type=str(instrument_types.get(row["instrument_id"]) or ""),
        )
        if rewritten != _json_value(row["payload_json"]):
            bind.execute(
                sa.update(summary)
                .where(summary.c.instrument_id == row["instrument_id"])
                .values(payload_json=rewritten)
            )

    field_scopes = {
        str(row["field_key"]): {
            str(value).strip().lower()
            for value in (_json_value(row["instrument_scope_json"]) or [])
            if str(value).strip()
        }
        for row in bind.execute(
            sa.select(field.c.field_key, field.c.instrument_scope_json)
        ).mappings()
    }
    discrete_fields = set(
        bind.execute(
            sa.select(field.c.field_key).where(field.c.group_mode == "discrete")
        ).scalars()
    )
    for row in bind.execute(sa.select(view)).mappings():
        view_instrument_types = {
            str(value).strip().lower()
            for value in bind.execute(
                sa.select(detail.c.instrument_type)
                .select_from(
                    watchlist_item.join(
                        detail,
                        detail.c.instrument_id == watchlist_item.c.instrument_id,
                    )
                )
                .where(watchlist_item.c.watchlist_id == row["watchlist_id"])
                .distinct()
            ).scalars()
            if str(value).strip()
        }
        unavailable_fields = set(REMOVED_FIELDS)
        unavailable_fields.update(
            field_key
            for field_key, scope in field_scopes.items()
            if scope and not view_instrument_types.issubset(scope)
        )
        if unavailable_fields:
            bind.execute(
                sa.delete(view_column).where(
                    view_column.c.watchlist_view_id == row["watchlist_view_id"],
                    view_column.c.field_key.in_(unavailable_fields),
                )
            )

        updates: dict[str, object] = {}
        for column_name in (
            "default_sort_json",
            "default_filters_json",
            "default_advanced_filter_json",
        ):
            current = row[column_name]
            rewritten = _rewrite_view_value(
                current,
                unavailable_fields=unavailable_fields,
            )
            if rewritten != _json_value(current):
                updates[column_name] = rewritten or (
                    [] if column_name == "default_sort_json" else {}
                )

        group_by = str(row["default_group_by"] or "none")
        group_is_invalid = (
            group_by in unavailable_fields
            or (group_by == "instrument_type" and len(view_instrument_types) <= 1)
            or (
                group_by not in SPECIAL_GROUP_FIELDS | {"instrument_type"}
                and group_by not in discrete_fields
            )
        )
        if group_is_invalid:
            updates["default_group_by"] = "none"
        if updates:
            bind.execute(
                sa.update(view)
                .where(view.c.watchlist_view_id == row["watchlist_view_id"])
                .values(**updates)
            )


def downgrade() -> None:
    raise RuntimeError(
        "20260824_0049 removes invalid multi-asset metadata and cannot be downgraded safely."
    )
