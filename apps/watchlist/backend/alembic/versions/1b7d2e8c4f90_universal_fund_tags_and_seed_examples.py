"""extend fund tags to public/private/ETF and seed examples

Revision ID: 1b7d2e8c4f90
Revises: 9f3e2c4d1a7b
Create Date: 2026-04-16 03:40:00.000000

"""

from __future__ import annotations

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1b7d2e8c4f90"
down_revision = "9f3e2c4d1a7b"
branch_labels = None
depends_on = None


REFERENCE_CREATED_AT = datetime(2026, 4, 16, 3, 40, tzinfo=UTC)
ATTRIBUTE_SOURCE_RECORD_ID = "seed/fund-tags-v2"
PRIVATE_VIEW_ID = "private-fund-screening"
PRIVATE_VIEW_COLUMNS = [
    ("asset_name", 1, 320),
    ("attr.fund_regime", 2, 120),
    ("attr.fund_vehicle", 3, 160),
    ("attr.strategy_family", 4, 140),
    ("attr.strategy_subtype", 5, 220),
    ("attr.implementation_style", 6, 140),
    ("attr.volatility_bucket", 7, 120),
    ("attr.drawdown_control", 8, 120),
    ("attr.equity_correlation_bucket", 9, 140),
    ("attr.preferred_regime", 10, 220),
    ("attr.weak_regime", 11, 220),
    ("attr.style_stability", 12, 120),
    ("attr.transparency_quality", 13, 120),
    ("data_freshness_status", 14, 140),
]
PRIVATE_VIEW_FILTERS = {"asset_type": ["fund"], "attr.fund_regime": ["私募"]}


FUND_ATTRIBUTE_DEFINITIONS = [
    {
        "attribute_key": "fund_regime",
        "label": "基金属性",
        "description": "公募还是私募，用于先分产品身份。",
        "data_type": "single_select",
        "options": ["公募", "私募"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "fund_vehicle",
        "label": "产品形态",
        "description": "ETF、场外开放式或资管计划等载体形态。",
        "data_type": "single_select",
        "options": ["ETF", "场外开放式", "LOF/场内开放式", "封闭式", "集合资管计划", "其他"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "strategy_family",
        "label": "策略大类",
        "description": "基金的主策略身份，用于第一层快速筛选。",
        "data_type": "single_select",
        "options": ["主动权益", "被动指数", "股票对冲", "CTA", "套利", "多策略", "债券策略", "宏观", "FOF/MOM", "期权/波动率", "其他"],
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
            "量化选股",
            "宽基指数",
            "行业主题指数",
            "债券指数",
            "QDII",
            "商品CTA",
            "金融CTA",
            "趋势跟踪",
            "短周期CTA",
            "跨期套利",
            "跨品种套利",
            "统计套利",
            "期权套利",
            "可转债套利",
            "可转债策略",
            "宏观择时",
            "信用债",
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
        "options": ["主观", "量化", "主观+量化", "被动规则"],
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
            "指数复制",
            "主题/风格暴露",
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
        "asset_scope_json": ["fund"],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["instrument_attribute_value"]},
        "source_domain": "custom_attribute",
        "source_metric_code": f"instrument_attribute_value.{definition['attribute_key']}",
        "default_width": 160,
        "default_visible": bool(definition.get("default_visible", False)),
    }


def _seed(
    *,
    regime: str,
    vehicle: str,
    family: str,
    subtype: list[str],
    implementation: str,
    universe: list[str],
    alpha: list[str],
    volatility: str,
    drawdown: str,
    correlation: str,
    preferred: list[str],
    weak: list[str],
    stability: str,
    transparency: str,
) -> dict[str, object]:
    return {
        "fund_regime": regime,
        "fund_vehicle": vehicle,
        "strategy_family": family,
        "strategy_subtype": subtype,
        "implementation_style": implementation,
        "trading_universe": universe,
        "alpha_source": alpha,
        "volatility_bucket": volatility,
        "drawdown_control": drawdown,
        "equity_correlation_bucket": correlation,
        "preferred_regime": preferred,
        "weak_regime": weak,
        "style_stability": stability,
        "transparency_quality": transparency,
    }


EXAMPLE_ATTRIBUTE_VALUES = {
    "AA": _seed(
        regime="公募",
        vehicle="场外开放式",
        family="其他",
        subtype=["股票多头"],
        implementation="主观",
        universe=["A股"],
        alpha=["选股Alpha"],
        volatility="中波",
        drawdown="中",
        correlation="高相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="低",
        transparency="低",
    ),
    "AAA": _seed(
        regime="公募",
        vehicle="场外开放式",
        family="其他",
        subtype=["股票多头"],
        implementation="主观",
        universe=["A股"],
        alpha=["选股Alpha"],
        volatility="中波",
        drawdown="中",
        correlation="高相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="低",
        transparency="低",
    ),
    "TEST": _seed(
        regime="公募",
        vehicle="场外开放式",
        family="其他",
        subtype=["股票多头"],
        implementation="主观",
        universe=["A股"],
        alpha=["选股Alpha"],
        volatility="中波",
        drawdown="中",
        correlation="高相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="低",
        transparency="低",
    ),
    "fund-us-agg": _seed(
        regime="公募",
        vehicle="ETF",
        family="被动指数",
        subtype=["债券指数"],
        implementation="被动规则",
        universe=["债券"],
        alpha=["指数复制"],
        volatility="低波",
        drawdown="强",
        correlation="低相关",
        preferred=["低波震荡", "流动性宽松"],
        weak=["流动性冲击"],
        stability="高",
        transparency="高",
    ),
    "cloz": _seed(
        regime="公募",
        vehicle="ETF",
        family="债券策略",
        subtype=["信用债"],
        implementation="被动规则",
        universe=["债券"],
        alpha=["Carry/票息"],
        volatility="中波",
        drawdown="中",
        correlation="低相关",
        preferred=["低波震荡", "流动性宽松"],
        weak=["流动性冲击"],
        stability="高",
        transparency="高",
    ),
    "fax": _seed(
        regime="公募",
        vehicle="封闭式",
        family="债券策略",
        subtype=["信用债", "QDII"],
        implementation="主观",
        universe=["债券"],
        alpha=["Carry/票息"],
        volatility="低波",
        drawdown="强",
        correlation="低相关",
        preferred=["低波震荡", "流动性宽松"],
        weak=["流动性冲击"],
        stability="高",
        transparency="高",
    ),
    "new-asia-income": _seed(
        regime="公募",
        vehicle="场外开放式",
        family="债券策略",
        subtype=["信用债", "QDII"],
        implementation="主观",
        universe=["债券"],
        alpha=["Carry/票息"],
        volatility="低波",
        drawdown="强",
        correlation="低相关",
        preferred=["低波震荡", "流动性宽松"],
        weak=["流动性冲击"],
        stability="中",
        transparency="高",
    ),
    "bosera-cb-etf": _seed(
        regime="公募",
        vehicle="ETF",
        family="被动指数",
        subtype=["债券指数", "可转债策略"],
        implementation="被动规则",
        universe=["可转债"],
        alpha=["指数复制"],
        volatility="中波",
        drawdown="中",
        correlation="中相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="高",
        transparency="高",
    ),
    "gf-hkinnodrug": _seed(
        regime="公募",
        vehicle="ETF",
        family="被动指数",
        subtype=["行业主题指数", "QDII"],
        implementation="被动规则",
        universe=["港股"],
        alpha=["指数复制", "主题/风格暴露"],
        volatility="高波",
        drawdown="弱",
        correlation="高相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="高",
        transparency="高",
    ),
    "efund-cnint50": _seed(
        regime="公募",
        vehicle="ETF",
        family="被动指数",
        subtype=["行业主题指数", "QDII"],
        implementation="被动规则",
        universe=["港股", "美股"],
        alpha=["指数复制", "主题/风格暴露"],
        volatility="高波",
        drawdown="弱",
        correlation="高相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="高",
        transparency="高",
    ),
    "hstech": _seed(
        regime="公募",
        vehicle="ETF",
        family="被动指数",
        subtype=["行业主题指数"],
        implementation="被动规则",
        universe=["港股"],
        alpha=["指数复制", "主题/风格暴露"],
        volatility="高波",
        drawdown="弱",
        correlation="高相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="高",
        transparency="高",
    ),
    "nasdaq100": _seed(
        regime="公募",
        vehicle="ETF",
        family="被动指数",
        subtype=["宽基指数", "QDII"],
        implementation="被动规则",
        universe=["美股"],
        alpha=["指数复制"],
        volatility="高波",
        drawdown="中",
        correlation="高相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="高",
        transparency="高",
    ),
    "dacheng-gx-c": _seed(
        regime="公募",
        vehicle="场外开放式",
        family="主动权益",
        subtype=["股票多头"],
        implementation="主观",
        universe=["A股"],
        alpha=["选股Alpha"],
        volatility="中波",
        drawdown="中",
        correlation="高相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="中",
        transparency="高",
    ),
    "cmb-quant-c": _seed(
        regime="公募",
        vehicle="场外开放式",
        family="主动权益",
        subtype=["股票多头", "量化选股"],
        implementation="量化",
        universe=["A股"],
        alpha=["选股Alpha", "贝塔增强"],
        volatility="高波",
        drawdown="中",
        correlation="高相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="高",
        transparency="高",
    ),
    "gj-multifactor-c": _seed(
        regime="公募",
        vehicle="场外开放式",
        family="主动权益",
        subtype=["量化选股", "指数增强"],
        implementation="量化",
        universe=["A股"],
        alpha=["选股Alpha", "贝塔增强"],
        volatility="高波",
        drawdown="中",
        correlation="高相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="高",
        transparency="高",
    ),
    "sxv264": _seed(
        regime="私募",
        vehicle="场外开放式",
        family="多策略",
        subtype=["复合多策略"],
        implementation="主观+量化",
        universe=["A股", "债券"],
        alpha=["选股Alpha", "Carry/票息"],
        volatility="低波",
        drawdown="强",
        correlation="中相关",
        preferred=["低波震荡", "流动性宽松"],
        weak=["高波反转", "流动性冲击"],
        stability="中",
        transparency="中",
    ),
    "savf63": _seed(
        regime="私募",
        vehicle="场外开放式",
        family="债券策略",
        subtype=["信用债"],
        implementation="主观+量化",
        universe=["债券"],
        alpha=["Carry/票息"],
        volatility="低波",
        drawdown="强",
        correlation="低相关",
        preferred=["低波震荡", "流动性宽松"],
        weak=["流动性冲击"],
        stability="中",
        transparency="中",
    ),
    "sazb60": _seed(
        regime="私募",
        vehicle="场外开放式",
        family="多策略",
        subtype=["复合多策略"],
        implementation="主观",
        universe=["A股", "债券"],
        alpha=["选股Alpha", "贝塔增强"],
        volatility="高波",
        drawdown="中",
        correlation="中相关",
        preferred=["流动性宽松", "单边上涨"],
        weak=["高波反转", "流动性冲击"],
        stability="中",
        transparency="中",
    ),
    "sample-yungu-1": _seed(
        regime="私募",
        vehicle="场外开放式",
        family="多策略",
        subtype=["复合多策略"],
        implementation="主观+量化",
        universe=["A股", "债券"],
        alpha=["选股Alpha", "Carry/票息"],
        volatility="低波",
        drawdown="强",
        correlation="中相关",
        preferred=["低波震荡", "流动性宽松"],
        weak=["高波反转", "流动性冲击"],
        stability="中",
        transparency="中",
    ),
    "sample-sanang-jinlu-1": _seed(
        regime="私募",
        vehicle="场外开放式",
        family="股票对冲",
        subtype=["市场中性", "统计套利"],
        implementation="量化",
        universe=["A股", "股指期货"],
        alpha=["选股Alpha", "高频/微观结构"],
        volatility="中波",
        drawdown="强",
        correlation="低相关",
        preferred=["低波震荡", "股指震荡"],
        weak=["高波反转", "流动性冲击"],
        stability="高",
        transparency="中",
    ),
    "svk343": _seed(
        regime="私募",
        vehicle="场外开放式",
        family="股票对冲",
        subtype=["市场中性", "统计套利"],
        implementation="量化",
        universe=["A股", "股指期货"],
        alpha=["选股Alpha", "高频/微观结构"],
        volatility="中波",
        drawdown="强",
        correlation="低相关",
        preferred=["低波震荡", "股指震荡"],
        weak=["高波反转", "流动性冲击"],
        stability="高",
        transparency="中",
    ),
    "anm38b": _seed(
        regime="私募",
        vehicle="场外开放式",
        family="股票对冲",
        subtype=["市场中性", "统计套利"],
        implementation="量化",
        universe=["A股", "股指期货"],
        alpha=["选股Alpha", "高频/微观结构"],
        volatility="低波",
        drawdown="强",
        correlation="低相关",
        preferred=["低波震荡", "股指震荡"],
        weak=["高波反转", "流动性冲击"],
        stability="高",
        transparency="中",
    ),
    "b3935b": _seed(
        regime="私募",
        vehicle="场外开放式",
        family="股票对冲",
        subtype=["市场中性"],
        implementation="主观+量化",
        universe=["A股", "股指期货"],
        alpha=["选股Alpha"],
        volatility="中波",
        drawdown="中",
        correlation="低相关",
        preferred=["低波震荡", "股指震荡"],
        weak=["高波反转", "流动性冲击"],
        stability="中",
        transparency="中",
    ),
    "sample-gtja-cta-2": _seed(
        regime="私募",
        vehicle="集合资管计划",
        family="CTA",
        subtype=["商品CTA", "金融CTA", "趋势跟踪"],
        implementation="量化",
        universe=["商品期货", "股指期货", "国债期货"],
        alpha=["趋势跟踪"],
        volatility="中波",
        drawdown="中",
        correlation="低相关",
        preferred=["高波趋势", "商品趋势"],
        weak=["无趋势震荡", "商品快速切换"],
        stability="高",
        transparency="中",
    ),
    "sbcj69": _seed(
        regime="私募",
        vehicle="集合资管计划",
        family="CTA",
        subtype=["商品CTA", "金融CTA", "趋势跟踪"],
        implementation="量化",
        universe=["商品期货", "股指期货", "国债期货"],
        alpha=["趋势跟踪"],
        volatility="中波",
        drawdown="中",
        correlation="低相关",
        preferred=["高波趋势", "商品趋势"],
        weak=["无趋势震荡", "商品快速切换"],
        stability="高",
        transparency="中",
    ),
    "zb945a": _seed(
        regime="私募",
        vehicle="场外开放式",
        family="主动权益",
        subtype=["股票多头"],
        implementation="主观",
        universe=["A股"],
        alpha=["选股Alpha"],
        volatility="中波",
        drawdown="中",
        correlation="高相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="中",
        transparency="中",
    ),
    "anz73a": _seed(
        regime="私募",
        vehicle="场外开放式",
        family="主动权益",
        subtype=["股票多头"],
        implementation="主观",
        universe=["A股"],
        alpha=["选股Alpha"],
        volatility="低波",
        drawdown="中",
        correlation="高相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="中",
        transparency="中",
    ),
    "are77a": _seed(
        regime="私募",
        vehicle="场外开放式",
        family="主动权益",
        subtype=["股票多头"],
        implementation="主观",
        universe=["A股"],
        alpha=["选股Alpha"],
        volatility="中波",
        drawdown="中",
        correlation="高相关",
        preferred=["单边上涨", "流动性宽松"],
        weak=["流动性冲击", "权益急跌后V形"],
        stability="中",
        transparency="中",
    ),
}


def _upsert_attribute_definitions(bind, definition_table, field_table) -> None:
    existing_keys = {
        row[0]
        for row in bind.execute(sa.select(definition_table.c.attribute_key))
    }
    for definition in FUND_ATTRIBUTE_DEFINITIONS:
        payload = {
            "label": definition["label"],
            "description": definition["description"],
            "data_type": definition["data_type"],
            "options_json": definition["options"],
            "is_groupable": definition["is_groupable"],
            "is_filterable": definition["is_filterable"],
            "is_view_column": definition["is_view_column"],
            "default_visible": definition["default_visible"],
        }
        if definition["attribute_key"] in existing_keys:
            bind.execute(
                sa.update(definition_table)
                .where(definition_table.c.attribute_key == definition["attribute_key"])
                .values(**payload)
            )
        else:
            bind.execute(
                sa.insert(definition_table).values(
                    attribute_key=definition["attribute_key"],
                    created_at=REFERENCE_CREATED_AT,
                    **payload,
                )
            )

        field_payload = _attribute_field_definition(definition)
        existing_field = bind.execute(
            sa.select(field_table.c.field_key).where(
                field_table.c.field_key == field_payload["field_key"]
            )
        ).first()
        if existing_field is None:
            bind.execute(sa.insert(field_table).values(**field_payload))
        else:
            bind.execute(
                sa.update(field_table)
                .where(field_table.c.field_key == field_payload["field_key"])
                .values(**field_payload)
            )


def _refresh_private_screening_views(bind, view_table, column_table) -> None:
    view_ids = [
        row[0]
        for row in bind.execute(
            sa.select(view_table.c.watchlist_view_id).where(
                view_table.c.watchlist_view_id.like(f"%::{PRIVATE_VIEW_ID}")
            )
        )
    ]
    if not view_ids:
        return
    bind.execute(
        sa.update(view_table)
        .where(view_table.c.watchlist_view_id.in_(view_ids))
        .values(
            default_filters_json=PRIVATE_VIEW_FILTERS,
            default_group_by="attr.strategy_family",
        )
    )
    bind.execute(
        sa.delete(column_table).where(column_table.c.watchlist_view_id.in_(view_ids))
    )
    for watchlist_view_id in view_ids:
        for field_key, display_order, width in PRIVATE_VIEW_COLUMNS:
            bind.execute(
                sa.insert(column_table).values(
                    watchlist_view_id=watchlist_view_id,
                    field_key=field_key,
                    display_order=display_order,
                    width=width,
                    is_visible=True,
                    pin_side=None,
                )
            )


def _current_attribute_values(bind, value_table, asset_id: str) -> dict[str, object]:
    rows = bind.execute(
        sa.select(
            value_table.c.attribute_key,
            value_table.c.value_json,
        )
        .where(value_table.c.asset_id == asset_id)
        .order_by(
            value_table.c.attribute_key,
            value_table.c.adopted_at.desc(),
            value_table.c.instrument_attribute_value_id.desc(),
        )
    )
    latest: dict[str, object] = {}
    for attribute_key, value_json in rows:
        if attribute_key not in latest:
            latest[str(attribute_key)] = value_json
    return latest


def _refresh_seeded_asset_payloads(bind, summary_table, row_table, value_table, asset_ids) -> None:
    for asset_id in asset_ids:
        merged_values = _current_attribute_values(bind, value_table, str(asset_id))
        bind.execute(
            sa.update(row_table)
            .where(row_table.c.asset_id == str(asset_id))
            .values(
                attributes_json=merged_values,
                last_recalculated_at=REFERENCE_CREATED_AT,
            )
        )

        summary_row = bind.execute(
            sa.select(summary_table.c.payload_json).where(
                summary_table.c.asset_id == str(asset_id)
            )
        ).first()
        if summary_row is None:
            continue
        payload_json = summary_row[0] if isinstance(summary_row[0], dict) else {}
        payload_json = dict(payload_json)
        payload_json["instrument_attributes"] = merged_values
        bind.execute(
            sa.update(summary_table)
            .where(summary_table.c.asset_id == str(asset_id))
            .values(payload_json=payload_json)
        )


def _seed_attribute_values(bind, asset_table, summary_table, row_table, value_table) -> None:
    asset_rows = bind.execute(
        sa.select(asset_table.c.asset_id).where(asset_table.c.asset_type == "fund")
    ).fetchall()
    if not asset_rows:
        return

    touched_asset_ids = []
    for (asset_id,) in asset_rows:
        seeded_values = EXAMPLE_ATTRIBUTE_VALUES.get(str(asset_id))
        if seeded_values is None:
            continue

        touched_asset_ids.append(str(asset_id))
        existing_values = _current_attribute_values(bind, value_table, str(asset_id))
        for attribute_key, value_json in seeded_values.items():
            if attribute_key in existing_values:
                continue
            bind.execute(
                sa.insert(value_table).values(
                    asset_id=str(asset_id),
                    attribute_key=attribute_key,
                    value_json=value_json,
                    effective_from=None,
                    adopted_at=REFERENCE_CREATED_AT,
                    source_record_id=ATTRIBUTE_SOURCE_RECORD_ID,
                )
            )
    _refresh_seeded_asset_payloads(
        bind,
        summary_table,
        row_table,
        value_table,
        touched_asset_ids,
    )


def upgrade() -> None:
    bind = op.get_bind()
    definition_table = sa.table(
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
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_group_by", sa.String()),
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
    asset_table = sa.table(
        "asset_detail",
        sa.column("asset_id", sa.String()),
        sa.column("asset_type", sa.String()),
    )
    summary_table = sa.table(
        "asset_summary_read_model",
        sa.column("asset_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )
    row_table = sa.table(
        "watchlist_row_read_model",
        sa.column("asset_id", sa.String()),
        sa.column("attributes_json", sa.JSON()),
        sa.column("last_recalculated_at", sa.DateTime(timezone=True)),
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

    _upsert_attribute_definitions(bind, definition_table, field_table)
    _refresh_private_screening_views(bind, view_table, column_table)
    _seed_attribute_values(bind, asset_table, summary_table, row_table, value_table)


def downgrade() -> None:
    bind = op.get_bind()
    definition_table = sa.table(
        "instrument_attribute_definition",
        sa.column("attribute_key", sa.String()),
    )
    field_table = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
    )
    value_table = sa.table(
        "instrument_attribute_value",
        sa.column("asset_id", sa.String()),
        sa.column("attribute_key", sa.String()),
        sa.column("source_record_id", sa.String()),
        sa.column("value_json", sa.JSON()),
        sa.column("adopted_at", sa.DateTime(timezone=True)),
        sa.column("instrument_attribute_value_id", sa.Integer()),
    )
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_filters_json", sa.JSON()),
    )
    column_table = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
    )
    summary_table = sa.table(
        "asset_summary_read_model",
        sa.column("asset_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )
    row_table = sa.table(
        "watchlist_row_read_model",
        sa.column("asset_id", sa.String()),
        sa.column("attributes_json", sa.JSON()),
        sa.column("last_recalculated_at", sa.DateTime(timezone=True)),
    )

    touched_asset_ids = [asset_id for asset_id in EXAMPLE_ATTRIBUTE_VALUES]
    bind.execute(
        sa.delete(value_table).where(value_table.c.source_record_id == ATTRIBUTE_SOURCE_RECORD_ID)
    )
    _refresh_seeded_asset_payloads(
        bind,
        summary_table,
        row_table,
        value_table,
        touched_asset_ids,
    )
    bind.execute(
        sa.delete(field_table).where(
            field_table.c.field_key.in_([f"attr.{item}" for item in ("fund_regime", "fund_vehicle")])
        )
    )
    bind.execute(
        sa.delete(definition_table).where(
            definition_table.c.attribute_key.in_(["fund_regime", "fund_vehicle"])
        )
    )

    watchlist_view_ids = [
        row[0]
        for row in bind.execute(
            sa.select(view_table.c.watchlist_view_id).where(
                view_table.c.watchlist_view_id.like(f"%::{PRIVATE_VIEW_ID}")
            )
        )
    ]
    if watchlist_view_ids:
        bind.execute(
            sa.update(view_table)
            .where(view_table.c.watchlist_view_id.in_(watchlist_view_ids))
            .values(default_filters_json={"asset_type": ["fund"]})
        )
        bind.execute(
            sa.delete(column_table).where(column_table.c.watchlist_view_id.in_(watchlist_view_ids))
        )
        legacy_columns = [
            ("asset_name", 1, 320),
            ("attr.strategy_family", 2, 140),
            ("attr.strategy_subtype", 3, 220),
            ("attr.implementation_style", 4, 140),
            ("attr.volatility_bucket", 5, 120),
            ("attr.drawdown_control", 6, 120),
            ("attr.equity_correlation_bucket", 7, 140),
            ("attr.preferred_regime", 8, 220),
            ("attr.weak_regime", 9, 220),
            ("attr.style_stability", 10, 120),
            ("attr.transparency_quality", 11, 120),
            ("data_freshness_status", 12, 140),
        ]
        for watchlist_view_id in watchlist_view_ids:
            for field_key, display_order, width in legacy_columns:
                bind.execute(
                    sa.insert(column_table).values(
                        watchlist_view_id=watchlist_view_id,
                        field_key=field_key,
                        display_order=display_order,
                        width=width,
                        is_visible=True,
                        pin_side=None,
                    )
                )
