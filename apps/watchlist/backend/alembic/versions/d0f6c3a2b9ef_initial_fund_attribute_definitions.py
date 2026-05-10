"""initial fund attribute definitions

Revision ID: d0f6c3a2b9ef
Revises: b144f727c418
Create Date: 2026-04-16 00:00:00.000000

"""

from __future__ import annotations

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d0f6c3a2b9ef"
down_revision = "b144f727c418"
branch_labels = None
depends_on = None


REFERENCE_CREATED_AT = datetime(2026, 4, 16, tzinfo=UTC)

PRIVATE_FUND_ATTRIBUTE_DEFINITIONS = [
    {
        "attribute_key": "strategy_family",
        "label": "策略大类",
        "description": "基金的主策略身份，用于第一层快速筛选。",
        "data_type": "single_select",
        "options": ["股票对冲", "CTA", "套利", "多策略", "债券策略", "宏观", "期权/波动率", "其他"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "strategy_subtype",
        "label": "策略细分",
        "description": "更具体的实现类型，可多选。",
        "data_type": "multi_select",
        "options": [
            "市场中性",
            "指数增强",
            "股票多头",
            "商品CTA",
            "金融CTA",
            "趋势跟踪",
            "短周期CTA",
            "跨期套利",
            "跨品种套利",
            "统计套利",
            "期权套利",
            "可转债套利",
            "宏观择时",
            "复合多策略",
        ],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "implementation_style",
        "label": "实现方式",
        "description": "主观、量化或混合实现。",
        "data_type": "single_select",
        "options": ["主观", "量化", "主观+量化"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "trading_universe",
        "label": "交易市场/品种",
        "description": "主要交易的底层市场与工具，可多选。",
        "data_type": "multi_select",
        "options": ["A股", "港股", "美股", "股指期货", "国债期货", "商品期货", "期权", "可转债", "债券"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "alpha_source",
        "label": "主要收益来源",
        "description": "基金最核心的收益引擎，可多选。",
        "data_type": "multi_select",
        "options": [
            "选股Alpha",
            "趋势跟踪",
            "期限结构",
            "价差收敛",
            "波动率交易",
            "事件驱动",
            "贝塔增强",
            "高频/微观结构",
            "宏观择时",
            "Carry/票息",
        ],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "volatility_bucket",
        "label": "波动分层",
        "description": "产品的典型波动水平。",
        "data_type": "single_select",
        "options": ["低波", "中波", "高波"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "drawdown_control",
        "label": "回撤控制",
        "description": "产品在压力阶段的回撤管理能力。",
        "data_type": "single_select",
        "options": ["强", "中", "弱"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "equity_correlation_bucket",
        "label": "权益相关性",
        "description": "与权益市场的典型相关性水平。",
        "data_type": "single_select",
        "options": ["低相关", "中相关", "高相关", "负相关/危机对冲"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "preferred_regime",
        "label": "适配环境",
        "description": "更容易发挥优势的市场环境，可多选。",
        "data_type": "multi_select",
        "options": ["低波震荡", "高波趋势", "流动性宽松", "流动性收紧", "单边上涨", "单边下跌", "商品趋势", "股指震荡"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "weak_regime",
        "label": "脆弱环境",
        "description": "更容易失效或放大风险的环境，可多选。",
        "data_type": "multi_select",
        "options": ["高波反转", "流动性冲击", "无趋势震荡", "权益急跌后V形", "商品快速切换", "基差异常/挤仓"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "style_stability",
        "label": "风格稳定性",
        "description": "策略暴露和执行风格是否稳定。",
        "data_type": "single_select",
        "options": ["高", "中", "低"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "transparency_quality",
        "label": "研究透明度",
        "description": "产品对外信息披露与可研究性质量。",
        "data_type": "single_select",
        "options": ["高", "中", "低"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
]

EXISTING_INTERNAL_ATTRIBUTE_KEYS = [
    "coverage_status",
    "focus_bucket",
    "is_invested",
]


def _attribute_field_definition(definition: dict[str, object]) -> dict[str, object]:
    data_type = str(definition["data_type"])
    formatter_code = "text"
    filter_mode = "multi_select"
    group_mode = "discrete"
    sort_mode = "alpha"
    if data_type == "boolean":
        formatter_code = "boolean"
    elif data_type == "number":
        formatter_code = "decimal"
        filter_mode = "range"
        group_mode = "bucket"
        sort_mode = "numeric"
    elif data_type == "date":
        formatter_code = "date"
        filter_mode = "date_range"
        group_mode = "bucket"
        sort_mode = "date"
    elif data_type == "multi_select":
        formatter_code = "tags"
    return {
        "field_key": f"attr.{definition['attribute_key']}",
        "label": str(definition["label"]),
        "description": str(definition["description"]) if definition.get("description") is not None else None,
        "category_code": "custom_attributes",
        "data_type": data_type,
        "formatter_code": formatter_code,
        "sort_mode": sort_mode,
        "filter_mode": filter_mode,
        "group_mode": group_mode if definition.get("is_groupable", True) else "none",
        "instrument_scope_json": ["fund"],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["instrument_attribute_value"]},
        "source_domain": "custom_attribute",
        "source_metric_code": f"instrument_attribute_value.{definition['attribute_key']}",
        "default_width": 160,
        "default_visible": bool(definition.get("default_visible", False)),
    }


def upgrade() -> None:
    instrument_attribute_definition_table = sa.table(
        "instrument_attribute_definition",
        sa.column("attribute_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.String()),
        sa.column("data_type", sa.String()),
        sa.column("options_json", sa.JSON()),
        sa.column("is_groupable", sa.Boolean()),
        sa.column("is_filterable", sa.Boolean()),
        sa.column("is_view_column", sa.Boolean()),
        sa.column("default_visible", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
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

    op.bulk_insert(
        instrument_attribute_definition_table,
        [
            {
                "attribute_key": str(item["attribute_key"]),
                "label": str(item["label"]),
                "description": str(item["description"]) if item.get("description") is not None else None,
                "data_type": str(item["data_type"]),
                "options_json": list(item.get("options", [])),
                "is_groupable": bool(item.get("is_groupable", True)),
                "is_filterable": bool(item.get("is_filterable", True)),
                "is_view_column": bool(item.get("is_view_column", True)),
                "default_visible": bool(item.get("default_visible", False)),
                "created_at": REFERENCE_CREATED_AT,
            }
            for item in PRIVATE_FUND_ATTRIBUTE_DEFINITIONS
        ],
    )

    op.bulk_insert(
        field_registry_table,
        [_attribute_field_definition(item) for item in PRIVATE_FUND_ATTRIBUTE_DEFINITIONS],
    )

    all_custom_attribute_keys = EXISTING_INTERNAL_ATTRIBUTE_KEYS + [
        str(item["attribute_key"]) for item in PRIVATE_FUND_ATTRIBUTE_DEFINITIONS
    ]
    bind = op.get_bind()
    bind.execute(
        sa.update(field_registry_table)
        .where(
            field_registry_table.c.field_key.in_([f"attr.{key}" for key in all_custom_attribute_keys])
        )
        .values(product_scope_json=[])
    )


def downgrade() -> None:
    instrument_attribute_definition_table = sa.table(
        "instrument_attribute_definition",
        sa.column("attribute_key", sa.String()),
    )
    field_registry_table = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("product_scope_json", sa.JSON()),
    )
    attribute_keys = [str(item["attribute_key"]) for item in PRIVATE_FUND_ATTRIBUTE_DEFINITIONS]
    field_keys = [f"attr.{key}" for key in attribute_keys]
    bind = op.get_bind()

    bind.execute(
        sa.delete(field_registry_table).where(field_registry_table.c.field_key.in_(field_keys))
    )
    bind.execute(
        sa.update(field_registry_table)
        .where(
            field_registry_table.c.field_key.in_(
                [f"attr.{key}" for key in EXISTING_INTERNAL_ATTRIBUTE_KEYS]
            )
        )
        .values(product_scope_json=["mutual_fund", "cef", "etf"])
    )
    bind.execute(
        sa.delete(instrument_attribute_definition_table).where(
            instrument_attribute_definition_table.c.attribute_key.in_(attribute_keys)
        )
    )
